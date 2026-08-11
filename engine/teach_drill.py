"""
Teach-drill phase: interleaved teach + immediate production drill.
Replaces the present + questions + guided_practice phases for grammar
new_skill sessions.
"""
import json
import re

from .core import call_llm


REDO_PENDING_MARKER = '<<REDO_PENDING>>'


def _strip_redo_pending_marker(text: str) -> tuple:
    """Return (cleaned_text, marker_present). Mirrors LESSON_COMPLETE handling.
    Emitted by the LLM when it deferred teaching a new unit in order to do a
    re-attempt of a wrong answer — code uses this to skip state advancement
    for that turn."""
    if REDO_PENDING_MARKER in text:
        return text.replace(REDO_PENDING_MARKER, '').rstrip(), True
    return text, False


UNIT_EXTRACTION_PROMPT = """You are decomposing a Spanish grammar skill into teachable units for a chunked lesson.

Skill name: {skill_name}
Skill description: {skill_description}
Student level: {cefr_level}

Return a JSON array of "units". Each unit is ONE thing that gets taught + drilled per lesson turn.

Rules for what counts as one unit:
- For skills involving multiple verbs: one verb per unit — UNLESS two verbs share the exact same conjugation (e.g. ser/ir → both fui/fuiste/fue). Group those into a single unit.
- For skills involving a single paradigm with multiple persons: one unit total; the drills will rotate through persons.
- For rule-based skills without paradigms (e.g. por/para distinction): one unit per rule or per usage context.

Each unit is a JSON object with these fields:
- "id": a short slug, unique within this skill (e.g. "ser_ir", "tener", "estar")
- "label": human-readable label for prompts (e.g. "ser / ir", "tener")
- "note": one line of teacher-facing metadata — the stem, a spelling quirk, a meaning shift, or empty string if nothing special. NOT taught verbatim to the student, but shapes what you teach.

Return ONLY the JSON array. No prose, no markdown fences, no explanation. Example valid response:
[{{"id":"ser_ir","label":"ser / ir","note":"share fui/fuiste/fue conjugation"}},{{"id":"estar","label":"estar","note":"stem estuv-"}}]"""


async def extract_units(skill_name: str, skill_description: str, cefr_level: str) -> list[dict]:
    """Ask the LLM to enumerate teachable units for a skill. Returns [] on parse failure."""
    prompt = UNIT_EXTRACTION_PROMPT.format(
        skill_name=skill_name,
        skill_description=skill_description,
        cefr_level=cefr_level,
    )
    raw = await call_llm([{"role": "user", "content": prompt}])
    return _parse_units_json(raw)


def _parse_units_json(raw: str) -> list[dict]:
    """Extract JSON array from LLM output. Handles markdown fences and stray prose."""
    # Strip common markdown code fences.
    cleaned = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip(), flags=re.MULTILINE)
    # Find the first [...] block, tolerating leading/trailing prose.
    match = re.search(r'\[.*\]', cleaned, flags=re.DOTALL)
    if not match:
        return []
    try:
        units = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(units, list):
        return []
    # Coerce shape — drop entries missing required keys.
    return [
        {"id": str(u.get("id", "")), "label": str(u.get("label", "")), "note": str(u.get("note", ""))}
        for u in units
        if isinstance(u, dict) and u.get("id") and u.get("label")
    ]


# ── Rotation / spacing (pure logic, no I/O) ───────────────────────────────────

PERSONS = ("yo", "tú", "él", "nosotros", "ellos")


def next_teach_unit(units: list[dict], taught_ids: list[str]) -> dict | None:
    """Return the next un-taught unit in declaration order, or None if all taught."""
    taught_set = set(taught_ids)
    for u in units:
        if u["id"] not in taught_set:
            return u
    return None


def select_retrieval_unit(taught_ids: list[str], drills: dict) -> str | None:
    """Pick a previously-taught unit for spaced retrieval.
    Least-drilled wins; ties break by earlier-taught (index in taught_ids)."""
    if not taught_ids:
        return None
    return min(
        taught_ids,
        key=lambda uid: (len(drills.get(uid, [])), taught_ids.index(uid)),
    )


def select_person(unit_id: str, drills: dict) -> str:
    """Return the next person (yo/tú/él/nosotros/ellos) to drill for a unit.
    Rotates through unused persons first; when all used, picks the least frequent
    (with canonical-order tiebreak)."""
    drilled = drills.get(unit_id, [])
    counts = {p: drilled.count(p) for p in PERSONS}
    # min by (count, canonical index) — canonical order is PERSONS order.
    return min(PERSONS, key=lambda p: (counts[p], PERSONS.index(p)))


