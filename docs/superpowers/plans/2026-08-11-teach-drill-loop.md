# Teach-Drill Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current `present → questions → guided_practice` grammar lesson flow with a single interleaved `teach_drill` phase that teaches one paradigm at a time, immediately drills the just-taught paradigm plus one spaced-retrieval of a prior unit, and advances only when all units for the skill have been taught and each has been drilled ≥2 times.

**Architecture:**
- New module `engine/teach_drill.py` holds unit extraction, rotation/spacing logic, prompt templates, and the per-turn handler. Kept out of `engine/session.py` because that file is already ~1500 lines.
- Grammar-only change. Vocab flow is untouched.
- Unit lists are extracted on-the-fly by an LLM call at session open (no curriculum curation required). LLM returns JSON; code parses and stores in `session.quiz_state`.
- The `teach_drill` phase replaces THREE existing phases: `present`, `questions`, and `guided_practice`. Assessment stays as-is (3 turns).

**Tech Stack:** Django 5 + async ORM via `sync_to_async`, Anthropic Claude via existing `engine.core.call_llm`, pytest + pytest-django + pytest-asyncio (asyncio_mode=auto in pytest.ini).

## Global Constraints

- Grammar `new_skill` sessions only. Vocab and other session types unchanged.
- Existing scoring model preserved: `SessionEvent.score_delta` is only set in `assessment` (unchanged). teach_drill turns produce `SessionEvent` rows with `event_type='conversation'` and no score_delta.
- Latin American Spanish only — paradigms teach yo/tú/él/nosotros/ellos (no vosotros).
- Anthropic model config comes from `django.conf.settings.ANTHROPIC_MODEL` — do not hardcode.
- Safety cap: no more than 16 teach_drill turns per session (force-complete after that).
- All new async code uses `sync_to_async` for ORM access; direct `.objects.create()` and `.filter()` calls only inside `sync_to_async` wrappers.
- Follow existing commit style: `feat: ...` or `fix: ...` — no bracketed prefixes, no emoji, add `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` trailer.
- Do not push to remote without explicit user instruction. Commits are fine.

---

## File Structure

**Files to create:**
- `engine/teach_drill.py` — new module (unit extraction, rotation, prompts, turn handler)
- `engine/tests/test_teach_drill.py` — tests for the new module

**Files to modify:**
- `engine/session.py` — hook teach_drill into the grammar path, collapse `GRAMMAR_PHASE_FLOW`, remove obsolete prompts
- `engine/tests/test_session.py` — update `_next_phase` tests for the new flow

**Files unchanged (verify):**
- `learner/models.py` — `Session.quiz_state` is already a `JSONField(null=True, blank=True)`; no schema change
- `curriculum/skills.yaml` — no changes; unit lists derived by LLM
- All vocab handling paths in `session.py`

---

## Task 1: Scaffold `engine/teach_drill.py` + unit extraction

**Files:**
- Create: `engine/teach_drill.py`
- Create: `engine/tests/test_teach_drill.py`

**Interfaces:**
- Produces: `async extract_units(skill_name: str, skill_description: str, cefr_level: str) -> list[dict]` — returns list of `{"id": str, "label": str, "note": str}` dicts. Each represents one teachable unit within the skill (typically a verb or a shared-conjugation pair).

- [ ] **Step 1: Write the failing test**

Create `engine/tests/test_teach_drill.py`:

```python
import json
import pytest
from unittest.mock import patch, AsyncMock


@pytest.mark.asyncio
async def test_extract_units_parses_llm_json():
    """extract_units parses valid JSON from the LLM into a list of unit dicts."""
    from engine.teach_drill import extract_units

    fake_json = json.dumps([
        {"id": "ser_ir", "label": "ser/ir", "note": "share fui/fuiste/fue"},
        {"id": "estar", "label": "estar", "note": "stem estuv-"},
    ])
    with patch('engine.teach_drill.call_llm', new=AsyncMock(return_value=fake_json)):
        units = await extract_units(
            skill_name="Preterite irregulars",
            skill_description="ser, ir, estar, tener",
            cefr_level="B1",
        )

    assert len(units) == 2
    assert units[0]["id"] == "ser_ir"
    assert units[1]["label"] == "estar"


@pytest.mark.asyncio
async def test_extract_units_strips_markdown_fences():
    """LLMs often wrap JSON in ```json fences. extract_units must handle that."""
    from engine.teach_drill import extract_units

    fenced = "```json\n" + json.dumps([{"id": "a", "label": "a", "note": ""}]) + "\n```"
    with patch('engine.teach_drill.call_llm', new=AsyncMock(return_value=fenced)):
        units = await extract_units("s", "d", "A2")

    assert len(units) == 1
    assert units[0]["id"] == "a"


@pytest.mark.asyncio
async def test_extract_units_returns_empty_list_on_parse_failure():
    """Bad JSON from LLM shouldn't crash — return empty list so caller can fall back."""
    from engine.teach_drill import extract_units

    with patch('engine.teach_drill.call_llm', new=AsyncMock(return_value="not json at all")):
        units = await extract_units("s", "d", "A2")

    assert units == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest engine/tests/test_teach_drill.py -v`
Expected: All FAIL with `ModuleNotFoundError: No module named 'engine.teach_drill'`.

- [ ] **Step 3: Create `engine/teach_drill.py` with `extract_units`**

Create the file with this content:

```python
"""
Teach-drill phase: interleaved teach + immediate production drill.
Replaces the present + questions + guided_practice phases for grammar
new_skill sessions.
"""
import json
import re

from .core import call_llm


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest engine/tests/test_teach_drill.py -v`
Expected: 3 PASSED.

- [ ] **Step 5: Run the full suite to make sure nothing else broke**

Run: `venv/bin/python -m pytest engine/tests/ -x --tb=short 2>&1 | tail -5`
Expected: All previously-passing tests still pass (81 + 3 = 84 total).

- [ ] **Step 6: Commit**

```bash
git add engine/teach_drill.py engine/tests/test_teach_drill.py
git commit -m "$(cat <<'EOF'
feat: add teach_drill module with LLM unit extraction

New engine/teach_drill.py holds the interleaved teach-drill lesson
logic. Task 1 adds extract_units — an LLM call that decomposes a
grammar skill into teachable units (typically one verb per unit,
grouped when verbs share paradigms). Returns [] on parse failure
so callers can fall back to the current dense-lesson path.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Pure rotation and spacing logic

**Files:**
- Modify: `engine/teach_drill.py`
- Modify: `engine/tests/test_teach_drill.py`

**Interfaces:**
- Produces:
  - `next_teach_unit(units: list[dict], taught_ids: list[str]) -> dict | None` — returns next un-taught unit in declaration order, or None if all taught.
  - `select_retrieval_unit(taught_ids: list[str], drills: dict[str, list[str]]) -> str | None` — returns id of a taught unit that has been drilled *least*, preferring earlier-taught for ties. Returns None if `taught_ids` is empty.
  - `select_person(unit_id: str, drills: dict[str, list[str]]) -> str` — returns one of "yo", "tú", "él", "nosotros", "ellos". Rotates through unused-for-this-unit; if all used, returns the least frequently drilled.

- [ ] **Step 1: Write the failing tests**

Append to `engine/tests/test_teach_drill.py`:

