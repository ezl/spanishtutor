"""
Teach-drill phase: interleaved teach + immediate production drill.
Replaces the present + questions + guided_practice phases for grammar
new_skill sessions.
"""
import json
import logging
import re

from .core import call_llm

logger = logging.getLogger(__name__)


REDO_PENDING_MARKER = '<<REDO_PENDING>>'


def _strip_redo_pending_marker(text: str) -> tuple:
    """Return (cleaned_text, marker_present). Mirrors LESSON_COMPLETE handling.
    Emitted by the LLM when it deferred teaching a new unit in order to do a
    re-attempt of a wrong answer — code uses this to skip state advancement
    for that turn."""
    if REDO_PENDING_MARKER in text:
        return text.replace(REDO_PENDING_MARKER, '').rstrip(), True
    return text, False


QUESTION_ANSWERED_MARKER = '<<QUESTION_ANSWERED>>'


def _strip_question_answered_marker(text: str) -> tuple:
    """Return (cleaned_text, marker_present). Emitted by the LLM when the
    student's message was a content question, an ambient acknowledgment, or
    otherwise not a drill answer — the LLM answered / acknowledged and
    re-served the pending drill. Code uses this to skip state advancement so
    the same drill fires again next turn and gets its actual answer."""
    if QUESTION_ANSWERED_MARKER in text:
        return text.replace(QUESTION_ANSWERED_MARKER, '').rstrip(), True
    return text, False


END_LESSON_EARLY_MARKER = '<<END_LESSON_EARLY>>'


def _strip_end_lesson_early_marker(text: str) -> tuple:
    """Return (cleaned_text, marker_present). Emitted by the LLM when the
    student explicitly asked to stop the current lesson ("let's move on",
    "skip this", "let's do vocab instead", "I'm done"). Code closes the
    session immediately (still runs scoring on the transcript so far) —
    does NOT transition to assessment."""
    if END_LESSON_EARLY_MARKER in text:
        return text.replace(END_LESSON_EARLY_MARKER, '').rstrip(), True
    return text, False


FEEDBACK_MARKER_OPEN = '<<FEEDBACK>>'
FEEDBACK_MARKER_CLOSE = '<<END_FEEDBACK>>'

# Canonical acknowledgment prepended by code when the LLM emits a FEEDBACK
# marker. Prompt instructs the LLM NOT to write its own acknowledgment;
# code guarantees the exact wording so it's deterministic across sessions.
FEEDBACK_ACKNOWLEDGMENT = "Got it, thanks for the feedback. Sigamos."


def _strip_feedback_marker(text: str) -> tuple:
    """Return (cleaned_text, interpretation_or_none). Extracts the LLM's
    paraphrase from between the FEEDBACK markers and removes the marker
    block (including its content) from the visible text. Only the FIRST
    marker block is honored — additional blocks (LLM emitting twice by
    accident) are left in place, protecting against double-log."""
    open_idx = text.find(FEEDBACK_MARKER_OPEN)
    if open_idx == -1:
        return text, None
    close_idx = text.find(FEEDBACK_MARKER_CLOSE, open_idx + len(FEEDBACK_MARKER_OPEN))
    if close_idx == -1:
        return text, None
    interpretation = text[open_idx + len(FEEDBACK_MARKER_OPEN):close_idx]
    end_of_block = close_idx + len(FEEDBACK_MARKER_CLOSE)
    prefix = text[:open_idx].rstrip('\n')
    suffix = text[end_of_block:].lstrip('\n')
    if prefix and suffix:
        cleaned = prefix + '\n' + suffix
    else:
        cleaned = prefix + suffix
    return cleaned, interpretation


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
- "kind": "paradigm" if this unit is a verb that gets conjugated, "usage" if it is a rule or usage context with no verb of its own. A "usage" unit is never rendered as a conjugation table.

For "paradigm" units ONLY, also include:
- "verb": the infinitive being conjugated (e.g. "ir")
- "known_tense": the Spanish name of the tense the student ALREADY knows, which forms the left side of each contrast row (e.g. "presente")
- "known_forms": an object mapping person to the known-tense form, for yo/tú/él/nosotros/ellos (e.g. {{"yo":"voy","tú":"vas","él":"va","nosotros":"vamos","ellos":"van"}})

"known_forms" must be the OTHER tense, never the tense being taught. If the known and target form for a person would be identical, you have picked the wrong known tense.