# ── State schema helpers ──────────────────────────────────────────────────────

from asgiref.sync import sync_to_async


EMPTY_STATE = {
    "units": [],
    "taught": [],
    "drills": {},
    "turn_count": 0,
    "lesson_complete": False,
}


def get_state(session) -> dict:
    """Return the teach_drill sub-dict of session.quiz_state, or a fresh empty structure.
    Never mutates the session; returns a new dict for the caller to modify + save."""
    qs = session.quiz_state or {}
    td = qs.get("teach_drill")
    if not td:
        # Return a fresh copy so callers don't share references.
        return {**EMPTY_STATE, "units": [], "taught": [], "drills": {}}
    # Copy defensively so caller mutation doesn't hit the session's dict.
    return {
        "units": list(td.get("units", [])),
        "taught": list(td.get("taught", [])),
        "drills": {k: list(v) for k, v in td.get("drills", {}).items()},
        "turn_count": int(td.get("turn_count", 0)),
        "lesson_complete": bool(td.get("lesson_complete", False)),
    }


async def save_state(session, state: dict) -> None:
    """Persist teach_drill state back to session.quiz_state."""
    from learner.models import Session as SessionModel
    current = session.quiz_state or {}
    updated = {**current, "teach_drill": state}
    await sync_to_async(
        lambda: SessionModel.objects.filter(pk=session.pk).update(quiz_state=updated)
    )()
    session.quiz_state = updated


def mark_taught(state: dict, unit_id: str) -> dict:
    """Idempotent: append unit_id to state['taught'] if not present."""
    if unit_id not in state["taught"]:
        state["taught"].append(unit_id)
    return state


def mark_drilled(state: dict, unit_id: str, person: str) -> dict:
    """Append person to state['drills'][unit_id], creating list if needed."""
    state["drills"].setdefault(unit_id, []).append(person)
    return state


def mark_complete(state: dict) -> dict:
    """Set state['lesson_complete'] = True."""
    state["lesson_complete"] = True
    return state


# ── Prompt templates ──────────────────────────────────────────────────────────

TEACH_DRILL_OPENING_PROMPT = """You are Luz Angela, opening a bite-sized chunked lesson on {skill_name} for a Spanish student.

Student level: {cefr_level}
Student interests: {interests}

This is the FIRST turn. Write ONLY the opening framing — do NOT teach any specific verb or paradigm yet. The next turn will do that.

Structure (2-4 short sentences total):
1. Outcome-framing — tell the student what they'll be able to DO after this lesson, with 1-2 concrete example target sentences in Spanish (translations in parens). Example: "You'll learn to talk about things that happened in the past — sentences like 'Ayer fui al cine' (I went to the movies)."
2. Explain that you'll teach one piece at a time and drill each piece immediately, so it locks in.
3. End with the literal line: Ready to start?

Chat style. No headers, no bullet points, no textbook tone.
Do not preview specific verbs or forms — the actual teaching starts next turn."""


TEACH_DRILL_CONTINUATION_SUFFIX = """CURRENT PHASE: Teach-drill loop for {skill_name}

You will receive a specific instruction each turn telling you which unit to teach and which questions to ask. Follow it exactly. Do NOT invent additional content or drill unrelated items.

Paradigm formatting (critical): when showing a conjugation, render it ONE LINE PER PERSON:
    Yo tuve
    Tú tuviste
    Él/ella/usted tuvo
    Nosotros tuvimos
    Ellos/ellas/ustedes tuvieron
(Latin American — skip vosotros.) Never inline comma-separated lists.

Chunk sizing (critical): teach EXACTLY ONE unit per turn, unless the instruction explicitly names a shared-paradigm pair.

Evaluation format: one line per response — "✓" plus short confirmation, or "✗" plus the correct form and a brief reason. No praise, no excess.

Wrong-answer reinforcement (critical): when ANY of the student's answers this turn get ✗, you MUST do a re-attempt instead of teaching new content:
  1. Write the ✗ line(s) with correct form and brief reason (same as normal evaluation).
  2. For each ✗ answer, add a line: "Try again: [restate that question with slight rephrasing]" — the redo must be about the SAME grammar item they just missed, not a new one.
  3. Do NOT teach a new unit and do NOT ask any new drill questions this turn — the instruction's teach/drill steps are DEFERRED.
  4. End the message with the literal marker on its own line: <<REDO_PENDING>>
On the FOLLOWING turn: evaluate the redo attempt(s). If correct, briefly acknowledge (✓ one line) and THEN follow the normal per-turn instruction (teach + drill). If the redo is wrong AGAIN, give the definitive correct answer in ONE line and THEN follow the normal instruction — do NOT ask a third time (avoid frustration spirals).

The marker <<LESSON_COMPLETE>>, if the instruction asks you to emit it, goes on its own line at the very end. Never emit it otherwise.
The marker <<REDO_PENDING>>, when emitted, goes on its own line at the very end. Never emit BOTH markers in the same message.

Chat style, no bold headers."""


