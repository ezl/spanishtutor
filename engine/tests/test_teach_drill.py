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


from asgiref.sync import sync_to_async


class TestState:
    def test_get_state_returns_empty_when_absent(self):
        from engine.teach_drill import get_state
        class FakeSession:
            quiz_state = None
        state = get_state(FakeSession())
        assert state == {"units": [], "taught": [], "drills": {}, "turn_count": 0,
                         "lesson_complete": False, "last_turn_type": None}

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


class TestPromptBuilding:
    def test_build_teach_instruction_teaches_and_drills_one_unit(self):
        """Teach turn: teach the unit, one production question. No retrieval."""
        from engine.teach_drill import build_teach_instruction
        unit = {"id": "tener", "label": "tener", "note": "stem tuv-"}
        instr = build_teach_instruction(unit, person_new="yo", is_final=False)
        assert "tener" in instr
        assert "tuv-" in instr
        assert "yo" in instr.lower()
        # Teach turn asks EXACTLY ONE question — the instruction must specify singular.
        assert "EXACTLY ONE" in instr

    def test_build_teach_instruction_final_turn_asks_for_marker(self):
        from engine.teach_drill import build_teach_instruction
        unit = {"id": "saber", "label": "saber", "note": "sup-"}
        instr = build_teach_instruction(unit, person_new="yo", is_final=True)
        assert "<<LESSON_COMPLETE>>" in instr

    def test_build_teach_instruction_includes_cue_selection_rules(self):
        """Cue selection guardrails must be in every teach instruction."""
        from engine.teach_drill import build_teach_instruction
        unit = {"id": "ser", "label": "ser", "note": ""}
        instr = build_teach_instruction(unit, person_new="yo", is_final=False)
        assert "CUE SELECTION" in instr
        # Must warn about the ser/estar location trap specifically.
        assert "I was at" in instr

    def test_opening_prompt_contains_skill_name_and_outcome_framing_directive(self):
        from engine.teach_drill import TEACH_DRILL_OPENING_PROMPT
        assert "{skill_name}" in TEACH_DRILL_OPENING_PROMPT
        # Must instruct the LLM to include outcome framing.
        assert "outcome" in TEACH_DRILL_OPENING_PROMPT.lower() or "you'll" in TEACH_DRILL_OPENING_PROMPT.lower()

    def test_continuation_suffix_contains_paradigm_format_rule(self):
        from engine.teach_drill import TEACH_DRILL_CONTINUATION_SUFFIX
        # Must instruct one-line-per-person paradigm rendering.
        assert "one line" in TEACH_DRILL_CONTINUATION_SUFFIX.lower() or "per person" in TEACH_DRILL_CONTINUATION_SUFFIX.lower()


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
    async def test_after_teach_turn_next_is_retrieval(self, make_user, make_skill):
        """Option B alternation: after a teach turn, the next turn is retrieval
        (drill prior unit) — NOT teach + retrieval piggybacked."""
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
                # Last turn was a teach of tener → next should be retrieval.
                "taught": ["tener"], "drills": {"tener": ["yo"]},
                "turn_count": 1, "lesson_complete": False,
                "last_turn_type": "teach",
            }},
        )

        with patch('engine.teach_drill.call_llm', new=AsyncMock(return_value="RETRIEVAL Q")):
            await handle_teach_drill_turn(user, session, text="tuve un buen día")

        await sync_to_async(session.refresh_from_db)()
        state = session.quiz_state["teach_drill"]
        # hacer must NOT be taught this turn — retrieval turn only.
        assert "hacer" not in state["taught"]
        assert "hacer" not in state["drills"]
        # tener gets an additional drill from the retrieval.
        assert len(state["drills"]["tener"]) == 2
        assert state["last_turn_type"] == "retrieval"

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_after_retrieval_turn_next_is_teach(self, make_user, make_skill):
        """After a retrieval turn, the next turn teaches a new unit."""
        from engine.teach_drill import handle_teach_drill_turn
        from learner.models import Session

        user = await sync_to_async(make_user)(discord_id='td_h2b', cefr_level='B1')
        skill = await sync_to_async(make_skill)(skill_id='sk_td_h2b')
        session = await sync_to_async(Session.objects.create)(
            user=user, session_type='new_skill', target_skill=skill,
            current_phase='teach_drill',
            quiz_state={"teach_drill": {
                "units": [{"id": "tener", "label": "tener", "note": "tuv-"},
                          {"id": "hacer", "label": "hacer", "note": "hic-"}],
                "taught": ["tener"], "drills": {"tener": ["yo", "tú"]},
                "turn_count": 2, "lesson_complete": False,
                "last_turn_type": "retrieval",
            }},
        )

        with patch('engine.teach_drill.call_llm', new=AsyncMock(return_value="TEACH HACER")):
            await handle_teach_drill_turn(user, session, text="tuviste")

        await sync_to_async(session.refresh_from_db)()
        state = session.quiz_state["teach_drill"]
        assert "hacer" in state["taught"]
        assert "hacer" in state["drills"]
        assert state["last_turn_type"] == "teach"

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


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_early_return_still_records_user_response(make_user, make_skill):
    """When state.lesson_complete is already True, the student's reply that
    triggered the advance-to-assessment must still be recorded on the pending event."""
    from unittest.mock import patch, AsyncMock
    from asgiref.sync import sync_to_async
    from engine.teach_drill import handle_teach_drill_turn
    from learner.models import Session, SessionEvent

    user = await sync_to_async(make_user)(discord_id='td_earlyret', cefr_level='B1')
    skill = await sync_to_async(make_skill)(skill_id='sk_td_earlyret')
    session = await sync_to_async(Session.objects.create)(
        user=user, session_type='new_skill', target_skill=skill,
        current_phase='teach_drill',
        quiz_state={"teach_drill": {
            "units": [{"id": "a", "label": "a", "note": ""}],
            "taught": ["a"], "drills": {"a": ["yo", "tú"]},
            "turn_count": 3, "lesson_complete": True,
        }},
    )
    # Pending event: last assistant turn with no user_response yet.
    await sync_to_async(SessionEvent.objects.create)(
        session=session, event_type='conversation',
        content="last drill question", user_response='',
    )

    with patch('engine.teach_drill.call_llm', new=AsyncMock()):
        result = await handle_teach_drill_turn(user, session, text="tuve un buen día")

    assert result["advance_to_assessment"] is True
    # The pending event should now have the student's response recorded.
    pending = await sync_to_async(
        lambda: SessionEvent.objects.get(session=session)
    )()
    assert pending.user_response == "tuve un buen día"


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_retrieval_only_turn_drills_under_drilled_unit(make_user, make_skill):
    """When all units are taught but some have <2 drills, next turn does drill-only (no new teach)."""
    from unittest.mock import patch, AsyncMock
    from asgiref.sync import sync_to_async
    from engine.teach_drill import handle_teach_drill_turn
    from learner.models import Session

    user = await sync_to_async(make_user)(discord_id='td_retonly', cefr_level='B1')
    skill = await sync_to_async(make_skill)(skill_id='sk_td_retonly')
    session = await sync_to_async(Session.objects.create)(
        user=user, session_type='new_skill', target_skill=skill,
        current_phase='teach_drill',
        quiz_state={"teach_drill": {
            "units": [{"id": "a", "label": "a", "note": ""},
                      {"id": "b", "label": "b", "note": ""}],
            "taught": ["a", "b"],  # both taught
            "drills": {"a": ["yo", "tú"], "b": ["yo"]},  # a has 2, b has 1
            "turn_count": 3, "lesson_complete": False,
        }},
    )

    with patch('engine.teach_drill.call_llm', new=AsyncMock(return_value="review question for b")):
        result = await handle_teach_drill_turn(user, session, text="tuve")

    await sync_to_async(session.refresh_from_db)()
    state = session.quiz_state["teach_drill"]
    # b should now have 2 drills. Nothing new taught.
    assert state["taught"] == ["a", "b"]
    assert len(state["drills"]["b"]) == 2  # was 1, now 2 (retrieval-only drilled b in tú)
    assert state["lesson_complete"] is False  # still not complete — but eligible for wrap-up next


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_every_unit_drilled_at_least_twice_before_completion(make_user, make_skill):
    """Run the full loop on a 2-unit skill and verify every unit ends up with ≥2 drills."""
    from unittest.mock import patch, AsyncMock
    from asgiref.sync import sync_to_async
    from engine.teach_drill import handle_teach_drill_turn
    from learner.models import Session

    user = await sync_to_async(make_user)(discord_id='td_inv', cefr_level='B1')
    skill = await sync_to_async(make_skill)(skill_id='sk_td_inv')
    session = await sync_to_async(Session.objects.create)(
        user=user, session_type='new_skill', target_skill=skill,
        current_phase='teach_drill',
        quiz_state={"teach_drill": {
            "units": [{"id": "a", "label": "a", "note": ""},
                      {"id": "b", "label": "b", "note": ""}],
            "taught": [], "drills": {}, "turn_count": 0, "lesson_complete": False,
        }},
    )

    # Simulate: LLM never spontaneously emits the marker; controller drives to completion via safety + drilling.
    with patch('engine.teach_drill.call_llm', new=AsyncMock(return_value="response, no marker")):
        # Drive turns until lesson_complete flips.
        for _ in range(10):  # generous upper bound
            # Reload session with target_skill pre-fetched to avoid async lazy-load.
            session = await sync_to_async(
                lambda: Session.objects.select_related('target_skill').get(pk=session.pk)
            )()
            if session.quiz_state.get("teach_drill", {}).get("lesson_complete"):
                break
            await handle_teach_drill_turn(user, session, text="ok")

    session = await sync_to_async(
        lambda: Session.objects.select_related('target_skill').get(pk=session.pk)
    )()
    state = session.quiz_state["teach_drill"]
    assert state["lesson_complete"] is True
    # Invariant: every taught unit has ≥2 drills.
    for uid in state["taught"]:
        assert len(state["drills"][uid]) >= 2, f"Unit {uid} has {len(state['drills'][uid])} drills"