Return ONLY the JSON array. No prose, no markdown fences, no explanation. Example valid response:
[{{"id":"ser_ir","label":"ser / ir","note":"share fui/fuiste/fue conjugation","kind":"paradigm","verb":"ir","known_tense":"presente","known_forms":{{"yo":"voy","tú":"vas","él":"va","nosotros":"vamos","ellos":"van"}}}},{{"id":"trigger_words","label":"Trigger words","note":"ayer, una vez","kind":"usage"}}]"""


# The prompt asks for known_forms (five conjugated forms per verb), which puts a
# realistic response around 1250 tokens. call_llm's 1024 default truncated every
# response mid-array, so nothing parsed and every grammar lesson silently fell
# back to the legacy dense prompt. Sized with headroom rather than to fit.
UNIT_EXTRACTION_MAX_TOKENS = 4096


def _is_incomplete_array(raw: str) -> bool:
    """True when the response opened a JSON array but never closed it.

    Distinguishes a cut-off response (worth retrying) from a well-formed empty
    array (a real answer). Without this the two are indistinguishable and a
    truncation is silently reported as "this skill has no units".
    """
    cleaned = re.sub(r'^```(?:json)?\s*|\s*```$', '', (raw or '').strip(),
                     flags=re.MULTILINE)
    return '[' in cleaned and re.search(r'\[.*\]', cleaned, flags=re.DOTALL) is None


async def extract_units(skill_name: str, skill_description: str, cefr_level: str) -> list[dict]:
    """Ask the LLM to enumerate teachable units for a skill. Returns [] on parse failure."""
    prompt = UNIT_EXTRACTION_PROMPT.format(
        skill_name=skill_name,
        skill_description=skill_description,
        cefr_level=cefr_level,
    )
    messages = [{"role": "user", "content": prompt}]
    raw = await call_llm(messages, max_tokens=UNIT_EXTRACTION_MAX_TOKENS)
    units = _parse_units_json(raw)

    if not units and _is_incomplete_array(raw):
        # Log the evidence: a bare "returned empty" warning gave no way to tell
        # truncation from a genuine empty result.
        logger.warning(
            "unit extraction truncated for %s (%d chars, array never closed); retrying. tail=%r",
            skill_name, len(raw), raw[-120:],
        )
        raw = await call_llm(messages, max_tokens=UNIT_EXTRACTION_MAX_TOKENS)
        units = _parse_units_json(raw)

    return units


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
    # Coerce shape — drop entries missing required keys. Optional paradigm fields
    # are carried through when present: dropping them would strip the known side
    # back off the unit and reintroduce the derivation the model gets wrong.
    coerced = []
    for u in units:
        if not (isinstance(u, dict) and u.get("id") and u.get("label")):
            continue
        unit = {
            "id": str(u.get("id", "")),
            "label": str(u.get("label", "")),
            "note": str(u.get("note", "")),
        }
        if u.get("kind") in ("usage", "paradigm"):
            unit["kind"] = u["kind"]
        if u.get("verb"):
            unit["verb"] = str(u["verb"])
        if u.get("known_tense"):
            unit["known_tense"] = str(u["known_tense"])
        known_forms = u.get("known_forms")
        if isinstance(known_forms, dict) and known_forms:
            unit["known_forms"] = {str(k): str(v) for k, v in known_forms.items()}
        coerced.append(unit)
    return coerced


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
    "turn_count": 0,       # every LLM call, including deferred (side questions, redos)
    "progress_count": 0,   # only teach/retrieval/wrap-up turns that actually advanced state
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
        "progress_count": int(td.get("progress_count", 0)),
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

TEACH_DRILL_OPENING_PROMPT = """You are Luz Ángela, opening a bite-sized chunked lesson on {skill_name} for a Spanish student.

Student level: {cefr_level}
Student interests: {interests}

This is the FIRST turn. Write ONLY the opening framing — do NOT teach any specific verb or paradigm yet. The next turn will do that.

LANGUAGE RULE (critical — read before choosing wording):
- If the student's level is A1 or A2: respond in English. Spanish appears only inside quoted target sentences or glosses.
- If the student's level is B1 or higher: respond in Spanish. English appears only as parenthetical glosses per the glossing rule, or inside a drill-cue that requires an English trigger. Assume B1+ students know common vocabulary like aprender, hablar, cosa, hoy, listo — do NOT translate those into English mid-sentence.
- NEVER code-switch mid-sentence at any level. "Hoy vas a learn how" is a violation — a sentence that starts in Spanish stays in Spanish; a sentence that starts in English stays in English. Parenthetical glosses at the end of a sentence do NOT count as code-switching.

Structure (2-4 short sentences total):

