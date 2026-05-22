"""
Tests for engine/scoring.py

Covers: score_session (LLM mocked), SRS intervals, _check_reteach/_bump_to_now,
check_win_state, get_frontier_skills.
"""
import json
import pytest
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from asgiref.sync import sync_to_async
from django.utils import timezone


# ── Area 4: score_session ─────────────────────────────────────────────────────

def _mock_llm_response(items: list) -> MagicMock:
    """Build a mock anthropic response returning the given scored items."""
    resp = MagicMock()
    resp.content = [MagicMock(text=json.dumps(items))]
    return resp


@pytest.mark.django_db(transaction=True)
async def test_score_session_writes_skill_score(make_user, make_skill):
    """score_session writes SkillScore row for a matched skill."""
    from learner.models import Session, SessionEvent, SessionSkill, SkillScore
    from engine.scoring import score_session

    user = await sync_to_async(make_user)(discord_id='u_sc1')
    skill = await sync_to_async(make_skill)(skill_id='a1_present_ar', cefr_level='A1')
    session = await sync_to_async(Session.objects.create)(user=user, session_type='new_skill')
    await sync_to_async(SessionSkill.objects.create)(session=session, skill=skill)
    await sync_to_async(SessionEvent.objects.create)(
        session=session, event_type='conversation',
        content='Conjugate hablar.', user_response='hablo, hablas, habla',
    )

    with patch('engine.scoring.anthropic.AsyncAnthropic') as MockClient:
        MockClient.return_value.messages.create = AsyncMock(
            return_value=_mock_llm_response([{'skill_id': 'a1_present_ar', 'score': 3}])
        )
        result = await score_session(session, user)

    assert result, "score_session should return scored items"
    assert result[0]['skill_id'] == 'a1_present_ar'
    assert result[0]['score'] == 3

    ss = await sync_to_async(SkillScore.objects.filter(user=user, skill=skill).first)()
    assert ss is not None
    assert ss.score == 3


@pytest.mark.django_db(transaction=True)
async def test_score_session_writes_skill_score_event(make_user, make_skill):
    """score_session creates a SkillScoreEvent row."""
    from learner.models import Session, SessionEvent, SessionSkill, SkillScoreEvent
    from engine.scoring import score_session

    user = await sync_to_async(make_user)(discord_id='u_sc2')
    skill = await sync_to_async(make_skill)(skill_id='a1_greetings', cefr_level='A1')
    session = await sync_to_async(Session.objects.create)(user=user, session_type='conversation')
    await sync_to_async(SessionSkill.objects.create)(session=session, skill=skill)
    await sync_to_async(SessionEvent.objects.create)(
        session=session, event_type='conversation', content='Say hello.', user_response='Hola.',
    )

    with patch('engine.scoring.anthropic.AsyncAnthropic') as MockClient:
        MockClient.return_value.messages.create = AsyncMock(
            return_value=_mock_llm_response([{'skill_id': 'a1_greetings', 'score': 4}])
        )
        await score_session(session, user)

    count = await sync_to_async(SkillScoreEvent.objects.filter(user=user, skill=skill).count)()
    assert count == 1


@pytest.mark.django_db(transaction=True)
async def test_score_session_srs_interval_is_correct(make_user, make_skill):
    """next_review_at is set to approximately now + SRS_INTERVALS[score] days."""
    from learner.models import Session, SessionEvent, SessionSkill, SkillScore
    from engine.scoring import score_session, SRS_INTERVALS

    user = await sync_to_async(make_user)(discord_id='u_sc3')
    skill = await sync_to_async(make_skill)(skill_id='a2_preterite_regular', cefr_level='A2')
    session = await sync_to_async(Session.objects.create)(user=user, session_type='new_skill')
    await sync_to_async(SessionSkill.objects.create)(session=session, skill=skill)
    await sync_to_async(SessionEvent.objects.create)(
        session=session, event_type='conversation', content='Practice preterite.', user_response='comí',
    )

    score_val = 2  # SRS_INTERVALS[2] = 3 days
    before = timezone.now()
    with patch('engine.scoring.anthropic.AsyncAnthropic') as MockClient:
        MockClient.return_value.messages.create = AsyncMock(
            return_value=_mock_llm_response([{'skill_id': 'a2_preterite_regular', 'score': score_val}])
        )
        await score_session(session, user)

    ss = await sync_to_async(SkillScore.objects.filter(user=user, skill=skill).first)()
    expected_days = SRS_INTERVALS[score_val]
    delta = ss.next_review_at - before
    assert expected_days - 1 <= delta.days <= expected_days + 1