# ── REDO_PENDING marker: wrong-answer reinforcement ──────────────────────────

class TestRedoPendingMarker:
    def test_strip_marker_present(self):
        from engine.teach_drill import _strip_redo_pending_marker
        cleaned, present = _strip_redo_pending_marker(
            "✗ tuvieste — correct is tuviste.\nTry again: ...\n<<REDO_PENDING>>"
        )
        assert present is True
        assert "<<REDO_PENDING>>" not in cleaned
        assert "Try again" in cleaned

    def test_strip_marker_absent(self):
        from engine.teach_drill import _strip_redo_pending_marker
        cleaned, present = _strip_redo_pending_marker("regular response, no marker")
        assert present is False
        assert cleaned == "regular response, no marker"

    def test_continuation_suffix_documents_the_marker(self):
        """The suffix must instruct the LLM about when to emit <<REDO_PENDING>>."""
        from engine.teach_drill import TEACH_DRILL_CONTINUATION_SUFFIX
        assert "<<REDO_PENDING>>" in TEACH_DRILL_CONTINUATION_SUFFIX
        # And explains the wrong-answer reinforcement rule.
        assert "Try again" in TEACH_DRILL_CONTINUATION_SUFFIX
        # And caps at one redo (no third try).
        assert "third time" in TEACH_DRILL_CONTINUATION_SUFFIX


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_redo_pending_defers_state_advancement(make_user, make_skill):
    """When the LLM emits <<REDO_PENDING>>, the code must NOT mark the planned
    next_unit as taught. Next turn the same unit is picked again for teaching."""
    from asgiref.sync import sync_to_async
    from unittest.mock import patch, AsyncMock
    from engine.teach_drill import handle_teach_drill_turn
    from learner.models import Session

    user = await sync_to_async(make_user)(discord_id='td_redo1', cefr_level='B1')
    skill = await sync_to_async(make_skill)(skill_id='sk_td_redo1')
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

    # Simulate the LLM doing a redo instead of teaching the next unit.
    redo_response = "✗ tuvieste — correct is tuviste.\nTry again: how would you say 'you had a son'?\n<<REDO_PENDING>>"
    with patch('engine.teach_drill.call_llm', new=AsyncMock(return_value=redo_response)):
        result = await handle_teach_drill_turn(user, session, text="tuvieste un hijo")

    # Marker should be stripped from visible text.
    assert "<<REDO_PENDING>>" not in result["text"]

    session = await sync_to_async(
        lambda: Session.objects.select_related('target_skill').get(pk=session.pk)
    )()
    state = session.quiz_state["teach_drill"]

    # State must NOT have advanced: hacer stays un-taught, tener's drill count unchanged.
    assert state["taught"] == ["tener"], "next_unit should NOT be marked taught on a redo turn"
    assert state["drills"] == {"tener": ["yo"]}, "no new drills should be recorded on a redo turn"

    # But turn_count DOES advance (safety cap remains meaningful).
    assert state["turn_count"] == 2

    # And lesson_complete stays False.
    assert state["lesson_complete"] is False


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_next_turn_after_redo_teaches_the_deferred_unit(make_user, make_skill):
    """After a redo turn, the next turn should teach the unit that was deferred."""
    from asgiref.sync import sync_to_async
    from unittest.mock import patch, AsyncMock
    from engine.teach_drill import handle_teach_drill_turn
    from learner.models import Session

    user = await sync_to_async(make_user)(discord_id='td_redo2', cefr_level='B1')
    skill = await sync_to_async(make_skill)(skill_id='sk_td_redo2')
    session = await sync_to_async(Session.objects.create)(
        user=user, session_type='new_skill', target_skill=skill,
        current_phase='teach_drill',
        quiz_state={"teach_drill": {
            "units": [{"id": "tener", "label": "tener", "note": "tuv-"},
                      {"id": "hacer", "label": "hacer", "note": "hic-"}],
            # State AFTER a redo turn just happened: hacer is still un-taught.
            "taught": ["tener"], "drills": {"tener": ["yo"]},
            "turn_count": 2, "lesson_complete": False,
        }},
    )

    # Now the LLM responds normally (no redo marker) — it should teach hacer.
    with patch('engine.teach_drill.call_llm', new=AsyncMock(return_value="teaches hacer + drills")):
        await handle_teach_drill_turn(user, session, text="tuviste un hijo")

    session = await sync_to_async(
        lambda: Session.objects.select_related('target_skill').get(pk=session.pk)
    )()
    state = session.quiz_state["teach_drill"]
    # Now hacer should be marked taught and drilled.
    assert "hacer" in state["taught"]
    assert "hacer" in state["drills"]


