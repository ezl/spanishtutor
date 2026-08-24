import pytest
from unittest.mock import patch, AsyncMock
from asgiref.sync import sync_to_async


class TestExplicitFeedbackDetection:
    """The developer prefixes feedback with the literal word. All 3 messages that
    were lost on 08-22/23 did, and so did 8 of the 9 that were captured."""

    @pytest.mark.parametrize("text", [
        "Feedback: the last time it said 2nd pass, this would be 3rd.",
        "Feedback note. In this turn it was great how it handled me not knowing.",
        "Feedback idea: I've noticed that we haven't gained more user knowledge.",
        "Feedback - verb conjugation structure isn't what we agreed",
        "feedback for later, no need to change now",
        "One piece of feedback: the cue was ambiguous",
    ])
    def test_explicit_feedback_is_detected(self, text):
        from engine.feedback import looks_like_explicit_feedback
        assert looks_like_explicit_feedback(text) is True

    @pytest.mark.parametrize("text", [
        "Yo tuve un dolor de cabeza",
        "Listo",
        "no sé",
        "",
        "   ",
        "Que significa clave en esta oración?",
    ])
    def test_ordinary_lesson_traffic_is_not(self, text):
        from engine.feedback import looks_like_explicit_feedback
        assert looks_like_explicit_feedback(text) is False

    def test_substring_does_not_count(self):
        """Whole-word only — a drill answer must never be logged as feedback."""
        from engine.feedback import looks_like_explicit_feedback
        assert looks_like_explicit_feedback("feedbacks are feedbacking") is False


@pytest.mark.django_db(transaction=True)
class TestRecordFeedbackIsIdempotent:
    """Two capture layers can see the same message — the deterministic one and
    the inline classifier. The same complaint must not be logged twice."""

    async def _session(self, make_user, make_skill, uid):
        from learner.models import Session
        user = await sync_to_async(make_user)(discord_id=uid, cefr_level='B1')
        skill = await sync_to_async(make_skill)(skill_id=uid + '_sk')
        return user, await sync_to_async(Session.objects.create)(
            user=user, session_type='new_skill', target_skill=skill)

    @pytest.mark.asyncio
    async def test_writes_a_row(self, make_user, make_skill):
        from engine.feedback import record_feedback
        from learner.models import SessionFeedback
        user, session = await self._session(make_user, make_skill, 'fb_w')

        await record_feedback(session, None, "Feedback: too fast", "Student says pacing is too fast")

        rows = await sync_to_async(lambda: list(SessionFeedback.objects.filter(session=session)))()
        assert len(rows) == 1
        assert rows[0].interpretation == "Student says pacing is too fast"

    @pytest.mark.asyncio
    async def test_same_message_twice_writes_once(self, make_user, make_skill):
        from engine.feedback import record_feedback
        from learner.models import SessionFeedback
        user, session = await self._session(make_user, make_skill, 'fb_d')

        await record_feedback(session, None, "Feedback: too fast", "first")
        await record_feedback(session, None, "Feedback: too fast", "second")

        count = await sync_to_async(
            lambda: SessionFeedback.objects.filter(session=session).count())()
        assert count == 1


