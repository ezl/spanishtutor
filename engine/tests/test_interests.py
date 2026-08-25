"""
Tests for engine/interests.py

Covers: _parse_extraction, extract_and_store_interests dedup logic.
"""
import pytest
from asgiref.sync import sync_to_async
from unittest.mock import patch, AsyncMock


# ── Area 10: _parse_extraction ───────────────────────────────────────────────

class TestParseExtraction:
    def test_parses_valid_lines(self):
        from engine.interests import _parse_extraction
        raw = "NEW | Chicago Cubs | exercise_sport | 0.9\nNEW | has a dog | pets | 0.8"
        results = _parse_extraction(raw)
        assert len(results) == 2
        assert results[0]['topic'] == 'Chicago Cubs'
        assert results[0]['category'] == 'exercise_sport'
        assert results[1]['topic'] == 'has a dog'

    def test_nothing_returns_empty_list(self):
        from engine.interests import _parse_extraction
        assert _parse_extraction('NOTHING') == []

    def test_malformed_lines_are_skipped(self):
        from engine.interests import _parse_extraction
        raw = "NEW | valid | pets | 0.7\nbad line no pipes\nalso | bad"
        results = _parse_extraction(raw)
        assert len(results) == 1
        assert results[0]['topic'] == 'valid'

    def test_non_numeric_confidence_is_skipped(self):
        from engine.interests import _parse_extraction
        raw = "topic | category | not_a_number"
        assert _parse_extraction(raw) == []


# ── extract_and_store_interests: dedup and new interest logic ─────────────────

@pytest.mark.django_db(transaction=True)
async def test_extract_dedup_increments_mention_count(make_user):
    """Same topic seen again → mention_count++ and confidence max-merged."""
    from learner.models import Session, SessionEvent, UserInterest
    from engine.interests import extract_and_store_interests

    user = await sync_to_async(make_user)(discord_id='u_int1')
    session = await sync_to_async(Session.objects.create)(user=user, session_type='conversation')
    await sync_to_async(SessionEvent.objects.create)(
        session=session, event_type='conversation',
        content='Do you like football?', user_response='Yes, I love the Cubs.',
    )

    await sync_to_async(UserInterest.objects.create)(
        user=user, topic='Chicago Cubs', category='sports', confidence=0.6, mention_count=1,
    )

    with patch('engine.interests._call_extraction_llm', AsyncMock(return_value='NEW | Chicago Cubs | exercise_sport | 0.9')):
        facts = await extract_and_store_interests(session, user)

    ui = await sync_to_async(UserInterest.objects.get)(user=user, topic='Chicago Cubs')
    assert ui.mention_count == 2
    assert ui.confidence == pytest.approx(0.9)
    assert facts[0].get('new') is False


@pytest.mark.django_db(transaction=True)
async def test_extract_creates_new_interest(make_user):
    """New topic → creates UserInterest with new=True."""
    from learner.models import Session, SessionEvent, UserInterest
    from engine.interests import extract_and_store_interests

    user = await sync_to_async(make_user)(discord_id='u_int2')
    session = await sync_to_async(Session.objects.create)(user=user, session_type='conversation')
    await sync_to_async(SessionEvent.objects.create)(
        session=session, event_type='conversation',
        content='What do you do?', user_response='I work in tech.',
    )

    with patch('engine.interests._call_extraction_llm', AsyncMock(return_value='NEW | works in tech | work_study | 0.7')):
        facts = await extract_and_store_interests(session, user)

    assert facts[0]['new'] is True
    count = await sync_to_async(UserInterest.objects.filter(user=user, topic='works in tech').count)()
    assert count == 1


@pytest.mark.django_db(transaction=True)
async def test_extract_returns_empty_when_nothing(make_user):
    """'NOTHING' response → empty list, no UserInterest rows created."""
    from learner.models import Session, SessionEvent, UserInterest
    from engine.interests import extract_and_store_interests

    user = await sync_to_async(make_user)(discord_id='u_int3')
    session = await sync_to_async(Session.objects.create)(user=user, session_type='conversation')
    await sync_to_async(SessionEvent.objects.create)(
        session=session, event_type='conversation',
        content='How are you?', user_response='Fine.',
    )

    with patch('engine.interests._call_extraction_llm', AsyncMock(return_value='NOTHING')):
        facts = await extract_and_store_interests(session, user)

    assert facts == []
    count = await sync_to_async(UserInterest.objects.filter(user=user).count)()
    assert count == 0