class TestSessionFeedbackModel:
    @pytest.mark.django_db(transaction=True)
    def test_sessionfeedback_creates_and_reads_back(self, make_user, make_skill):
        from learner.models import Session, SessionEvent, SessionFeedback
        user = make_user(discord_id='sf_model1')
        skill = make_skill(skill_id='sk_sf_model1')
        session = Session.objects.create(user=user, session_type='new_skill', target_skill=skill)
        event = SessionEvent.objects.create(
            session=session, event_type='conversation',
            content='Luz asked a question', user_response='',
        )
        fb = SessionFeedback.objects.create(
            session=session,
            anchor_event=event,
            user_message="that cue was ambiguous",
            interpretation="Student flagged that the English cue was ambiguous between ser and estar.",
        )
        reloaded = SessionFeedback.objects.get(pk=fb.pk)
        assert reloaded.session_id == session.pk
        assert reloaded.anchor_event_id == event.pk
        assert reloaded.user_message == "that cue was ambiguous"
        assert reloaded.interpretation.startswith("Student flagged")
        assert reloaded.resolved is False
        assert reloaded.resolution_note == ""
        assert list(session.feedback.all()) == [reloaded]

    @pytest.mark.django_db(transaction=True)
    def test_anchor_event_deletion_keeps_feedback_with_null_anchor(self, make_user, make_skill):
        """Deleting the anchoring event must not delete the feedback (SET_NULL semantics)."""
        from learner.models import Session, SessionEvent, SessionFeedback
        user = make_user(discord_id='sf_model2')
        skill = make_skill(skill_id='sk_sf_model2')
        session = Session.objects.create(user=user, session_type='new_skill', target_skill=skill)
        event = SessionEvent.objects.create(
            session=session, event_type='conversation', content='c', user_response='',
        )
        fb = SessionFeedback.objects.create(
            session=session, anchor_event=event,
            user_message="msg", interpretation="paraphrase",
        )
        event.delete()
        fb.refresh_from_db()
        assert fb.anchor_event is None
        assert fb.user_message == "msg"

    @pytest.mark.django_db(transaction=True)
    def test_session_deletion_cascades_to_feedback(self, make_user, make_skill):
        """Deleting the session should delete its feedback (CASCADE semantics)."""
        from learner.models import Session, SessionFeedback
        user = make_user(discord_id='sf_model3')
        skill = make_skill(skill_id='sk_sf_model3')
        session = Session.objects.create(user=user, session_type='new_skill', target_skill=skill)
        fb = SessionFeedback.objects.create(
            session=session, user_message="msg", interpretation="p",
        )
        fb_pk = fb.pk
        session.delete()
        assert not SessionFeedback.objects.filter(pk=fb_pk).exists()