def build_retrieval_only_instruction(retrieval: dict, person_retrieve: str) -> str:
    """Instruction for a drill-only turn (no new teaching): review a prior unit."""
    return (
        f"1) Evaluate the student's previous response in ONE line if it contained an answer (✓/✗ format).\n\n"
        f"2) Do NOT teach any new content — this is a review turn.\n\n"
        f"3) Ask ONE production question testing **{retrieval['label']}** in the "
        f"**{person_retrieve}** form. English cue that requires the target form. "
        f"Brief context ('quick review:' or similar) is fine, but no paradigms.\n\n"
        f"4) End with a natural line inviting the student to answer. Do NOT emit <<LESSON_COMPLETE>>."
    )


def build_teach_instruction(unit: dict, retrieval: dict | None,
                            person_new: str, person_retrieve: str | None,
                            is_final: bool) -> str:
    """Assemble the per-turn instruction telling the LLM exactly what to do."""
    parts = []

    # If we're evaluating a prior response, the LLM sees the response in the
    # conversation history (the previous user turn). It should evaluate first.
    parts.append(
        f"1) If the student's previous message contained answer attempts, evaluate them "
        f"in ONE LINE EACH before doing anything else. Use ✓/✗ format."
    )

    label = unit["label"]
    note = unit["note"] or "no special notes"
    parts.append(
        f"2) Teach exactly this unit: **{label}** (metadata: {note}).\n"
        f"   - Show the full paradigm one line per person (Yo/Tú/Él/Nosotros/Ellos).\n"
        f"   - Give ONE natural example sentence using an item from this paradigm.\n"
        f"   - Keep it under 100 words for this step."
    )

    parts.append(
        f"3) Immediately ask ONE production question testing **{label}** in the "
        f"**{person_new}** form. Give an English cue that requires the student to "
        f"produce the target form (e.g. 'How would you say _she had a great time_?')."
    )

    if retrieval is not None and person_retrieve is not None:
        parts.append(
            f"4) Then ask ONE production question testing a PRIOR unit: "
            f"**{retrieval['label']}** in the **{person_retrieve}** form. "
            f"Again, English cue that requires the target form."
        )

    if is_final:
        parts.append(
            f"5) End the message with the literal marker on its own line: <<LESSON_COMPLETE>>"
        )
    else:
        parts.append(
            f"5) End the message with a natural line inviting the student to answer "
            f"(no ceremony — just wait for their reply). Do NOT emit <<LESSON_COMPLETE>>."
        )

    return "\n\n".join(parts)


# ── Turn handler ──────────────────────────────────────────────────────────────

from .session import _strip_lesson_complete_marker, _build_new_skill_history


TEACH_DRILL_MAX_TURNS = 16