class TestProvenance:
    """The extractor read the whole transcript, so Luz's own sentences became
    facts. Session 57 produced "Has a friend named Melodie" from a session where
    the student never typed the name — Luz said it once, drawn from interests."""

    def test_transcript_labels_who_said_what(self):
        from engine.interests import _build_transcript

        class E:
            def __init__(self, content, user_response):
                self.content, self.user_response = content, user_response

        out = _build_transcript([E('¿Y el sushi con Melodie?', 'Fuimos con nuestra amiga')])
        assert 'STUDENT:' in out and 'TUTOR:' in out

    def test_a_topic_the_tutor_introduced_is_an_echo(self):
        from engine.interests import _looks_echoed
        assert _looks_echoed('knows someone named Melodie',
                             tutor_text='¿Y el sushi con Linda y Melodie?',
                             student_text='Melodie te lo mandó') is True

    def test_a_topic_only_the_student_used_is_volunteered(self):
        from engine.interests import _looks_echoed
        assert _looks_echoed('has a friend named Linda',
                             tutor_text='¿Qué hiciste el fin de semana?',
                             student_text='Salí con Linda el sábado') is False

    def test_a_topic_nobody_named_is_not_volunteered(self):
        from engine.interests import _looks_echoed
        assert _looks_echoed('goes to the gym',
                             tutor_text='¿Qué tal?', student_text='Bien, gracias') is True


class TestExtractionOutputFormat:
    def test_parses_a_new_fact(self):
        from engine.interests import _parse_extraction
        out = _parse_extraction('NEW | plays guitar | hobbies | 0.9')
        assert out == [{'action': 'new', 'supersedes': None, 'topic': 'plays guitar',
                        'category': 'hobbies', 'confidence': 0.9}]

    def test_parses_a_supersede(self):
        from engine.interests import _parse_extraction
        out = _parse_extraction(
            'SUPERSEDES has a friend named Melodie | has a wife named Melodie | family | 1.0')
        assert out[0]['action'] == 'supersede'
        assert out[0]['supersedes'] == 'has a friend named Melodie'
        assert out[0]['topic'] == 'has a wife named Melodie'

    def test_rejects_a_category_outside_the_vocabulary(self):
        """Free-text categories are what made gaps uncomputable."""
        from engine.interests import _parse_extraction
        out = _parse_extraction('NEW | plays guitar | fitness/hobbies/interests | 0.9')
        assert out == [] or out[0]['category'] in _allowed()

    def test_nothing_yields_nothing(self):
        from engine.interests import _parse_extraction
        assert _parse_extraction('NOTHING') == []


def _allowed():
    from engine.elicitation import load_bank
    return set(load_bank()['categories'])