```python
class TestRotation:
    def test_next_teach_unit_returns_first_untaught(self):
        from engine.teach_drill import next_teach_unit
        units = [{"id": "a", "label": "a", "note": ""},
                 {"id": "b", "label": "b", "note": ""},
                 {"id": "c", "label": "c", "note": ""}]
        assert next_teach_unit(units, taught_ids=[])["id"] == "a"
        assert next_teach_unit(units, taught_ids=["a"])["id"] == "b"
        assert next_teach_unit(units, taught_ids=["a", "b"])["id"] == "c"

    def test_next_teach_unit_returns_none_when_all_taught(self):
        from engine.teach_drill import next_teach_unit
        units = [{"id": "a", "label": "a", "note": ""}]
        assert next_teach_unit(units, taught_ids=["a"]) is None

    def test_select_retrieval_unit_prefers_least_drilled(self):
        from engine.teach_drill import select_retrieval_unit
        taught = ["a", "b", "c"]
        drills = {"a": ["yo", "tú"], "b": [], "c": ["yo"]}
        # b has 0 drills — should be picked.
        assert select_retrieval_unit(taught, drills) == "b"

    def test_select_retrieval_unit_tiebreak_by_earlier_taught(self):
        from engine.teach_drill import select_retrieval_unit
        taught = ["a", "b", "c"]  # taught in this order
        drills = {"a": ["yo"], "b": ["yo"], "c": ["yo"]}
        # All tied at 1 drill; earlier-taught wins.
        assert select_retrieval_unit(taught, drills) == "a"

    def test_select_retrieval_unit_returns_none_when_no_taught(self):
        from engine.teach_drill import select_retrieval_unit
        assert select_retrieval_unit([], {}) is None

    def test_select_person_returns_yo_when_none_drilled(self):
        from engine.teach_drill import select_person
        assert select_person("tener", {"tener": []}) == "yo"

    def test_select_person_rotates_through_unused(self):
        from engine.teach_drill import select_person
        # Drilled yo already; should advance to tú.
        assert select_person("tener", {"tener": ["yo"]}) == "tú"
        # Drilled yo + tú; should advance to él.
        assert select_person("tener", {"tener": ["yo", "tú"]}) == "él"

    def test_select_person_picks_least_frequent_when_all_used(self):
        from engine.teach_drill import select_person
        # Every person drilled once except él (drilled twice).
        drills = {"tener": ["yo", "tú", "él", "él", "nosotros", "ellos"]}
        # yo/tú/nosotros/ellos all at count 1; yo comes first in canonical order.
        assert select_person("tener", drills) == "yo"

    def test_select_person_handles_missing_unit_key(self):
        from engine.teach_drill import select_person
        # Unit not yet drilled at all — dict lookup should default cleanly.
        assert select_person("newverb", drills={}) == "yo"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest engine/tests/test_teach_drill.py::TestRotation -v`
Expected: All 9 FAIL with `ImportError`.

- [ ] **Step 3: Implement the rotation helpers**

Append to `engine/teach_drill.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest engine/tests/test_teach_drill.py::TestRotation -v`
Expected: 9 PASSED.

- [ ] **Step 5: Commit**

```bash
git add engine/teach_drill.py engine/tests/test_teach_drill.py
git commit -m "$(cat <<'EOF'
feat: add rotation and spacing helpers to teach_drill

Pure functions for the teach-drill loop's per-turn decisions:
- next_teach_unit picks the next un-taught unit in declaration order
- select_retrieval_unit picks the least-drilled prior unit for spaced
  retrieval, tiebreaking by earlier-taught
- select_person rotates through unused yo/tú/él/nosotros/ellos and
  falls back to least-frequent when all persons have been drilled

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: State schema helpers

**Files:**
- Modify: `engine/teach_drill.py`
- Modify: `engine/tests/test_teach_drill.py`

**Interfaces:**
- Produces:
  - `get_state(session) -> dict` — returns the teach_drill sub-dict of `session.quiz_state`, or a fresh empty structure.
  - `async save_state(session, state: dict) -> None` — persists to DB.
  - `mark_taught(state: dict, unit_id: str) -> dict` — pure: appends unit_id to state["taught"] if not present.
  - `mark_drilled(state: dict, unit_id: str, person: str) -> dict` — pure: appends person to state["drills"][unit_id].
  - `mark_complete(state: dict) -> dict` — pure: sets state["lesson_complete"] = True.

State shape (canonical):
```python
{
    "units": [{"id": ..., "label": ..., "note": ...}, ...],
    "taught": ["id1", "id2", ...],      # ordered
    "drills": {"id1": ["yo", "tú"], "id2": ["él"]},
    "turn_count": 3,                    # for safety cap
    "lesson_complete": False,
}
```

- [ ] **Step 1: Write the failing tests**

Append to `engine/tests/test_teach_drill.py`:

```python
from asgiref.sync import sync_to_async


class TestState:
    def test_get_state_returns_empty_when_absent(self):
        from engine.teach_drill import get_state
        class FakeSession:
            quiz_state = None
        state = get_state(FakeSession())
        assert state == {"units": [], "taught": [], "drills": {}, "turn_count": 0, "lesson_complete": False}

    def test_get_state_returns_existing_teach_drill_subdict(self):
        from engine.teach_drill import get_state
        class FakeSession:
            quiz_state = {"teach_drill": {"units": [{"id": "a", "label": "a", "note": ""}],
                                          "taught": ["a"], "drills": {"a": ["yo"]},
                                          "turn_count": 2, "lesson_complete": False}}
        state = get_state(FakeSession())
        assert state["taught"] == ["a"]
        assert state["drills"]["a"] == ["yo"]

    def test_mark_taught_appends_when_absent(self):
        from engine.teach_drill import mark_taught
        state = {"taught": [], "drills": {}}
        result = mark_taught(state, "tener")
        assert result["taught"] == ["tener"]

    def test_mark_taught_idempotent(self):
        from engine.teach_drill import mark_taught
        state = {"taught": ["tener"], "drills": {}}
        result = mark_taught(state, "tener")
        assert result["taught"] == ["tener"]

    def test_mark_drilled_appends(self):
        from engine.teach_drill import mark_drilled
        state = {"taught": ["tener"], "drills": {}}
        result = mark_drilled(state, "tener", "yo")
        assert result["drills"] == {"tener": ["yo"]}
        result = mark_drilled(result, "tener", "tú")
        assert result["drills"]["tener"] == ["yo", "tú"]

    def test_mark_complete_sets_flag(self):
        from engine.teach_drill import mark_complete
        state = {"lesson_complete": False}
        assert mark_complete(state)["lesson_complete"] is True


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_save_state_persists_to_session_quiz_state(make_user, make_skill):
    from engine.teach_drill import save_state
    from learner.models import Session

    user = await sync_to_async(make_user)(discord_id='ts_state1', cefr_level='B1')
    skill = await sync_to_async(make_skill)(skill_id='sk_state1')
    session = await sync_to_async(Session.objects.create)(
        user=user, session_type='new_skill', target_skill=skill,
    )
    state = {"units": [{"id": "a", "label": "a", "note": ""}],
             "taught": ["a"], "drills": {"a": ["yo"]},
             "turn_count": 1, "lesson_complete": False}

    await save_state(session, state)

    reloaded = await sync_to_async(Session.objects.get)(pk=session.pk)
    assert reloaded.quiz_state["teach_drill"]["taught"] == ["a"]
    assert reloaded.quiz_state["teach_drill"]["drills"] == {"a": ["yo"]}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest engine/tests/test_teach_drill.py::TestState -v engine/tests/test_teach_drill.py::test_save_state_persists_to_session_quiz_state -v`
Expected: 7 FAIL with ImportError.

- [ ] **Step 3: Implement state helpers**

Append to `engine/teach_drill.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest engine/tests/test_teach_drill.py -v`
Expected: All tests in `test_teach_drill.py` PASS (should be ~19 now).

- [ ] **Step 5: Commit**

```bash
git add engine/teach_drill.py engine/tests/test_teach_drill.py
git commit -m "$(cat <<'EOF'
feat: add teach_drill state schema and persistence helpers