@pytest.mark.django_db(transaction=True)
class TestCaptureWorksInEverySessionType:
    """Capture lived only inside teach_drill, so feedback typed during an SRS
    review had nowhere to go — that is how the 08-23 20:26 message was lost."""

    async def _open(self, make_user, make_skill, uid, session_type):
        from learner.models import Session, SessionEvent
        user = await sync_to_async(make_user)(discord_id=uid, cefr_level='B1')
        skill = await sync_to_async(make_skill)(skill_id=uid + '_sk')
        session = await sync_to_async(Session.objects.create)(
            user=user, session_type=session_type, target_skill=skill,
            current_phase='review',
        )
        await sync_to_async(SessionEvent.objects.create)(
            session=session, event_type='quiz', content='Quick review: comer, imperfect?',
            user_response='')
        return user, session

    @pytest.mark.asyncio
    async def test_feedback_during_srs_review_is_captured(self, make_user, make_skill):
        from engine.session import handle_session
        from learner.models import SessionFeedback

        user, session = await self._open(make_user, make_skill, 'fb_srs', 'srs_review')
        reply = {"text": "ok", "audio_url": None, "session_ended": False}
        with patch('engine.session._continue_srs_review', new=AsyncMock(return_value=reply)):
            await handle_session(user, "Feedback: 2 questions isn't enough for a review")

        rows = await sync_to_async(lambda: list(SessionFeedback.objects.filter(session=session)))()
        assert len(rows) == 1
        assert "2 questions" in rows[0].user_message

    @pytest.mark.asyncio
    async def test_the_lesson_still_proceeds(self, make_user, make_skill):
        """Feedback is orthogonal — logging it must not swallow the turn."""
        from engine.session import handle_session

        user, session = await self._open(make_user, make_skill, 'fb_cont', 'srs_review')
        reply = {"text": "LESSON CONTINUES", "audio_url": None, "session_ended": False}
        with patch('engine.session._continue_srs_review', new=AsyncMock(return_value=reply)):
            result = await handle_session(user, "Feedback: too fast please slow down")

        assert result["text"] == "LESSON CONTINUES"

    @pytest.mark.asyncio
    async def test_an_ordinary_answer_is_not_logged(self, make_user, make_skill):
        from engine.session import handle_session
        from learner.models import SessionFeedback

        user, session = await self._open(make_user, make_skill, 'fb_none', 'srs_review')
        reply = {"text": "ok", "audio_url": None, "session_ended": False}
        with patch('engine.session._continue_srs_review', new=AsyncMock(return_value=reply)):
            await handle_session(user, "Yo comía pan cada mañana")

        count = await sync_to_async(
            lambda: SessionFeedback.objects.filter(session=session).count())()
        assert count == 0

    @pytest.mark.asyncio
    async def test_the_anchor_is_the_turn_being_reacted_to(self, make_user, make_skill):
        from engine.session import handle_session
        from learner.models import SessionFeedback

        user, session = await self._open(make_user, make_skill, 'fb_anch', 'srs_review')
        reply = {"text": "ok", "audio_url": None, "session_ended": False}
        with patch('engine.session._continue_srs_review', new=AsyncMock(return_value=reply)):
            await handle_session(user, "Feedback: that cue was ambiguous")

        row = await sync_to_async(
            lambda: SessionFeedback.objects.select_related('anchor_event')
                                           .filter(session=session).first())()
        assert row.anchor_event is not None
        assert 'Quick review' in row.anchor_event.content


@pytest.mark.django_db(transaction=True)
class TestOfflineSweep:
    """The layer that catches a real user complaining without ever using the
    word 'feedback'. Reads SessionEvent rows, so it cannot be blind to a session
    type, and can be re-run over history when the detection prompt improves."""

    async def _turn(self, make_user, make_skill, uid, said, luz='Quick review: comer?'):
        from learner.models import Session, SessionEvent
        user = await sync_to_async(make_user)(discord_id=uid, cefr_level='B1')
        skill = await sync_to_async(make_skill)(skill_id=uid + '_sk')
        session = await sync_to_async(Session.objects.create)(
            user=user, session_type='srs_review', target_skill=skill)
        ev = await sync_to_async(SessionEvent.objects.create)(
            session=session, event_type='quiz', content=luz, user_response=said)
        return session, ev

    @pytest.mark.asyncio
    async def test_already_logged_turns_are_not_candidates(self, make_user, make_skill):
        from engine.feedback import sweep_candidates, record_feedback
        session, ev = await self._turn(make_user, make_skill, 'sw_dup', 'this is too hard')
        await record_feedback(session, ev, 'this is too hard', 'already known')

        cands = await sweep_candidates(days=3)
        assert all(c.user_response != 'this is too hard' for c in cands)

    @pytest.mark.asyncio
    async def test_unlogged_turns_are_candidates(self, make_user, make_skill):
        from engine.feedback import sweep_candidates
        await self._turn(make_user, make_skill, 'sw_new', 'why do you keep asking me this')

        cands = await sweep_candidates(days=3)
        assert any(c.user_response == 'why do you keep asking me this' for c in cands)

    @pytest.mark.asyncio
    async def test_sweep_logs_what_the_model_flags(self, make_user, make_skill):
        from engine.feedback import sweep
        from learner.models import SessionFeedback
        session, ev = await self._turn(
            make_user, make_skill, 'sw_hit', 'honestly this is really boring')

        payload = '[{"n": 0, "interpretation": "Student finds the drills boring."}]'
        with patch('engine.feedback.call_llm', new=AsyncMock(return_value=payload)):
            result = await sweep(days=3)

        rows = await sync_to_async(lambda: list(SessionFeedback.objects.filter(session=session)))()
        assert len(rows) == 1
        assert rows[0].interpretation == "Student finds the drills boring."
        assert result['created'] == 1

    @pytest.mark.asyncio
    async def test_sweep_ignores_ordinary_turns(self, make_user, make_skill):
        from engine.feedback import sweep
        from learner.models import SessionFeedback
        session, ev = await self._turn(make_user, make_skill, 'sw_miss', 'Yo comía pan')

        with patch('engine.feedback.call_llm', new=AsyncMock(return_value='[]')):
            await sweep(days=3)

        count = await sync_to_async(
            lambda: SessionFeedback.objects.filter(session=session).count())()
        assert count == 0

    @pytest.mark.asyncio
    async def test_dry_run_writes_nothing(self, make_user, make_skill):
        from engine.feedback import sweep
        from learner.models import SessionFeedback
        session, ev = await self._turn(make_user, make_skill, 'sw_dry', 'this is confusing')

        payload = '[{"n": 0, "interpretation": "Student is confused."}]'
        with patch('engine.feedback.call_llm', new=AsyncMock(return_value=payload)):
            result = await sweep(days=3, dry_run=True)

        count = await sync_to_async(
            lambda: SessionFeedback.objects.filter(session=session).count())()
        assert count == 0
        assert result['would_create'] == 1


