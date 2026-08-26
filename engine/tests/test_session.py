"""
Tests for engine/session.py

Covers: _srs_position, _srs_build_schedule, phase flow helpers,
and _select_session decision tree.
"""
import pytest
from unittest.mock import patch, AsyncMock
from datetime import timedelta
from asgiref.sync import sync_to_async
from django.utils import timezone


# ── Area 7: _srs_position / _srs_build_schedule ──────────────────────────────

class TestSrsPosition:
    def _make_schedule(self, counts):
        """Build a fake schedule: list of (fake_skill, question_count)."""
        class FakeSkill:
            def __init__(self, sid):
                self.skill_id = sid
        return [(FakeSkill(f'skill_{i}'), c) for i, c in enumerate(counts)]

    def test_first_question_first_skill(self):
        """turns=0 → first question of first skill."""
        from engine.session import _srs_position
        schedule = self._make_schedule([2, 3])
        skill, q_num, total = _srs_position(0, schedule)
        assert skill.skill_id == 'skill_0'
        assert q_num == 1
        assert total == 2

    def test_advances_to_second_skill(self):
        """turns=2 with [2, 3] schedule → first question of second skill."""
        from engine.session import _srs_position
        schedule = self._make_schedule([2, 3])
        skill, q_num, total = _srs_position(2, schedule)
        assert skill.skill_id == 'skill_1'
        assert q_num == 1

    def test_past_end_returns_none(self):
        """turns beyond total questions → None."""
        from engine.session import _srs_position
        schedule = self._make_schedule([2])
        assert _srs_position(5, schedule) is None

    def test_last_question_of_last_skill(self):
        from engine.session import _srs_position
        schedule = self._make_schedule([1, 2])
        skill, q_num, total = _srs_position(2, schedule)  # last q of skill_1
        assert skill.skill_id == 'skill_1'
        assert q_num == 2


class TestSrsBuildSchedule:
    def test_score_to_question_count(self):
        """SCORE_TO_QUESTIONS maps each score to the correct question count."""
        from engine.session import _srs_build_schedule, SCORE_TO_QUESTIONS

        class FakeSkill:
            def __init__(self, sid):
                self.skill_id = sid

        class FakeSS:
            def __init__(self, sid):
                self.skill = FakeSkill(sid)

        session_skills = [FakeSS('a'), FakeSS('b'), FakeSS('c')]
        score_map = {'a': 4, 'b': 0, 'c': 2}
        schedule = _srs_build_schedule(session_skills, score_map)

        assert schedule[0][1] == SCORE_TO_QUESTIONS[4]
        assert schedule[1][1] == SCORE_TO_QUESTIONS[0]
        assert schedule[2][1] == SCORE_TO_QUESTIONS[2]


# ── Area 8: Phase flow helpers ────────────────────────────────────────────────

class TestPhaseFlow:
    def test_grammar_teach_drill_next_phase_is_guided_practice(self):
        from engine.session import _next_phase
        assert _next_phase('teach_drill', 'grammar') == 'guided_practice'

    def test_vocab_present_skips_to_guided_practice(self):
        """Vocab has no questions phase — goes straight to guided_practice."""
        from engine.session import _next_phase
        assert _next_phase('present', 'vocab') == 'guided_practice'

    def test_grammar_assessment_advances_to_complete(self):
        from engine.session import _next_phase
        assert _next_phase('assessment', 'grammar') == 'complete'

    def test_grammar_reinforcement_advances_to_assessment(self):
        """Reinforcement must advance to assessment — was silently landing in
        'complete' because 'reinforcement' was missing from GRAMMAR_PHASE_FLOW."""
        from engine.session import _next_phase
        assert _next_phase('reinforcement', 'grammar') == 'assessment'

    def test_vocab_reinforcement_advances_to_assessment(self):
        from engine.session import _next_phase
        assert _next_phase('reinforcement', 'vocab') == 'assessment'

    def test_phase_max_turns_grammar(self):
        from engine.session import _phase_max_turns, GRAMMAR_PHASE_TURNS
        for phase, expected in GRAMMAR_PHASE_TURNS.items():
            assert _phase_max_turns(phase, 'grammar') == expected

    def test_phase_max_turns_unknown_returns_zero(self):
        from engine.session import _phase_max_turns
        assert _phase_max_turns('nonexistent_phase', 'grammar') == 0


