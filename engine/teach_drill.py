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


def select_retrieval_unit(taught_ids: list[str], drills: dict,
                          skip_most_recent: bool = False) -> str | None:
    """Pick a previously-taught unit for spaced retrieval.
    Least-drilled wins; ties break by earlier-taught (index in taught_ids).
    If skip_most_recent=True, exclude the most-recently-taught unit when there
    are alternatives — improves spacing between teach and retrieval of the
    same unit. Falls back to including it if it's the only option."""
    if not taught_ids:
        return None
    candidates = taught_ids[:-1] if (skip_most_recent and len(taught_ids) > 1) else taught_ids
    if not candidates:
        return None
    return min(
        candidates,
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
    "last_turn_type": None,  # "teach" | "retrieval" | None — drives Option-B alternation
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
        "last_turn_type": td.get("last_turn_type"),
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
2. Explain the pattern: you'll teach one verb, drill it with ONE question, then on the next turn ask ONE recall question about an earlier verb. Bite-sized — one thing at a time.
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

One-question-per-turn (critical): each turn asks EXACTLY ONE production question — never two. Whether the instruction is a teach turn or a retrieval turn, the last thing you write to the student is a single question.

Evaluation format: one line per response — "✓" plus short confirmation, or "✗" plus the correct form and a brief reason. No praise, no excess.

Wrong-answer reinforcement (CRITICAL — strictly enforced): when the student's answer gets ✗, your ENTIRE response this turn is limited to EXACTLY these parts, in this order, and nothing else:
  1. The ✗ line with correct form and brief reason (one line).
  2. "Try again: [restate the SAME question with slight rephrasing]" (one line).
  3. The literal marker on its own line at the end: <<REDO_PENDING>>

Forbidden on a redo turn (any of these = pattern violation):
  - Paradigms of ANY verb (no conjugation tables)
  - Introducing a new verb by name
  - Phrases like "let me show you", "here's the paradigm", "Aquí está", "Hablando de X"
  - Any new drill or production question
  - Any teaching content, even brief

If the student's answer used a different but grammatically valid verb (e.g. they used estar when you asked for ser): STILL just say the ✗ line ("that's estar; for ser use fui"), the try-again line, and the marker. Do NOT teach the other verb — that's a separate lesson.

On the FOLLOWING turn: evaluate the redo attempt in ONE line. If correct, briefly ✓ acknowledge and THEN follow the normal per-turn instruction. If wrong AGAIN, give the definitive correct answer in ONE line and THEN follow the normal instruction — do NOT ask a third time.

The marker <<LESSON_COMPLETE>>, if the instruction asks you to emit it, goes on its own line at the very end. Never emit it otherwise.
The marker <<REDO_PENDING>>, when emitted, goes on its own line at the very end. Never emit BOTH markers in the same message.

Chat style, no bold headers."""


CUE_SELECTION_RULES = """CUE SELECTION (critical): the English cue MUST have exactly one natural Spanish translation using the target verb. Before writing the cue, verify: could a native Spanish speaker translate this English sentence naturally using a DIFFERENT verb (estar/ser/hacer/tener/etc.)? If yes, the cue is ambiguous — pick a different one.

Common traps to AVOID:
- For ser: NEVER use "I was at [location]" or "I was [tired/happy/hungry]" — those are estar, not ser. Use identity cues like "I was a student", "It was 3pm", "She was from Medellín".
- For ir: use motion cues like "I went to X", "We went to the party".
- For estar: use location or state cues like "I was at Y", "You were tired".
- For tener: use possession or obligation cues like "I had a headache", "You had to work".
- For hacer: use actions like "What did you do yesterday", "I made breakfast".
- For poder: use ability/managed-to cues like "I couldn't finish", "She was able to leave early". Note preterite meaning is "managed to" not just "could".
- For querer: use "tried to / meant to" cues, not just "wanted" (preterite meaning shift).
- For saber: use "found out / learned" cues, not just "knew" (preterite meaning shift)."""


REDO_FIRST_CHECK = """FIRST DECISION (do this BEFORE anything else): did the student's previous answer contain ANY error? Any preposition mistake, gender/agreement error, tense error, wrong verb form, wrong verb choice, spelling error, or missing accent counts as an error. Do NOT soften with "Close, but..." or partial-credit — an answer is either fully correct (✓) or has an error (✗).

  - If the answer had ANY error (✗) — your ENTIRE response this turn is the REDO pattern, nothing else:
    * The ✗ line with the correct form and a brief reason (one line).
    * "Try again: [restate the SAME question with a slight rephrasing]" (one line).
    * The literal marker on its own line at the very end: <<REDO_PENDING>>
    STOP THERE. Do NOT execute the teach/drill steps below this turn.
    Do NOT introduce a new verb, do NOT show a new paradigm, do NOT ask any other question.

  - If the answer was fully correct (✓): write a single "✓" line acknowledging it, then continue to the steps below.
  - If there was no prior answer (this is the first turn): just proceed to the steps below."""


def build_retrieval_only_instruction(retrieval: dict, person_retrieve: str) -> str:
    """Instruction for a drill-only turn (no new teaching): review a prior unit."""
    return (
        f"{REDO_FIRST_CHECK}\n\n"
        f"— IF you passed the first check (answer was ✓ or no prior answer), continue: —\n\n"
        f"1) Do NOT teach any new content — this is a review turn.\n\n"
        f"2) Ask ONE production question testing **{retrieval['label']}** in the "
        f"**{person_retrieve}** form. Brief context ('quick review:' or similar) is fine, "
        f"but no paradigms.\n\n"
        f"{CUE_SELECTION_RULES}\n\n"
        f"3) End with a natural line inviting the student to answer. Do NOT emit <<LESSON_COMPLETE>>."
    )


def build_teach_instruction(unit: dict, person_new: str, is_final: bool) -> str:
    """Instruction for a teach turn: teach one unit + ONE production question about it.
    No retrieval on teach turns — retrieval happens on its own alternating turn."""
    parts = []

    parts.append(REDO_FIRST_CHECK)

    parts.append("— IF you passed the first check (answer was ✓ or no prior answer), continue: —")

    label = unit["label"]
    note = unit["note"] or "no special notes"
    parts.append(
        f"1) Teach exactly this unit: **{label}** (metadata: {note}).\n"
        f"   - Show the full paradigm one line per person (Yo/Tú/Él/Nosotros/Ellos).\n"
        f"   - Give ONE natural example sentence using an item from this paradigm.\n"
        f"   - Keep it under 100 words for this step."
    )

    parts.append(
        f"2) Ask EXACTLY ONE production question testing **{label}** in the "
        f"**{person_new}** form. English cue that requires the student to produce the target form."
    )

    parts.append(CUE_SELECTION_RULES)

    if is_final:
        parts.append(
            "3) End the message with the literal marker on its own line: <<LESSON_COMPLETE>>"
        )
    else:
        parts.append(
            "3) End with a natural line inviting the student to answer "
            "(no ceremony — just wait for their reply). Do NOT emit <<LESSON_COMPLETE>>."
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

    # Pick next un-taught unit (or None if all taught).
    next_unit = next_teach_unit(units, taught_ids)

    # Force completion at safety cap.
    force_complete = state["turn_count"] >= TEACH_DRILL_MAX_TURNS

    # Every taught unit has been drilled at least twice (invariant target for completion).
    all_units_drilled = all(len(drills.get(uid, [])) >= 2 for uid in taught_ids)

    # Decide turn type: teach | retrieval | wrap_up. Alternate teach/retrieval so
    # each turn asks exactly ONE question — teach turns teach + drill new unit;
    # retrieval turns drill a prior unit (no new content).
    last_type = state.get("last_turn_type")

    if next_unit is None and (all_units_drilled or force_complete):
        turn_type = "wrap_up"
    elif next_unit is None:
        # No new units, but some under-drilled — do retrieval to reach ≥2 drills.
        turn_type = "retrieval"
    elif last_type == "teach" and len(taught_ids) >= 1:
        # Just taught; alternate to retrieval of a prior unit.
        turn_type = "retrieval"
    else:
        # First turn OR just did retrieval — teach the next unit.
        turn_type = "teach"

    # Resolve unit/person for the chosen turn type.
    teach_unit = None
    teach_person = None
    retrieval_unit = None
    retrieval_person = None

    if turn_type == "teach":
        teach_unit = next_unit
        teach_person = select_person(next_unit["id"], drills)
        # is_final: this is the last unit AND after this turn all units drilled ≥2.
        # In Option B, "final teach" isn't necessarily the final TURN — there may still
        # be retrieval turns needed after. Only emit <<LESSON_COMPLETE>> on a true wrap-up.
        is_final = False
    elif turn_type == "retrieval":
        # Skip the most-recently-taught only while we still have units to teach
        # (improves spacing). Once teaching is done and we're filling in drills
        # to hit ≥2 per unit, drill the actually-under-drilled unit regardless.
        skip_recent = (next_unit is not None)
        retrieval_id = select_retrieval_unit(taught_ids, drills, skip_most_recent=skip_recent)
        retrieval_unit = next((u for u in units if u["id"] == retrieval_id), None) if retrieval_id else None
        retrieval_person = select_person(retrieval_unit["id"], drills) if retrieval_unit else None
        is_final = False
        # Edge case: retrieval was chosen but no candidate found (shouldn't happen
        # given the logic above, but safety net) — fall back to teach.
        if retrieval_unit is None and next_unit is not None:
            turn_type = "teach"
            teach_unit = next_unit
            teach_person = select_person(next_unit["id"], drills)
    else:  # wrap_up
        is_final = True

    # Build message history and per-turn instruction.
    history = _build_new_skill_history(events)
    if turn_type == "teach":
        instruction = build_teach_instruction(teach_unit, teach_person, is_final)
    elif turn_type == "retrieval":
        instruction = build_retrieval_only_instruction(retrieval_unit, retrieval_person)
    else:  # wrap_up
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
    # If the LLM emitted <<REDO_PENDING>>, it deferred teach/retrieval to do a
    # re-attempt. Skip mark_taught/mark_drilled and last_turn_type updates — the
    # same turn will replay next call (after the redo is resolved). turn_count
    # still advances so the safety cap remains meaningful.
    if not redo_pending:
        if turn_type == "teach":
            mark_taught(state, teach_unit["id"])
            mark_drilled(state, teach_unit["id"], teach_person)
            state["last_turn_type"] = "teach"
        elif turn_type == "retrieval" and retrieval_unit is not None:
            mark_drilled(state, retrieval_unit["id"], retrieval_person)
            state["last_turn_type"] = "retrieval"
        # wrap_up doesn't update taught/drilled; marker or force_complete handles completion.
    state["turn_count"] += 1
    if marker_seen or force_complete or (turn_type == "wrap_up"):
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