class TestFeedbackMarker:
    def test_strip_marker_extracts_interpretation(self):
        from engine.teach_drill import _strip_feedback_marker
        text = ("Got it, logged that. Sigamos.\n"
                "<<FEEDBACK>>Student flagged that the ser/estar cue was ambiguous.<<END_FEEDBACK>>\n"
                "Next question: how would you say 'I went to the wedding'?")
        cleaned, interp = _strip_feedback_marker(text)
        assert interp == "Student flagged that the ser/estar cue was ambiguous."
        assert "<<FEEDBACK>>" not in cleaned
        assert "<<END_FEEDBACK>>" not in cleaned
        assert "Got it, logged that. Sigamos." in cleaned
        assert "Next question" in cleaned

    def test_strip_marker_absent_returns_none(self):
        from engine.teach_drill import _strip_feedback_marker
        cleaned, interp = _strip_feedback_marker("regular response, no marker")
        assert interp is None
        assert cleaned == "regular response, no marker"

    def test_strip_marker_only_removes_first_block(self):
        """Guard against LLM accidentally emitting two — only honor the first."""
        from engine.teach_drill import _strip_feedback_marker
        text = ("<<FEEDBACK>>first<<END_FEEDBACK>> mid "
                "<<FEEDBACK>>second<<END_FEEDBACK>>")
        cleaned, interp = _strip_feedback_marker(text)
        assert interp == "first"
        assert "<<FEEDBACK>>second<<END_FEEDBACK>>" in cleaned

    def test_strip_marker_handles_multiline_interpretation(self):
        from engine.teach_drill import _strip_feedback_marker
        text = "<<FEEDBACK>>this is\na multiline\nparaphrase<<END_FEEDBACK>>"
        cleaned, interp = _strip_feedback_marker(text)
        assert interp == "this is\na multiline\nparaphrase"

    def test_marker_constants_are_the_expected_strings(self):
        from engine.teach_drill import FEEDBACK_MARKER_OPEN, FEEDBACK_MARKER_CLOSE
        assert FEEDBACK_MARKER_OPEN == '<<FEEDBACK>>'
        assert FEEDBACK_MARKER_CLOSE == '<<END_FEEDBACK>>'