Adds get_state / save_state / mark_taught / mark_drilled / mark_complete
for managing the teach_drill sub-dict of session.quiz_state. Pure
helpers return new state; save_state persists via sync_to_async.
Defensive copying prevents caller mutation from leaking into the
session object.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Prompt template + `build_teach_prompt`

**Files:**
- Modify: `engine/teach_drill.py`
- Modify: `engine/tests/test_teach_drill.py`

**Interfaces:**
- Produces:
  - `TEACH_DRILL_OPENING_PROMPT` — string constant. Format-substituted with skill_name, cefr_level, interests. Passed as first user message when teach_drill turn is the opening turn.
  - `TEACH_DRILL_CONTINUATION_SUFFIX` — string constant. Format-substituted with skill_name. Passed as `system_suffix` to `call_llm` for continuation turns.
  - `build_teach_instruction(unit: dict, retrieval: dict | None, person_new: str, person_retrieve: str | None, is_final: bool) -> str` — returns a user-message string that instructs the LLM what to do on this specific turn (teach unit X, drill in person Y, and optionally spaced-retrieve unit Z in person W). Used as the last user message before the LLM call.

Rationale for splitting into three constants: the OPENING prompt introduces Luz's role for the whole lesson (outcome framing, etc.). The CONTINUATION SUFFIX gives per-turn constraints (paradigm formatting, marker rules). The build_teach_instruction generates the turn-specific "here's what to do now" message that can be reused across every turn.

- [ ] **Step 1: Write the failing tests**

Append to `engine/tests/test_teach_drill.py`:

```python
class TestPromptBuilding:
    def test_build_teach_instruction_new_unit_only(self):
        """First turn: teach a new unit, drill it in one person, no retrieval yet."""
        from engine.teach_drill import build_teach_instruction
        unit = {"id": "tener", "label": "tener", "note": "stem tuv-"}
        instr = build_teach_instruction(unit, retrieval=None,
                                        person_new="yo", person_retrieve=None,
                                        is_final=False)
        assert "tener" in instr
        assert "tuv-" in instr
        assert "yo" in instr.lower()
        # No retrieval instructions if none passed.
        assert "previously-taught" not in instr.lower() and "prior" not in instr.lower()

    def test_build_teach_instruction_with_retrieval(self):
        """Subsequent turn: teach new + retrieve prior."""
        from engine.teach_drill import build_teach_instruction
        new = {"id": "hacer", "label": "hacer", "note": "stem hic-; hizo in él"}
        retr = {"id": "tener", "label": "tener", "note": "stem tuv-"}
        instr = build_teach_instruction(new, retrieval=retr,
                                        person_new="tú", person_retrieve="nosotros",
                                        is_final=False)
        assert "hacer" in instr and "tú" in instr.lower()
        assert "tener" in instr and "nosotros" in instr.lower()

    def test_build_teach_instruction_final_turn_asks_for_marker(self):
        """Final teaching turn instructs the LLM to emit <<LESSON_COMPLETE>>."""
        from engine.teach_drill import build_teach_instruction
        unit = {"id": "saber", "label": "saber", "note": "sup-"}
        instr = build_teach_instruction(unit, retrieval=None,
                                        person_new="yo", person_retrieve=None,
                                        is_final=True)
        assert "<<LESSON_COMPLETE>>" in instr

    def test_opening_prompt_contains_skill_name_and_outcome_framing_directive(self):
        from engine.teach_drill import TEACH_DRILL_OPENING_PROMPT
        assert "{skill_name}" in TEACH_DRILL_OPENING_PROMPT
        # Must instruct the LLM to include outcome framing.
        assert "outcome" in TEACH_DRILL_OPENING_PROMPT.lower() or "you'll" in TEACH_DRILL_OPENING_PROMPT.lower()

    def test_continuation_suffix_contains_paradigm_format_rule(self):
        from engine.teach_drill import TEACH_DRILL_CONTINUATION_SUFFIX
        # Must instruct one-line-per-person paradigm rendering.
        assert "one line" in TEACH_DRILL_CONTINUATION_SUFFIX.lower() or "per person" in TEACH_DRILL_CONTINUATION_SUFFIX.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest engine/tests/test_teach_drill.py::TestPromptBuilding -v`
Expected: 5 FAIL with ImportError.

- [ ] **Step 3: Implement prompt constants and builder**

Append to `engine/teach_drill.py`:

```python
# ── Prompt templates ──────────────────────────────────────────────────────────

TEACH_DRILL_OPENING_PROMPT = """You are Luz Ángela, opening a bite-sized chunked lesson on {skill_name} for a Spanish student.

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

If evaluating a student's prior response: one line per response — either "✓" plus a short confirmation, or "✗" plus the correct form and a brief reason. No praise, no excess.

The marker <<LESSON_COMPLETE>>, if the instruction asks you to emit it, goes on its own line at the very end of the message with nothing after it. Never emit it otherwise.

Chat style, no bold headers."""


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest engine/tests/test_teach_drill.py::TestPromptBuilding -v`
Expected: 5 PASSED.

- [ ] **Step 5: Full suite check**

Run: `venv/bin/python -m pytest engine/tests/ -x --tb=short 2>&1 | tail -5`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add engine/teach_drill.py engine/tests/test_teach_drill.py
git commit -m "$(cat <<'EOF'
feat: add prompt templates for teach_drill phase

Adds TEACH_DRILL_OPENING_PROMPT (first-turn outcome framing),
TEACH_DRILL_CONTINUATION_SUFFIX (per-turn constraints on formatting
and marker usage), and build_teach_instruction which assembles the
turn-specific instruction telling the LLM exactly which unit to
teach, which person to drill for the new unit, and (when applicable)
which prior unit + person to spaced-retrieve.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Turn handler — `handle_teach_drill_turn`

**Files:**
- Modify: `engine/teach_drill.py`
- Modify: `engine/tests/test_teach_drill.py`

**Interfaces:**
- Consumes: `session` with `.quiz_state`, `.target_skill`, `.pk`; `user` with `.estimated_cefr_level`, `.interests`.
- Produces:
  - `async handle_teach_drill_turn(user, session, text: str) -> dict` — returns `{"text": <llm response, marker stripped>, "audio_url": None, "session_ended": False, "advance_to_assessment": bool}`. The caller (session.py) reads `advance_to_assessment` and updates `current_phase` accordingly.
  - Constant: `TEACH_DRILL_MAX_TURNS = 16` (safety cap).

