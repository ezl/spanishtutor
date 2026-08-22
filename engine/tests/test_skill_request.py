import pytest
from unittest.mock import patch, AsyncMock
from asgiref.sync import sync_to_async


class TestLooksLikeSkillRequest:
    """Deterministic gate. It runs on every message in every phase, so a false
    positive would hijack a lesson — it must be conservative."""

    @pytest.mark.parametrize("text", [
        "I want to learn the subjunctive",
        "can we do the subjunctive instead",
        "teach me ser vs estar",
        "quiero aprender el subjuntivo",
        "let's do vocab instead",
        "next lesson please",
    ])
    def test_requests_are_detected(self, text):
        from engine.skill_request import looks_like_skill_request
        assert looks_like_skill_request(text) is True

    @pytest.mark.parametrize("text", [
        "Yo tuve un buen día",
        "fuiste al gimnasio",
        "ok",
        "hmm, got it",
        "why is it hizo not hico?",
        "",
        "   ",
    ])
    def test_lesson_traffic_is_not_a_request(self, text):
        from engine.skill_request import looks_like_skill_request
        assert looks_like_skill_request(text) is False


@pytest.fixture
def grid(make_skill):
    return [
        make_skill(skill_id='b1_subjunctive_formation',
                   name='Subjunctive — present formation', cefr_level='B1',
                   description='Forming the present subjunctive'),
        make_skill(skill_id='b1_subjunctive_triggers_doubt_emotion',
                   name='Subjunctive — doubt and emotion triggers', cefr_level='B1',
                   description='Dudo que, me alegra que'),
        make_skill(skill_id='b1_preterite_vs_imperfect',
                   name='Preterite vs. imperfect — contrast', cefr_level='B1',
                   description='Completed events vs background'),
    ]


async def _session_for(make_user, uid, grid):
    from learner.models import Session
    user = await sync_to_async(make_user)(discord_id=uid, cefr_level='B1')
    session = await sync_to_async(Session.objects.create)(
        user=user, session_type='new_skill', target_skill=grid[2],
        current_phase='teach_drill',
    )
    return user, session


@pytest.mark.django_db(transaction=True)
class TestHandleSkillRequest:

    @pytest.mark.asyncio
    async def test_unique_request_ends_the_lesson_and_starts_the_skill(
            self, make_user, grid):
        from engine.skill_request import handle_skill_request
        from learner.models import Session

        user, session = await _session_for(make_user, 'sr_uniq', grid)
        with patch('engine.teach_drill.call_llm',
                   new=AsyncMock(return_value='[{"id":"u","label":"u","note":""}]')), \
             patch('engine.session.call_llm', new=AsyncMock(return_value='OPENING')):
            await handle_skill_request(user, session, "teach me the subjunctive formation")

        old = await sync_to_async(Session.objects.get)(pk=session.pk)
        assert old.ended_at is not None, "the requested lesson must terminate the old one"

        newest = await sync_to_async(
            lambda: Session.objects.filter(user=user).exclude(pk=session.pk)
                                   .select_related('target_skill').order_by('-id').first())()
        assert newest.target_skill.skill_id == 'b1_subjunctive_formation'

    @pytest.mark.asyncio
    async def test_ambiguous_request_asks_instead_of_guessing(self, make_user, grid):
        from engine.skill_request import handle_skill_request
        from learner.models import Session

        user, session = await _session_for(make_user, 'sr_amb', grid)
        result = await handle_skill_request(user, session, "I want to learn subjunctive")

        assert "formation" in result["text"].lower()
        assert "doubt" in result["text"].lower()

        reloaded = await sync_to_async(Session.objects.get)(pk=session.pk)
        assert reloaded.current_phase == 'skill_request'
        assert reloaded.ended_at is None, "don't kill the lesson until they choose"
        parked = reloaded.quiz_state['skill_request']['candidates']
        assert set(parked) == {'b1_subjunctive_formation',
                               'b1_subjunctive_triggers_doubt_emotion'}

    @pytest.mark.asyncio
    async def test_unknown_topic_is_answered_honestly(self, make_user, grid):
        """No invented pedagogical excuse — say it isn't in the curriculum."""
        from engine.skill_request import handle_skill_request

        user, session = await _session_for(make_user, 'sr_unk', grid)
        with patch('engine.skill_request.call_llm', new=AsyncMock(return_value='NONE')):
            result = await handle_skill_request(user, session, "teach me Klingon")

        assert result["text"]
        assert result.get("session_ended") is False


@pytest.mark.django_db(transaction=True)
class TestResolvePendingRequest:

    @pytest.mark.asyncio
    async def test_reply_picks_the_candidate_and_starts_it(self, make_user, grid):
        from engine.skill_request import handle_skill_request, resolve_pending_request
        from learner.models import Session

        user, session = await _session_for(make_user, 'sr_res', grid)
        await handle_skill_request(user, session, "I want to learn subjunctive")
        session = await sync_to_async(Session.objects.get)(pk=session.pk)

        with patch('engine.teach_drill.call_llm',
                   new=AsyncMock(return_value='[{"id":"u","label":"u","note":""}]')), \
             patch('engine.session.call_llm', new=AsyncMock(return_value='OPENING')):
            await resolve_pending_request(user, session, "formation")

        newest = await sync_to_async(
            lambda: Session.objects.filter(user=user).exclude(pk=session.pk)
                                   .select_related('target_skill').order_by('-id').first())()
        assert newest.target_skill.skill_id == 'b1_subjunctive_formation'

    @pytest.mark.asyncio
    async def test_unresolvable_reply_asks_again(self, make_user, grid):
        from engine.skill_request import handle_skill_request, resolve_pending_request
        from learner.models import Session

        user, session = await _session_for(make_user, 'sr_res2', grid)
        await handle_skill_request(user, session, "I want to learn subjunctive")
        session = await sync_to_async(Session.objects.get)(pk=session.pk)

        result = await resolve_pending_request(user, session, "uhh")

        reloaded = await sync_to_async(Session.objects.get)(pk=session.pk)
        assert reloaded.current_phase == 'skill_request'
        assert result["text"]


class TestPickCandidate:
    CANDS = [{'id': 'b1_subjunctive_formation', 'name': 'Subjunctive — present formation'},
             {'id': 'b1_subjunctive_triggers_doubt_emotion', 'name': 'Subjunctive — doubt triggers'}]

    def test_number_selects_by_position(self):
        """The clarifying question is a numbered list, so '2' must work."""
        from engine.skill_request import pick_candidate
        assert pick_candidate(self.CANDS, "2")['id'] == 'b1_subjunctive_triggers_doubt_emotion'

    def test_number_out_of_range_is_unresolved(self):
        from engine.skill_request import pick_candidate
        assert pick_candidate(self.CANDS, "7") is None

    def test_name_still_works(self):
        from engine.skill_request import pick_candidate
        assert pick_candidate(self.CANDS, "formation")['id'] == 'b1_subjunctive_formation'

    def test_ambiguous_reply_is_unresolved(self):
        from engine.skill_request import pick_candidate
        assert pick_candidate(self.CANDS, "subjunctive") is None
