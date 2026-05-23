"""
Tests for engine/session.py

Covers: _srs_position, _srs_build_schedule, phase flow helpers,
and _select_session decision tree.
"""
import pytest
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
    def test_grammar_present_advances_to_questions(self):
        from engine.session import _next_phase
        assert _next_phase('present', 'grammar') == 'questions'

    def test_vocab_present_skips_to_guided_practice(self):
        """Vocab has no questions phase — goes straight to guided_practice."""
        from engine.session import _next_phase
        assert _next_phase('present', 'vocab') == 'guided_practice'

    def test_grammar_assessment_advances_to_complete(self):
        from engine.session import _next_phase
        assert _next_phase('assessment', 'grammar') == 'complete'

    def test_phase_max_turns_grammar(self):
        from engine.session import _phase_max_turns, GRAMMAR_PHASE_TURNS
        for phase, expected in GRAMMAR_PHASE_TURNS.items():
            assert _phase_max_turns(phase, 'grammar') == expected

    def test_phase_max_turns_unknown_returns_zero(self):
        from engine.session import _phase_max_turns
        assert _phase_max_turns('nonexistent_phase', 'grammar') == 0


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
