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
                         "progress_count": 0,
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

    def test_opening_prompt_provides_level_conditional_examples(self):
        """The observed failure ('Hoy vas a learn how...') stemmed from the
        prompt only having an English example — LLM pattern-matched to that.
        Now must provide separate examples per level so the LLM has a
        Spanish template for B1+."""
        from engine.teach_drill import TEACH_DRILL_OPENING_PROMPT
        # Both an English example (for A1/A2) AND a Spanish example (for B1+).
        assert "You'll learn" in TEACH_DRILL_OPENING_PROMPT
        assert "vas a poder" in TEACH_DRILL_OPENING_PROMPT

    def test_opening_prompt_forbids_mid_sentence_code_switching(self):
        """Explicit ban on the observed failure mode."""
        from engine.teach_drill import TEACH_DRILL_OPENING_PROMPT
        assert "code-switch" in TEACH_DRILL_OPENING_PROMPT.lower()
        # The specific failing example is called out by name.
        assert "vas a learn how" in TEACH_DRILL_OPENING_PROMPT.lower()

    def test_continuation_suffix_contains_paradigm_format_rule(self):
        from engine.teach_drill import TEACH_DRILL_CONTINUATION_SUFFIX
        # Must instruct one-line-per-person paradigm rendering.
        assert "one line" in TEACH_DRILL_CONTINUATION_SUFFIX.lower() or "per person" in TEACH_DRILL_CONTINUATION_SUFFIX.lower()

    def test_continuation_suffix_has_language_consistency_rule(self):
        """Language consistency rule must apply to all teach/retrieval turns,
        not just the opening. Same failure mode can happen in prose framing
        of any turn."""
        from engine.teach_drill import TEACH_DRILL_CONTINUATION_SUFFIX
        assert "LANGUAGE CONSISTENCY" in TEACH_DRILL_CONTINUATION_SUFFIX
        assert "code-switch" in TEACH_DRILL_CONTINUATION_SUFFIX.lower()


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
        """Hard cap on total LLM calls (TEACH_DRILL_MAX_TURNS) fires
        force_complete regardless of marker — emergency brake."""
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
    async def test_progress_cap_forces_completion(self, make_user, make_skill):
        """Pedagogical cap (TEACH_DRILL_MAX_PROGRESS_TURNS) fires force_complete
        based on actual lesson progress, independent of total LLM calls."""
        from engine.teach_drill import handle_teach_drill_turn, TEACH_DRILL_MAX_PROGRESS_TURNS
        from learner.models import Session

        user = await sync_to_async(make_user)(discord_id='td_progress_cap', cefr_level='B1')
        skill = await sync_to_async(make_skill)(skill_id='sk_progress_cap')
        session = await sync_to_async(Session.objects.create)(
            user=user, session_type='new_skill', target_skill=skill,
            current_phase='teach_drill',
            quiz_state={"teach_drill": {
                "units": [{"id": "a", "label": "a", "note": ""}],
                "taught": ["a"], "drills": {"a": ["yo"]},
                "turn_count": 5,  # well under hard cap
                "progress_count": TEACH_DRILL_MAX_PROGRESS_TURNS,  # at pedagogical cap
                "lesson_complete": False, "last_turn_type": "teach",
            }},
        )

        with patch('engine.teach_drill.call_llm',
                   new=AsyncMock(return_value="response")):
            await handle_teach_drill_turn(user, session, text="ok")

        await sync_to_async(session.refresh_from_db)()
        assert session.quiz_state["teach_drill"]["lesson_complete"] is True

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_deferred_turns_do_not_burn_progress_count(self, make_user, make_skill):
        """Deferred turns (REDO / QUESTION_ANSWERED) must NOT increment
        progress_count — a student's clarifying conversation shouldn't burn
        down the pedagogical cap. turn_count still advances for the hard cap."""
        from engine.teach_drill import handle_teach_drill_turn
        from learner.models import Session

        user = await sync_to_async(make_user)(discord_id='td_defer_count', cefr_level='B1')
        skill = await sync_to_async(make_skill)(skill_id='sk_defer_count')
        session = await sync_to_async(Session.objects.create)(
            user=user, session_type='new_skill', target_skill=skill,
            current_phase='teach_drill',
            quiz_state={"teach_drill": {
                "units": [{"id": "a", "label": "a", "note": ""},
                          {"id": "b", "label": "b", "note": ""}],
                "taught": ["a"], "drills": {"a": ["yo"]},
                "turn_count": 4, "progress_count": 3,
                "lesson_complete": False, "last_turn_type": "teach",
            }},
        )
        # LLM returns a content-question deferral.
        qa_response = "Answer... Volviendo: [drill]\n<<QUESTION_ANSWERED>>"
        with patch('engine.teach_drill.call_llm', new=AsyncMock(return_value=qa_response)):
            await handle_teach_drill_turn(user, session, text="quick question")

        await sync_to_async(session.refresh_from_db)()
        state = session.quiz_state["teach_drill"]
        # turn_count bumps (hard cap counts every LLM call).
        assert state["turn_count"] == 5
        # progress_count does NOT bump (deferred turn is free).
        assert state["progress_count"] == 3
        # lesson_complete still False — neither cap hit.
        assert state["lesson_complete"] is False


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


