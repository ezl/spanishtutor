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


class TestRecordingExposure:
    """Turning a finished transcript into UserWord rows. No model call: the word
    list is known, so this is string matching."""

    @pytest.fixture
    def session(self, user, db):
        from learner.models import Session
        return Session.objects.create(user=user, session_type='new_skill')

    def _turn(self, session, tutor='', student=''):
        from learner.models import SessionEvent
        return SessionEvent.objects.create(
            session=session, event_type='conversation',
            content=tutor, user_response=student)

    def _record(self, session, user):
        from engine.vocabulary import _record
        return _record(session, user)

    def test_a_word_luz_used_is_seen_not_produced(self, session, user):
        _word('gimnasio', en='gym')
        self._turn(session, tutor='Vamos al gimnasio hoy.', student='ok')
        self._record(session, user)
        uw = UserWord.objects.get(user=user, item__es='gimnasio')
        assert (uw.times_seen, uw.times_produced) == (1, 0)

    def test_a_word_the_student_used_is_produced(self, session, user):
        _word('gimnasio', en='gym')
        self._turn(session, tutor='¿Adónde vas?', student='Voy al gimnasio.')
        self._record(session, user)
        uw = UserWord.objects.get(user=user, item__es='gimnasio')
        assert uw.times_produced == 1
        assert uw.state == 'practiced'

    def test_producing_outranks_seeing_in_the_same_session(self, session, user):
        """If both sides used it, the student produced it. That is the stronger
        signal and must not be downgraded by Luz also having said it."""
        _word('casa', en='house')
        self._turn(session, tutor='Mi casa es grande.', student='Mi casa también.')
        self._record(session, user)
        uw = UserWord.objects.get(user=user, item__es='casa')
        assert (uw.times_seen, uw.times_produced) == (0, 1)

    def test_accents_are_not_required_to_match(self, session, user):
        """Beginners type "como estas". Refusing that under-records exactly the
        students who most need the reinforcement."""
        _word('¿cómo estás?', en='how are you', analyzable=False)
        self._turn(session, tutor='', student='como estas')
        self._record(session, user)
        assert UserWord.objects.filter(user=user, item__es='¿cómo estás?').exists()

    def test_a_word_inside_a_longer_word_does_not_match(self, session, user):
        _word('casa', en='house')
        self._turn(session, tutor='Está casado.', student='')
        self._record(session, user)
        assert not UserWord.objects.filter(user=user, item__es='casa').exists()

    def test_multi_word_chunks_match_as_a_phrase(self, session, user):
        _word('me llamo', en='my name is', analyzable=False)
        self._turn(session, tutor='', student='Hola, me llamo Eric.')
        self._record(session, user)
        assert UserWord.objects.filter(user=user, item__es='me llamo').exists()

    def test_absent_words_are_left_alone(self, session, user):
        _word('gimnasio', en='gym')
        self._turn(session, tutor='Hola.', student='Hola.')
        result = self._record(session, user)
        assert UserWord.objects.filter(user=user).count() == 0
        assert result == {'seen': 0, 'produced': 0, 'failed': 0}

    def test_one_increment_per_session_however_often_it_appears(self, session, user):
        """'Produced in N separate sessions' has to mean sessions, not mentions."""
        _word('casa', en='house')
        self._turn(session, tutor='', student='casa')
        self._turn(session, tutor='', student='casa casa casa')
        self._record(session, user)
        assert UserWord.objects.get(user=user, item__es='casa').times_produced == 1

    def test_another_users_words_are_not_recorded(self, session, user):
        other = User.objects.create(discord_id='v9', display_name='Ana')
        _word('pesas', en='weights', owner=other)
        self._turn(session, tutor='Levanto pesas.', student='')
        self._record(session, user)
        assert UserWord.objects.filter(user=user).count() == 0

    def test_a_word_graduates_after_enough_productions(self, session, user):
        from engine.vocabulary import load_settings
        item = _word('casa', en='house')
        for _ in range(load_settings()['graduation_productions']):
            s2 = session.__class__.objects.create(user=user, session_type='new_skill')
            self._turn(s2, tutor='', student='casa')
            self._record(s2, user)
        uw = UserWord.objects.get(user=user, item=item)
        assert uw.state == 'known'
        assert uw.next_due_at is None

    def test_graduated_words_leave_the_selection_pool(self, session, user):
        """The whole point: the active set stays bounded as the corpus grows."""
        from engine.vocabulary import load_settings, _select
        item = _word('casa', en='house')
        for _ in range(load_settings()['graduation_productions']):
            s2 = session.__class__.objects.create(user=user, session_type='new_skill')
            self._turn(s2, tutor='', student='casa')
            self._record(s2, user)
        assert item not in _select(user)['due']

    def test_intervals_lengthen_with_strength(self, session, user):
        _word('casa', en='house')
        self._turn(session, tutor='Mi casa.', student='')
        self._record(session, user)
        first = UserWord.objects.get(user=user, item__es='casa').next_due_at

        s2 = session.__class__.objects.create(user=user, session_type='new_skill')
        self._turn(s2, tutor='Tu casa.', student='')
        self._record(s2, user)
        second = UserWord.objects.get(user=user, item__es='casa').next_due_at
        assert second > first


