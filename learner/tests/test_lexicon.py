"""The guarantees the lexicon design rests on.

Packs are tags rather than rows specifically so that a word appearing in several
packs stays ONE row with ONE review schedule. If that stops being true, a student
reviews casa twice as two unrelated things and the whole point of making words
first-class is lost -- so it is tested here rather than assumed.
"""
import pytest
from django.core.management import call_command

from learner.models import LexItem, User, UserWord


@pytest.fixture
def synced(db):
    call_command('sync_lexicon')


class TestPacksAreTags:
    def test_a_word_in_two_packs_is_one_row(self, synced):
        """ciudad is in places_everyday and words_you_already_know."""
        rows = LexItem.objects.filter(es='ciudad', owner__isnull=True)
        assert rows.count() == 1
        assert set(rows.first().pack_list) == {'places_everyday', 'words_you_already_know'}

    def test_a_later_pack_fills_a_blank_without_clobbering(self, synced):
        """places_everyday lists ciudad with no note; the cognate pack adds one."""
        ciudad = LexItem.objects.get(es='ciudad', owner__isnull=True)
        assert ciudad.gender == 'f'          # from places_everyday
        assert '-dad' in ciudad.note         # from words_you_already_know


class TestChunks:
    def test_chunks_are_marked_unanalyzable(self, synced):
        for es in ('me llamo', 'voy a', '¿cómo estás?'):
            assert LexItem.objects.get(es=es).analyzable is False, es

    def test_ordinary_words_stay_analyzable(self, synced):
        assert LexItem.objects.get(es='casa').analyzable is True

    def test_the_ir_chunk_warns_against_the_paradigm(self, synced):
        assert 'paradigm' in LexItem.objects.get(es='voy a').note


class TestSyncIsSafeToRerun:
    def test_rerunning_creates_nothing_new(self, synced):
        before = LexItem.objects.count()
        call_command('sync_lexicon')
        assert LexItem.objects.count() == before

    def test_a_word_dropped_from_yaml_is_deactivated_not_deleted(self, synced):
        LexItem.objects.create(es='zzz_retired', en='gone', owner=None)
        call_command('sync_lexicon')
        retired = LexItem.objects.get(es='zzz_retired')
        assert retired.active is False

    def test_owned_words_are_never_touched(self, synced):
        """Interest vocabulary belongs to a person; sync owns only the catalogue."""
        user = User.objects.create(discord_id='1', display_name='Eric')
        mine = LexItem.objects.create(es='pesas', en='weights', owner=user, packs='interest:gym')
        call_command('sync_lexicon')
        mine.refresh_from_db()
        assert mine.active is True

    def test_an_owned_word_may_duplicate_a_core_word(self, synced):
        """Partial unique constraints: unique per-owner, and unique among core."""
        user = User.objects.create(discord_id='2', display_name='Ana')
        LexItem.objects.create(es='casa', en='house', owner=user)
        assert LexItem.objects.filter(es='casa').count() == 2


class TestDisplay:
    def test_nouns_render_their_article(self, synced):
        assert LexItem.objects.get(es='casa').display_es == 'la casa'
        assert LexItem.objects.get(es='gimnasio').display_es == 'el gimnasio'

    def test_non_nouns_render_bare(self, synced):
        assert LexItem.objects.get(es='hablar').display_es == 'hablar'


class TestUserWord:
    def test_seeing_and_producing_are_counted_separately(self, synced):
        user = User.objects.create(discord_id='3', display_name='Eric')
        word = LexItem.objects.get(es='casa')
        uw = UserWord.objects.create(user=user, item=word, times_seen=4, times_produced=1)
        assert (uw.times_seen, uw.times_produced) == (4, 1)
        assert uw.state == 'introduced'

    def test_one_row_per_user_per_word(self, synced):
        from django.db import IntegrityError
        user = User.objects.create(discord_id='4', display_name='Eric')
        word = LexItem.objects.get(es='casa')
        UserWord.objects.create(user=user, item=word)
        with pytest.raises(IntegrityError):
            UserWord.objects.create(user=user, item=word)