class TestClassifyFirstCheckClassification:
    """The classify-first structure lives in CLASSIFY_FIRST_CHECK (injected
    into every per-turn instruction). Previously in TEACH_DRILL_CONTINUATION_SUFFIX
    under a SECOND DECISION heading."""

    def test_check_documents_all_six_classifications(self):
        from engine.teach_drill import CLASSIFY_FIRST_CHECK
        assert "LESSON ANSWER" in CLASSIFY_FIRST_CHECK
        assert "CONTENT QUESTION" in CLASSIFY_FIRST_CHECK
        assert "META-FEEDBACK" in CLASSIFY_FIRST_CHECK
        assert "AMBIENT ACKNOWLEDGMENT" in CLASSIFY_FIRST_CHECK
        assert "SESSION CONTROL" in CLASSIFY_FIRST_CHECK
        assert "COMBINATIONS" in CLASSIFY_FIRST_CHECK

    def test_suffix_still_names_the_feedback_markers(self):
        from engine.teach_drill import TEACH_DRILL_CONTINUATION_SUFFIX
        assert "<<FEEDBACK>>" in TEACH_DRILL_CONTINUATION_SUFFIX
        assert "<<END_FEEDBACK>>" in TEACH_DRILL_CONTINUATION_SUFFIX

    def test_check_biases_ambiguous_cases_toward_content_question(self):
        """Ambiguity between LESSON ANSWER and CONTENT QUESTION should default
        to content question (safer to defer than falsely mark ✗)."""
        from engine.teach_drill import CLASSIFY_FIRST_CHECK
        lower = CLASSIFY_FIRST_CHECK.lower()
        assert "prefer content question" in lower

    def test_check_handles_combinations(self):
        """Combined messages (e.g., answer + feedback) must be extractable."""
        from engine.teach_drill import CLASSIFY_FIRST_CHECK
        assert "COMBINATIONS" in CLASSIFY_FIRST_CHECK
        # Orthogonality of FEEDBACK vs state markers is spelled out.
        assert "orthogonal" in CLASSIFY_FIRST_CHECK.lower()

    def test_correctness_evaluation_only_runs_after_lesson_answer_classification(self):
        """Architectural check: REDO evaluation must be gated on classification,
        not the first thing the LLM does. Previously REDO was FIRST DECISION,
        which caused 'ok' acknowledgments to be marked ✗."""
        from engine.teach_drill import CLASSIFY_FIRST_CHECK
        # The word 'CORRECTNESS' or 'EVALUATION' appears BELOW the classification.
        assert "CORRECTNESS EVALUATION" in CLASSIFY_FIRST_CHECK
        # And is explicitly gated on lesson-answer classification.
        assert "only if you classified as LESSON ANSWER" in CLASSIFY_FIRST_CHECK


class TestParadigmAndGlossingRules:
    def test_suffix_carries_cefr_level_placeholder(self):
        """Must have a {cefr_level} slot so runtime can inject the student's level
        into the glossing decision."""
        from engine.teach_drill import TEACH_DRILL_CONTINUATION_SUFFIX
        assert "{cefr_level}" in TEACH_DRILL_CONTINUATION_SUFFIX

    def test_suffix_formats_with_cefr_level(self):
        """Formatting with both required kwargs must succeed and produce the
        student's level in the resulting text."""
        from engine.teach_drill import TEACH_DRILL_CONTINUATION_SUFFIX
        formatted = TEACH_DRILL_CONTINUATION_SUFFIX.format(
            skill_name='Preterite irregulars', cefr_level='B1',
        )
        # Level appears in the formatted output.
        assert 'B1' in formatted

    def test_suffix_documents_contrast_paradigm_format(self):
        """Format A (known → target) must be documented for tense-conjugation
        skills so the LLM produces contrast rows instead of bare paradigms."""
        from engine.teach_drill import TEACH_DRILL_CONTINUATION_SUFFIX
        # Rule name.
        assert "CONTRAST" in TEACH_DRILL_CONTINUATION_SUFFIX
        # Concrete example.
        assert "puedo" in TEACH_DRILL_CONTINUATION_SUFFIX and "pude" in TEACH_DRILL_CONTINUATION_SUFFIX
        # Arrow syntax spelled out.
        assert "→" in TEACH_DRILL_CONTINUATION_SUFFIX

    def test_suffix_documents_level_conditional_glossing(self):
        """Glossing rules must differentiate at least three level tiers so the
        LLM adjusts glossing density to the student's level."""
        from engine.teach_drill import TEACH_DRILL_CONTINUATION_SUFFIX
        lower = TEACH_DRILL_CONTINUATION_SUFFIX.lower()
        # Reference to level-conditional glossing.
        assert "glossing" in lower
        # A1/A2, B1, and B2+ each named with distinct guidance.
        assert "a1" in lower or "a2" in lower
        assert "b1" in lower
        assert "b2" in lower

    def test_contrast_paradigm_is_mandatory_not_preferred(self):
        """Loose 'preferred' language let the LLM skip CONTRAST intermittently.
        Must be phrased as MANDATORY / MUST / FORBIDDEN to match the REDO-block
        pattern that reliably enforces LLM compliance."""
        from engine.teach_drill import TEACH_DRILL_CONTINUATION_SUFFIX
        # Hard-requirement language, not soft preference.
        assert "MANDATORY" in TEACH_DRILL_CONTINUATION_SUFFIX
        assert "MUST" in TEACH_DRILL_CONTINUATION_SUFFIX
        assert "FORBIDDEN" in TEACH_DRILL_CONTINUATION_SUFFIX

    def test_contrast_paradigm_names_imperfect_specifically(self):
        """The observed failure was imperfect skills (comer, vivir) — imperfect
        must appear in the mandatory list AND in the concrete examples so the
        LLM pattern-matches to that specific tense."""
        from engine.teach_drill import TEACH_DRILL_CONTINUATION_SUFFIX
        # Named in the required-skill-types list.
        assert "Imperfect" in TEACH_DRILL_CONTINUATION_SUFFIX
        # Concrete example uses the exact verb that misfired.
        assert "comía" in TEACH_DRILL_CONTINUATION_SUFFIX
        assert "vivía" in TEACH_DRILL_CONTINUATION_SUFFIX

    def test_forbidden_block_names_the_failure_mode(self):
        """The forbidden section must specifically call out the observed
        failure mode (bare imperfect rows without the known-side bridge)."""
        from engine.teach_drill import TEACH_DRILL_CONTINUATION_SUFFIX
        lower = TEACH_DRILL_CONTINUATION_SUFFIX.lower()
        # Named the anti-pattern.
        assert "forbidden" in lower
        # Called out the inconsistency case (contrast on one verb, bare on another).
        assert "inconsistent" in lower or "uniformly" in lower