1. Outcome-framing — tell the student what they'll be able to DO after this lesson, illustrated with 1-2 concrete example target sentences in Spanish (with English translation in parens for A1/A2 or B1; drop the gloss for B2+).

   Example for A1 / A2 (English framing):
   "You'll learn to talk about things that happened in the past — sentences like 'Ayer fui al cine' (I went to the movies) or 'No pude ir al gimnasio' (I couldn't go to the gym)."

   Example for B1 (Spanish framing, glosses on target sentences):
   "Después de esta lección vas a poder hablar de cosas que ya pasaron — frases como 'Ayer fui al cine' (I went to the movies) o 'No pude ir al gimnasio' (I couldn't go to the gym)."

   Example for B2+ (Spanish framing, no glosses on target sentences):
   "Después de esta lección vas a poder hablar de cosas que ya pasaron — frases como 'Ayer fui al cine' o 'No pude ir al gimnasio'."

2. Explain the pattern: you'll teach one verb, drill it with ONE question, then on the next turn ask ONE recall question about an earlier verb. Bite-sized — one thing at a time.
   A1/A2: in English. B1+: in Spanish.

3. End with the literal line asking if they're ready:
   - A1/A2: "Ready to start?"
   - B1+: "¿Listo/a para empezar?"

Chat style. No headers, no bullet points, no textbook tone.
Do not preview specific verbs or forms — the actual teaching starts next turn."""


TEACH_DRILL_CONTINUATION_SUFFIX = """CURRENT PHASE: Teach-drill loop for {skill_name}

Student CEFR level: {cefr_level}

You will receive a specific instruction each turn telling you which unit to teach and which questions to ask. Follow it exactly. Do NOT invent additional content or drill unrelated items.

Paradigm formatting (critical): render conjugations ONE LINE PER PERSON (Yo/Tú/Él/Nosotros/Ellos, Latin American — skip vosotros). Never inline comma-separated lists.

TWO paradigm formats. Which one you use is NOT your choice — it is determined by the skill type below.

Format A — CONTRAST paradigm — MANDATORY for tense-conjugation skills and their close relatives. If the target unit is a form of a verb in any of these skill types, you MUST render as CONTRAST. BARE format is FORBIDDEN for these skills — no exceptions, no per-verb judgment calls.

Skill types that REQUIRE CONTRAST format:
- Preterite (any verb, regular or irregular)
- Imperfect (any verb, regular or irregular)
- Future (any verb, regular or irregular)
- Conditional (any verb, regular or irregular)
- Present subjunctive (contrasted with present indicative)
- Imperfect subjunctive (contrasted with present indicative)
- Reflexive verbs (contrasted with non-reflexive)
- Present progressive (contrasted with simple present)
- Compound perfect tenses (contrasted with the closest simple tense)

Render as `known → target` with English glosses per the GLOSSING rule below:

Preterite example (yo poder — note the preterite meaning shift, which is exactly why every row needs its gloss):
    Yo puedo → Yo pude (I can → I managed to)
    Tú puedes → Tú pudiste (you can → you managed to)
    Él puede → Él pudo (he can → he managed to)
    Nosotros podemos → Nosotros pudimos (we can → we managed to)
    Ellos pueden → Ellos pudieron (they can → they managed to)

Imperfect example (yo comer — this is the ACTUAL FAILURE MODE — do NOT render bare):
    Yo como → Yo comía (I eat → I used to eat)
    Tú comes → Tú comías (you eat → you used to eat)
    Él come → Él comía (he eats → he used to eat)
    Nosotros comemos → Nosotros comíamos (we eat → we used to eat)
    Ellos comen → Ellos comían (they eat → they used to eat)

Imperfect example (yo vivir):
    Yo vivo → Yo vivía (I live → I used to live)
    Tú vives → Tú vivías (you live → you used to live)
    Él vive → Él vivía (he lives → he used to live)
    Nosotros vivimos → Nosotros vivíamos (we live → we used to live)
    Ellos viven → Ellos vivían (they live → they used to live)

Preterite vs. imperfect (and any skill contrasting two PAST tenses with each other): the contrast is about USAGE, not conjugation. Neither past tense is the "known" side of the other. Teach when each one applies with example sentences. If you do show a paradigm for such a skill, the known side is the PRESENT tense — never the other past tense.

The known and target sides of a row must NEVER be identical. A row like `Yo fui → Yo fui` shows the student no transformation and is a hard error; if you cannot produce a different known form, the unit is a usage unit and needs no paradigm at all.

The `known` side is what the student already has automated; the `target` side is what they're learning. The bridge IS the point — surfaces the transformation, links new to known, and trains the retrieval switch. WITHOUT the bridge, the student is memorizing forms in isolation, which is exactly what teach_drill exists to prevent.

