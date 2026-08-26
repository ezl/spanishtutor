"""Selection and rendering of a lesson's vocabulary.

The guarantee worth protecting here is that there is no review debt: an overdue
backlog larger than the slot budget must produce an ordinary lesson, not a
punishment. That is the failure that makes people abandon spaced-repetition
systems, and it is easy to reintroduce by accident.
"""
import pytest
from datetime import timedelta
from django.utils import timezone

from engine.vocabulary import _select, render_block, load_settings, pack_for_skill
from learner.models import LexItem, User, UserWord


@pytest.fixture
def user(db):
    return User.objects.create(discord_id='v1', display_name='Eric',
                               onboarding_complete=True, estimated_cefr_level='A1')


def _word(es, **kw):
    return LexItem.objects.create(es=es, en=kw.pop('en', es), **kw)


def _due(user, item, minutes_ago):
    return UserWord.objects.create(
        user=user, item=item,
        next_due_at=timezone.now() - timedelta(minutes=minutes_ago))


class TestNoReviewDebt:
    def test_a_huge_backlog_yields_an_ordinary_lesson(self, user):
        for n in range(60):
            _due(user, _word(f'w{n}'), minutes_ago=n)
        selected = _select(user)
        assert len(selected['due']) == load_settings()['recycled_words_per_lesson']

    def test_the_most_overdue_words_come_first(self, user):
        _due(user, _word('recent'), minutes_ago=1)
        _due(user, _word('ancient'), minutes_ago=99999)
        assert _select(user)['due'][0].es == 'ancient'

    def test_nothing_due_yet_is_not_selected(self, user):
        item = _word('futuro')
        UserWord.objects.create(user=user, item=item,
                                next_due_at=timezone.now() + timedelta(days=3))
        assert _select(user)['due'] == []


class TestGraduation:
    def test_known_words_stop_consuming_slots(self, user):
        graduated = _due(user, _word('dominado'), minutes_ago=500)
        graduated.state = 'known'
        graduated.save()
        _due(user, _word('pendiente'), minutes_ago=1)
        due = _select(user)['due']
        assert [i.es for i in due] == ['pendiente']


class TestNewWords:
    def test_already_taught_words_are_never_introduced_again(self, user):
        seen, unseen = _word('visto'), _word('nuevo')
        UserWord.objects.create(user=user, item=seen)
        assert seen not in _select(user)['new']

    def test_another_users_words_are_not_offered(self, user):
        other = User.objects.create(discord_id='v2', display_name='Ana')
        _word('suya', owner=other)
        assert 'suya' not in [i.es for i in _select(user)['new']]

    def test_your_own_interest_words_are_offered(self, user):
        _word('pesas', owner=user, packs='interest:gym')
        assert 'pesas' in [i.es for i in _select(user)['new']]

    def test_a_vocabulary_lesson_draws_from_its_own_pack(self, user, db):
        from django.core.management import call_command
        call_command('sync_lexicon')
        selected = _select(user, skill_id='a1_place_words')
        assert selected['new'], 'the places pack should supply words'
        assert all('places_everyday' in i.pack_list for i in selected['new'])

    def test_a_vocabulary_lesson_gets_the_larger_budget(self, user, db):
        from django.core.management import call_command
        call_command('sync_lexicon')
        conf = load_settings()
        assert conf['new_words_per_vocab_lesson'] > conf['new_words_per_lesson']
        grammar = _select(user, skill_id='a1_present_ar')
        assert len(grammar['new']) == conf['new_words_per_lesson']


class TestRendering:
    def test_nothing_to_say_renders_nothing(self):
        assert render_block([], []) == ''

    def test_chunks_are_flagged_as_unbreakable(self, db):
        chunk = _word('me llamo', en='my name is', analyzable=False)
        assert 'FIXED CHUNK' in render_block([], [chunk])

    def test_ordinary_words_are_not_flagged(self, db):
        assert 'FIXED CHUNK' not in render_block([], [_word('casa', en='house')])

    def test_nouns_render_with_their_article(self, db):
        block = render_block([], [_word('casa', en='house', pos='noun', gender='f')])
        assert 'la casa' in block

    def test_review_words_are_not_announced_as_review(self, db):
        block = render_block([_word('viejo', en='old')], [])
        assert 'viejo' in block
        assert 'Do not announce' in block


class TestPackLookup:
    def test_a1_lessons_map_to_a_pack(self, db):
        assert pack_for_skill('a1_place_words') == 'places_everyday'

    def test_grammar_lessons_map_to_nothing(self, db):
        assert pack_for_skill('a1_present_ar') is None