class TestFeedbackCapture:
    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_feedback_marker_in_response_creates_row(self, make_user, make_skill):
        """When the LLM response contains the feedback markers, a SessionFeedback
        row is created with the paraphrase, the raw user message, and the FK to
        the most-recent prior SessionEvent."""
        from unittest.mock import patch, AsyncMock
        from engine.teach_drill import handle_teach_drill_turn
        from learner.models import Session, SessionEvent, SessionFeedback

        user = await sync_to_async(make_user)(discord_id='fb1', cefr_level='B1')
        skill = await sync_to_async(make_skill)(skill_id='sk_fb1')
        session = await sync_to_async(Session.objects.create)(
            user=user, session_type='new_skill', target_skill=skill,
            current_phase='teach_drill',
            quiz_state={"teach_drill": {
                "units": [{"id": "ser_ir", "label": "ser/ir", "note": ""}],
                "taught": [], "drills": {}, "turn_count": 0,
                "lesson_complete": False, "last_turn_type": None,
            }},
        )
        prior_event = await sync_to_async(SessionEvent.objects.create)(
            session=session, event_type='conversation',
            content='Luz asked something', user_response='',
        )

        llm_response = (
            "Got it, logged that. Sigamos.\n"
            "<<FEEDBACK>>Student thinks the previous cue was ambiguous.<<END_FEEDBACK>>\n"
            "[normal teach content here...]"
        )
        with patch('engine.teach_drill.call_llm', new=AsyncMock(return_value=llm_response)):
            result = await handle_teach_drill_turn(
                user, session, text="that cue was ambiguous, honestly",
            )

        assert "<<FEEDBACK>>" not in result["text"]
        assert "<<END_FEEDBACK>>" not in result["text"]

        fb_qs = await sync_to_async(list)(SessionFeedback.objects.filter(session=session))
        assert len(fb_qs) == 1
        fb = fb_qs[0]
        assert fb.user_message == "that cue was ambiguous, honestly"
        assert fb.interpretation == "Student thinks the previous cue was ambiguous."
        assert fb.anchor_event_id == prior_event.pk
        assert fb.resolved is False

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_feedback_prepends_canonical_acknowledgment(self, make_user, make_skill):
        """When FEEDBACK marker fires, code prepends the exact canonical
        acknowledgment string — deterministic across every interaction."""
        from unittest.mock import patch, AsyncMock
        from engine.teach_drill import handle_teach_drill_turn, FEEDBACK_ACKNOWLEDGMENT
        from learner.models import Session

        user = await sync_to_async(make_user)(discord_id='fb_canon', cefr_level='B1')
        skill = await sync_to_async(make_skill)(skill_id='sk_fb_canon')
        session = await sync_to_async(Session.objects.create)(
            user=user, session_type='new_skill', target_skill=skill,
            current_phase='teach_drill',
            quiz_state={"teach_drill": {
                "units": [{"id": "a", "label": "a", "note": ""}],
                "taught": [], "drills": {}, "turn_count": 0,
                "lesson_complete": False, "last_turn_type": None,
            }},
        )
        # LLM emits marker + lesson content, NO acknowledgment (per prompt).
        llm_response = (
            "<<FEEDBACK>>Student flagged X.<<END_FEEDBACK>>\n"
            "Ahora, teaching content for unit a..."
        )
        with patch('engine.teach_drill.call_llm', new=AsyncMock(return_value=llm_response)):
            result = await handle_teach_drill_turn(user, session, text="feedback text")

        # The exact canonical string is prepended at the start of the response.
        assert result["text"].startswith(FEEDBACK_ACKNOWLEDGMENT)
        # The lesson content follows.
        assert "Ahora, teaching content" in result["text"]

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_no_feedback_no_prepended_acknowledgment(self, make_user, make_skill):
        """When no FEEDBACK marker, the acknowledgment must NOT appear."""
        from unittest.mock import patch, AsyncMock
        from engine.teach_drill import handle_teach_drill_turn, FEEDBACK_ACKNOWLEDGMENT
        from learner.models import Session

        user = await sync_to_async(make_user)(discord_id='fb_no_canon', cefr_level='B1')
        skill = await sync_to_async(make_skill)(skill_id='sk_fb_no_canon')
        session = await sync_to_async(Session.objects.create)(
            user=user, session_type='new_skill', target_skill=skill,
            current_phase='teach_drill',
            quiz_state={"teach_drill": {
                "units": [{"id": "a", "label": "a", "note": ""}],
                "taught": [], "drills": {}, "turn_count": 0,
                "lesson_complete": False, "last_turn_type": None,
            }},
        )
        with patch('engine.teach_drill.call_llm', new=AsyncMock(return_value="just a teach turn")):
            result = await handle_teach_drill_turn(user, session, text="tuve un día bueno")

        assert FEEDBACK_ACKNOWLEDGMENT not in result["text"]

    def test_canonical_acknowledgment_is_exact_string(self):
        """Locks in the exact wording so it can't be changed accidentally."""
        from engine.teach_drill import FEEDBACK_ACKNOWLEDGMENT
        assert FEEDBACK_ACKNOWLEDGMENT == "Got it, thanks for the feedback. Sigamos."

    def test_prompt_instructs_llm_not_to_write_own_acknowledgment(self):
        """The prompt must tell the LLM to skip writing an ack — otherwise
        we'd get duplicates (LLM ack + code-prepended canonical ack)."""
        from engine.teach_drill import CLASSIFY_FIRST_CHECK
        assert "Do NOT write any acknowledgment" in CLASSIFY_FIRST_CHECK

    def test_prompt_forbids_change_promises(self):
        """The LLM can't actually change its prompt, so promising behavior
        change ('I'll drop the hybrid phrasing going forward') was misleading."""
        from engine.teach_drill import CLASSIFY_FIRST_CHECK
        assert "Do NOT promise to change your behavior" in CLASSIFY_FIRST_CHECK

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_no_marker_creates_no_row(self, make_user, make_skill):
        from unittest.mock import patch, AsyncMock
        from engine.teach_drill import handle_teach_drill_turn
        from learner.models import Session, SessionFeedback

        user = await sync_to_async(make_user)(discord_id='fb2', cefr_level='B1')
        skill = await sync_to_async(make_skill)(skill_id='sk_fb2')
        session = await sync_to_async(Session.objects.create)(
            user=user, session_type='new_skill', target_skill=skill,
            current_phase='teach_drill',
            quiz_state={"teach_drill": {
                "units": [{"id": "a", "label": "a", "note": ""}],
                "taught": [], "drills": {}, "turn_count": 0,
                "lesson_complete": False, "last_turn_type": None,
            }},
        )

        with patch('engine.teach_drill.call_llm', new=AsyncMock(return_value="normal teach response")):
            await handle_teach_drill_turn(user, session, text="fui al gym")

        assert await sync_to_async(SessionFeedback.objects.filter(session=session).count)() == 0

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_feedback_coexists_with_redo_pending(self, make_user, make_skill):
        """A response containing BOTH markers must log feedback AND still gate
        state advancement per REDO_PENDING semantics."""
        from unittest.mock import patch, AsyncMock
        from engine.teach_drill import handle_teach_drill_turn
        from learner.models import Session, SessionFeedback

        user = await sync_to_async(make_user)(discord_id='fb3', cefr_level='B1')
        skill = await sync_to_async(make_skill)(skill_id='sk_fb3')
        session = await sync_to_async(Session.objects.create)(
            user=user, session_type='new_skill', target_skill=skill,
            current_phase='teach_drill',
            quiz_state={"teach_drill": {
                "units": [{"id": "a", "label": "a", "note": ""},
                          {"id": "b", "label": "b", "note": ""}],
                "taught": ["a"], "drills": {"a": ["yo"]},
                "turn_count": 1, "lesson_complete": False,
                "last_turn_type": "teach",
            }},
        )
        llm_response = (
            "✗ correction line.\nTry again: [rephrased Q]\n"
            "<<FEEDBACK>>Student says the drills are too fast.<<END_FEEDBACK>>\n"
            "<<REDO_PENDING>>"
        )
        with patch('engine.teach_drill.call_llm', new=AsyncMock(return_value=llm_response)):
            await handle_teach_drill_turn(user, session, text="wrong answer + this is too fast")

        fb_count = await sync_to_async(SessionFeedback.objects.filter(session=session).count)()
        assert fb_count == 1

        session = await sync_to_async(
            lambda: Session.objects.select_related('target_skill').get(pk=session.pk)
        )()
        state = session.quiz_state["teach_drill"]
        assert "b" not in state["taught"]

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_feedback_stored_content_does_not_contain_marker(self, make_user, make_skill):
        """The SessionEvent that persists the LLM response for later turns must
        have the marker block stripped so it doesn't leak into future prompts."""
        from unittest.mock import patch, AsyncMock
        from engine.teach_drill import handle_teach_drill_turn
        from learner.models import Session, SessionEvent

        user = await sync_to_async(make_user)(discord_id='fb4', cefr_level='B1')
        skill = await sync_to_async(make_skill)(skill_id='sk_fb4')
        session = await sync_to_async(Session.objects.create)(
            user=user, session_type='new_skill', target_skill=skill,
            current_phase='teach_drill',
            quiz_state={"teach_drill": {
                "units": [{"id": "a", "label": "a", "note": ""}],
                "taught": [], "drills": {}, "turn_count": 0,
                "lesson_complete": False, "last_turn_type": None,
            }},
        )
        llm_response = "prefix\n<<FEEDBACK>>paraphrase<<END_FEEDBACK>>\nsuffix"
        with patch('engine.teach_drill.call_llm', new=AsyncMock(return_value=llm_response)):
            await handle_teach_drill_turn(user, session, text="msg")

        latest_event = await sync_to_async(
            lambda: SessionEvent.objects.filter(session=session).order_by('-timestamp').first()
        )()
        assert "<<FEEDBACK>>" not in latest_event.content
        assert "<<END_FEEDBACK>>" not in latest_event.content
        assert "paraphrase" not in latest_event.content