class TestCheckinResumption:
    """_build_checkin frames new_skill sessions differently when the user is
    picking up a skill they've started before but not finished."""

    def _user(self, cefr='B1'):
        class U:
            estimated_cefr_level = cefr
        return U()

    def test_new_skill_resumption_uses_pickup_framing_spanish(self):
        from engine.session import _build_checkin
        text = _build_checkin(self._user('B1'), 'new_skill', 'previous session',
                              {'is_resumption': True}, target_name='Preterite')
        assert 'Preterite' in text
        assert 'retomarlo' in text
        # Must NOT use the "algo nuevo" framing.
        assert 'algo nuevo' not in text

    def test_new_skill_resumption_uses_pickup_framing_english(self):
        from engine.session import _build_checkin
        text = _build_checkin(self._user('A2'), 'new_skill', 'previous session',
                              {'is_resumption': True}, target_name='Preterite')
        assert 'Preterite' in text
        assert 'pick it back up' in text.lower()
        assert 'push forward with something new' not in text.lower()

    def test_new_skill_fresh_uses_original_framing(self):
        from engine.session import _build_checkin
        text = _build_checkin(self._user('B1'), 'new_skill', 'previous session',
                              {}, target_name='Preterite')
        assert 'algo nuevo' in text
        assert 'retomarlo' not in text

    def test_new_skill_default_is_not_resumption(self):
        """Missing is_resumption key defaults to False (not resumption)."""
        from engine.session import _build_checkin
        text = _build_checkin(self._user('B1'), 'new_skill', 'previous session',
                              {'skill': {'id': 'foo'}}, target_name='Preterite')
        assert 'algo nuevo' in text


class TestWrongAnswerReinforcementPrompts:
    """Fallback-path drill suffixes require re-attempts on wrong answers with
    a one-redo cap. These are prompt-string content checks — behavior is
    LLM-driven at runtime."""

    def test_guided_practice_grammar_requires_redo_on_wrong(self):
        from engine.session import GUIDED_PRACTICE_GRAMMAR_SUFFIX
        assert 'Try again' in GUIDED_PRACTICE_GRAMMAR_SUFFIX
        assert 'third time' in GUIDED_PRACTICE_GRAMMAR_SUFFIX

    def test_guided_practice_vocab_requires_redo_on_wrong(self):
        from engine.session import GUIDED_PRACTICE_VOCAB_SUFFIX
        assert 'Try again' in GUIDED_PRACTICE_VOCAB_SUFFIX
        assert 'third time' in GUIDED_PRACTICE_VOCAB_SUFFIX

    def test_free_production_requires_redo_on_wrong(self):
        from engine.session import FREE_PRODUCTION_SUFFIX
        assert 'Try again' in FREE_PRODUCTION_SUFFIX
        assert 'third time' in FREE_PRODUCTION_SUFFIX

    def test_assessment_does_NOT_require_redo(self):
        """Assessment is evaluation — keep it clean, no re-attempts."""
        from engine.session import ASSESSMENT_SUFFIX
        assert 'Try again' not in ASSESSMENT_SUFFIX


class TestIdleNoticeCopy:
    """The idle notice framing was updated to say 'stopped our lesson' and
    prompt with 'listo' — collapses the previous idle+checkin double-prompt."""

    def _user(self, cefr='B1'):
        class U:
            estimated_cefr_level = cefr
        return U()

    def test_idle_notice_spanish_says_stopped_lesson(self):
        from engine.session import build_idle_notice
        text = build_idle_notice(self._user('B1'), 90)
        assert 'paré nuestra clase' in text or 'listo' in text.lower()

    def test_idle_notice_english_says_stopped_lesson(self):
        from engine.session import build_idle_notice
        text = build_idle_notice(self._user('A2'), 90)
        assert 'stopped our lesson' in text
        assert 'listo' in text


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_resume_pending_flag_skips_checkin(make_user, make_skill):
    """When user.resume_pending=True on session open, the scripted check-in
    is skipped and the flag is cleared — the user goes straight into content."""
    from unittest.mock import patch, AsyncMock
    from asgiref.sync import sync_to_async
    from engine.session import handle_session
    from learner.models import Session, User

    user = await sync_to_async(make_user)(discord_id='td_resume1', cefr_level='B1')
    # Simulate a prior completed session (needed for the check-in codepath).
    old = await sync_to_async(Session.objects.create)(
        user=user, session_type='new_skill',
    )
    from django.utils import timezone as _tz
    from datetime import timedelta as _td
    await sync_to_async(
        lambda: Session.objects.filter(pk=old.pk).update(
            ended_at=_tz.now() - _td(hours=1), summary='prior session summary'
        )
    )()
    # Set the resume flag as if an idle-close just happened.
    await sync_to_async(
        lambda: User.objects.filter(pk=user.pk).update(resume_pending=True)
    )()
    await sync_to_async(user.refresh_from_db)()

    skill = await sync_to_async(make_skill)(skill_id='b1_resume_skill', name='Resume test',
                                            description='desc', cefr_level='B1')
    units_json = '[{"id":"a","label":"a","note":""}]'
    with patch('engine.teach_drill.call_llm', new=AsyncMock(return_value=units_json)):
        with patch('engine.session.call_llm', new=AsyncMock(return_value="LESSON OPENING")):
            with patch('engine.session._select_session',
                       new=AsyncMock(return_value=('new_skill', {'skill': {'id': skill.skill_id}}, []))):
                result = await handle_session(user, "listo")

    # Response should be the lesson opening (not the scripted "¿Listo?" check-in).
    assert result['text'] == "LESSON OPENING"

    # Flag should be cleared.
    await sync_to_async(user.refresh_from_db)()
    assert user.resume_pending is False