@pytest.mark.django_db(transaction=True)
class TestSupersedeAndReinforcement:

    async def _user(self, make_user, uid):
        return await sync_to_async(make_user)(discord_id=uid, cefr_level='B1')

    @pytest.mark.asyncio
    async def test_supersede_updates_instead_of_adding_a_row(self, make_user, make_skill):
        from engine.interests import extract_and_store_interests
        from learner.models import Session, SessionEvent, UserInterest

        user = await self._user(make_user, 'prov_sup')
        session = await sync_to_async(Session.objects.create)(
            user=user, session_type='conversation')
        await sync_to_async(SessionEvent.objects.create)(
            session=session, event_type='conversation', content='¿Con quién vives?',
            user_response='Vivo con mi esposa Melodie')
        await sync_to_async(UserInterest.objects.create)(
            user=user, topic='has a friend named Melodie', category='friends', confidence=0.9)

        raw = 'SUPERSEDES has a friend named Melodie | has a wife named Melodie | family | 1.0'
        with patch('engine.interests._call_extraction_llm', new=AsyncMock(return_value=raw)):
            await extract_and_store_interests(session, user)

        topics = await sync_to_async(
            lambda: sorted(UserInterest.objects.filter(user=user).values_list('topic', flat=True)))()
        assert topics == ['has a wife named Melodie'], topics

    @pytest.mark.asyncio
    async def test_an_echoed_mention_does_not_reinforce(self, make_user):
        from engine.interests import extract_and_store_interests
        from learner.models import Session, SessionEvent, UserInterest

        user = await self._user(make_user, 'prov_echo')
        session = await sync_to_async(Session.objects.create)(
            user=user, session_type='new_skill')
        # Luz introduces the name; the student only reuses it in a drill answer.
        await sync_to_async(SessionEvent.objects.create)(
            session=session, event_type='quiz',
            content='How would you say "Melodie sent it to you"?',
            user_response='Melodie te lo mandó')
        row = await sync_to_async(UserInterest.objects.create)(
            user=user, topic='knows someone named Melodie', category='friends',
            confidence=0.9, mention_count=3)

        raw = 'NEW | knows someone named Melodie | friends | 0.9'
        with patch('engine.interests._call_extraction_llm', new=AsyncMock(return_value=raw)):
            await extract_and_store_interests(session, user)

        reloaded = await sync_to_async(lambda: UserInterest.objects.get(pk=row.pk))()
        assert reloaded.mention_count == 3, "an echo is the system reading its own output"

    @pytest.mark.asyncio
    async def test_a_volunteered_mention_does_reinforce(self, make_user):
        from engine.interests import extract_and_store_interests
        from learner.models import Session, SessionEvent, UserInterest

        user = await self._user(make_user, 'prov_vol')
        session = await sync_to_async(Session.objects.create)(
            user=user, session_type='conversation')
        await sync_to_async(SessionEvent.objects.create)(
            session=session, event_type='conversation',
            content='¿Qué hiciste el fin de semana?',
            user_response='Salí a correr con Linda')
        row = await sync_to_async(UserInterest.objects.create)(
            user=user, topic='has a friend named Linda', category='friends',
            confidence=0.9, mention_count=1)

        raw = 'NEW | has a friend named Linda | friends | 0.9'
        with patch('engine.interests._call_extraction_llm', new=AsyncMock(return_value=raw)):
            await extract_and_store_interests(session, user)

        reloaded = await sync_to_async(lambda: UserInterest.objects.get(pk=row.pk))()
        assert reloaded.mention_count == 2


@pytest.mark.django_db(transaction=True)
class TestProseDiversity:
    @pytest.mark.asyncio
    async def test_one_category_cannot_dominate_the_prose(self, make_user):
        """Duplicates each carried their own mention_count, so the gym cluster
        crowded out everything else in the field lessons are generated from."""
        from engine.interests import _regenerate_interests_prose
        from learner.models import UserInterest

        user = await sync_to_async(make_user)(discord_id='prose_div', cefr_level='B1')
        for i in range(8):
            await sync_to_async(UserInterest.objects.create)(
                user=user, topic=f'gym fact {i}', category='exercise_sport',
                confidence=1.0, mention_count=20 - i)
        await sync_to_async(UserInterest.objects.create)(
            user=user, topic='works in tech', category='work_study',
            confidence=0.9, mention_count=1)

        await _regenerate_interests_prose(user)

        reloaded = await sync_to_async(lambda: type(user).objects.get(pk=user.pk))()
        assert 'works in tech' in reloaded.interests
        assert reloaded.interests.count('gym fact') <= 3


class TestCleanupPlanParsing:
    def test_parses_a_plan(self):
        from engine.interests import _parse_cleanup_plan
        raw = '''{"clusters":[{"canonical":"goes to the gym","category":"exercise_sport","members":[1,2,3]}],
                  "contradictions":[{"reason":"friend vs son","members":[7,8]}],
                  "drop":[9]}'''
        plan = _parse_cleanup_plan(raw, valid_ids={1, 2, 3, 7, 8, 9})
        assert plan['clusters'][0]['members'] == [1, 2, 3]
        assert plan['contradictions'][0]['members'] == [7, 8]
        assert plan['drop'] == [9]

    def test_tolerates_prose_around_the_json(self):
        from engine.interests import _parse_cleanup_plan
        raw = 'Here is the plan:\n{"clusters":[],"contradictions":[],"drop":[4]}\nDone.'
        assert _parse_cleanup_plan(raw, valid_ids={4})['drop'] == [4]

    def test_drops_clusters_with_an_invalid_category(self):
        from engine.interests import _parse_cleanup_plan
        raw = '{"clusters":[{"canonical":"x","category":"fitness/hobbies","members":[1]}]}'
        assert _parse_cleanup_plan(raw, valid_ids={1})['clusters'] == []

    def test_ignores_row_ids_that_do_not_exist(self):
        """The model must never be able to delete a row it was not shown."""
        from engine.interests import _parse_cleanup_plan
        raw = '{"clusters":[{"canonical":"x","category":"pets","members":[1,999]}],"drop":[888]}'
        plan = _parse_cleanup_plan(raw, valid_ids={1})
        assert plan['clusters'][0]['members'] == [1]
        assert plan['drop'] == []

    def test_garbage_yields_an_empty_plan(self):
        from engine.interests import _parse_cleanup_plan
        plan = _parse_cleanup_plan('no json at all', valid_ids={1})
        assert plan == {'clusters': [], 'contradictions': [], 'drop': []}