Turn logic:
1. Load state via `get_state(session)`.
2. If `state["lesson_complete"]` — return `{"advance_to_assessment": True}`. Caller advances phase.
3. Increment `turn_count`. If `turn_count >= TEACH_DRILL_MAX_TURNS` — force `lesson_complete=True`, still generate one more LLM turn but mark it as final.
4. Pick `next_unit = next_teach_unit(units, taught)`.
5. If `next_unit is None` (all taught) AND every unit has ≥2 drills: mark complete, generate a wrap-up turn with the marker, return with `advance_to_assessment=False` (caller sees marker + sets state; NEXT turn returns `advance_to_assessment=True`).
6. Otherwise pick `retrieval_unit = select_retrieval_unit(taught, drills)` (may be None on very first turn).
7. Pick persons: `person_new = select_person(next_unit["id"], drills)`; `person_retrieve = select_person(retrieval_unit["id"], drills) if retrieval_unit else None`.
8. Determine `is_final = (next_unit is last untaught AND all others have ≥2 drills after this turn will complete them)`. Approximation: `is_final = len(taught) == len(units) - 1 and all len(drills[uid]) >= 2 for uid in taught)`.
9. Build the LLM call: system suffix = `TEACH_DRILL_CONTINUATION_SUFFIX`; append `build_teach_instruction(...)` as the last user message in the history.
10. Call `call_llm` with the message history (existing SessionEvent-derived).
11. Strip marker (reuse `_strip_lesson_complete_marker` from session.py — import it).
12. Update state: `mark_taught(state, next_unit["id"])`, `mark_drilled(state, next_unit["id"], person_new)`, and if retrieval was used `mark_drilled(state, retrieval_unit["id"], person_retrieve)`.
13. If marker present: `mark_complete(state)`.
14. Save state.
15. Return the response.

Note: the LLM history construction and the `SessionEvent` creation are the caller's responsibility (session.py already has `_build_new_skill_history`). The turn handler only orchestrates the LLM call and the state updates. This mirrors how `_continue_new_skill` works today.

- [ ] **Step 1: Write the failing tests**

Append to `engine/tests/test_teach_drill.py`:

```python
class TestHandleTeachDrillTurn:

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_first_turn_teaches_first_unit_no_retrieval(self, make_user, make_skill):
        """On the first teach_drill turn, teach the first unit; no retrieval yet."""
        from engine.teach_drill import handle_teach_drill_turn
        from learner.models import Session, SessionEvent

        user = await sync_to_async(make_user)(discord_id='td_h1', cefr_level='B1')
        skill = await sync_to_async(make_skill)(skill_id='sk_td_h1')
        session = await sync_to_async(Session.objects.create)(
            user=user, session_type='new_skill', target_skill=skill,
            current_phase='teach_drill',
            quiz_state={"teach_drill": {
                "units": [{"id": "tener", "label": "tener", "note": "tuv-"},
                          {"id": "hacer", "label": "hacer", "note": "hic-"}],
                "taught": [], "drills": {}, "turn_count": 0, "lesson_complete": False,
            }},
        )

        with patch('engine.teach_drill.call_llm', new=AsyncMock(return_value="LLM RESPONSE")):
            result = await handle_teach_drill_turn(user, session, text="listo")

        assert result["text"] == "LLM RESPONSE"
        assert result["advance_to_assessment"] is False

        # State should now reflect: tener taught, tener drilled in yo.
        await sync_to_async(session.refresh_from_db)()
        state = session.quiz_state["teach_drill"]
        assert state["taught"] == ["tener"]
        assert state["drills"] == {"tener": ["yo"]}
        assert state["turn_count"] == 1
        assert state["lesson_complete"] is False

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_second_turn_interleaves_retrieval(self, make_user, make_skill):
        """On the second turn, teach next unit + retrieve first unit."""
        from engine.teach_drill import handle_teach_drill_turn
        from learner.models import Session

        user = await sync_to_async(make_user)(discord_id='td_h2', cefr_level='B1')
        skill = await sync_to_async(make_skill)(skill_id='sk_td_h2')
        session = await sync_to_async(Session.objects.create)(
            user=user, session_type='new_skill', target_skill=skill,
            current_phase='teach_drill',
            quiz_state={"teach_drill": {
                "units": [{"id": "tener", "label": "tener", "note": "tuv-"},
                          {"id": "hacer", "label": "hacer", "note": "hic-"}],
                "taught": ["tener"], "drills": {"tener": ["yo"]},
                "turn_count": 1, "lesson_complete": False,
            }},
        )

        with patch('engine.teach_drill.call_llm', new=AsyncMock(return_value="TURN 2")):
            result = await handle_teach_drill_turn(user, session, text="tuve un buen día")

        await sync_to_async(session.refresh_from_db)()
        state = session.quiz_state["teach_drill"]
        # Second unit taught + drilled in yo; first unit gets a retrieval drill in tú.
        assert state["taught"] == ["tener", "hacer"]
        assert state["drills"]["hacer"] == ["yo"]
        assert state["drills"]["tener"] == ["yo", "tú"]  # retrieval added tú

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_marker_in_response_sets_lesson_complete(self, make_user, make_skill):
        """When the LLM emits <<LESSON_COMPLETE>>, state.lesson_complete becomes True."""
        from engine.teach_drill import handle_teach_drill_turn
        from learner.models import Session

        user = await sync_to_async(make_user)(discord_id='td_h3', cefr_level='B1')
        skill = await sync_to_async(make_skill)(skill_id='sk_td_h3')
        session = await sync_to_async(Session.objects.create)(
            user=user, session_type='new_skill', target_skill=skill,
            current_phase='teach_drill',
            quiz_state={"teach_drill": {
                "units": [{"id": "tener", "label": "tener", "note": "tuv-"}],
                "taught": [], "drills": {}, "turn_count": 0, "lesson_complete": False,
            }},
        )

        with patch('engine.teach_drill.call_llm',
                   new=AsyncMock(return_value="TURN\n<<LESSON_COMPLETE>>")):
            result = await handle_teach_drill_turn(user, session, text="ok")

        # Marker should be stripped from the visible text.
        assert "<<LESSON_COMPLETE>>" not in result["text"]

        await sync_to_async(session.refresh_from_db)()
        assert session.quiz_state["teach_drill"]["lesson_complete"] is True

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_already_complete_returns_advance_flag(self, make_user, make_skill):
        """When state.lesson_complete is already True, return advance flag without calling LLM."""
        from engine.teach_drill import handle_teach_drill_turn
        from learner.models import Session

        user = await sync_to_async(make_user)(discord_id='td_h4', cefr_level='B1')
        skill = await sync_to_async(make_skill)(skill_id='sk_td_h4')
        session = await sync_to_async(Session.objects.create)(
            user=user, session_type='new_skill', target_skill=skill,
            current_phase='teach_drill',
            quiz_state={"teach_drill": {
                "units": [{"id": "a", "label": "a", "note": ""}],
                "taught": ["a"], "drills": {"a": ["yo", "tú"]},
                "turn_count": 3, "lesson_complete": True,
            }},
        )

        with patch('engine.teach_drill.call_llm', new=AsyncMock()) as mock_llm:
            result = await handle_teach_drill_turn(user, session, text="ok")

        assert result["advance_to_assessment"] is True
        mock_llm.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_safety_cap_forces_completion(self, make_user, make_skill):
        """After TEACH_DRILL_MAX_TURNS, mark lesson_complete on the next turn regardless of marker."""
        from engine.teach_drill import handle_teach_drill_turn, TEACH_DRILL_MAX_TURNS
        from learner.models import Session

        user = await sync_to_async(make_user)(discord_id='td_h5', cefr_level='B1')
        skill = await sync_to_async(make_skill)(skill_id='sk_td_h5')
        session = await sync_to_async(Session.objects.create)(
            user=user, session_type='new_skill', target_skill=skill,
            current_phase='teach_drill',
            quiz_state={"teach_drill": {
                "units": [{"id": "a", "label": "a", "note": ""},
                          {"id": "b", "label": "b", "note": ""}],
                "taught": ["a"], "drills": {"a": ["yo"]},
                "turn_count": TEACH_DRILL_MAX_TURNS, "lesson_complete": False,
            }},
        )

        with patch('engine.teach_drill.call_llm',
                   new=AsyncMock(return_value="forced end no marker")):
            result = await handle_teach_drill_turn(user, session, text="ok")

        await sync_to_async(session.refresh_from_db)()
        assert session.quiz_state["teach_drill"]["lesson_complete"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest engine/tests/test_teach_drill.py::TestHandleTeachDrillTurn -v`