# ── Area 9: _select_session decision tree ────────────────────────────────────

@pytest.mark.django_db(transaction=True)
async def test_select_session_defaults_to_new_skill(make_user, make_skill):
    """No overdue skills, A1 user, no completed sessions → new_skill."""
    from engine.session import _select_session

    user = await sync_to_async(make_user)(discord_id='u_sel1', cefr_level='A1')
    await sync_to_async(make_skill)(skill_id='a1_ser_estar_basic', cefr_level='A1', order=1)

    session_type, context, _ = await _select_session(user)
    assert session_type == 'new_skill'


@pytest.mark.django_db(transaction=True)
async def test_select_session_srs_review_when_3_overdue(make_user, make_skill):
    """3+ overdue skills (score>0, next_review_at in the past) → srs_review."""
    from learner.models import SkillScore
    from engine.session import _select_session

    user = await sync_to_async(make_user)(discord_id='u_sel2', cefr_level='A2')
    overdue = timezone.now() - timedelta(days=1)

    for i in range(3):
        skill = await sync_to_async(make_skill)(
            skill_id=f'a1_overdue_{i}', cefr_level='A1', order=i, active=True
        )
        await sync_to_async(SkillScore.objects.create)(
            user=user, skill=skill, mode='writing', score=2, next_review_at=overdue,
        )

    session_type, _, _ = await _select_session(user)
    assert session_type == 'srs_review'


@pytest.mark.django_db(transaction=True)
async def test_select_session_b1_first_session_is_new_skill(make_user, make_skill):
    """B1 user with no prior sessions → new_skill, not conversation (conversation requires prior sessions)."""
    from engine.session import _select_session

    user = await sync_to_async(make_user)(discord_id='u_sel3', cefr_level='B1')
    await sync_to_async(make_skill)(skill_id='b1_imperfect', cefr_level='B1', order=1)

    session_type, _, _ = await _select_session(user)
    assert session_type == 'new_skill'


@pytest.mark.django_db(transaction=True)
async def test_select_session_conversation_after_prior_sessions(make_user, make_skill):
    """B1 user with 4 prior non-conversation sessions → conversation fires."""
    from learner.models import Session
    from engine.session import _select_session
    from django.utils import timezone

    user = await sync_to_async(make_user)(discord_id='u_sel4', cefr_level='B1')
    await sync_to_async(make_skill)(skill_id='b1_imperfect', cefr_level='B1', order=1)

    for _ in range(4):
        await sync_to_async(Session.objects.create)(
            user=user, session_type='new_skill', ended_at=timezone.now()
        )

    session_type, _, _ = await _select_session(user)
    assert session_type == 'conversation'


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
    with patch('engine.teach_drill.call_llm', new=AsyncMock(return_value=units_json)):
        with patch('engine.session.call_llm', new=AsyncMock(return_value="TEACH TURN 1")):
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


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_new_skill_target_gets_added_to_session_skills(make_user, make_skill):
    """The target_skill for a new_skill session MUST be present in SessionSkill,
    otherwise score_session grades against the wrong pool and returns [].
    Reading and writing branches already do this; the new_skill branch used to
    miss it — this test guards the fix."""
    from unittest.mock import patch, AsyncMock
    from asgiref.sync import sync_to_async
    from engine.session import handle_session
    from learner.models import Session, SessionSkill

    user = await sync_to_async(make_user)(discord_id='td_ss1', cefr_level='B1')
    target_skill = await sync_to_async(make_skill)(
        skill_id='b1_ss_target', name='SS target', description='desc', cefr_level='B1',
    )

    units_json = '[{"id":"a","label":"a","note":""}]'
    with patch('engine.teach_drill.call_llm', new=AsyncMock(return_value=units_json)):
        with patch('engine.session.call_llm', new=AsyncMock(return_value="opening")):
            with patch('engine.session._select_session',
                       new=AsyncMock(return_value=('new_skill', {'skill': {'id': target_skill.skill_id}}, []))):
                await handle_session(user, "hola")

    session = await sync_to_async(
        lambda: Session.objects.filter(user=user, ended_at__isnull=True).first()
    )()
    ss_skill_ids = await sync_to_async(
        lambda: set(SessionSkill.objects.filter(session=session).values_list('skill__skill_id', flat=True))
    )()
    assert target_skill.skill_id in ss_skill_ids, \
        f"target skill {target_skill.skill_id} missing from SessionSkill (found: {ss_skill_ids})"


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


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_grammar_new_skill_falls_back_when_unit_extraction_fails(make_user, make_skill):
    """When extract_units returns [], session lands in the legacy questions phase
    with a dense lesson (not stuck in teach_drill or advanced to complete)."""
    from unittest.mock import patch, AsyncMock
    from asgiref.sync import sync_to_async
    from engine.session import handle_session
    from learner.models import Session

    user = await sync_to_async(make_user)(discord_id='td_fallback1', cefr_level='B1')
    skill = await sync_to_async(make_skill)(skill_id='b1_fallback_test', name='Fallback test', description='desc', cefr_level='B1')

    # extract_units returns [] (LLM parse failure); the legacy path's call_llm returns a lesson.
    with patch('engine.teach_drill.call_llm', new=AsyncMock(return_value="not json — parse fails")):
        with patch('engine.session.call_llm', new=AsyncMock(return_value="LEGACY LESSON TEXT")):
            with patch('engine.session._select_session',
                       new=AsyncMock(return_value=('new_skill', {'skill': {'id': skill.skill_id}}, []))):
                await handle_session(user, "hola")

    session = await sync_to_async(
        lambda: Session.objects.filter(user=user, ended_at__isnull=True).first()
    )()
    assert session is not None
    assert session.current_phase == 'questions'  # legacy fallback path
    # quiz_state should NOT have teach_drill sub-dict (fallback never entered teach_drill)
    assert not session.quiz_state or 'teach_drill' not in (session.quiz_state or {})


