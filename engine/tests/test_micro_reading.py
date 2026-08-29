"""The reception channel.

Micro-reading exists because nothing else in the system has the student
comprehend language they did not produce. The guarantees worth protecting are
that it seeds real due words, that it is complete after one message, and that a
failed generation cannot be mistaken for an empty one.
"""
import pytest
from unittest.mock import AsyncMock, patch

from engine import micro_reading
from engine.micro_reading import PassageGenerationFailed, _question_language
from learner.models import LexItem, User, UserWord


@pytest.fixture
def overdue_gym(user, db):
    from datetime import timedelta
    from django.utils import timezone
    item = LexItem.objects.create(es='gimnasio', en='gym', cefr_level='A1')
    return UserWord.objects.create(
        user=user, item=item, next_due_at=timezone.now() - timedelta(days=1))


@pytest.fixture
def user(db):
    return User.objects.create(
        discord_id='mr1', display_name='Eric', onboarding_complete=True,
        estimated_cefr_level='A1', interests='Goes to the gym. Drinks coffee.')


class TestFailureIsNotEmptiness:
    @pytest.mark.asyncio
    async def test_an_empty_passage_raises(self, user):
        with patch('engine.micro_reading.call_llm', new_callable=AsyncMock) as llm:
            llm.return_value = '   '
            with pytest.raises(PassageGenerationFailed):
                await micro_reading.build_passage(user, [])

    @pytest.mark.asyncio
    async def test_an_empty_question_raises(self, user):
        with patch('engine.micro_reading.call_llm', new_callable=AsyncMock) as llm:
            llm.return_value = ''
            with pytest.raises(PassageGenerationFailed):
                await micro_reading.build_question(user, 'Voy al gimnasio.')


class TestSeeding:
    @pytest.mark.asyncio
    async def test_selected_words_reach_the_generator(self, user, db):
        """micro_reading's own contract: whatever selection hands it goes into
        the prompt. Which words are DUE is vocabulary's contract and is tested
        there -- asserting it here would need a live connection, because
        sync_to_async runs on a different one and cannot see the test's
        uncommitted transaction."""
        gym = LexItem(es='gimnasio', en='gym', cefr_level='A1')
        with patch('engine.vocabulary._select', return_value={'due': [gym], 'new': []}), \
             patch('engine.micro_reading.call_llm', new_callable=AsyncMock) as llm:
            llm.return_value = 'Voy al gimnasio todos los días.'
            result = await micro_reading.open_reading(user)

        assert 'gimnasio' in result['seeded']
        prompt = llm.await_args_list[0][0][0][0]['content']
        assert 'gimnasio' in prompt

    @pytest.mark.asyncio
    async def test_the_persons_interests_reach_the_prompt(self, user, db):
        with patch('engine.micro_reading.call_llm', new_callable=AsyncMock) as llm:
            llm.return_value = 'Texto.'
            await micro_reading.open_reading(user)
        prompt = llm.await_args_list[0][0][0][0]['content']
        assert 'gym' in prompt


class TestCompleteAfterOneMessage:
    """Median session is a couple of turns. The passage has to be worth reading
    even if the student never answers the question."""

    @pytest.mark.asyncio
    async def test_passage_and_question_arrive_together(self, user, db):
        with patch('engine.micro_reading.call_llm', new_callable=AsyncMock) as llm:
            llm.side_effect = ['Voy al gimnasio.', '¿Adónde va?']
            result = await micro_reading.open_reading(user)
        assert result['text'].startswith('Voy al gimnasio.')
        assert '¿Adónde va?' in result['text']


class TestQuestionLanguage:
    def test_beginners_are_asked_in_english(self):
        assert _question_language('A1') == 'English'
        assert _question_language('A2') == 'English'

    def test_intermediates_are_asked_in_spanish(self):
        assert _question_language('B1') == 'Spanish'
        assert _question_language('C1') == 'Spanish'

    def test_an_unknown_level_defaults_to_english(self):
        assert _question_language('') == 'English'


class TestNoSkillRow:
    """Shaped as a task under a constraint rather than a lesson with a skill_id,
    so it still works above B2 where "the next unscored skill" has no meaning.

    Asserted by BEHAVIOUR, not by grepping the source. An earlier version of this
    test searched the module text for "skill_id" and passed only because the word
    appeared in a docstring -- a guard that cannot fail is not a guard."""

    @pytest.mark.asyncio
    async def test_it_runs_with_no_skills_in_the_curriculum_at_all(self, user, db):
        from asgiref.sync import sync_to_async
        from learner.models import Skill
        assert await sync_to_async(Skill.objects.count)() == 0

        with patch('engine.micro_reading.call_llm', new_callable=AsyncMock) as llm:
            llm.side_effect = ['Voy al gimnasio.', '¿Adónde va?']
            result = await micro_reading.open_reading(user)

        assert result['passage'] == 'Voy al gimnasio.'