Expected: 5 FAIL with ImportError or NameError.

- [ ] **Step 3: Implement `handle_teach_drill_turn`**

Append to `engine/teach_drill.py`:

```python
# ── Turn handler ──────────────────────────────────────────────────────────────

from asgiref.sync import sync_to_async

from .session import _strip_lesson_complete_marker, _build_new_skill_history


TEACH_DRILL_MAX_TURNS = 16


async def handle_teach_drill_turn(user, session, text: str) -> dict:
    """Orchestrate one teach_drill turn: pick next actions from state, call LLM, update state."""
    from learner.models import SessionEvent

    state = get_state(session)

    # If we've already told the caller the lesson is complete, this call is a no-op
    # from our side — caller should have transitioned to assessment already, but
    # in case they poll us: return the advance flag.
    if state["lesson_complete"]:
        return {"text": "", "audio_url": None, "session_ended": False,
                "advance_to_assessment": True}

    # Record student response on the pending event, mirroring _continue_new_skill.
    events = await sync_to_async(
        lambda: list(session.events.order_by('timestamp')[:40])
    )()
    pending = next((e for e in reversed(events) if not e.user_response), None)
    if pending:
        await sync_to_async(
            lambda: SessionEvent.objects.filter(pk=pending.pk).update(user_response=text)
        )()
        pending.user_response = text

    units = state["units"]
    taught_ids = state["taught"]
    drills = state["drills"]

    # Pick next unit (or None if all taught).
    next_unit = next_teach_unit(units, taught_ids)

    # Force completion at safety cap.
    force_complete = state["turn_count"] >= TEACH_DRILL_MAX_TURNS

    # If nothing new to teach AND drill coverage is adequate, this is a wrap-up turn.
    all_units_drilled = all(len(drills.get(uid, [])) >= 2 for uid in taught_ids)
    is_wrap_up = next_unit is None and all_units_drilled

    if next_unit is None:
        # Nothing left to teach. Retrieval-only turn (or wrap-up).
        retrieval = None
        person_new = "yo"  # unused
        person_retrieve = None
        # Force final in wrap-up or safety cap.
        is_final = True
    else:
        retrieval_id = select_retrieval_unit(taught_ids, drills)
        retrieval = next((u for u in units if u["id"] == retrieval_id), None) if retrieval_id else None
        person_new = select_person(next_unit["id"], drills)
        person_retrieve = select_person(retrieval["id"], drills) if retrieval else None
        # is_final: this is the last unit AND after this turn every unit will have ≥2 drills.
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
    else:
        # Wrap-up turn: just ask for a brief recap and the marker.
        instruction = (
            "1) Evaluate the student's previous response in ONE line if it contained an answer.\n\n"
            f"2) Briefly recap the units taught in ONE sentence, no lists.\n\n"
            "3) End with the literal marker on its own line: <<LESSON_COMPLETE>>"
        )
    history.append({"role": "user", "content": instruction})

    # Call LLM.
    skill_name = session.target_skill.name if session.target_skill else "this skill"
    suffix = TEACH_DRILL_CONTINUATION_SUFFIX.format(skill_name=skill_name)
    response = await call_llm(history, user=user, system_suffix=suffix)

    # Strip marker.
    response, marker_seen = _strip_lesson_complete_marker(response)

    # Update state.
    if next_unit is not None:
        mark_taught(state, next_unit["id"])
        mark_drilled(state, next_unit["id"], person_new)
        if retrieval is not None and person_retrieve is not None:
            mark_drilled(state, retrieval["id"], person_retrieve)
    state["turn_count"] += 1
    if marker_seen or force_complete or is_wrap_up:
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/python -m pytest engine/tests/test_teach_drill.py::TestHandleTeachDrillTurn -v`
Expected: 5 PASSED.

- [ ] **Step 5: Full suite check**

Run: `venv/bin/python -m pytest engine/tests/ --tb=short 2>&1 | tail -5`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add engine/teach_drill.py engine/tests/test_teach_drill.py
git commit -m "$(cat <<'EOF'
feat: add handle_teach_drill_turn orchestrator

Per-turn orchestration for the teach-drill loop: picks next unit
to teach, picks a prior unit for spaced retrieval, chooses which
persons to drill, builds the LLM prompt, calls the model, strips
the completion marker, and persists state updates. Includes
TEACH_DRILL_MAX_TURNS safety cap to prevent runaway sessions.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Wire `teach_drill` into session opening

**Files:**
- Modify: `engine/session.py`
- Modify: `engine/tests/test_session.py` (may need small update; verify existing tests)

**Interfaces consumed:** `extract_units`, `save_state`, `get_state` from `engine.teach_drill`.

Both `_open_session` (line ~730-ish for grammar branch, current) and `_handle_check_in` (line ~900-ish for grammar branch) currently produce a dense-lesson opening using `GRAMMAR_PRESENT_PROMPT`. We replace both grammar branches to:
1. Call `extract_units(skill.name, skill.description, cefr_level)`.
2. Initialize teach_drill state in `quiz_state` with the units list.
3. Call `handle_teach_drill_turn(user, session, text)` — this generates the first teach turn and stores it as a SessionEvent.
4. Return the result of that call.
5. Set `current_phase = 'teach_drill'`.

If `extract_units` returns an empty list (LLM parse failure), fall back to the old dense-lesson path so we never leave the user with nothing. Log a warning.

- [ ] **Step 1: Locate the two grammar branches and confirm structure**

Run: `grep -n "if stype == 'grammar':" engine/session.py`
Expected: Two matches at approximately lines 764 and 900. Confirm both call `GRAMMAR_PRESENT_PROMPT.format(...)`.

- [ ] **Step 2: Write a failing integration test**

Append to `engine/tests/test_session.py` (near other new_skill tests):

```python
@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_grammar_new_skill_uses_teach_drill_phase(make_user, make_skill):
    """When a grammar new_skill session opens, it enters teach_drill phase
    with a unit list extracted by the LLM."""
    from unittest.mock import patch, AsyncMock
    from asgiref.sync import sync_to_async
    from engine.session import handle_session
    from learner.models import Session

    user = await sync_to_async(make_user)(discord_id='td_open1', cefr_level='B1')
    skill = await sync_to_async(make_skill)(
        skill_id='b1_preterite_test',
        name='Preterite test',
        description='ser, ir, estar',
        cefr_level='B1',
    )

    # Mock the unit extraction and the teach LLM call.
    units_json = '[{"id":"ser_ir","label":"ser/ir","note":"share fui"},{"id":"estar","label":"estar","note":"estuv-"}]'
    with patch('engine.teach_drill.call_llm', new=AsyncMock(side_effect=[units_json, "TEACH TURN 1"])):
        # Ensure _select_session picks this skill (only one exists).
        with patch('engine.session._select_session',
                   new=AsyncMock(return_value=('new_skill', {'skill': {'id': skill.skill_id}}, []))):
            result = await handle_session(user, "hola")

    # Check that a session was created in teach_drill phase with units populated.
    session = await sync_to_async(
        lambda: Session.objects.filter(user=user, ended_at__isnull=True).first()
    )()
    assert session is not None
    assert session.current_phase == 'teach_drill'
    assert session.quiz_state["teach_drill"]["units"][0]["id"] == "ser_ir"
```