# ── Conversation wrap-up marker gating ────────────────────────────────────────

class TestConversationEndMarker:
    def test_strip_marker_present(self):
        from engine.session import _strip_conversation_end_marker
        cleaned, present = _strip_conversation_end_marker(
            "Nice work today.\nSee you next time.\n<<CONVERSATION_END>>"
        )
        assert present is True
        assert "<<CONVERSATION_END>>" not in cleaned
        assert "See you next time." in cleaned

    def test_strip_marker_absent(self):
        from engine.session import _strip_conversation_end_marker
        cleaned, present = _strip_conversation_end_marker("some ordinary response")
        assert present is False
        assert cleaned == "some ordinary response"

    def test_close_suffix_requires_marker(self):
        from engine.session import CONVERSATION_CLOSE_SUFFIX
        assert "<<CONVERSATION_END>>" in CONVERSATION_CLOSE_SUFFIX
        # Explicit forbidden content list catches the failure mode we saw.
        assert "Forbidden" in CONVERSATION_CLOSE_SUFFIX or "REQUIRED" in CONVERSATION_CLOSE_SUFFIX

    def test_penultimate_suffix_warns_of_wrapup(self):
        from engine.session import CONVERSATION_PENULTIMATE_SUFFIX
        # Must instruct the LLM to tell the student the wrap-up is coming.
        assert "next turn" in CONVERSATION_PENULTIMATE_SUFFIX.lower() or "wrapping" in CONVERSATION_PENULTIMATE_SUFFIX.lower()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_conversation_stays_open_when_llm_omits_end_marker(make_user):
    """If the LLM ignores the wrap-up instruction and produces a continuation
    (no marker), the code MUST NOT close the session — this is the bug that
    caused a mid-conversation silent termination in the wild."""
    from unittest.mock import patch, AsyncMock
    from asgiref.sync import sync_to_async
    from engine.session import _continue_conversation, CONVERSATION_TURNS
    from learner.models import Session

    user = await sync_to_async(make_user)(discord_id='conv_no_marker', cefr_level='B1')
    session = await sync_to_async(Session.objects.create)(
        user=user, session_type='conversation',
        current_phase='conversation',
        phase_turns_completed=CONVERSATION_TURNS,  # at the cap
    )
    # LLM disobeys — returns a continuation question instead of the wrap-up
    # (no <<CONVERSATION_END>> marker). This is the exact failure we saw in
    # session 40.
    disobedient_response = "¿Y qué opinas de la boda de tu amigo?"
    with patch('engine.session.call_llm', new=AsyncMock(return_value=disobedient_response)):
        result = await _continue_conversation(user, session, text="anything")

    # Session must remain open.
    assert result["session_ended"] is False
    await sync_to_async(session.refresh_from_db)()
    assert session.ended_at is None
    # Attempt counter incremented so the safety cap eventually forces close.
    assert (session.quiz_state or {}).get('conversation_close_attempts') == 1


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_conversation_closes_when_llm_emits_end_marker(make_user):
    """When the LLM DOES emit the marker (obedient wrap-up), the session
    closes cleanly and the marker is stripped from the visible text and summary."""
    from unittest.mock import patch, AsyncMock
    from asgiref.sync import sync_to_async
    from engine.session import _continue_conversation, CONVERSATION_TURNS
    from learner.models import Session

    user = await sync_to_async(make_user)(discord_id='conv_marker', cefr_level='B1')
    session = await sync_to_async(Session.objects.create)(
        user=user, session_type='conversation',
        current_phase='conversation',
        phase_turns_completed=CONVERSATION_TURNS,
    )
    good_wrapup = (
        "Great work today — you nailed the preterite forms on the wedding stories.\n"
        "Next time, watch the ser/estar for location.\n"
        "Nos vemos, ¡vamos a una lección ahora!\n"
        "<<CONVERSATION_END>>"
    )
    # extract_and_store_interests and score_session are imported inside the
    # function body — patch them at their source modules.
    with patch('engine.session.call_llm', new=AsyncMock(return_value=good_wrapup)):
        with patch('engine.interests.extract_and_store_interests', new=AsyncMock()):
            with patch('engine.scoring.score_session', new=AsyncMock()):
                result = await _continue_conversation(user, session, text="thanks")

    assert result["session_ended"] is True
    assert "<<CONVERSATION_END>>" not in result["text"]
    await sync_to_async(session.refresh_from_db)()
    assert session.ended_at is not None
    assert "<<CONVERSATION_END>>" not in session.summary


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_conversation_force_closes_at_safety_cap(make_user):
    """After the safety cap of failed close attempts, force close even without
    the marker so a broken LLM can't keep the conversation open forever."""
    from unittest.mock import patch, AsyncMock
    from asgiref.sync import sync_to_async
    from engine.session import _continue_conversation, CONVERSATION_TURNS
    from learner.models import Session

    user = await sync_to_async(make_user)(discord_id='conv_cap', cefr_level='B1')
    session = await sync_to_async(Session.objects.create)(
        user=user, session_type='conversation',
        current_phase='conversation',
        phase_turns_completed=CONVERSATION_TURNS,
        quiz_state={'conversation_close_attempts': 1},  # one prior failed attempt
    )
    disobedient_response = "¿Y algo más que quieras contar?"  # no marker
    with patch('engine.session.call_llm', new=AsyncMock(return_value=disobedient_response)):
        with patch('engine.interests.extract_and_store_interests', new=AsyncMock()):
            with patch('engine.scoring.score_session', new=AsyncMock()):
                result = await _continue_conversation(user, session, text="k")

    assert result["session_ended"] is True
    await sync_to_async(session.refresh_from_db)()
    assert session.ended_at is not None


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_open_session_skips_empty_summary_sessions(make_user, make_skill):
    """last_session lookup must walk back past sessions whose summary is empty
    or NULL. Otherwise the check-in quotes an empty snippet and the LLM greets
    the user as a first-timer (bug seen in the wild: session 40 opening said
    'Bienvenido a tu primera sesión')."""
    from unittest.mock import patch, AsyncMock
    from asgiref.sync import sync_to_async
    from django.utils import timezone as _tz
    from datetime import timedelta as _td
    from engine.session import handle_session
    from learner.models import Session, SessionEvent

    user = await sync_to_async(make_user)(discord_id='taint_summary', cefr_level='B1')

    # Real session, 2 days ago, with a meaningful summary.
    real_session = await sync_to_async(Session.objects.create)(
        user=user, session_type='new_skill',
    )
    two_days_ago = _tz.now() - _td(days=2)
    await sync_to_async(
        lambda: Session.objects.filter(pk=real_session.pk).update(
            ended_at=two_days_ago,
            summary='Practicamos preterite irregulars con ser, ir, estar.',
        )
    )()

    # Empty-summary session, 1 hour ago. This is what the auto-close leaves
    # when a session barely gets going.
    empty_session = await sync_to_async(Session.objects.create)(
        user=user, session_type='new_skill',
    )
    one_hour_ago = _tz.now() - _td(hours=1)
    await sync_to_async(
        lambda: Session.objects.filter(pk=empty_session.pk).update(
            ended_at=one_hour_ago, summary='',  # blank!
        )
    )()

    # Force a conversation session (which triggers the check-in path — and
    # the check-in message directly quotes the last_session summary).
    with patch('engine.session._select_session',
               new=AsyncMock(return_value=('conversation', {}, []))):
        await handle_session(user, "hola")

    # The new session's check-in event content must reference the REAL summary,
    # NOT the empty one — proving the query walked back past the empty session.
    new_session = await sync_to_async(
        lambda: Session.objects.filter(user=user, session_type='conversation').first()
    )()
    check_in_event = await sync_to_async(
        lambda: SessionEvent.objects.filter(session=new_session).first()
    )()
    assert 'preterite irregulars' in check_in_event.content, \
        f"Check-in should quote real summary but was: {check_in_event.content!r}"