@pytest.mark.django_db(transaction=True)
async def test_score_session_ignores_unknown_skill_id(make_user, make_skill):
    """score_session silently skips skill_ids not in the DB."""
    from learner.models import Session, SessionEvent, SessionSkill, SkillScore
    from engine.scoring import score_session

    user = await sync_to_async(make_user)(discord_id='u_sc4')
    skill = await sync_to_async(make_skill)(skill_id='a1_ser_estar_basic', cefr_level='A1')
    session = await sync_to_async(Session.objects.create)(user=user, session_type='new_skill')
    await sync_to_async(SessionSkill.objects.create)(session=session, skill=skill)
    await sync_to_async(SessionEvent.objects.create)(
        session=session, event_type='conversation', content='Q', user_response='A',
    )

    with patch('engine.scoring.anthropic.AsyncAnthropic') as MockClient:
        MockClient.return_value.messages.create = AsyncMock(
            return_value=_mock_llm_response([{'skill_id': 'does_not_exist', 'score': 3}])
        )
        result = await score_session(session, user)

    assert result == []
    count = await sync_to_async(SkillScore.objects.filter(user=user).count)()
    assert count == 0


@pytest.mark.django_db(transaction=True)
async def test_score_session_returns_empty_on_no_events(make_user):
    """score_session returns [] without calling LLM when session has no events."""
    from learner.models import Session
    from engine.scoring import score_session

    user = await sync_to_async(make_user)(discord_id='u_sc5')
    session = await sync_to_async(Session.objects.create)(user=user, session_type='conversation')
    result = await score_session(session, user)
    assert result == []


# ── Area 5: _check_reteach / _bump_to_now ────────────────────────────────────

@pytest.mark.django_db(transaction=True)
async def test_check_reteach_score1_bumps_to_now(make_user, make_skill):
    """score=1 on the latest event → next_review_at is bumped to now immediately."""
    from learner.models import Session, SkillScore, SkillScoreEvent
    from engine.scoring import _check_reteach

    user = await sync_to_async(make_user)(discord_id='u_cr1')
    skill = await sync_to_async(make_skill)(skill_id='a1_present_ar', cefr_level='A1')
    session = await sync_to_async(Session.objects.create)(user=user, session_type='new_skill')

    future = timezone.now() + timedelta(days=10)
    await sync_to_async(SkillScore.objects.create)(
        user=user, skill=skill, mode='writing', score=1, next_review_at=future,
    )
    await sync_to_async(SkillScoreEvent.objects.create)(
        user=user, skill=skill, mode='writing', score=1, session=session,
    )

    before = timezone.now()
    await _check_reteach(user, skill, 'writing')

    ss = await sync_to_async(SkillScore.objects.get)(user=user, skill=skill, mode='writing')
    assert ss.next_review_at <= before + timedelta(seconds=5), (
        "next_review_at should be bumped to now for score=1"
    )


@pytest.mark.django_db(transaction=True)
async def test_check_reteach_two_consecutive_score2_bumps(make_user, make_skill):
    """Two consecutive score ≤2 sessions → next_review_at is bumped to now."""
    from learner.models import Session, SkillScore, SkillScoreEvent
    from engine.scoring import _check_reteach

    user = await sync_to_async(make_user)(discord_id='u_cr2')
    skill = await sync_to_async(make_skill)(skill_id='a1_present_ar', cefr_level='A1')
    s1 = await sync_to_async(Session.objects.create)(user=user, session_type='new_skill')
    s2 = await sync_to_async(Session.objects.create)(user=user, session_type='new_skill')

    future = timezone.now() + timedelta(days=10)
    await sync_to_async(SkillScore.objects.create)(
        user=user, skill=skill, mode='writing', score=2, next_review_at=future,
    )
    await sync_to_async(SkillScoreEvent.objects.create)(
        user=user, skill=skill, mode='writing', score=2, session=s1,
    )
    await sync_to_async(SkillScoreEvent.objects.create)(
        user=user, skill=skill, mode='writing', score=2, session=s2,
    )

    before = timezone.now()
    await _check_reteach(user, skill, 'writing')

    ss = await sync_to_async(SkillScore.objects.get)(user=user, skill=skill, mode='writing')
    assert ss.next_review_at <= before + timedelta(seconds=5)


