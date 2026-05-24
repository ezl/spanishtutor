"""
Tests for engine/onboarding.py
"""
import pytest
from asgiref.sync import sync_to_async


# ── Area 3: _classify_input ──────────────────────────────────────────────────

class TestClassifyInput:
    def test_dont_know_phrases(self):
        from engine.onboarding import _classify_input
        assert _classify_input("I don't know") == 'dont_know'
        assert _classify_input("idk") == 'dont_know'
        assert _classify_input("no sé") == 'dont_know'

    def test_rage_words(self):
        from engine.onboarding import _classify_input
        assert _classify_input("this is stupid") == 'rage'
        assert _classify_input("wtf is this") == 'rage'

    def test_long_question_is_off_topic(self):
        from engine.onboarding import _classify_input
        assert _classify_input("What does irregular verb conjugation mean?") == 'off_topic'

    def test_regular_answer(self):
        from engine.onboarding import _classify_input
        assert _classify_input("soy estudiante") == 'answer'
        assert _classify_input("a") == 'answer'

    def test_short_question_is_answer(self):
        """Questions under 15 chars are treated as answers, not off-topic."""
        from engine.onboarding import _classify_input
        assert _classify_input("¿Qué?") == 'answer'


# ── _step_adaptive_quiz integration tests ────────────────────────────────────

@pytest.mark.django_db(transaction=True)
async def test_step_adaptive_quiz_first_question(make_user, make_skill):
    """First question is drawn from the bank, state saved, quiz event created."""
    from engine.onboarding import _step_adaptive_quiz, QUIZ_START_SENTINEL
    from learner.models import Session, SessionEvent, QuizQuestion

    user = await sync_to_async(make_user)(discord_id='u_aq1', display_name='Ana')
    skill = await sync_to_async(make_skill)(skill_id='a1_ser_estar_basic', cefr_level='A1', order=0)
    q = await sync_to_async(QuizQuestion.objects.create)(
        skill=skill, format='multiple_choice',
        question_text='What does "ser" mean?',
        options={'a': 'to be', 'b': 'to have', 'c': 'to go', 'd': 'to see'},
        correct_answer='a', active=True,
    )

    result = await _step_adaptive_quiz(user, QUIZ_START_SENTINEL)

    assert result['text'], "Should return formatted question text"
    assert 'to be' in result['text'], "MC options should be in the response"

    session = await sync_to_async(
        Session.objects.filter(user=user, session_type='onboarding').first
    )()
    assert session is not None
    assert session.quiz_state is not None
    assert session.quiz_state['current_question_id'] == q.pk

    quiz_event = await sync_to_async(
        SessionEvent.objects.filter(session=session, event_type='quiz').first
    )()
    assert quiz_event is not None
    assert quiz_event.user_response == ''


@pytest.mark.django_db(transaction=True)
async def test_step_adaptive_quiz_evaluates_and_saves_skill_score(make_user, make_skill):
    """Submitting a correct answer evaluates it via evaluate_answer and saves SkillScore."""
    from unittest.mock import patch, AsyncMock
    from engine.onboarding import _step_adaptive_quiz
    from learner.models import Session, SessionEvent, SkillScore, QuizQuestion
    from engine.quiz_flow import quiz_initial_state

    user = await sync_to_async(make_user)(discord_id='u_aq2', display_name='Ana')
    skill = await sync_to_async(make_skill)(skill_id='a1_ser_estar_basic', cefr_level='A1', order=0)
    q = await sync_to_async(QuizQuestion.objects.create)(
        skill=skill, format='multiple_choice',
        question_text='What does "ser" mean?',
        correct_answer='a', options={'a': 'to be', 'b': 'to have'}, active=True,
    )

    state = quiz_initial_state(1)
    state['current_question_id'] = q.pk
    state['current_skill_idx'] = 0
    session = await sync_to_async(Session.objects.create)(
        user=user, session_type='onboarding', quiz_state=state,
    )
    await sync_to_async(SessionEvent.objects.create)(
        session=session, event_type='quiz', dimension='writing',
        content='What does "ser" mean?', user_response='',
    )

    dummy_conclude = AsyncMock(return_value={"text": "done", "audio_url": None, "session_ended": False})

    with patch('engine.quiz_evaluator.evaluate_answer', new=AsyncMock(return_value=4)):
        with patch('engine.onboarding._conclude_quiz', new=dummy_conclude):
            await _step_adaptive_quiz(user, 'a')

    score = await sync_to_async(
        SkillScore.objects.filter(user=user, skill=skill, mode='writing').first
    )()
    assert score is not None, "SkillScore should be created after answering"
    assert score.score == 4