class TestCheckInReplyReachesTheModel:
    """The student's reply to the greeting was written to the DB and then dropped
    before the LLM saw it — 'I want to learn subjunctive' was structurally
    discarded in sessions 46 and 47 (2026-08-19)."""

    async def _open(self, make_user, make_skill, text, uid):
        from engine.session import _handle_check_in
        from learner.models import Session, SessionEvent

        user = await sync_to_async(make_user)(discord_id=uid, cefr_level='B1')
        skill = await sync_to_async(make_skill)(skill_id=uid + '_gram', name='Preterite')
        session = await sync_to_async(Session.objects.create)(
            user=user, session_type='new_skill', target_skill=skill,
            current_phase='check_in',
        )
        await sync_to_async(SessionEvent.objects.create)(
            session=session, event_type='system',
            content='Today I want to push forward with something new. Ready?',
            user_response='',
        )
        units = AsyncMock(return_value='[{"id":"u1","label":"u1","note":""}]')
        opening = AsyncMock(return_value='OPENING TURN')
        with patch('engine.teach_drill.call_llm', new=units), \
             patch('engine.session.call_llm', new=opening):
            await _handle_check_in(user, session, text=text)
        return opening

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_student_reply_is_in_the_history(self, make_user, make_skill):
        opening = await self._open(
            make_user, make_skill, "I want to learn the subjunctive", 'ci_reach')

        sent = opening.await_args.args[0]
        assert "I want to learn the subjunctive" in " ".join(m["content"] for m in sent)

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_history_roles_alternate(self, make_user, make_skill):
        """call_llm passes messages straight to the API with no normalisation."""
        opening = await self._open(make_user, make_skill, "listo", 'ci_alt')

        roles = [m["role"] for m in opening.await_args.args[0]]
        assert all(a != b for a, b in zip(roles, roles[1:])), roles

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_empty_reply_still_works(self, make_user, make_skill):
        opening = await self._open(make_user, make_skill, "", 'ci_empty')
        assert opening.await_count == 1