@pytest.mark.django_db(transaction=True)
async def test_check_reteach_single_score2_does_not_bump(make_user, make_skill):
    """A single score=2 (no consecutive pair) does NOT bump next_review_at."""
    from learner.models import Session, SkillScore, SkillScoreEvent
    from engine.scoring import _check_reteach

    user = await sync_to_async(make_user)(discord_id='u_cr3')
    skill = await sync_to_async(make_skill)(skill_id='a1_present_ar', cefr_level='A1')
    session = await sync_to_async(Session.objects.create)(user=user, session_type='new_skill')

    future = timezone.now() + timedelta(days=7)
    await sync_to_async(SkillScore.objects.create)(
        user=user, skill=skill, mode='writing', score=2, next_review_at=future,
    )
    await sync_to_async(SkillScoreEvent.objects.create)(
        user=user, skill=skill, mode='writing', score=2, session=session,
    )

    await _check_reteach(user, skill, 'writing')

    ss = await sync_to_async(SkillScore.objects.get)(user=user, skill=skill, mode='writing')
    assert ss.next_review_at >= timezone.now() + timedelta(days=5), (
        "Single score=2 should not bump next_review_at to now"
    )


# ── Area 6: check_win_state / get_frontier_skills ────────────────────────────

@pytest.mark.django_db(transaction=True)
async def test_check_win_state_true_when_all_mastered(make_user, make_skill):
    """All active skills with writing score ≥ 3 → win state is True."""
    from learner.models import SkillScore
    from engine.scoring import check_win_state

    user = await sync_to_async(make_user)(discord_id='u_win1')
    skill = await sync_to_async(make_skill)(skill_id='a1_present_ar', active=True)
    await sync_to_async(SkillScore.objects.create)(user=user, skill=skill, mode='writing', score=4)

    assert await check_win_state(user) is True


@pytest.mark.django_db(transaction=True)
async def test_check_win_state_false_when_any_untested(make_user, make_skill):
    """Any active skill with no writing score → win state is False."""
    from learner.models import SkillScore
    from engine.scoring import check_win_state

    user = await sync_to_async(make_user)(discord_id='u_win2')
    s1 = await sync_to_async(make_skill)(skill_id='a1_present_ar', order=1, active=True)
    await sync_to_async(make_skill)(skill_id='a1_greetings', order=2, active=True)
    await sync_to_async(SkillScore.objects.create)(user=user, skill=s1, mode='writing', score=4)
    # a1_greetings has no score

    assert await check_win_state(user) is False


@pytest.mark.django_db(transaction=True)
async def test_get_frontier_skills_pool_around_frontier(make_user, make_skill):
    """Frontier is first skill with 0 < score < 3. Pool includes skills below and above."""
    from learner.models import SkillScore
    from engine.scoring import get_frontier_skills

    user = await sync_to_async(make_user)(discord_id='u_fro1')
    skills = [
        await sync_to_async(make_skill)(skill_id=f'skill_fr_{i}', order=i, active=True)
        for i in range(5)
    ]

    # skills[0], [1] mastered; skills[2] is frontier (score=2); [3], [4] untested
    await sync_to_async(SkillScore.objects.create)(user=user, skill=skills[0], mode='writing', score=4)
    await sync_to_async(SkillScore.objects.create)(user=user, skill=skills[1], mode='writing', score=3)
    await sync_to_async(SkillScore.objects.create)(user=user, skill=skills[2], mode='writing', score=2)

    pool = await get_frontier_skills(user)
    pool_ids = {s.skill_id for s in pool}

    assert 'skill_fr_2' in pool_ids, "Frontier skill should be in the pool"
    assert 'skill_fr_3' in pool_ids, "Skill above frontier should be in the pool"


@pytest.mark.django_db(transaction=True)
async def test_get_frontier_skills_cold_start(make_user, make_skill):
    """No scores at all (cold start) → returns first skills in order."""
    from engine.scoring import get_frontier_skills

    user = await sync_to_async(make_user)(discord_id='u_fro2')
    for i in range(4):
        await sync_to_async(make_skill)(skill_id=f'cold_{i}', order=i, active=True)

    pool = await get_frontier_skills(user)
    assert len(pool) > 0
    assert pool[0].skill_id == 'cold_0'