async def handle_teach_drill_turn(user, session, text: str) -> dict:
    """Orchestrate one teach_drill turn: pick next actions from state, call LLM, update state."""
    from learner.models import SessionEvent

    state = get_state(session)

    # Record student response on the pending event BEFORE the early-return check.
    # Even if the lesson is already complete, the student's reply that triggered
    # the advance-to-assessment must be persisted so the scoring pipeline sees it.
    events = await sync_to_async(
        lambda: list(session.events.order_by('timestamp')[:40])
    )()
    pending = next((e for e in reversed(events) if not e.user_response), None)
    if pending:
        await sync_to_async(
            lambda: SessionEvent.objects.filter(pk=pending.pk).update(user_response=text)
        )()
        pending.user_response = text

    # If we've already told the caller the lesson is complete, this call is a no-op
    # from our side — caller should have transitioned to assessment already, but
    # in case they poll us: return the advance flag.
    if state["lesson_complete"]:
        return {"text": "", "audio_url": None, "session_ended": False,
                "advance_to_assessment": True}

    units = state["units"]
    taught_ids = state["taught"]
    drills = state["drills"]

    # Pick next unit (or None if all taught).
    next_unit = next_teach_unit(units, taught_ids)

    # Force completion at safety cap.
    force_complete = state["turn_count"] >= TEACH_DRILL_MAX_TURNS

    # Every taught unit has been drilled at least twice (invariant target for completion).
    all_units_drilled = all(len(drills.get(uid, [])) >= 2 for uid in taught_ids)

    if next_unit is None:
        if all_units_drilled or force_complete:
            # Wrap-up turn: no drill, emit marker.
            retrieval = None
            person_new = "yo"  # unused
            person_retrieve = None
            is_final = True
        else:
            # Retrieval-only turn: drill an under-drilled prior unit; do not teach anything new.
            retrieval_id = select_retrieval_unit(taught_ids, drills)
            retrieval = next((u for u in units if u["id"] == retrieval_id), None) if retrieval_id else None
            person_new = "yo"  # unused when next_unit is None
            person_retrieve = select_person(retrieval["id"], drills) if retrieval else None
            is_final = False
    else:
        retrieval_id = select_retrieval_unit(taught_ids, drills)
        retrieval = next((u for u in units if u["id"] == retrieval_id), None) if retrieval_id else None
        person_new = select_person(next_unit["id"], drills)
        person_retrieve = select_person(retrieval["id"], drills) if retrieval else None
        # is_final: this is the last unit AND after this turn every unit will have >=2 drills.
        will_have_2 = len(drills.get(next_unit["id"], [])) + 1 >= 2 and all(
            len(drills.get(uid, [])) >= 2 for uid in taught_ids if uid != next_unit["id"]
        )
        is_final = force_complete or (
            len(taught_ids) == len(units) - 1 and will_have_2
        )

    # Build message history using session events + append the per-turn instruction.
    history = _build_new_skill_history(events)
    if next_unit is not None:
        instruction = build_teach_instruction(
            next_unit, retrieval, person_new, person_retrieve, is_final,
        )
    elif retrieval is not None:
        instruction = build_retrieval_only_instruction(retrieval, person_retrieve)
    else:
        # Wrap-up turn: just ask for a brief recap and the marker.
        instruction = (
            "1) Evaluate the student's previous response in ONE line if it contained an answer.\n\n"
            "2) Briefly recap the units taught in ONE sentence, no lists.\n\n"
            "3) End with the literal marker on its own line: <<LESSON_COMPLETE>>"
        )
    history.append({"role": "user", "content": instruction})

    # Call LLM.
    skill_name = session.target_skill.name if session.target_skill else "this skill"
    suffix = TEACH_DRILL_CONTINUATION_SUFFIX.format(skill_name=skill_name)
    response = await call_llm(history, user=user, system_suffix=suffix)

    # Strip markers.
    response, marker_seen = _strip_lesson_complete_marker(response)
    response, redo_pending = _strip_redo_pending_marker(response)

    # Update state.
    # If the LLM emitted <<REDO_PENDING>>, it deferred teaching to do a re-attempt.
    # Skip the taught/drilled updates for THIS turn — the same next_unit/retrieval
    # will be picked again next turn (after the redo is resolved) and taught then.
    # turn_count still advances so the safety cap remains meaningful.
    if not redo_pending:
        if next_unit is not None:
            mark_taught(state, next_unit["id"])
            mark_drilled(state, next_unit["id"], person_new)
            if retrieval is not None and person_retrieve is not None:
                mark_drilled(state, retrieval["id"], person_retrieve)
        elif retrieval is not None and person_retrieve is not None:
            # Retrieval-only turn: still record the drill.
            mark_drilled(state, retrieval["id"], person_retrieve)
    state["turn_count"] += 1
    if marker_seen or force_complete or (next_unit is None and all_units_drilled):
        mark_complete(state)

    await save_state(session, state)

    # Persist the LLM response as a new SessionEvent for the next turn to build on.
    await sync_to_async(SessionEvent.objects.create)(
        session=session, event_type='conversation',
        content=response, user_response='',
    )

    return {
        "text": response,
        "audio_url": None,
        "session_ended": False,
        "advance_to_assessment": False,
    }