class TestForcedSkill:
    """A skill request must be able to override normal session selection."""

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_forced_skill_overrides_selection(self, make_user, make_skill):
        from engine.session import _open_session
        from learner.models import Session

        user = await sync_to_async(make_user)(discord_id='forced_1', cefr_level='B1')
        await sync_to_async(make_skill)(skill_id='a1_default', name='Default pick')
        wanted = await sync_to_async(make_skill)(
            skill_id='b1_subjunctive_formation', name='Subjunctive — formation',
            cefr_level='B1')

        units = AsyncMock(return_value='[{"id":"u1","label":"u1","note":""}]')
        with patch('engine.teach_drill.call_llm', new=units), \
             patch('engine.session.call_llm', new=AsyncMock(return_value='OPENING')):
            await _open_session(user, text="quiero el subjuntivo", forced_skill={
                'id': wanted.skill_id, 'name': wanted.name,
                'cefr_level': 'B1', 'description': '', 'order': 1})

        session = await sync_to_async(
            lambda: Session.objects.filter(user=user).order_by('-id').select_related('target_skill').first()
        )()
        assert session.session_type == 'new_skill'
        assert session.target_skill.skill_id == 'b1_subjunctive_formation'


@pytest.mark.django_db(transaction=True)
class TestSkillRequestRouting:
    """Detection must be phase-independent: the original bug lost two of three
    turns because the only classifier lived inside teach_drill, and the student
    asked at the check-in greeting."""

    async def _grid(self, make_skill):
        return [
            await sync_to_async(make_skill)(
                skill_id='b1_subjunctive_formation',
                name='Subjunctive — present formation', cefr_level='B1',
                description='Forming the present subjunctive'),
            await sync_to_async(make_skill)(
                skill_id='b1_subjunctive_triggers_doubt_emotion',
                name='Subjunctive — doubt and emotion triggers', cefr_level='B1',
                description='Dudo que'),
        ]

    async def _session(self, make_user, make_skill, uid, phase):
        from learner.models import Session
        grid = await self._grid(make_skill)
        user = await sync_to_async(make_user)(discord_id=uid, cefr_level='B1')
        session = await sync_to_async(Session.objects.create)(
            user=user, session_type='new_skill', target_skill=grid[0],
            current_phase=phase,
            quiz_state={"teach_drill": {"units": [{"id": "u", "label": "u", "note": ""}],
                                        "taught": [], "drills": {}, "turn_count": 0,
                                        "lesson_complete": False}},
        )
        return user, session

    @pytest.mark.asyncio
    async def test_request_at_the_greeting_is_caught(self, make_user, make_skill):
        from engine.session import handle_session
        from learner.models import Session

        user, session = await self._session(make_user, make_skill, 'rt_ci', 'check_in')
        result = await handle_session(user, "I want to learn subjunctive")

        assert "formation" in result["text"].lower()
        reloaded = await sync_to_async(Session.objects.get)(pk=session.pk)
        assert reloaded.current_phase == 'skill_request'

    @pytest.mark.asyncio
    async def test_request_mid_lesson_is_caught(self, make_user, make_skill):
        from engine.session import handle_session
        from learner.models import Session

        user, session = await self._session(make_user, make_skill, 'rt_td', 'teach_drill')
        result = await handle_session(user, "can we learn subjunctive instead")

        assert "formation" in result["text"].lower()
        reloaded = await sync_to_async(Session.objects.get)(pk=session.pk)
        assert reloaded.current_phase == 'skill_request'

    @pytest.mark.asyncio
    async def test_a_drill_answer_is_never_intercepted(self, make_user, make_skill):
        from engine.session import handle_session
        from learner.models import Session

        user, session = await self._session(make_user, make_skill, 'rt_ans', 'teach_drill')
        with patch('engine.teach_drill.call_llm', new=AsyncMock(return_value='EVALUATION')):
            await handle_session(user, "Yo tuve un buen día")

        reloaded = await sync_to_async(Session.objects.get)(pk=session.pk)
        assert reloaded.current_phase != 'skill_request'