@pytest.mark.django_db(transaction=True)
class TestApplyCleanupPlan:

    async def _rows(self, make_user, uid):
        from learner.models import UserInterest
        user = await sync_to_async(make_user)(discord_id=uid, cefr_level='B1')
        a = await sync_to_async(UserInterest.objects.create)(
            user=user, topic='Goes to the gym', category='fitness/hobbies',
            confidence=1.0, mention_count=7)
        b = await sync_to_async(UserInterest.objects.create)(
            user=user, topic='Lifts weights', category='hobbies/fitness',
            confidence=1.0, mention_count=2)
        c = await sync_to_async(UserInterest.objects.create)(
            user=user, topic='weightlifting / lifting weights', category='hobbies',
            confidence=0.9, mention_count=1)
        return user, a, b, c

    @pytest.mark.asyncio
    async def test_a_cluster_collapses_to_one_row(self, make_user):
        from engine.interests import apply_cleanup_plan
        from learner.models import UserInterest

        user, a, b, c = await self._rows(make_user, 'cln_merge')
        plan = {'clusters': [{'canonical': 'goes to the gym and lifts weights',
                              'category': 'exercise_sport',
                              'members': [a.pk, b.pk, c.pk]}],
                'contradictions': [], 'drop': []}

        await apply_cleanup_plan(user, plan)

        rows = await sync_to_async(lambda: list(UserInterest.objects.filter(user=user)))()
        assert len(rows) == 1
        assert rows[0].topic == 'goes to the gym and lifts weights'
        assert rows[0].category == 'exercise_sport'

    @pytest.mark.asyncio
    async def test_merged_count_is_the_max_not_the_sum(self, make_user):
        """The counts are echo-inflated, so summing would compound the very
        signal that made one cluster dominate."""
        from engine.interests import apply_cleanup_plan
        from learner.models import UserInterest

        user, a, b, c = await self._rows(make_user, 'cln_count')
        plan = {'clusters': [{'canonical': 'gym', 'category': 'exercise_sport',
                              'members': [a.pk, b.pk, c.pk]}],
                'contradictions': [], 'drop': []}

        await apply_cleanup_plan(user, plan)

        row = await sync_to_async(lambda: UserInterest.objects.get(user=user))()
        assert row.mention_count == 7

    @pytest.mark.asyncio
    async def test_contradictions_are_removed_entirely(self, make_user):
        """A wrong fact is worse than a missing one -- it generates sentences
        about a son who does not exist. Elicitation can re-learn the truth."""
        from engine.interests import apply_cleanup_plan
        from learner.models import UserInterest

        user, a, b, c = await self._rows(make_user, 'cln_contra')
        plan = {'clusters': [], 'drop': [],
                'contradictions': [{'reason': 'friend vs son', 'members': [a.pk, b.pk]}]}

        await apply_cleanup_plan(user, plan)

        remaining = await sync_to_async(
            lambda: sorted(UserInterest.objects.filter(user=user).values_list('pk', flat=True)))()
        assert remaining == [c.pk]

    @pytest.mark.asyncio
    async def test_dry_run_changes_nothing(self, make_user):
        from engine.interests import apply_cleanup_plan
        from learner.models import UserInterest

        user, a, b, c = await self._rows(make_user, 'cln_dry')
        plan = {'clusters': [{'canonical': 'gym', 'category': 'exercise_sport',
                              'members': [a.pk, b.pk, c.pk]}],
                'contradictions': [], 'drop': []}

        summary = await apply_cleanup_plan(user, plan, dry_run=True)

        count = await sync_to_async(lambda: UserInterest.objects.filter(user=user).count())()
        assert count == 3
        assert summary['merged'] == 3