Run: `venv/bin/python -m pytest engine/tests/test_session.py::test_grammar_new_skill_uses_teach_drill_phase -v`
Expected: FAIL (current code enters `check_in`, not `teach_drill`).

- [ ] **Step 3: Locate and modify `_open_session` grammar branch**

Read `engine/session.py` lines 760-785 to confirm the current shape, then replace the `if stype == 'grammar':` block inside `_open_session` (should be the first of the two matches from Step 1).

Replace this exact block:

```python
            if stype == 'grammar':
                prompt = GRAMMAR_PRESENT_PROMPT.format(
                    skill_name=target_skill_obj.name,
                    skill_description=target_skill_obj.description,
                    cefr_level=level, interests=interests,
                )
                opening = await call_llm([{"role": "user", "content": prompt}], user=user)
                opening, lesson_complete = _strip_lesson_complete_marker(opening)
                if lesson_complete:
                    opening = opening + "\n\n" + CLARIFYING_QUESTIONS_STRING
                    await sync_to_async(
                        lambda: Session.objects.filter(pk=session.pk).update(
                            quiz_state={'lesson_complete': True}
                        )
                    )()
                initial_phase = 'questions'
```

With:

```python
            if stype == 'grammar':
                from .teach_drill import extract_units, save_state, EMPTY_STATE
                units = await extract_units(
                    skill_name=target_skill_obj.name,
                    skill_description=target_skill_obj.description,
                    cefr_level=level,
                )
                if units:
                    # Enter teach_drill phase with the extracted units.
                    fresh_state = {**EMPTY_STATE, "units": units,
                                   "taught": [], "drills": {}}
                    await save_state(session, fresh_state)
                    initial_phase = 'teach_drill'
                    # Generate the opening framing turn — TEACH_DRILL_OPENING_PROMPT
                    # is a scripted framing message; the first actual teaching turn
                    # happens on the student's next reply.
                    from .teach_drill import TEACH_DRILL_OPENING_PROMPT
                    prompt = TEACH_DRILL_OPENING_PROMPT.format(
                        skill_name=target_skill_obj.name,
                        cefr_level=level, interests=interests,
                    )
                    opening = await call_llm([{"role": "user", "content": prompt}], user=user)
                else:
                    # Fallback: LLM failed to enumerate units. Use the legacy dense lesson.
                    prompt = GRAMMAR_PRESENT_PROMPT.format(
                        skill_name=target_skill_obj.name,
                        skill_description=target_skill_obj.description,
                        cefr_level=level, interests=interests,
                    )
                    opening = await call_llm([{"role": "user", "content": prompt}], user=user)
                    opening, lesson_complete = _strip_lesson_complete_marker(opening)
                    if lesson_complete:
                        opening = opening + "\n\n" + CLARIFYING_QUESTIONS_STRING
                        await sync_to_async(
                            lambda: Session.objects.filter(pk=session.pk).update(
                                quiz_state={'lesson_complete': True}
                            )
                        )()
                    initial_phase = 'questions'
```

- [ ] **Step 4: Apply the exact same replacement in `_handle_check_in`**