class TestFailureIsNotAnEmptyResult:
    def test_a_broken_matcher_raises_rather_than_reporting_nothing(self, user, db):
        """"No words appeared" and "the matcher blew up" must be distinguishable
        at the call site. That ambiguity is this repo's most reliable bug."""
        from unittest.mock import patch
        from learner.models import Session
        from engine.vocabulary import _record

        session = Session.objects.create(user=user, session_type='new_skill')
        with patch('engine.vocabulary._appears', side_effect=RuntimeError('boom')):
            _word('casa', en='house')
            with pytest.raises(RuntimeError):
                _record(session, user)


class TestCorrectnessNotAppearance:
    """Counting a word as produced because it appeared in the student's text
    marks words mastered on the evidence that the student typed them. Luz's
    correction format is the failure signal, and it was already being emitted."""

    @pytest.fixture
    def session(self, user, db):
        from learner.models import Session
        return Session.objects.create(user=user, session_type='new_skill')

    def _turn(self, session, tutor='', student=''):
        from learner.models import SessionEvent
        return SessionEvent.objects.create(
            session=session, event_type='conversation',
            content=tutor, user_response=student)

    def _record(self, session, user):
        from engine.vocabulary import _record
        return _record(session, user)

    def test_a_corrected_word_counts_as_failed_not_produced(self, session, user):
        _word('gimnasio', en='gym')
        self._turn(
            session,
            tutor="Dijiste: 'yo ir gimnasio'. Sería más natural: 'voy al gimnasio'.",
            student='yo ir gimnasio')
        self._record(session, user)
        uw = UserWord.objects.get(user=user, item__es='gimnasio')
        assert (uw.times_produced, uw.times_failed) == (0, 1)

    def test_an_uncorrected_word_still_counts_as_produced(self, session, user):
        _word('gimnasio', en='gym')
        self._turn(session, tutor='¡Muy bien!', student='Voy al gimnasio.')
        self._record(session, user)
        uw = UserWord.objects.get(user=user, item__es='gimnasio')
        assert (uw.times_produced, uw.times_failed) == (1, 0)

    def test_only_the_word_inside_the_correction_fails(self, session, user):
        """Other words in the same turn were used fine."""
        _word('gimnasio', en='gym'); _word('casa', en='house')
        self._turn(
            session,
            tutor="Dijiste: 'yo ir gimnasio'. Sería más natural: 'voy al gimnasio'.",
            student='yo ir gimnasio, luego a la casa')
        self._record(session, user)
        assert UserWord.objects.get(user=user, item__es='gimnasio').times_failed == 1
        assert UserWord.objects.get(user=user, item__es='casa').times_produced == 1

    def test_failure_drops_the_word_to_the_shortest_interval(self, session, user):
        from engine.vocabulary import REVIEW_INTERVALS_DAYS
        item = _word('casa', en='house')
        uw = UserWord.objects.create(user=user, item=item, interval_index=4,
                                     times_produced=2)
        self._turn(session,
                   tutor="Dijiste: 'la casa es azul'. Sería más natural: 'la casa está azul'.",
                   student='la casa es azul')
        self._record(session, user)
        uw.refresh_from_db()
        assert uw.interval_index == 0

    def test_a_word_that_keeps_failing_never_graduates(self, session, user):
        """Otherwise volume of attempts substitutes for getting it right."""
        from engine.vocabulary import load_settings
        item = _word('casa', en='house')
        for _ in range(load_settings()['graduation_productions'] + 2):
            s2 = session.__class__.objects.create(user=user, session_type='new_skill')
            self._turn(s2, tutor="Dijiste: 'casa mal'. Sería más natural: 'la casa'.",
                       student='casa mal')
            self._record(s2, user)
        assert UserWord.objects.get(user=user, item=item).state != 'known'


class TestSeeingDoesNotPromote:
    """Retrieval and exposure are not on the same scale. Being shown a word
    again refreshes it against decay; it is not progress."""

    @pytest.fixture
    def session(self, user, db):
        from learner.models import Session
        return Session.objects.create(user=user, session_type='new_skill')

    def _turn(self, session, tutor='', student=''):
        from learner.models import SessionEvent
        return SessionEvent.objects.create(
            session=session, event_type='conversation',
            content=tutor, user_response=student)

    def _record(self, session, user):
        from engine.vocabulary import _record
        return _record(session, user)

    def test_seeing_holds_position_on_the_ladder(self, session, user):
        item = _word('casa', en='house')
        uw = UserWord.objects.create(user=user, item=item, interval_index=3)
        self._turn(session, tutor='Mi casa es grande.', student='ok')
        self._record(session, user)
        uw.refresh_from_db()
        assert uw.interval_index == 3
        assert uw.times_seen == 1

    def test_producing_advances_it(self, session, user):
        item = _word('casa', en='house')
        uw = UserWord.objects.create(user=user, item=item, interval_index=3)
        self._turn(session, tutor='¿Dónde estás?', student='En mi casa.')
        self._record(session, user)
        uw.refresh_from_db()
        assert uw.interval_index == 4


class TestRetrieveToCorrectRecall:
    def test_the_block_asks_for_one_at_a_time_and_a_retry(self, db):
        from engine.vocabulary import render_block
        block = render_block([], [_word('casa', en='house')])
        assert 'ONE AT A TIME' in block
        assert 'RETURN to it' in block