@pytest.mark.django_db(transaction=True)
class TestContradictionKeepsWhatIsStillTrue:
    """Deleting every row in a contradiction loses too much: "friend named
    Chris" vs "son named Chris" disagree about the relationship, but that a
    Chris exists is not in dispute."""

    async def _pair(self, make_user, uid):
        from learner.models import UserInterest
        user = await sync_to_async(make_user)(discord_id=uid, cefr_level='B1')
        a = await sync_to_async(UserInterest.objects.create)(
            user=user, topic='Friend named Chris', category='friends', confidence=1.0)
        b = await sync_to_async(UserInterest.objects.create)(
            user=user, topic='Son named Chris', category='family', confidence=1.0)
        return user, a, b

    @pytest.mark.asyncio
    async def test_collapses_to_the_safe_fact_when_given_one(self, make_user):
        from engine.interests import apply_cleanup_plan
        from learner.models import UserInterest

        user, a, b = await self._pair(make_user, 'contra_safe')
        plan = {'clusters': [], 'drop': [], 'contradictions': [
            {'reason': 'friend vs son', 'members': [a.pk, b.pk],
             'safe': 'knows someone named Chris', 'category': 'friends'}]}

        await apply_cleanup_plan(user, plan)

        rows = await sync_to_async(lambda: list(UserInterest.objects.filter(user=user)))()
        assert len(rows) == 1
        assert rows[0].topic == 'knows someone named Chris'

    @pytest.mark.asyncio
    async def test_deletes_everything_when_nothing_is_salvageable(self, make_user):
        from engine.interests import apply_cleanup_plan
        from learner.models import UserInterest

        user, a, b = await self._pair(make_user, 'contra_nosafe')
        plan = {'clusters': [], 'drop': [],
                'contradictions': [{'reason': 'irreconcilable', 'members': [a.pk, b.pk]}]}

        await apply_cleanup_plan(user, plan)

        count = await sync_to_async(lambda: UserInterest.objects.filter(user=user).count())()
        assert count == 0

    def test_plan_parsing_keeps_the_safe_field(self):
        from engine.interests import _parse_cleanup_plan
        raw = ('{"contradictions":[{"reason":"r","members":[1,2],'
               '"safe":"knows someone named Chris","category":"friends"}]}')
        plan = _parse_cleanup_plan(raw, valid_ids={1, 2})
        assert plan['contradictions'][0]['safe'] == 'knows someone named Chris'

    def test_a_safe_fact_with_a_bad_category_is_discarded(self):
        from engine.interests import _parse_cleanup_plan
        raw = ('{"contradictions":[{"reason":"r","members":[1,2],'
               '"safe":"x","category":"relationships"}]}')
        plan = _parse_cleanup_plan(raw, valid_ids={1, 2})
        assert not plan['contradictions'][0].get('safe')


class TestCleanupParseFailuresAreLoud:
    """An undecodable plan used to return {} — indistinguishable from "nothing
    to clean", so a failed run looked like a successful no-op."""

    def test_malformed_json_raises_rather_than_returning_empty(self):
        from engine.interests import CleanupParseError, _parse_cleanup_plan
        with pytest.raises(CleanupParseError):
            _parse_cleanup_plan('{"clusters": [ {"canonical": }} nope', valid_ids={1})

    def test_no_json_at_all_is_still_an_empty_plan(self):
        """Distinct case: the model declined, rather than answering badly."""
        from engine.interests import _parse_cleanup_plan
        assert _parse_cleanup_plan('nothing to do here', valid_ids={1}) == {
            'clusters': [], 'contradictions': [], 'drop': []}

    def test_code_fences_are_tolerated(self):
        from engine.interests import _parse_cleanup_plan
        raw = '```json\n{"clusters":[],"contradictions":[],"drop":[1]}\n```'
        assert _parse_cleanup_plan(raw, valid_ids={1})['drop'] == [1]