class TestSummaryTrim:
    """Feedback #12a: "It's Chris's birthday. This says Chri. That's wrong."
    A hard character slice cut the name in half."""

    def test_does_not_cut_a_word_in_half(self):
        from engine.session import _trim_summary
        raw = ("Practiced preterite and imperfect, talked about the gym and "
               "about la boda de Chris and coffee with Melodie")
        out = _trim_summary(raw, limit=80)
        assert 'Chri…' not in out
        assert not out.rstrip('…').rstrip().endswith('Chri')

    def test_short_summaries_pass_through_untouched(self):
        from engine.session import _trim_summary
        assert _trim_summary('Short one', limit=80) == 'Short one'

    def test_long_summary_is_still_shortened(self):
        from engine.session import _trim_summary
        raw = 'palabra ' * 40
        out = _trim_summary(raw, limit=80)
        assert len(out) <= 81
        assert out.endswith('…')

    def test_a_single_unbroken_token_still_truncates(self):
        from engine.session import _trim_summary
        out = _trim_summary('x' * 200, limit=80)
        assert len(out) <= 81
        assert out.endswith('…')

    def test_empty_is_safe(self):
        from engine.session import _trim_summary
        assert _trim_summary('', limit=80) == ''


class TestAnotherPassCopy:
    """Feedback #15a: the review always offered a "second pass", including on the
    third. The loop was already correct — only the wording claimed a count."""

    def test_copy_does_not_claim_a_number(self):
        from engine.session import ANOTHER_PASS_CHECK_STRING
        assert 'second pass' not in ANOTHER_PASS_CHECK_STRING.lower()
        assert 'another pass' in ANOTHER_PASS_CHECK_STRING.lower()

    def test_sessions_mid_flight_on_the_old_phase_still_work(self):
        """Renaming the phase must not strand a session already sitting in it."""
        from engine.session import _is_pass_check_phase
        assert _is_pass_check_phase('another_pass_check') is True
        assert _is_pass_check_phase('second_pass_check') is True
        assert _is_pass_check_phase('review') is False


class TestQuestionFloor:
    """A mastered skill got ONE question, which cannot separate knowing it from
    guessing — that is how a review of three skills came to be 3 questions."""

    def test_no_skill_gets_fewer_than_two(self):
        from engine.session import SCORE_TO_QUESTIONS
        assert min(SCORE_TO_QUESTIONS.values()) >= 2

    def test_untested_is_not_sampled_less_than_known_weak(self):
        """Score 0 means zero information; it was getting fewer questions than
        score 1, which is backwards."""
        from engine.session import SCORE_TO_QUESTIONS
        assert SCORE_TO_QUESTIONS[0] >= SCORE_TO_QUESTIONS[1]

    def test_strong_skills_stay_tapered(self):
        """Not flat — as more skills are mastered more come due at once."""
        from engine.session import SCORE_TO_QUESTIONS
        assert SCORE_TO_QUESTIONS[4] < SCORE_TO_QUESTIONS[0]