@pytest.mark.django_db(transaction=True)
class TestSweepRunsOnSessionClose:
    """Session close is the trigger instead of a cron: it batches naturally,
    scales with usage, and needs no scheduler. It sweeps the recent WINDOW, not
    just the closing session, so an abandoned session that never closes still
    gets picked up when some other session does."""

    async def _stranded_turn(self, make_user, make_skill):
        """A turn in a session that is still open — the abandonment case."""
        from learner.models import Session, SessionEvent
        user = await sync_to_async(make_user)(discord_id='cl_open', cefr_level='B1')
        skill = await sync_to_async(make_skill)(skill_id='cl_open_sk')
        session = await sync_to_async(Session.objects.create)(
            user=user, session_type='new_skill', target_skill=skill)
        await sync_to_async(SessionEvent.objects.create)(
            session=session, event_type='quiz', content='Try this one',
            user_response='honestly this is really boring')
        return session

    async def _closing_session(self, make_user, make_skill):
        from learner.models import Session
        user = await sync_to_async(make_user)(discord_id='cl_close', cefr_level='B1')
        skill = await sync_to_async(make_skill)(skill_id='cl_close_sk')
        session = await sync_to_async(Session.objects.create)(
            user=user, session_type='new_skill', target_skill=skill)
        return user, session

    @pytest.mark.asyncio
    async def test_closing_a_session_sweeps_a_stranded_one(self, make_user, make_skill):
        from engine.session import _close_session_record
        from learner.models import SessionFeedback

        stranded = await self._stranded_turn(make_user, make_skill)
        user, closing = await self._closing_session(make_user, make_skill)

        payload = '[{"n": 0, "interpretation": "Student finds the drills boring."}]'
        with patch('engine.scoring.score_session', new=AsyncMock(return_value=[])), \
             patch('engine.interests.extract_and_store_interests', new=AsyncMock()), \
             patch('engine.feedback.call_llm', new=AsyncMock(return_value=payload)):
            await _close_session_record(closing, user)

        rows = await sync_to_async(
            lambda: list(SessionFeedback.objects.filter(session=stranded)))()
        assert len(rows) == 1, "a still-open session's turns must still be swept"

    @pytest.mark.asyncio
    async def test_a_sweep_failure_does_not_break_the_close(self, make_user, make_skill):
        from engine.session import _close_session_record
        from learner.models import Session

        user, closing = await self._closing_session(make_user, make_skill)

        with patch('engine.scoring.score_session', new=AsyncMock(return_value=[])), \
             patch('engine.interests.extract_and_store_interests', new=AsyncMock()), \
             patch('engine.feedback.sweep', new=AsyncMock(side_effect=RuntimeError('boom'))):
            await _close_session_record(closing, user)

        reloaded = await sync_to_async(Session.objects.get)(pk=closing.pk)
        assert reloaded.ended_at is not None, "close must survive a sweep failure"