FORBIDDEN for the skill types above (any of these is a pattern violation):
- Rendering only the target-tense forms without the known-tense bridge (e.g. just `Yo comía / Tú comías / ...` for an imperfect skill — this is BARE format applied where CONTRAST is required)
- Deciding a specific verb "doesn't need" the contrast because it's regular or familiar
- Rendering CONTRAST inconsistently within a single skill (e.g. contrast for one verb, bare for another) — apply CONTRAST uniformly across every verb in a mandatory-CONTRAST skill
- Omitting the `→` arrow syntax
- Rendering as a two-column table instead of `known → target` per row

If you catch yourself about to write a bare `Yo comía / Tú comías / ...` block for a preterite/imperfect/future/etc. skill, STOP and rewrite as CONTRAST before sending.

Format B — BARE paradigm — ONLY for skill types where no natural `known → target` counterpart exists. Examples of BARE-appropriate skills:
- Present-tense stem changes (the present tense IS what they're learning; there's no "prior" tense to contrast with)
- Vocabulary sets (not paradigmatic)
- Rule-based skills without paradigms (por vs para, ser/estar usage, subjunctive triggers)
If the skill isn't in the mandatory-CONTRAST list above AND doesn't have an obvious paired form the student already knows, use BARE:
    Yo hablo
    Tú hablas
    Él/ella/usted habla
    Nosotros hablamos
    Ellos/ellas/ustedes hablan
With target-form glosses per the level-conditional rule below.

GLOSSING (level-conditional based on student CEFR level above):
- ALWAYS, at EVERY level: every row of a paradigm carries its own gloss in parens. Never gloss only the first row and leave the rest bare. The row format is `Spanish → Spanish (English → English)`.
- A1 or A2: gloss ALL Spanish content in parens. Paradigm rows, drill cues, correction lines, example sentences. Meaning-first.
- B1: every paradigm row, plus drill cues that use the target form and correction lines showing the target form. Do NOT gloss vocabulary the student already knows or example sentences whose meaning is transparent from cognates/context.
- B2 or higher (C1, C2): every paradigm row, and otherwise minimal glossing — only exotic vocabulary or idiomatic phrases whose meaning isn't inferable. Push toward Spanish-only processing (desirable difficulty) everywhere EXCEPT the paradigm.
When in doubt at B1+, err on LESS glossing rather than more, EXCEPT for paradigm rows, which are always fully glossed at every level.

LANGUAGE CONSISTENCY (critical — applies to all prose you write this turn):
- A1 or A2: respond in English. Spanish appears in paradigm rows, quoted example sentences, and drill/target-form content. Prose framing ("Now for X", "Try this one") stays in English.
- B1 or higher: respond in Spanish. Prose framing ("Ahora vamos con X", "Aquí va otra") stays in Spanish. English appears only as parenthetical glosses per the glossing rule above, and inside the drill-cue formulation "how would you say ..." when that's the natural English trigger for the target form.
- Assume B1+ students know common connective vocabulary — aprender, hablar, cosa, hoy, listo, ahora, ahora vamos, siguiente, próximo, etc. Do NOT translate those into English mid-sentence.
- NEVER code-switch mid-sentence at ANY level. A sentence that starts in Spanish stays in Spanish; a sentence that starts in English stays in English. Parenthetical glosses at the end of a sentence (e.g. "Yo fui (I went)") do NOT count as code-switching. "Hoy vas a learn how to talk about..." is a violation.

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

MARKER RULES (critical):
- <<LESSON_COMPLETE>> — natural end of the lesson via wrap-up. Emit only when the per-turn instruction explicitly asks you to.
- <<REDO_PENDING>> — student's lesson answer had an error; you deferred to re-ask.
- <<QUESTION_ANSWERED>> — student's message was a content question or ambient acknowledgment (not a drill answer); you re-served the pending drill.
- <<END_LESSON_EARLY>> — student asked to stop the lesson; the session closes on this turn.
- <<FEEDBACK>>[paraphrase]<<END_FEEDBACK>> — meta-feedback about the app/pedagogy; logged for developer review.

The four STATE markers (LESSON_COMPLETE, REDO_PENDING, QUESTION_ANSWERED, END_LESSON_EARLY) are mutually exclusive — emit at most ONE per response. The FEEDBACK marker is orthogonal and may coexist with any state marker.

Every marker, when emitted, goes on its own line at the very end of the message.

Message classification and per-turn action selection is defined in the per-turn instruction (which arrives as your last user message this turn). Follow the classification and evaluation rules there — do NOT default to the old "REDO first, then classify" flow.

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