# ── QUESTION_ANSWERED marker: content-question deferral ──────────────────────

class TestQuestionAnsweredMarker:
    def test_strip_marker_present(self):
        from engine.teach_drill import _strip_question_answered_marker
        cleaned, present = _strip_question_answered_marker(
            "Answering: estar is for locations. Volviendo: how would you say 'she went'?\n<<QUESTION_ANSWERED>>"
        )
        assert present is True
        assert "<<QUESTION_ANSWERED>>" not in cleaned
        assert "Volviendo" in cleaned

    def test_strip_marker_absent(self):
        from engine.teach_drill import _strip_question_answered_marker
        cleaned, present = _strip_question_answered_marker("regular teach response")
        assert present is False
        assert cleaned == "regular teach response"

    def test_classification_check_documents_the_marker(self):
        """The classify-first check (injected into every per-turn instruction)
        must instruct the LLM about when to emit the marker. After the
        restructure, this lives in CLASSIFY_FIRST_CHECK rather than the suffix."""
        from engine.teach_drill import CLASSIFY_FIRST_CHECK, TEACH_DRILL_CONTINUATION_SUFFIX
        assert "<<QUESTION_ANSWERED>>" in CLASSIFY_FIRST_CHECK
        # The suffix still mentions the marker in the MARKER RULES summary.
        assert "<<QUESTION_ANSWERED>>" in TEACH_DRILL_CONTINUATION_SUFFIX
        # Re-ask requirement in Spanish.
        assert "Volviendo" in CLASSIFY_FIRST_CHECK
        # Forbid teaching new content on this turn.
        assert "DEFERRED" in CLASSIFY_FIRST_CHECK or "do NOT teach a new unit" in CLASSIFY_FIRST_CHECK


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_question_answered_defers_state_advancement(make_user, make_skill):
    """When the LLM emits <<QUESTION_ANSWERED>>, code must NOT mark the
    planned unit as taught or drilled — the same drill will re-fire next turn
    and get its actual answer."""
    from unittest.mock import patch, AsyncMock
    from engine.teach_drill import handle_teach_drill_turn
    from learner.models import Session

    user = await sync_to_async(make_user)(discord_id='qa_defer1', cefr_level='B1')
    skill = await sync_to_async(make_skill)(skill_id='sk_qa_defer1')
    session = await sync_to_async(Session.objects.create)(
        user=user, session_type='new_skill', target_skill=skill,
        current_phase='teach_drill',
        quiz_state={"teach_drill": {
            "units": [{"id": "tener", "label": "tener", "note": "tuv-"},
                      {"id": "hacer", "label": "hacer", "note": "hic-"}],
            "taught": ["tener"], "drills": {"tener": ["yo"]},
            "turn_count": 1, "lesson_complete": False,
            "last_turn_type": "teach",
        }},
    )
    # LLM classifies user's message as a content question and defers.
    qa_response = (
        "In preterite, 'poder' means 'managed to' more than 'was able to'.\n"
        "Volviendo a lo que te preguntaba: how would you say 'she had'?\n"
        "<<QUESTION_ANSWERED>>"
    )
    with patch('engine.teach_drill.call_llm', new=AsyncMock(return_value=qa_response)):
        result = await handle_teach_drill_turn(
            user, session, text="wait, does poder always mean managed-to?",
        )

    # Marker stripped from visible text.
    assert "<<QUESTION_ANSWERED>>" not in result["text"]
    assert "Volviendo" in result["text"]

    session = await sync_to_async(
        lambda: Session.objects.select_related('target_skill').get(pk=session.pk)
    )()
    state = session.quiz_state["teach_drill"]
    # State must NOT have advanced — hacer stays un-taught, tener stays at 1 drill.
    assert "hacer" not in state["taught"]
    assert state["drills"] == {"tener": ["yo"]}
    # last_turn_type unchanged (was "teach"), so next turn's alternation logic
    # will still trigger retrieval, giving the pending drill another chance.
    assert state["last_turn_type"] == "teach"
    # turn_count advances so safety cap still applies.
    assert state["turn_count"] == 2
    # Not marked complete.
    assert state["lesson_complete"] is False


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_question_answered_and_feedback_coexist(make_user, make_skill):
    """A single response may carry both <<QUESTION_ANSWERED>> AND
    <<FEEDBACK>>...<<END_FEEDBACK>> — feedback logs independently while
    state deferral fires normally."""
    from unittest.mock import patch, AsyncMock
    from engine.teach_drill import handle_teach_drill_turn
    from learner.models import Session, SessionFeedback

    user = await sync_to_async(make_user)(discord_id='qa_fb1', cefr_level='B1')
    skill = await sync_to_async(make_skill)(skill_id='sk_qa_fb1')
    session = await sync_to_async(Session.objects.create)(
        user=user, session_type='new_skill', target_skill=skill,
        current_phase='teach_drill',
        quiz_state={"teach_drill": {
            "units": [{"id": "a", "label": "a", "note": ""}],
            "taught": [], "drills": {}, "turn_count": 0,
            "lesson_complete": False, "last_turn_type": None,
        }},
    )
    combined = (
        "Answering: estar is used for locations.\n"
        "Volviendo: how would you say 'I was at the wedding'?\n"
        "<<FEEDBACK>>Student flagged the earlier cue as ambiguous.<<END_FEEDBACK>>\n"
        "<<QUESTION_ANSWERED>>"
    )
    with patch('engine.teach_drill.call_llm', new=AsyncMock(return_value=combined)):
        await handle_teach_drill_turn(user, session, text="wait, is estar for location?")

    # Feedback row was created.
    fb_count = await sync_to_async(SessionFeedback.objects.filter(session=session).count)()
    assert fb_count == 1

    # State deferred (unit a NOT marked taught despite this being a teach turn).
    session = await sync_to_async(
        lambda: Session.objects.select_related('target_skill').get(pk=session.pk)
    )()
    state = session.quiz_state["teach_drill"]
    assert "a" not in state["taught"]


