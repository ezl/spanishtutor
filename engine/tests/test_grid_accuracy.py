"""The five changes that make the skill grid mean something.

Nine of fourteen "mastered" skills had zero scoring events behind them -- green
because the placement quiz asked one question. See docs/grid-accuracy.md.
"""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from asgiref.sync import sync_to_async


def _llm(items):
    resp = MagicMock()
    resp.content = [MagicMock(text=json.dumps(items))]
    return resp


async def _run_scoring(user, skill, score, sid):
    """One scored session against `skill`, with the LLM returning `score`."""
    from learner.models import Session, SessionEvent, SessionSkill
    from engine.scoring import score_session

    session = await sync_to_async(Session.objects.create)(
        user=user, session_type='new_skill')
    await sync_to_async(SessionEvent.objects.create)(
        session=session, event_type='conversation',
        content='¿Cómo se dice?', user_response='lo dije')
    await sync_to_async(SessionSkill.objects.create)(session=session, skill=skill)

    with patch('engine.scoring.anthropic.AsyncAnthropic') as MockClient:
        MockClient.return_value.messages.create = AsyncMock(
            return_value=_llm([{'skill_id': sid, 'score': score}]))
        return await score_session(session, user)


class TestCorrectAnswersAdvanceOneStep:
    """Succeeding once is weak evidence -- you might have it, or guessed, or the
    question was easy. Mastery has to be earned by repeated confirmation."""

    @pytest.mark.django_db(transaction=True)
    async def test_an_untested_skill_cannot_jump_to_mastery(self, make_user, make_skill):
        from learner.models import SkillScore
        user = await sync_to_async(make_user)(discord_id='ga_jump')
        skill = await sync_to_async(make_skill)(skill_id='ga_jump_s', cefr_level='B1')

        await _run_scoring(user, skill, 4, 'ga_jump_s')

        ss = await sync_to_async(SkillScore.objects.get)(user=user, skill=skill)
        assert ss.score == 1, 'one session took an untested skill straight to mastery'

    @pytest.mark.django_db(transaction=True)
    async def test_repeated_confirmation_climbs_to_mastery(self, make_user, make_skill):
        from learner.models import SkillScore
        user = await sync_to_async(make_user)(discord_id='ga_climb')
        skill = await sync_to_async(make_skill)(skill_id='ga_climb_s', cefr_level='B1')

        for expected in (1, 2, 3, 4):
            await _run_scoring(user, skill, 4, 'ga_climb_s')
            ss = await sync_to_async(SkillScore.objects.get)(user=user, skill=skill)
            assert ss.score == expected

    @pytest.mark.django_db(transaction=True)
    async def test_failure_drops_all_the_way_without_stepping(self, make_user, make_skill):
        """Recency wins descending: if you cannot do it now, you do not have it
        now. The asymmetry between hits and misses is the whole point."""
        from learner.models import SkillScore
        user = await sync_to_async(make_user)(discord_id='ga_drop')
        skill = await sync_to_async(make_skill)(skill_id='ga_drop_s', cefr_level='B1')
        await sync_to_async(SkillScore.objects.create)(
            user=user, skill=skill, mode='writing', score=4)

        await _run_scoring(user, skill, 1, 'ga_drop_s')

        ss = await sync_to_async(SkillScore.objects.get)(user=user, skill=skill)
        assert ss.score == 1


class TestFailureRoutesToTeaching:
    """_check_reteach used to bump next_review_at to now, so a failed skill was
    re-TESTED forever and never taught. Retrieval practice only works on
    something already encoded; testing an unlearned item is failing on a
    schedule."""

    @pytest.mark.django_db(transaction=True)
    async def test_a_failed_skill_leaves_the_review_queue(self, make_user, make_skill):
        from learner.models import SkillScore
        user = await sync_to_async(make_user)(discord_id='ga_reteach')
        skill = await sync_to_async(make_skill)(skill_id='ga_reteach_s', cefr_level='B1')

        await _run_scoring(user, skill, 1, 'ga_reteach_s')

        ss = await sync_to_async(SkillScore.objects.get)(user=user, skill=skill)
        assert ss.next_review_at is None, 'a failed skill stayed queued for review'