CLASSIFY_FIRST_CHECK = """FIRST DECISION (do this BEFORE anything else): classify the student's most recent message. Read what they wrote and pick ONE category:

  (a) LESSON ANSWER — a response to the drill question you just asked. Two kinds, both belong here:
      - An attempt at the form, even if partial or wrong. Examples: "Yo tuve", "fuiste al gimnasio", any Spanish text that looks like an attempt at the target form.
      - Declining to answer: "I don't know", "no sé", "ni idea", "no idea", "pass", "skip this one", a shrug. Saying they don't know IS a response to the question — it is not filler and it is not a wrong answer.
      → Go to CORRECTNESS EVALUATION below.

  (b) CONTENT QUESTION — asking about Spanish itself (grammar, meaning, usage, comparisons). Examples: "wait, is estar always for locations?", "why is it hizo not hico?", "does poder always mean 'managed to' in preterite?"
      → Your ENTIRE response this turn is EXACTLY these parts:
        1. Answer the question in 2-3 sentences.
        2. "Volviendo a lo que te preguntaba: [restate the drill question verbatim or with minimal rephrasing]" (Spanish for B1+) or "Back to what I was asking: [restate]" (English for A1/A2).
        3. Literal marker on its own line at the very end: <<QUESTION_ANSWERED>>
      Do NOT teach a new unit, do NOT introduce a new drill. The teach/drill steps below this check are DEFERRED to the next turn.

  (c) META-FEEDBACK — commenting on the SYSTEM rather than doing the lesson. That covers you and your teaching (the pedagogy, the pacing, the cues, the style) AND how the system behaves around it: review scheduling and how often something comes back, how many questions a review asks, what it remembers about them, which interests it draws on, session structure and length. Examples: "that cue was ambiguous", "you keep asking me the same person", "this is going too fast", "shouldn't 'I was at the wedding' be estar?" (meta because the student is questioning YOUR choice), "the last time it said 2nd pass, this would be 3rd", "2 questions isn't enough for a review", "we keep coming back to the same few topics".
      A complaint, a frustration, a suggestion and a bug report are all META-FEEDBACK. So is one phrased as a question ("why does it keep asking me this?", "how did that get incorporated already?") — the question form does NOT make it a CONTENT QUESTION, because it is not asking about Spanish.
      → Emit <<FEEDBACK>>[one-sentence paraphrase]<<END_FEEDBACK>> block, then CONTINUE with the teach/drill steps below this check. FEEDBACK is orthogonal — proceed normally with the lesson.
      Do NOT write any acknowledgment yourself — code prepends a canonical acknowledgment automatically after the marker is stripped. Any acknowledgment you write will duplicate it.
      Do NOT promise to change your behavior in future turns (e.g., "I'll drop the hybrid phrasing going forward"). You cannot actually change your prompt — feedback is logged for the developer to review offline. A promise you can't keep is worse than no promise.

  (d) AMBIENT ACKNOWLEDGMENT — short filler with no linguistic content. Examples: "ok", "hmm", "got it", "yeah", "sure", 👍, "makes sense", "cool". This is NOT an answer and NOT a question — it's a nudge to proceed. Critical: do NOT treat this as a wrong lesson answer.
      → Your ENTIRE response this turn is:
        1. Brief re-serve of the pending drill: "Volviendo: [restate]" (Spanish for B1+) or "Back to it: [restate]" (English for A1/A2). No explanation, no praise, no ✗.
        2. Literal marker on its own line at the very end: <<QUESTION_ANSWERED>>
      Do NOT fire REDO — an ambient ack is NOT a wrong answer. Do NOT teach new content. Teach/drill steps deferred.

  (e) SESSION CONTROL — student wants to STOP this lesson. Examples: "let's move on", "skip this", "let's do something else", "let's do vocab instead", "I'm done", "next lesson please", "boring, next", "otro tema", "cambiemos".
      → Your ENTIRE response this turn is:
        1. Brief warm acknowledgment: "Ok, cerramos por hoy. ¡Nos vemos!" (Spanish for B1+) or "Ok, wrapping this up. See you next time!" (English for A1/A2).
        2. Literal marker on its own line at the very end: <<END_LESSON_EARLY>>
      Do NOT teach anything more, do NOT ask another question. The session closes on this turn.

  (f) COMBINATIONS — a message may contain multiple types. FEEDBACK is orthogonal to all others. If the student's message is BOTH e.g. a lesson answer AND meta-feedback, evaluate the answer per CORRECTNESS EVALUATION below AND include the <<FEEDBACK>>...<<END_FEEDBACK>> block. If BOTH a content question AND feedback: (b) response with the FEEDBACK block added. State markers (QUESTION_ANSWERED, END_LESSON_EARLY, REDO_PENDING) are mutually exclusive — pick the primary intent for the state marker.

Classification bar:
- Uncertain between LESSON ANSWER and CONTENT QUESTION → prefer CONTENT QUESTION (safer to defer than falsely mark ✗).
- Uncertain between CONTENT QUESTION and AMBIENT ACK → use AMBIENT ACK for one-to-three-word fillers with no linguistic substance; CONTENT QUESTION for anything that's actually asking something.
- Uncertain between CONTENT QUESTION and META-FEEDBACK → is it about SPANISH, or about the lesson/system? About Spanish is a CONTENT QUESTION; about the lesson or the system is META-FEEDBACK, even when phrased as a question. If still genuinely torn, log it as META-FEEDBACK: a stray log row costs a scroll, a missed complaint is gone for good.
- Uncertain between LESSON ANSWER and AMBIENT ACK → if it's in the target language and looks like it could be attempting the form, treat as LESSON ANSWER. "no sé" and every other way of saying "I don't know" is LESSON ANSWER via the NO ATTEMPT branch — never AMBIENT ACK, and never scored as a wrong answer.

CORRECTNESS EVALUATION (only if you classified as LESSON ANSWER): first, did they actually attempt the form?

  - NO ATTEMPT — they said they don't know, or asked to skip or pass, instead of producing Spanish. This is NOT an error: give no ✗, no correction framing, and do not treat it as a failed attempt. Your ENTIRE response this turn is:
    * ONE short reassuring line — "Tranquilo/a, es normal" (Spanish for B1+) or "No worries, that one's tricky" (English for A1/A2). No praise theatre, no lecture.
    * Give the answer outright: the target form on its own line, glossed.
    * Re-ask it: "Te la vuelvo a preguntar: [restate the SAME question]" (Spanish for B1+) or "Let me ask you that same one again: [restate]" (English for A1/A2).
    * Literal marker on its own line at the very end: <<REDO_PENDING>>
    STOP THERE. Do NOT teach a new unit, do NOT show a new paradigm, do NOT ask a different question. The student asked to be shown this one — show it and come straight back to it.

  If they DID attempt the form: did the Spanish contain ANY error?

  Any preposition mistake, gender/agreement error, tense error, wrong verb form, wrong verb choice, spelling error, or missing accent counts as an error. Do NOT soften with "Close, but..." or partial-credit — an answer is either fully correct (✓) or has an error (✗).

  - If ✗ — your ENTIRE response this turn is the REDO pattern, nothing else:
    * The ✗ line with the correct form and a brief reason (one line).
    * "Try again: [restate the SAME question with a slight rephrasing]" (one line).
    * Literal marker on its own line at the very end: <<REDO_PENDING>>
    STOP THERE. Do NOT execute the teach/drill steps below. Do NOT introduce a new verb, do NOT show a new paradigm, do NOT ask any other question.

  - If ✓: write a single "✓" line acknowledging it, then continue to the teach/drill steps below.
  - If there was no prior answer (this is the first turn of the session): just proceed to the steps below."""