class TestReviewTopUp:
    """A review shorter than the minimum pulls forward the next-due skills rather
    than drilling mastered ones harder."""

    def test_no_top_up_when_already_long_enough(self):
        from engine.session import _topped_up_skill_ids
        scheduled = [('a', 0), ('b', 0)]          # 3 + 3 = 6 questions
        assert _topped_up_skill_ids(scheduled, [('c', 0)]) == []

    def test_pulls_forward_until_the_minimum_is_met(self):
        from engine.session import _topped_up_skill_ids
        scheduled = [('a', 4)]                     # 2 questions — under the floor
        extra = _topped_up_skill_ids(scheduled, [('b', 4), ('c', 4)])
        assert extra == ['b']                      # 2 + 2 = 4, stop

    def test_never_duplicates_a_scheduled_skill(self):
        from engine.session import _topped_up_skill_ids
        extra = _topped_up_skill_ids([('a', 4)], [('a', 4), ('b', 4)])
        assert 'a' not in extra

    def test_degrades_gracefully_with_no_candidates(self):
        from engine.session import _topped_up_skill_ids
        assert _topped_up_skill_ids([('a', 4)], []) == []


@pytest.mark.django_db(transaction=True)
class TestPassRestartAsksTheFirstQuestion:
    """Every pass after the first was one question short: the restart set the
    counter to 0, claiming Q0 was outstanding, but nothing had asked it."""

    @pytest.mark.asyncio
    async def test_restart_starts_at_question_zero(self, make_user, make_skill):
        from engine.session import _continue_srs_review, PASS_CHECK_PHASE
        from learner.models import Session, SessionSkill

        user = await sync_to_async(make_user)(discord_id='pass_0', cefr_level='B1')
        first = await sync_to_async(make_skill)(
            skill_id='sk_first', name='Preterite irregulars', order=1)
        second = await sync_to_async(make_skill)(
            skill_id='sk_second', name='Imperfect AR verbs', order=2)
        session = await sync_to_async(Session.objects.create)(
            user=user, session_type='srs_review', current_phase=PASS_CHECK_PHASE,
            phase_turns_completed=0)
        for sk in (first, second):
            await sync_to_async(SessionSkill.objects.create)(session=session, skill=sk)

        mock = AsyncMock(return_value='Next question')
        with patch('engine.session.call_llm', new=mock):
            await _continue_srs_review(user, session, text='si')

        suffix = mock.await_args.kwargs.get('system_suffix', '')
        assert 'Preterite irregulars' in suffix, f"wrong skill: {suffix[:200]}"
        # The telling detail: a restart must ask question 1, not question 2.
        # The bug consumed Q0 without ever asking it.
        assert 'question 1 of' in suffix, (
            f"restart skipped the first question; got: {suffix[:200]}")


@pytest.mark.django_db(transaction=True)
class TestInactivityWindow:
    """A lesson used to be closed after an hour idle. Six hours instead, so
    stepping away for an afternoon lets you pick the same lesson back up."""

    async def _session_idle_for(self, make_user, make_skill, uid, minutes):
        from learner.models import Session, SessionEvent
        user = await sync_to_async(make_user)(discord_id=uid, cefr_level='B1')
        skill = await sync_to_async(make_skill)(skill_id=uid + '_sk')
        session = await sync_to_async(Session.objects.create)(
            user=user, session_type='new_skill', target_skill=skill,
            current_phase='teach_drill')
        event = await sync_to_async(SessionEvent.objects.create)(
            session=session, event_type='quiz', content='Q', user_response='A')
        stale_at = timezone.now() - timedelta(minutes=minutes)
        await sync_to_async(
            lambda: SessionEvent.objects.filter(pk=event.pk).update(timestamp=stale_at))()
        return user, session

    def test_the_window_is_six_hours(self):
        from engine.session import INACTIVITY_TIMEOUT_MINUTES
        assert INACTIVITY_TIMEOUT_MINUTES == 360

    @pytest.mark.asyncio
    async def test_five_hours_idle_is_still_the_same_lesson(self, make_user, make_skill):
        from engine.session import _check_inactivity
        user, session = await self._session_idle_for(make_user, make_skill, 'idle_5h', 300)
        stale, idle_minutes = await _check_inactivity(user, session)
        assert stale is False
        assert idle_minutes == 300

    @pytest.mark.asyncio
    async def test_seven_hours_idle_ends_the_lesson(self, make_user, make_skill):
        from engine.session import _check_inactivity
        user, session = await self._session_idle_for(make_user, make_skill, 'idle_7h', 420)
        stale, _ = await _check_inactivity(user, session)
        assert stale is True

    @pytest.mark.asyncio
    async def test_just_under_six_hours_survives(self, make_user, make_skill):
        from engine.session import _check_inactivity
        user, session = await self._session_idle_for(make_user, make_skill, 'idle_359', 359)
        stale, _ = await _check_inactivity(user, session)
        assert stale is False