# ── AMBIENT ACK: reuses QUESTION_ANSWERED marker; must not fire REDO ─────────

class TestAmbientAckBranch:
    def test_check_documents_ambient_ack_branch(self):
        """CLASSIFY_FIRST_CHECK branch (d) must exist with example ack tokens."""
        from engine.teach_drill import CLASSIFY_FIRST_CHECK
        assert "AMBIENT ACKNOWLEDGMENT" in CLASSIFY_FIRST_CHECK
        # Named example tokens.
        lower = CLASSIFY_FIRST_CHECK.lower()
        assert "ok" in lower
        # Must forbid REDO for ambient acks (the whole point of this branch).
        assert "do NOT fire REDO" in CLASSIFY_FIRST_CHECK or "not a wrong answer" in CLASSIFY_FIRST_CHECK.lower()

    def test_check_says_ambient_ack_uses_question_answered_marker(self):
        """Ambient ack reuses the QUESTION_ANSWERED marker (not a new one) —
        code path is the same as content question: defer state, re-serve drill."""
        from engine.teach_drill import CLASSIFY_FIRST_CHECK
        # The ambient-ack branch must reference the QUESTION_ANSWERED marker.
        ack_section_start = CLASSIFY_FIRST_CHECK.find("AMBIENT ACKNOWLEDGMENT")
        ack_section_end = CLASSIFY_FIRST_CHECK.find("SESSION CONTROL")
        assert ack_section_start >= 0 and ack_section_end > ack_section_start
        ack_section = CLASSIFY_FIRST_CHECK[ack_section_start:ack_section_end]
        assert "<<QUESTION_ANSWERED>>" in ack_section