def build_retrieval_only_instruction(retrieval: dict, person_retrieve: str) -> str:
    """Instruction for a drill-only turn (no new teaching): review a prior unit."""
    return (
        f"{CLASSIFY_FIRST_CHECK}\n\n"
        f"— IF classification was LESSON ANSWER + evaluated ✓, OR there was no prior answer, OR you're proceeding after a META-FEEDBACK acknowledgment — continue with the teach/drill steps below: —\n\n"
        f"1) Do NOT teach any new content — this is a review turn.\n\n"
        f"2) Ask ONE production question testing **{retrieval['label']}** in the "
        f"**{person_retrieve}** form. Brief context ('quick review:' or similar) is fine, "
        f"but no paradigms.\n\n"
        f"{CUE_SELECTION_RULES}\n\n"
        f"3) End with a natural line inviting the student to answer. Do NOT emit <<LESSON_COMPLETE>>."
    )


PARADIGM_CORRECTION_INSTRUCTION = """Your previous message contained an invalid CONTRAST paradigm row:

{rows}

In a CONTRAST row the LEFT side is the form the student ALREADY KNOWS and the RIGHT side is the TARGET form being taught. The two sides must be DIFFERENT forms — a row with an identical left and right side shows no transformation and teaches the student nothing.

Resend your entire message, corrected. Keep everything else identical: same unit, same example sentence, same drill question, same format, same end markers. Fix only the broken row(s)."""


# Person pronouns that open a CONTRAST paradigm row. Used to tell a paradigm
# row apart from ordinary prose that happens to contain an arrow.
CONTRAST_ROW_PERSONS = frozenset({
    "yo", "tú", "tu", "él", "el", "ella", "usted",
    "nosotros", "nosotras", "ellos", "ellas", "ustedes",
})