class TestOnlyMasteryBlocksTeaching:
    """The quiz writes score=1 for "I don't know", and scored_ids used to be
    every row regardless of value -- so saying "I don't know" barred a skill
    from ever being taught."""

    @pytest.mark.django_db
    def test_a_failed_skill_is_still_teachable(self, make_skill):
        from engine.curriculum import next_new_skill
        make_skill(skill_id='ga_teach_known', cefr_level='B1', order=901)
        make_skill(skill_id='ga_teach_failed', cefr_level='B1', order=902)
        nxt = next_new_skill('B1', {'ga_teach_known'})
        assert nxt['id'] == 'ga_teach_failed'


class TestPlacementCap:
    """A single quiz answer certifies mastery only when the learner is
    demonstrably two or more levels above the material."""

    @pytest.mark.django_db
    def test_a_four_at_the_placed_level_is_held_at_three(self, make_user, make_skill):
        from learner.models import SkillScore
        from engine.onboarding import cap_placement_mastery
        user = make_user(discord_id='ga_cap1')
        s = make_skill(skill_id='ga_cap_b1', cefr_level='B1')
        SkillScore.objects.create(user=user, skill=s, mode='writing', score=4)
        assert cap_placement_mastery(user, 'B1') == 1
        assert SkillScore.objects.get(user=user, skill=s).score == 3

    @pytest.mark.django_db
    def test_one_level_below_is_also_held(self, make_user, make_skill):
        from learner.models import SkillScore
        from engine.onboarding import cap_placement_mastery
        user = make_user(discord_id='ga_cap2')
        s = make_skill(skill_id='ga_cap_a2', cefr_level='A2')
        SkillScore.objects.create(user=user, skill=s, mode='writing', score=4)
        cap_placement_mastery(user, 'B1')
        assert SkillScore.objects.get(user=user, skill=s).score == 3

    @pytest.mark.django_db
    def test_two_levels_below_keeps_its_four(self, make_user, make_skill):
        """A genuine B2 must not be retested on A1 material they obviously have."""
        from learner.models import SkillScore
        from engine.onboarding import cap_placement_mastery
        user = make_user(discord_id='ga_cap3')
        s = make_skill(skill_id='ga_cap_a1', cefr_level='A1')
        SkillScore.objects.create(user=user, skill=s, mode='writing', score=4)
        cap_placement_mastery(user, 'B1')
        assert SkillScore.objects.get(user=user, skill=s).score == 4

    @pytest.mark.django_db
    def test_scores_below_four_are_untouched(self, make_user, make_skill):
        from learner.models import SkillScore
        from engine.onboarding import cap_placement_mastery
        user = make_user(discord_id='ga_cap4')
        s = make_skill(skill_id='ga_cap_partial', cefr_level='B1')
        SkillScore.objects.create(user=user, skill=s, mode='writing', score=2)
        cap_placement_mastery(user, 'B1')
        assert SkillScore.objects.get(user=user, skill=s).score == 2


class TestFeedbackIsNotAWrongAnswer:
    """The classifier read "Feedback: broken..." as a student who did not know,
    and supplied the answer. Deterministic knowledge must not be re-inferred."""

    def test_the_override_carries_what_the_code_already_determined(self):
        from engine.teach_drill import classify_first_check, note_explicit_feedback
        try:
            note_explicit_feedback(False)
            assert 'DETERMINISTIC OVERRIDE' not in classify_first_check()

            note_explicit_feedback(True)
            block = classify_first_check()
            assert 'DETERMINISTIC OVERRIDE' in block
            assert 'Do NOT supply the answer' in block
            assert 're-ask the SAME pending question' in block
        finally:
            note_explicit_feedback(False)