class TestContinuationSuffixClassification:
    def test_suffix_documents_the_three_classifications(self):
        from engine.teach_drill import TEACH_DRILL_CONTINUATION_SUFFIX
        assert "Lesson answer" in TEACH_DRILL_CONTINUATION_SUFFIX
        assert "Content question" in TEACH_DRILL_CONTINUATION_SUFFIX
        assert "Meta-feedback" in TEACH_DRILL_CONTINUATION_SUFFIX

    def test_suffix_names_the_feedback_markers(self):
        from engine.teach_drill import TEACH_DRILL_CONTINUATION_SUFFIX
        assert "<<FEEDBACK>>" in TEACH_DRILL_CONTINUATION_SUFFIX
        assert "<<END_FEEDBACK>>" in TEACH_DRILL_CONTINUATION_SUFFIX

    def test_suffix_biases_ambiguous_cases_toward_content_question(self):
        """The spec calls out that ambiguity should default to content question,
        not feedback — avoids false-positive log entries."""
        from engine.teach_drill import TEACH_DRILL_CONTINUATION_SUFFIX
        assert "prefer content question" in TEACH_DRILL_CONTINUATION_SUFFIX.lower() \
            or "default to content question" in TEACH_DRILL_CONTINUATION_SUFFIX.lower()

    def test_suffix_handles_mixed_messages(self):
        """Mixed message (answer + feedback in one) must be extractable per the spec.
        Tightened from the plan's `assert 'both' in suffix` — the word 'both'
        already appears in the existing 'Never emit BOTH markers' rule, which
        would make the plan's assertion a false positive."""
        from engine.teach_drill import TEACH_DRILL_CONTINUATION_SUFFIX
        assert "both an answer" in TEACH_DRILL_CONTINUATION_SUFFIX.lower() \
            or "answer and feedback" in TEACH_DRILL_CONTINUATION_SUFFIX.lower()