_TRAILING_GLOSS = re.compile(r'\([^)]*\)\s*$')


def find_degenerate_contrast_rows(text: str) -> list[str]:
    """Return CONTRAST rows whose known side is identical to its target side.

    A row like `Yo fui -> Yo fui` shows the student no transformation at all, so
    it is always wrong regardless of which verb or tense is being taught. This is
    a pure structural check: it needs no conjugation table, and it cannot flag a
    correct row. Four prompt-level fixes failed to stop this class of error
    (08-15 x2, 08-16 x2, recurred 08-19) -- hence a deterministic guard.
    """
    bad = []
    for line in text.splitlines():
        row = line.strip()
        if not row:
            continue
        if row.split()[0].strip('*_:.').casefold() not in CONTRAST_ROW_PERSONS:
            continue
        # The English gloss carries its own arrow; only the Spanish sides count.
        sides = _TRAILING_GLOSS.sub('', row).strip().split('\u2192')
        if len(sides) != 2:
            continue
        known, target = sides[0].strip(), sides[1].strip()
        if known and known.casefold() == target.casefold():
            bad.append(row)
    return bad


def build_teach_instruction(unit: dict, person_new: str, is_final: bool) -> str:
    """Instruction for a teach turn: teach one unit + ONE production question about it.
    No retrieval on teach turns — retrieval happens on its own alternating turn."""
    parts = []

    parts.append(CLASSIFY_FIRST_CHECK)

    parts.append("— IF classification was LESSON ANSWER + evaluated ✓, OR there was no prior answer, OR you're proceeding after a META-FEEDBACK acknowledgment — continue with the teach/drill steps below: —")

    label = unit["label"]
    note = unit["note"] or "no special notes"
    # A usage unit contains no verb, so demanding a paradigm from it forces the
    # model to invent one -- the root cause of the `Yo fui -> Yo fui` row that
    # shipped on 2026-08-19. Units with no "kind" key predate this field and keep
    # the original paradigm behaviour so lessons already in flight don't change.
    if unit.get("kind") == "usage":
        parts.append(
            f"1) Teach exactly this unit: **{label}** (metadata: {note}).\n"
            f"   - This is a USAGE unit, NOT a verb paradigm. Do NOT render a conjugation "
            f"table and do NOT pick a verb to conjugate.\n"
            f"   - Explain when this usage applies, then give TWO short contrasting example "
            f"sentences that show it against the alternative.\n"
            f"   - Keep it under 100 words for this step."
        )
    else:
        if "kind" not in unit:
            # Silent otherwise: if extraction stops emitting "kind", every unit
            # reverts to paradigm behaviour and nothing says so. That is how an
            # `ir` paradigm reached a usage lesson on 2026-08-22 -- the session
            # opened on the pre-fix build, so its stored units had no kind.
            logging.getLogger(__name__).warning(
                "teach unit %r has no 'kind'; falling back to the paradigm "
                "instruction. Stale session state, or extraction dropped the field.",
                unit.get("id", ""),
            )
        paradigm_lines = (
            f"   - Show the full paradigm one line per person (Yo/Tú/Él/Nosotros/Ellos).\n"
            f"   - Gloss EVERY row, not just the first: "
            f"`Spanish → Spanish (English → English)` on every single line.\n"
        )
        known_forms = unit.get("known_forms") or {}
        if known_forms:
            forms = ", ".join(f"{person} {form}" for person, form in known_forms.items())
            known_tense = unit.get("known_tense") or "the tense the student already knows"
            paradigm_lines += (
                f"   - The KNOWN side of each CONTRAST row is the {known_tense}: {forms}. "
                f"Use these exact forms on the left of each `→`. Do NOT derive your own "
                f"known side and never repeat the target form there.\n"
            )
        parts.append(
            f"1) Teach exactly this unit: **{label}** (metadata: {note}).\n"
            f"{paradigm_lines}"
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


# Two safety caps for teach_drill sessions:
# - PROGRESS cap fires when the lesson has advanced state that many times.
#   Deferred turns (REDO, QUESTION_ANSWERED) don't count. This is the
#   pedagogical ceiling — protects against a lesson drifting past its natural
#   endpoint without cutting short the student's clarifying conversations.
# - HARD cap fires on total LLM calls including deferred ones. Emergency brake
#   against pathological loops. Set generously so legitimate side-question
#   sessions don't trip it.
TEACH_DRILL_MAX_PROGRESS_TURNS = 12
TEACH_DRILL_MAX_TURNS = 40


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

    # Force completion when EITHER cap is hit:
    # - progress_count fires the pedagogical cap (deferred turns don't count)
    # - turn_count fires the hard emergency cap on total LLM calls
    force_complete = (
        state["progress_count"] >= TEACH_DRILL_MAX_PROGRESS_TURNS
        or state["turn_count"] >= TEACH_DRILL_MAX_TURNS
    )

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
            "3) Explicitly invite the student into the next step so they know what to do: "
            "ask if they're ready for a quick quiz to check what stuck. Warm and clear, "
            "e.g. 'Ready to check what stuck? Say \"listo\" and I'll run a quick quiz.'\n\n"
            "4) End with the literal marker on its own line: <<LESSON_COMPLETE>>"
        )
    history.append({"role": "user", "content": instruction})

    # Call LLM.
    skill_name = session.target_skill.name if session.target_skill else "this skill"
    cefr_level = user.estimated_cefr_level or 'A2'
    suffix = TEACH_DRILL_CONTINUATION_SUFFIX.format(skill_name=skill_name, cefr_level=cefr_level)
    response = await call_llm(history, user=user, system_suffix=suffix)

    # Deterministic guard: a CONTRAST row whose known side equals its target side
    # teaches no transformation and is always wrong. Regenerate once with an
    # explicit correction rather than ship a bad form to the student. Runs before
    # marker stripping so the retry sees the message exactly as the model wrote it.
    degenerate_rows = find_degenerate_contrast_rows(response)
    if degenerate_rows:
        retry_history = history + [
            {"role": "assistant", "content": response},
            {"role": "user", "content": PARADIGM_CORRECTION_INSTRUCTION.format(
                rows="\n".join(degenerate_rows))},
        ]
        # One retry only. If the model fails twice the turn still goes out --
        # a lesson that stalls is worse than one bad row, and the drill question
        # and markers still need to reach the student.
        response = await call_llm(retry_history, user=user, system_suffix=suffix)

    # Strip markers.
    response, marker_seen = _strip_lesson_complete_marker(response)
    response, redo_pending = _strip_redo_pending_marker(response)
    response, question_answered = _strip_question_answered_marker(response)
    response, end_lesson_early = _strip_end_lesson_early_marker(response)
    response, feedback_interpretation = _strip_feedback_marker(response)

    # Log feedback if the LLM captured any. Anchor to the most recent existing
    # SessionEvent — that's the assistant turn the user was reacting to.
    if feedback_interpretation:
        from learner.models import SessionFeedback
        anchor = events[-1] if events else None
        await sync_to_async(SessionFeedback.objects.create)(
            session=session,
            anchor_event=anchor,
            user_message=text,
            interpretation=feedback_interpretation,
        )
        # Prepend the canonical acknowledgment. Prompt tells the LLM to skip
        # writing one; code guarantees the exact wording so the ack is
        # deterministic across every feedback interaction (no LLM drift,
        # no accidental change-promises).
        if response.strip():
            response = f"{FEEDBACK_ACKNOWLEDGMENT}\n\n{response}"
        else:
            response = FEEDBACK_ACKNOWLEDGMENT

    # Update state.
    # If the LLM emitted <<REDO_PENDING>> OR <<QUESTION_ANSWERED>>, it deferred
    # teach/retrieval to either do a re-attempt (redo) or answer a content question
    # and re-ask the pending drill (question). In both cases the current turn's
    # planned state advancement should NOT happen — the same turn will replay next
    # call and produce the actual drill answer then. turn_count still advances so
    # the safety cap remains meaningful.
    defer_state = redo_pending or question_answered
    if not defer_state:
        if turn_type == "teach":
            mark_taught(state, teach_unit["id"])
            mark_drilled(state, teach_unit["id"], teach_person)
            state["last_turn_type"] = "teach"
        elif turn_type == "retrieval" and retrieval_unit is not None:
            mark_drilled(state, retrieval_unit["id"], retrieval_person)
            state["last_turn_type"] = "retrieval"
        # wrap_up doesn't update taught/drilled; marker or force_complete handles completion.
    state["turn_count"] += 1
    # progress_count only advances on real lesson turns (not deferred).
    # Deferred turns (REDO, QUESTION_ANSWERED) are "free" — a student asking
    # many clarifying questions shouldn't burn down the pedagogical cap.
    # END_LESSON_EARLY is also "free" (it's a stop, not progress).
    if not defer_state and not end_lesson_early:
        state["progress_count"] += 1
    if marker_seen or force_complete or (turn_type == "wrap_up" and not defer_state) or end_lesson_early:
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
        "end_lesson_early": end_lesson_early,
    }
