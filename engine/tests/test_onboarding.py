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


# ── Menu: "start over" ───────────────────────────────────────────────────────

class TestMenuStartOver:
    """The 'start over' branch of the placement-quiz menu.

    Regression guard: this branch used to delete by discord_id, which is NULL
    for every Messenger user — so Django turned it into `WHERE discord_id IS
    NULL` and one Messenger user typing '1' wiped every Messenger user in the
    database.
    """

    @pytest.mark.django_db(transaction=True)
    async def test_start_over_does_not_delete_unrelated_messenger_users(self):
        from engine.onboarding import _step_adaptive_quiz
        from learner.models import Session, SessionEvent, User

        bystander = await sync_to_async(User.objects.create)(
            messenger_psid='psid_bystander', display_name='Bystander',
            estimated_cefr_level='B1', onboarding_complete=True,
        )
        user = await sync_to_async(User.objects.create)(
            messenger_psid='psid_resetter', display_name='Resetter',
        )
        session = await sync_to_async(Session.objects.create)(
            user=user, session_type='onboarding',
        )
        await sync_to_async(SessionEvent.objects.create)(
            session=session, event_type='menu', content='menu', user_response='',
        )

        await _step_adaptive_quiz(user, '1')

        assert await sync_to_async(User.objects.filter(pk=bystander.pk).exists)(), \
            'start over deleted an unrelated Messenger user'

    @pytest.mark.django_db(transaction=True)
    async def test_start_over_clears_the_users_learning_state(self):
        from engine.onboarding import _step_adaptive_quiz
        from learner.models import Session, SessionEvent, User

        user = await sync_to_async(User.objects.create)(
            messenger_psid='psid_state', display_name='Ana',
            estimated_cefr_level='B1', onboarding_complete=True,
            instruction_language='english', interests='fútbol, cocina',
        )
        session = await sync_to_async(Session.objects.create)(
            user=user, session_type='onboarding',
        )
        await sync_to_async(SessionEvent.objects.create)(
            session=session, event_type='menu', content='menu', user_response='',
        )

        await _step_adaptive_quiz(user, '1')

        refreshed = await sync_to_async(User.objects.get)(pk=user.pk)
        assert refreshed.display_name == ''
        assert refreshed.estimated_cefr_level == ''
        assert refreshed.onboarding_complete is False
        assert refreshed.instruction_language == 'auto'
        assert refreshed.interests == ''

    @pytest.mark.django_db(transaction=True)
    async def test_start_over_preserves_the_row_and_its_platform_identity(self):
        """Guard against reverting to delete-and-recreate: that path rebuilt the
        row from discord_id, so a Messenger user lost their PSID and their pk
        (invalidating any outstanding magic link)."""
        from engine.onboarding import _step_adaptive_quiz
        from learner.models import Session, SessionEvent, User

        user = await sync_to_async(User.objects.create)(
            messenger_psid='psid_identity', display_name='Ana',
        )
        session = await sync_to_async(Session.objects.create)(
            user=user, session_type='onboarding',
        )
        await sync_to_async(SessionEvent.objects.create)(
            session=session, event_type='menu', content='menu', user_response='',
        )

        await _step_adaptive_quiz(user, '1')

        survivors = await sync_to_async(
            lambda: list(User.objects.filter(messenger_psid='psid_identity'))
        )()
        assert len(survivors) == 1
        assert survivors[0].pk == user.pk

    @pytest.mark.django_db(transaction=True)
    async def test_start_over_discards_sessions_scores_and_interests(self, make_skill):
        """Deleting the row used to cascade all of this away. In-place reset has
        to do it explicitly or !reset silently stops wiping progress."""
        from engine.onboarding import _step_adaptive_quiz
        from learner.models import (
            Session, SessionEvent, SkillScore, User, UserInterest,
        )

        user = await sync_to_async(User.objects.create)(
            messenger_psid='psid_cascade', display_name='Ana',
        )
        skill = await sync_to_async(make_skill)(skill_id='a1_cascade', cefr_level='A1')
        await sync_to_async(SkillScore.objects.create)(
            user=user, skill=skill, mode='writing', score=3,
        )
        await sync_to_async(UserInterest.objects.create)(
            user=user, topic='fútbol', category='sport',
        )
        session = await sync_to_async(Session.objects.create)(
            user=user, session_type='onboarding',
        )
        await sync_to_async(SessionEvent.objects.create)(
            session=session, event_type='menu', content='menu', user_response='',
        )

        await _step_adaptive_quiz(user, '1')

        assert not await sync_to_async(SkillScore.objects.filter(user=user).exists)()
        assert not await sync_to_async(UserInterest.objects.filter(user=user).exists)()
        assert not await sync_to_async(Session.objects.filter(user=user).exists)()


class TestFirstMessage:
    def test_introduces_the_assessment_and_still_asks_for_a_name(self):
        """FIRST_MESSAGE is the de-facto greeting: Meta won't let a Page speak
        first, so this is the earliest thing a stranger from the website can
        see. It has to set up the assessment AND ask for a name — the very next
        message is stored verbatim as display_name by _step_collect_name, so a
        greeting ending in "Ready to start?" would name the user "Yes"."""
        from engine.onboarding import FIRST_MESSAGE
        t = FIRST_MESSAGE.lower()
        assert 'luz ángela' in t
        assert 'assessment' in t
        assert 'name' in t or 'call you' in t, (
            'FIRST_MESSAGE must end by asking for a name; the next message is '
            'stored verbatim as display_name'
        )