# ── END_LESSON_EARLY: session control marker ─────────────────────────────────

class TestEndLessonEarlyMarker:
    def test_strip_marker_present(self):
        from engine.teach_drill import _strip_end_lesson_early_marker
        cleaned, present = _strip_end_lesson_early_marker(
            "Ok, cerramos por hoy. ¡Nos vemos!\n<<END_LESSON_EARLY>>"
        )
        assert present is True
        assert "<<END_LESSON_EARLY>>" not in cleaned
        assert "cerramos" in cleaned

    def test_strip_marker_absent(self):
        from engine.teach_drill import _strip_end_lesson_early_marker
        cleaned, present = _strip_end_lesson_early_marker("normal response")
        assert present is False
        assert cleaned == "normal response"

    def test_check_documents_session_control_branch(self):
        """CLASSIFY_FIRST_CHECK branch (e) must exist with example utterances
        and require emitting <<END_LESSON_EARLY>>."""
        from engine.teach_drill import CLASSIFY_FIRST_CHECK
        assert "SESSION CONTROL" in CLASSIFY_FIRST_CHECK
        assert "<<END_LESSON_EARLY>>" in CLASSIFY_FIRST_CHECK
        # Example utterances from the spec.
        assert "let's move on" in CLASSIFY_FIRST_CHECK or "skip this" in CLASSIFY_FIRST_CHECK

    def test_suffix_names_end_lesson_early_marker(self):
        """The suffix's marker summary must list END_LESSON_EARLY too."""
        from engine.teach_drill import TEACH_DRILL_CONTINUATION_SUFFIX
        assert "<<END_LESSON_EARLY>>" in TEACH_DRILL_CONTINUATION_SUFFIX


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_end_lesson_early_signals_caller_and_closes_session(make_user, make_skill):
    """When the LLM emits <<END_LESSON_EARLY>>, handle_teach_drill_turn:
    - marks lesson_complete=True (immediately, not by wrap-up)
    - returns end_lesson_early=True so _continue_new_skill closes the session"""
    from unittest.mock import patch, AsyncMock
    from engine.teach_drill import handle_teach_drill_turn
    from learner.models import Session

    user = await sync_to_async(make_user)(discord_id='td_end_early', cefr_level='B1')
    skill = await sync_to_async(make_skill)(skill_id='sk_td_end_early')
    session = await sync_to_async(Session.objects.create)(
        user=user, session_type='new_skill', target_skill=skill,
        current_phase='teach_drill',
        quiz_state={"teach_drill": {
            "units": [{"id": "a", "label": "a", "note": ""},
                      {"id": "b", "label": "b", "note": ""}],
            "taught": ["a"], "drills": {"a": ["yo"]},
            "turn_count": 3, "progress_count": 2,
            "lesson_complete": False, "last_turn_type": "teach",
        }},
    )
    end_response = "Ok, cerramos por hoy. ¡Nos vemos!\n<<END_LESSON_EARLY>>"
    with patch('engine.teach_drill.call_llm', new=AsyncMock(return_value=end_response)):
        result = await handle_teach_drill_turn(user, session, text="let's move on")

    # Marker stripped from visible text.
    assert "<<END_LESSON_EARLY>>" not in result["text"]
    # Signal fires so caller closes the session.
    assert result["end_lesson_early"] is True

    session = await sync_to_async(
        lambda: Session.objects.select_related('target_skill').get(pk=session.pk)
    )()
    state = session.quiz_state["teach_drill"]
    # Marked complete immediately (not waiting for wrap-up).
    assert state["lesson_complete"] is True
    # progress_count did NOT advance (end-early isn't lesson progress).
    assert state["progress_count"] == 2


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_continue_new_skill_closes_session_on_end_lesson_early(make_user, make_skill):
    """_continue_new_skill must call _close_session_record when
    handle_teach_drill_turn signals end_lesson_early — session gets ended_at
    set and scoring runs on the transcript."""
    from unittest.mock import patch, AsyncMock
    from engine.session import _continue_new_skill
    from learner.models import Session

    user = await sync_to_async(make_user)(discord_id='cont_end_early', cefr_level='B1')
    skill = await sync_to_async(make_skill)(skill_id='sk_cont_end_early')
    session = await sync_to_async(Session.objects.create)(
        user=user, session_type='new_skill', target_skill=skill,
        current_phase='teach_drill',
        quiz_state={"teach_drill": {
            "units": [{"id": "a", "label": "a", "note": ""}],
            "taught": ["a"], "drills": {"a": ["yo"]},
            "turn_count": 2, "progress_count": 1,
            "lesson_complete": False, "last_turn_type": "teach",
        }},
    )
    end_response = "Ok, cerramos por hoy.\n<<END_LESSON_EARLY>>"
    with patch('engine.teach_drill.call_llm', new=AsyncMock(return_value=end_response)):
        with patch('engine.interests.extract_and_store_interests', new=AsyncMock()):
            with patch('engine.scoring.score_session', new=AsyncMock()):
                result = await _continue_new_skill(user, session, "let's do something else")

    assert result["session_ended"] is True
    await sync_to_async(session.refresh_from_db)()
    assert session.ended_at is not None