The second grammar branch (from Step 1's second match) uses slightly different variable names — `skill` instead of `target_skill_obj`. Replace this block:

```python
            if stype == 'grammar':
                prompt = GRAMMAR_PRESENT_PROMPT.format(
                    skill_name=skill.name, skill_description=skill.description,
                    cefr_level=level, interests=interests,
                )
                opening = await call_llm([{"role": "user", "content": prompt}], user=user)
                opening, lesson_complete = _strip_lesson_complete_marker(opening)
                if lesson_complete:
                    opening = opening + "\n\n" + CLARIFYING_QUESTIONS_STRING
                    await sync_to_async(
                        lambda: Session.objects.filter(pk=session.pk).update(
                            quiz_state={'lesson_complete': True}
                        )
                    )()
                initial_phase = 'questions'
```

With the analogous replacement using `skill.name` / `skill.description` (same shape as Step 3 but with `skill` instead of `target_skill_obj`):

```python
            if stype == 'grammar':
                from .teach_drill import extract_units, save_state, EMPTY_STATE
                units = await extract_units(
                    skill_name=skill.name,
                    skill_description=skill.description,
                    cefr_level=level,
                )
                if units:
                    fresh_state = {**EMPTY_STATE, "units": units,
                                   "taught": [], "drills": {}}
                    await save_state(session, fresh_state)
                    initial_phase = 'teach_drill'
                    from .teach_drill import TEACH_DRILL_OPENING_PROMPT
                    prompt = TEACH_DRILL_OPENING_PROMPT.format(
                        skill_name=skill.name,
                        cefr_level=level, interests=interests,
                    )
                    opening = await call_llm([{"role": "user", "content": prompt}], user=user)
                else:
                    prompt = GRAMMAR_PRESENT_PROMPT.format(
                        skill_name=skill.name, skill_description=skill.description,
                        cefr_level=level, interests=interests,
                    )
                    opening = await call_llm([{"role": "user", "content": prompt}], user=user)
                    opening, lesson_complete = _strip_lesson_complete_marker(opening)
                    if lesson_complete:
                        opening = opening + "\n\n" + CLARIFYING_QUESTIONS_STRING
                        await sync_to_async(
                            lambda: Session.objects.filter(pk=session.pk).update(
                                quiz_state={'lesson_complete': True}
                            )
                        )()
                    initial_phase = 'questions'
```

- [ ] **Step 5: Run the failing test to verify it now passes**

Run: `venv/bin/python -m pytest engine/tests/test_session.py::test_grammar_new_skill_uses_teach_drill_phase -v`
Expected: PASS.

- [ ] **Step 6: Run the full suite**

Run: `venv/bin/python -m pytest engine/tests/ --tb=short 2>&1 | tail -8`
Expected: All existing tests still pass. If any grammar-new_skill test now fails because it expected `phase='questions'`, note it and either update the test to reflect new behavior (if teach_drill is the correct outcome) or investigate the discrepancy.

- [ ] **Step 7: Commit**

```bash
git add engine/session.py engine/tests/test_session.py
git commit -m "$(cat <<'EOF'
feat: enter teach_drill phase when opening grammar sessions

Both _open_session and _handle_check_in now extract teachable units
via the LLM at session open and initialize teach_drill state. On
LLM extraction failure, falls back to the legacy dense-lesson path
so users are never stranded.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Wire `teach_drill` into session continuation

**Files:**
- Modify: `engine/session.py`
- Modify: `engine/tests/test_session.py`

**Interfaces consumed:** `handle_teach_drill_turn` from `engine.teach_drill`.

Modify `_continue_new_skill` in `engine/session.py` so that when `phase == 'teach_drill'`, it delegates to the new handler and, if the handler returns `advance_to_assessment: True`, transitions phase to `assessment` and generates the first assessment turn.

Look at the existing structure of `_continue_new_skill` (around line 1030+):
- It reads phase, then dispatches through the phase-specific branches
- Currently handles: `complete`, `questions`, `reinforcement_check`, then the general LLM-response path with turn counting

Add a new branch for `phase == 'teach_drill'` at the top (after the `complete` check).

- [ ] **Step 1: Write the failing test**

Append to `engine/tests/test_session.py`:

```python
@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_teach_drill_phase_delegates_to_handler(make_user, make_skill):
    """When a session is in teach_drill phase, _continue_new_skill calls the handler."""
    from unittest.mock import patch, AsyncMock
    from asgiref.sync import sync_to_async
    from engine.session import _continue_new_skill
    from learner.models import Session

    user = await sync_to_async(make_user)(discord_id='td_cont1', cefr_level='B1')
    skill = await sync_to_async(make_skill)(skill_id='sk_td_cont1', name='Test')
    session = await sync_to_async(Session.objects.create)(
        user=user, session_type='new_skill', target_skill=skill,
        current_phase='teach_drill',
        quiz_state={"teach_drill": {
            "units": [{"id": "tener", "label": "tener", "note": ""}],
            "taught": [], "drills": {}, "turn_count": 0, "lesson_complete": False,
        }},
    )

    fake_handler_result = {"text": "TEACH", "audio_url": None,
                           "session_ended": False, "advance_to_assessment": False}
    with patch('engine.teach_drill.handle_teach_drill_turn',
               new=AsyncMock(return_value=fake_handler_result)) as mock_handler:
        result = await _continue_new_skill(user, session, "listo")

    mock_handler.assert_called_once()
    assert result["text"] == "TEACH"


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_teach_drill_advance_to_assessment_transitions_phase(make_user, make_skill):
    """When handler returns advance_to_assessment=True, phase transitions to 'assessment'."""
    from unittest.mock import patch, AsyncMock
    from asgiref.sync import sync_to_async
    from engine.session import _continue_new_skill
    from learner.models import Session

    user = await sync_to_async(make_user)(discord_id='td_cont2', cefr_level='B1')
    skill = await sync_to_async(make_skill)(skill_id='sk_td_cont2', name='Test')
    session = await sync_to_async(Session.objects.create)(
        user=user, session_type='new_skill', target_skill=skill,
        current_phase='teach_drill',
        quiz_state={"teach_drill": {
            "units": [{"id": "a", "label": "a", "note": ""}],
            "taught": ["a"], "drills": {"a": ["yo", "tú"]},
            "turn_count": 3, "lesson_complete": True,
        }},
    )

    fake_handler_result = {"text": "", "audio_url": None,
                           "session_ended": False, "advance_to_assessment": True}
    with patch('engine.teach_drill.handle_teach_drill_turn',
               new=AsyncMock(return_value=fake_handler_result)):
        # Also mock call_llm since the assessment turn will call it.
        with patch('engine.session.call_llm', new=AsyncMock(return_value="ASSESSMENT Q1")):
            result = await _continue_new_skill(user, session, "ok")

    await sync_to_async(session.refresh_from_db)()
    assert session.current_phase == 'assessment'
    assert session.phase_turns_completed == 0
```

Run: `venv/bin/python -m pytest engine/tests/test_session.py -k teach_drill -v`
Expected: 2 new tests FAIL.

- [ ] **Step 2: Modify `_continue_new_skill`**

Locate `_continue_new_skill` in `engine/session.py` (around line 1030). After the existing `complete` phase check, add:

```python
    if phase == 'teach_drill':
        from .teach_drill import handle_teach_drill_turn
        result = await handle_teach_drill_turn(user, session, text)
        if result["advance_to_assessment"]:
            await _set_phase(session, 'assessment', 0)
            # Immediately generate the first assessment question so the student
            # doesn't sit staring at an empty response.
            suffix = _get_phase_suffix('assessment', skill, stype, [])
            # Load fresh event history for the LLM.
            events = await sync_to_async(
                lambda: list(session.events.order_by('timestamp')[:40])
            )()
            history = _build_new_skill_history(events)
            first_q = await call_llm(history, user=user, system_suffix=suffix)
            await sync_to_async(SessionEvent.objects.create)(
                session=session, event_type='conversation',
                content=first_q, user_response='',
            )
            return {"text": first_q, "audio_url": None, "session_ended": False}
        return {"text": result["text"], "audio_url": result["audio_url"],
                "session_ended": result["session_ended"]}
```

Note: this branch must run before the existing `phase == 'questions'` logic so that teach_drill takes over cleanly.

- [ ] **Step 3: Run the failing tests to verify they now pass**

Run: `venv/bin/python -m pytest engine/tests/test_session.py -k teach_drill -v`
Expected: 3 PASS (the Task 6 test + 2 new ones).

- [ ] **Step 4: Full suite check**

Run: `venv/bin/python -m pytest engine/tests/ --tb=short 2>&1 | tail -5`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add engine/session.py engine/tests/test_session.py
git commit -m "$(cat <<'EOF'
feat: dispatch teach_drill phase in _continue_new_skill

Adds a top-level branch in _continue_new_skill for the teach_drill
phase. Delegates each turn to handle_teach_drill_turn; when the
handler signals advance_to_assessment, transitions phase to
assessment and immediately generates the first assessment question
so the student doesn't see an empty response.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Collapse phase flow constants and update tests

**Files:**
- Modify: `engine/session.py`
- Modify: `engine/tests/test_session.py`

Since teach_drill absorbs `present`, `questions`, and `guided_practice`, the grammar phase flow becomes shorter:

Before:
```python
GRAMMAR_PHASE_FLOW = ['present', 'questions', 'guided_practice', 'free_production', 'reinforcement_check', 'assessment', 'complete']
```

After:
```python
GRAMMAR_PHASE_FLOW = ['teach_drill', 'free_production', 'reinforcement_check', 'assessment', 'complete']
```

`free_production` and `reinforcement_check` are retained for now — cutting them is a separate design decision the user has not signed off on. The teach_drill → assessment jump is handled explicitly in Task 7's code, not via `_next_phase`, so flow ordering only matters if `_next_phase` is ever called with `teach_drill` — in which case it should advance to `free_production` (safe default).

- [ ] **Step 1: Update the failing existing test**

Locate `test_grammar_present_advances_to_questions` in `engine/tests/test_session.py`. The test asserts `_next_phase('present', 'grammar') == 'questions'`. Since `present` and `questions` are removed from the flow, this test becomes stale.

Replace the two grammar-flow tests:

```python
    def test_grammar_present_advances_to_questions(self):
        from engine.session import _next_phase
        assert _next_phase('present', 'grammar') == 'questions'
```

With:

```python
    def test_grammar_teach_drill_advances_to_free_production(self):
        from engine.session import _next_phase
        assert _next_phase('teach_drill', 'grammar') == 'free_production'
```

The vocab test for `_next_phase('present', 'vocab') == 'guided_practice'` stays as-is — vocab flow is unchanged.

- [ ] **Step 2: Update `GRAMMAR_PHASE_FLOW`**

In `engine/session.py`, change the constant:

Find:
```python
GRAMMAR_PHASE_FLOW = ['present', 'questions', 'guided_practice', 'free_production', 'reinforcement_check', 'assessment', 'complete']
```

Replace with:
```python
GRAMMAR_PHASE_FLOW = ['teach_drill', 'free_production', 'reinforcement_check', 'assessment', 'complete']
```

Also update `GRAMMAR_PHASE_TURNS` — remove `guided_practice` since teach_drill absorbs it:

Find:
```python
GRAMMAR_PHASE_TURNS = {'guided_practice': 4, 'free_production': 3, 'reinforcement': 4, 'assessment': 3}
```

Replace with:
```python
GRAMMAR_PHASE_TURNS = {'free_production': 3, 'reinforcement': 4, 'assessment': 3}
```

- [ ] **Step 3: Run the phase flow tests**

Run: `venv/bin/python -m pytest engine/tests/test_session.py::TestPhaseFlow -v`
Expected: All PASS with the updated assertion.

- [ ] **Step 4: Full suite**

Run: `venv/bin/python -m pytest engine/tests/ --tb=short 2>&1 | tail -5`
Expected: All tests pass. If any test relied on `phase='questions'` for a grammar new_skill session, either update it to `teach_drill` or investigate. Report any test that fails and cannot be trivially updated.

- [ ] **Step 5: Commit**

```bash
git add engine/session.py engine/tests/test_session.py
git commit -m "$(cat <<'EOF'
refactor: collapse GRAMMAR_PHASE_FLOW; teach_drill absorbs 3 phases

Grammar new_skill flow is now: teach_drill → free_production →
reinforcement_check → assessment → complete. present, questions,
and guided_practice are removed from the flow (teach_drill handles
all three). guided_practice removed from GRAMMAR_PHASE_TURNS.

Vocab flow unchanged. The teach_drill → assessment transition is
handled explicitly in _continue_new_skill (Task 7), not via
_next_phase — the flow constant's teach_drill → free_production
ordering is a safe fallback for any code path that calls
_next_phase('teach_drill').

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Delete obsolete grammar prompts and code

**Files:**
- Modify: `engine/session.py`

Remove code that is no longer reachable on the grammar path. Be careful — some of it (e.g. `CLARIFYING_QUESTIONS_STRING`) is still used by the fallback path in Task 6. Only remove what is truly dead.

Do NOT remove:
- `GRAMMAR_PRESENT_PROMPT` — still used by the extract_units-failed fallback in Task 6
- `CLARIFYING_QUESTIONS_STRING` — still used by fallback
- `QUESTIONS_PHASE_SUFFIX` — used if fallback lands in `questions` phase
- `GUIDED_PRACTICE_GRAMMAR_SUFFIX`, `GUIDED_PRACTICE_VOCAB_SUFFIX` — vocab still uses them (guided_practice is still in VOCAB flow)
- `_strip_lesson_complete_marker` — imported by teach_drill.py

Actually, **at this point nothing is safely dead** — everything is retained for the fallback path or vocab. Skip deletions in this pass.

- [ ] **Step 1: Verify current dead-code hypothesis is empty**

Run: `grep -c "GRAMMAR_PRESENT_PROMPT\|CLARIFYING_QUESTIONS_STRING\|QUESTIONS_PHASE_SUFFIX" engine/session.py`
Expected: Non-zero — these are all still referenced by the fallback path.

- [ ] **Step 2: No-op commit — record decision**

There is no code to remove yet. Skip this task's commit step. In a future cleanup (once we've validated teach_drill in production and are confident the fallback isn't needed), remove:
- The fallback branches in `_open_session` and `_handle_check_in`
- `GRAMMAR_PRESENT_PROMPT`
- `QUESTIONS_PHASE_SUFFIX`
- The `phase == 'questions'` branch in `_continue_new_skill` (grammar-only usage)

For now: proceed to Task 10.

---

## Task 10: Smoke test in dev + push to prod

**Files:**
- No file changes — this is manual QA + a git push.

- [ ] **Step 1: Run the full test suite one final time**

Run: `venv/bin/python -m pytest engine/tests/ --tb=short 2>&1 | tail -8`
Expected: All tests pass.

- [ ] **Step 2: Check for uncommitted changes**

Run: `git status`
Expected: Clean working tree. If there are unstaged changes, review them — they may indicate an unfinished task.

- [ ] **Step 3: Push to origin (Railway auto-deploys)**

Run: `git push`
Expected: Push succeeds, Railway begins auto-deploying.

- [ ] **Step 4: Close any open grammar session for the primary user**

Wait for Railway deploy to complete, then close Eric's session via the production DB so his next DM triggers a fresh teach_drill session:

```bash
DATABASE_URL='<from-railway-vars>' venv/bin/python manage.py shell <<'EOF'
import asyncio
from learner.models import Session
from engine.session import _close_session_record

s = Session.objects.filter(user__discord_id='548592030981292032', ended_at__isnull=True).select_related('user').order_by('-started_at').first()
if s:
    print(f"Closing session {s.id} (phase={s.current_phase})")
    asyncio.run(_close_session_record(s, s.user))
else:
    print("No open session.")
EOF
```

- [ ] **Step 5: Manual smoke test on Discord**

DM Luz Ángela with any message. Expected behavior:
1. Bot sends a scripted check-in ("¿Listo/a?" for B1+).
2. Student replies affirmatively.
3. Bot sends the teach_drill opening (outcome framing + "Ready to start?").
4. Student says "listo" or "ready".
5. Bot teaches the FIRST unit only (paradigm one line per person + 1 example + a production question).
6. Student answers.
7. Bot evaluates the answer (✓/✗ one line), teaches the NEXT unit, drills it, AND drills the previous unit in a different person.
8. Loop continues until all units taught and each drilled ≥2 times.
9. Final teach_drill turn contains `<<LESSON_COMPLETE>>` (invisible — stripped).
10. Next student message triggers phase transition to assessment; bot asks an assessment question.

- [ ] **Step 6: Report smoke test findings back to the user**

Note any deviations from the expected behavior, especially:
- LLM extracts weird units (wrong grouping, missing verbs, spurious extras)
- Marker never fires (loop runs to safety cap)
- Marker fires too early (before all units taught)
- Paradigms revert to inline comma-separated
- More than one unit taught per turn
- Retrieval questions asked before enough units are available

If any of the above occurs, that's Task 11 (unwritten, feedback-driven).

---

## Self-Review Checklist

Before handing this to the implementer:

**Spec coverage:**
- Unit extraction from LLM → Task 1 ✓
- Rotation/spacing logic → Task 2 ✓
- State schema in quiz_state → Task 3 ✓
- Prompt templates for teach + drill → Task 4 ✓
- Per-turn orchestration → Task 5 ✓
- Session opening enters teach_drill → Task 6 ✓
- Continuation dispatches to handler → Task 7 ✓
- Phase flow updated → Task 8 ✓
- Assessment stays as-is with existing scoring → confirmed (no changes to ASSESSMENT_SUFFIX or `_next_phase` past `assessment`)
- Fallback for unit extraction failure → Task 6 ✓
- Safety cap on runaway sessions → Task 5 (TEACH_DRILL_MAX_TURNS) ✓
- Vocab unchanged → confirmed (no edits to vocab paths)

**Placeholder scan:** No TBD / TODO / "implement later" — all steps have complete code or exact commands.

**Type consistency:**
- `extract_units` returns `list[dict]` with `{"id", "label", "note"}` — used consistently in `next_teach_unit`, `select_retrieval_unit`, `build_teach_instruction`.
- `handle_teach_drill_turn` return shape `{"text", "audio_url", "session_ended", "advance_to_assessment"}` — Task 7 reads `advance_to_assessment` correctly.
- `save_state` / `get_state` operate on `session.quiz_state["teach_drill"]` — consistent across tasks.