@pytest.mark.django_db(transaction=True)
async def test_step_adaptive_quiz_dont_know_scores_1(make_user, make_skill):
    """'I don't know' skips evaluate_answer and saves score 1."""
    from unittest.mock import patch, AsyncMock
    from engine.onboarding import _step_adaptive_quiz
    from learner.models import Session, SessionEvent, SkillScore, QuizQuestion
    from engine.quiz_flow import quiz_initial_state

    user = await sync_to_async(make_user)(discord_id='u_aq5', display_name='Ana')
    skill = await sync_to_async(make_skill)(skill_id='a1_greetings', cefr_level='A1', order=0)
    q = await sync_to_async(QuizQuestion.objects.create)(
        skill=skill, format='multiple_choice',
        question_text='What does "hola" mean?',
        correct_answer='a', options={'a': 'hello', 'b': 'goodbye'}, active=True,
    )

    state = quiz_initial_state(1)
    state['current_question_id'] = q.pk
    state['current_skill_idx'] = 0
    session = await sync_to_async(Session.objects.create)(
        user=user, session_type='onboarding', quiz_state=state,
    )
    await sync_to_async(SessionEvent.objects.create)(
        session=session, event_type='quiz', dimension='writing',
        content='What does "hola" mean?', user_response='',
    )

    dummy_conclude = AsyncMock(return_value={"text": "done", "audio_url": None, "session_ended": False})

    with patch('engine.quiz_evaluator.evaluate_answer', new=AsyncMock(return_value=4)) as mock_eval:
        with patch('engine.onboarding._conclude_quiz', new=dummy_conclude):
            await _step_adaptive_quiz(user, "I don't know")

    mock_eval.assert_not_called()
    score = await sync_to_async(
        SkillScore.objects.filter(user=user, skill=skill, mode='writing').first
    )()
    assert score is not None
    assert score.score == 1


@pytest.mark.django_db(transaction=True)
async def test_step_adaptive_quiz_concludes_when_done(make_user, make_skill):
    """After the 20th answer, _conclude_quiz is called."""
    from unittest.mock import patch, AsyncMock
    from engine.onboarding import _step_adaptive_quiz
    from learner.models import Session, SessionEvent, QuizQuestion
    from engine.quiz_flow import quiz_initial_state

    user = await sync_to_async(make_user)(discord_id='u_aq3', display_name='Ana')
    skill = await sync_to_async(make_skill)(skill_id='a1_ser_estar_basic', cefr_level='A1', order=0)
    q = await sync_to_async(QuizQuestion.objects.create)(
        skill=skill, format='multiple_choice',
        question_text='What does "ser" mean?',
        correct_answer='a', options={'a': 'to be', 'b': 'to have'}, active=True,
    )

    # question_count=19: after scoring one more → 20 → done
    state = quiz_initial_state(1)
    state['current_question_id'] = q.pk
    state['current_skill_idx'] = 0
    state['question_count'] = 19
    session = await sync_to_async(Session.objects.create)(
        user=user, session_type='onboarding', quiz_state=state,
    )
    await sync_to_async(SessionEvent.objects.create)(
        session=session, event_type='quiz', dimension='writing',
        content='What does "ser" mean?', user_response='',
    )

    mock_conclude = AsyncMock(
        return_value={"text": "📊 **Your Spanish level: A1**", "audio_url": None, "session_ended": False}
    )

    with patch('engine.quiz_evaluator.evaluate_answer', new=AsyncMock(return_value=4)):
        with patch('engine.onboarding._conclude_quiz', new=mock_conclude):
            result = await _step_adaptive_quiz(user, 'a')

    assert mock_conclude.called, "_conclude_quiz must be called when question_count reaches 20"
    # The state passed to _conclude_quiz should reflect question_count=20
    call_kwargs = mock_conclude.call_args
    passed_state = call_kwargs.args[2] if call_kwargs.args else call_kwargs.kwargs.get('quiz_state')
    assert passed_state['question_count'] == 20