class TestDegenerateContrastRows:
    """A CONTRAST row whose known side equals its target side teaches nothing and
    is always wrong. This is the `Yo fui → Yo fui` failure that shipped to the
    student on 2026-08-19 (skill b1_preterite_vs_imperfect)."""

    def test_detects_the_row_that_shipped(self):
        from engine.teach_drill import find_degenerate_contrast_rows

        paradigm = (
            "Yo fui → Yo fui (I go → I went)\n"
            "Tú vas → Tú fuiste\n"
            "Él va → Él fue\n"
            "Nosotros vamos → Nosotros fuimos\n"
            "Ellos van → Ellos fueron"
        )
        assert find_degenerate_contrast_rows(paradigm) == ["Yo fui → Yo fui (I go → I went)"]

    def test_correct_paradigm_is_clean(self):
        from engine.teach_drill import find_degenerate_contrast_rows

        paradigm = (
            "Yo voy → Yo fui (I go → I went)\n"
            "Tú vas → Tú fuiste\n"
            "Él va → Él fue"
        )
        assert find_degenerate_contrast_rows(paradigm) == []

    def test_gloss_arrow_alone_does_not_trigger(self):
        """The English gloss carries its own arrow. Only the Spanish sides count."""
        from engine.teach_drill import find_degenerate_contrast_rows

        assert find_degenerate_contrast_rows("Yo como → Yo comía (I eat → I eat)") == []

    def test_prose_with_an_arrow_is_not_a_paradigm_row(self):
        """Only rows opening with a person pronoun are CONTRAST rows."""
        from engine.teach_drill import find_degenerate_contrast_rows

        assert find_degenerate_contrast_rows("presente → pretérito") == []

    def test_ignores_case_and_surrounding_whitespace(self):
        from engine.teach_drill import find_degenerate_contrast_rows

        assert find_degenerate_contrast_rows("  Yo Fui  →  yo fui  ") != []


class TestDegenerateParadigmRegeneration:
    """A degenerate paradigm must never reach the student. Four prompt-level fixes
    failed to prevent it, so the turn regenerates deterministically instead."""

    BAD = ("Aquí va:\n"
           "Yo fui → Yo fui (I go → I went)\n"
           "Tú vas → Tú fuiste\n"
           "Él va → Él fue\n"
           "¿Cómo dices 'I went'?")
    GOOD = ("Aquí va:\n"
            "Yo voy → Yo fui (I go → I went)\n"
            "Tú vas → Tú fuiste\n"
            "Él va → Él fue\n"
            "¿Cómo dices 'I went'?")

    async def _run(self, make_user, make_skill, responses, uid):
        from engine.teach_drill import handle_teach_drill_turn
        from learner.models import Session

        user = await sync_to_async(make_user)(discord_id=uid, cefr_level='B1')
        skill = await sync_to_async(make_skill)(skill_id='sk_' + uid)
        session = await sync_to_async(Session.objects.create)(
            user=user, session_type='new_skill', target_skill=skill,
            current_phase='teach_drill',
            quiz_state={"teach_drill": {
                "units": [{"id": "ir", "label": "ir", "note": "fui/fuiste/fue"}],
                "taught": [], "drills": {}, "turn_count": 0, "lesson_complete": False,
            }},
        )
        mock = AsyncMock(side_effect=responses)
        with patch('engine.teach_drill.call_llm', new=mock):
            result = await handle_teach_drill_turn(user, session, text="listo")
        return result, mock

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_degenerate_paradigm_is_regenerated(self, make_user, make_skill):
        result, mock = await self._run(make_user, make_skill, [self.BAD, self.GOOD], 'td_deg1')

        assert "Yo fui → Yo fui" not in result["text"]
        assert "Yo voy → Yo fui" in result["text"]
        assert mock.await_count == 2

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_clean_paradigm_is_not_regenerated(self, make_user, make_skill):
        """No wasted API call when the first response is already correct."""
        result, mock = await self._run(make_user, make_skill, [self.GOOD, self.BAD], 'td_deg2')

        assert result["text"] == self.GOOD
        assert mock.await_count == 1

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_regeneration_is_attempted_only_once(self, make_user, make_skill):
        """Two bad responses must not loop; the turn still returns something."""
        result, mock = await self._run(make_user, make_skill, [self.BAD, self.BAD], 'td_deg3')

        assert mock.await_count == 2
        assert result["text"].strip()


class TestParadigmDemandMatchesUnit:
    """The root cause of the `Yo fui → Yo fui` bug: the instruction ordered a full
    paradigm from a unit that contained no verb, so the model invented both the
    verb and the known side."""

    def test_usage_unit_is_not_asked_for_a_paradigm(self):
        from engine.teach_drill import build_teach_instruction
        unit = {"id": "completed", "label": "Preterite — completed events",
                "note": "trigger words: ayer, una vez", "kind": "usage"}

        instr = build_teach_instruction(unit, person_new="yo", is_final=False)

        assert "full paradigm" not in instr.lower()
        assert "Preterite — completed events" in instr

    def test_paradigm_unit_is_given_the_known_side_explicitly(self):
        """A mandatory-CONTRAST unit must carry its known forms so the model
        never has to derive them."""
        from engine.teach_drill import build_teach_instruction
        unit = {"id": "ir", "label": "ir", "note": "fui/fuiste/fue", "kind": "paradigm",
                "verb": "ir", "known_tense": "presente",
                "known_forms": {"yo": "voy", "tú": "vas", "él": "va"}}

        instr = build_teach_instruction(unit, person_new="yo", is_final=False)

        assert "presente" in instr
        assert "voy" in instr

    def test_legacy_unit_without_kind_still_gets_a_paradigm(self):
        """Sessions already in flight have units with no 'kind' key. They must keep
        the old behaviour rather than silently losing their paradigm."""
        from engine.teach_drill import build_teach_instruction
        unit = {"id": "tener", "label": "tener", "note": "stem tuv-"}

        instr = build_teach_instruction(unit, person_new="yo", is_final=False)

        assert "full paradigm" in instr.lower()


class TestUnitExtractionCarriesTheKnownSide:
    def test_extraction_prompt_requires_a_kind_field(self):
        from engine.teach_drill import UNIT_EXTRACTION_PROMPT
        assert '"kind"' in UNIT_EXTRACTION_PROMPT

    def test_extraction_prompt_requires_known_forms_for_contrast_skills(self):
        """The unit must carry the known side so the model never derives it."""
        from engine.teach_drill import UNIT_EXTRACTION_PROMPT
        assert '"known_forms"' in UNIT_EXTRACTION_PROMPT
        assert '"known_tense"' in UNIT_EXTRACTION_PROMPT

    @pytest.mark.asyncio
    async def test_extract_units_preserves_the_new_fields(self):
        from engine.teach_drill import extract_units
        fake = json.dumps([{
            "id": "ir", "label": "ir", "note": "", "kind": "paradigm",
            "verb": "ir", "known_tense": "presente",
            "known_forms": {"yo": "voy", "tú": "vas"},
        }])
        with patch('engine.teach_drill.call_llm', new=AsyncMock(return_value=fake)):
            units = await extract_units("Preterite", "ir", "B1")

        assert units[0]["kind"] == "paradigm"
        assert units[0]["known_forms"]["yo"] == "voy"


class TestContrastSpecCoversTwoPastTenseSkills:
    def test_spec_says_what_known_means_when_both_sides_are_past(self):
        """`preterite vs imperfect` contrasts two PAST tenses, so 'the tense the
        student already knows' was undefined — the gap the model filled by
        collapsing the yo row."""
        from engine.teach_drill import TEACH_DRILL_CONTINUATION_SUFFIX
        spec = TEACH_DRILL_CONTINUATION_SUFFIX.lower()
        assert "preterite vs" in spec or "preterite-vs" in spec

    def test_spec_forbids_an_identical_known_and_target_side(self):
        from engine.teach_drill import TEACH_DRILL_CONTINUATION_SUFFIX
        assert "identical" in TEACH_DRILL_CONTINUATION_SUFFIX.lower()
