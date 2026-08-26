import pytest


class TestBankIntegrity:
    """The bank is config, so the tests guard its shape rather than its wording."""

    def test_loads(self):
        from engine.elicitation import load_bank
        bank = load_bank()
        assert bank['questions'] and bank['categories']

    def test_every_question_is_well_formed(self):
        from engine.elicitation import load_bank
        bank = load_bank()
        for q in bank['questions']:
            for field in ('id', 'category', 'grammar',
                          'opener_es', 'opener_en', 'follow_up_es', 'follow_up_en'):
                assert q.get(field), f"{q.get('id')} missing {field}"

    def test_every_category_is_in_the_controlled_vocabulary(self):
        """Categories exist so gaps are computable — a stray label breaks that."""
        from engine.elicitation import load_bank
        bank = load_bank()
        allowed = set(bank['categories'])
        for q in bank['questions']:
            assert q['category'] in allowed, f"{q['id']} uses {q['category']}"

    def test_ids_are_unique(self):
        from engine.elicitation import load_bank
        ids = [q['id'] for q in load_bank()['questions']]
        assert len(ids) == len(set(ids))

    def test_every_category_has_a_question(self):
        """Otherwise a gap can be detected but never filled."""
        from engine.elicitation import load_bank
        bank = load_bank()
        covered = {q['category'] for q in bank['questions']}
        assert set(bank['categories']) - covered == set()


class TestCategoryGaps:
    def test_empty_categories_come_first(self):
        from engine.elicitation import category_gaps
        held = {'food': 3, 'travel': 0, 'pets': 0, 'family': 1}
        gaps = category_gaps(held)
        # Everything we hold nothing on outranks everything we hold something on.
        assert gaps.index('travel') < gaps.index('family')
        assert gaps.index('pets') < gaps.index('family')
        # And thinner outranks thicker among those we do hold.
        assert gaps.index('family') < gaps.index('food')

    def test_categories_never_seen_count_as_empty(self):
        from engine.elicitation import category_gaps
        gaps = category_gaps({'food': 5})
        assert 'pets' in gaps
        assert gaps[-1] == 'food'


class TestSelectQuestion:
    def test_prefers_a_question_matching_the_grammar_being_taught(self):
        from engine.elicitation import select_question
        q = select_question({}, grammar_tags=['subjunctive'])
        assert 'subjunctive' in q['grammar']

    def test_among_grammar_matches_prefers_the_thinnest_category(self):
        from engine.elicitation import select_question
        # Both are preterite questions; travel is empty, friends is well covered.
        # Every other category carrying a preterite question is filled, so travel
        # is uniquely the thinnest -- otherwise this passes or fails on the
        # alphabetical tiebreak rather than on the ranking being tested.
        held = {'friends': 9, 'travel': 0, 'daily_routine': 9, 'celebrations': 9,
                'problems': 9, 'people_in_life': 9, 'past_childhood': 9,
                'food': 9, 'health': 9, 'home_place': 9, 'work_study': 9,
                'exercise_sport': 9, 'hobbies': 9, 'music_media': 9, 'pets': 9,
                'family': 9, 'goals_dreams': 9, 'preferences': 9}
        q = select_question(held, grammar_tags=['preterite'])
        assert q['category'] == 'travel'

    def test_falls_back_to_gap_when_no_grammar_matches(self):
        from engine.elicitation import select_question
        q = select_question({'pets': 0}, grammar_tags=['nonexistent_structure'])
        assert q is not None

    def test_does_not_repeat_an_asked_question(self):
        from engine.elicitation import select_question
        first = select_question({}, grammar_tags=['subjunctive'])
        second = select_question({}, grammar_tags=['subjunctive'],
                                 exclude_ids=[first['id']])
        assert second is None or second['id'] != first['id']

    def test_returns_none_when_everything_is_excluded(self):
        from engine.elicitation import load_bank, select_question
        all_ids = [q['id'] for q in load_bank()['questions']]
        assert select_question({}, exclude_ids=all_ids) is None


class TestGrammarTagsForSkill:
    def test_derives_tags_from_the_skill_id(self):
        from engine.elicitation import grammar_tags_for_skill
        tags = grammar_tags_for_skill('b1_preterite_vs_imperfect')
        assert 'preterite_vs_imperfect' in tags or 'preterite' in tags

    def test_matches_future_and_subjunctive_skills(self):
        from engine.elicitation import grammar_tags_for_skill
        assert 'future_periphrastic' in grammar_tags_for_skill('b1_future_periphrastic')
        assert 'subjunctive' in grammar_tags_for_skill('b1_subjunctive_formation')

    def test_unknown_skill_yields_no_tags(self):
        from engine.elicitation import grammar_tags_for_skill
        assert grammar_tags_for_skill('a1_greetings') == []


@pytest.mark.django_db(transaction=True)
class TestPickForSession:
    @pytest.mark.asyncio
    async def test_counts_are_grouped_by_category(self, make_user):
        from asgiref.sync import sync_to_async
        from engine.elicitation import held_category_counts
        from learner.models import UserInterest

        user = await sync_to_async(make_user)(discord_id='elic_c', cefr_level='B1')
        for topic in ('a', 'b'):
            await sync_to_async(UserInterest.objects.create)(
                user=user, topic=topic, category='food', confidence=0.9)

        counts = await held_category_counts(user)
        assert counts['food'] == 2

    @pytest.mark.asyncio
    async def test_question_matches_the_skill_being_taught(self, make_user):
        from engine.elicitation import pick_for_session
        user = await __import__('asgiref.sync', fromlist=['sync_to_async']).sync_to_async(
            make_user)(discord_id='elic_p', cefr_level='B1')

        q = await pick_for_session(user, skill_id='b1_subjunctive_formation')
        assert q is not None
        assert 'subjunctive' in q['grammar']


class TestPromptsAskForNewTerritory:
    def test_conversation_prompt_no_longer_recycles_known_interests(self):
        """It literally instructed 'Draw from their interests or last session',
        which is the echo chamber written down as an instruction."""
        from engine.session import CONVERSATION_PROMPT
        assert 'Draw from their interests or last session' not in CONVERSATION_PROMPT

    def test_conversation_prompt_has_an_elicitation_slot(self):
        from engine.session import CONVERSATION_PROMPT
        assert '{elicitation}' in CONVERSATION_PROMPT

    def test_lesson_opening_has_an_elicitation_slot(self):
        from engine.teach_drill import TEACH_DRILL_OPENING_PROMPT
        assert '{elicitation}' in TEACH_DRILL_OPENING_PROMPT


class TestElicitationBlock:
    def test_block_permits_english_and_forbids_marking(self):
        from engine.elicitation import build_elicitation_block, load_bank
        q = load_bank()['questions'][0]
        block = build_elicitation_block(q, cefr_level='B1')
        low = block.lower()
        assert 'english' in low, "answering must not be gated on L2 ability"
        assert 'do not correct' in low or 'not a test' in low

    def test_block_is_empty_when_there_is_no_question(self):
        from engine.elicitation import build_elicitation_block
        assert build_elicitation_block(None, cefr_level='B1') == ''

    def test_block_carries_the_follow_up(self):
        """The fact lives in the follow-up, not the opener."""
        from engine.elicitation import build_elicitation_block, load_bank
        q = load_bank()['questions'][0]
        block = build_elicitation_block(q, cefr_level='B1')
        assert q['follow_up_es'] in block or q['follow_up_en'] in block


class TestTheSameQuestionIsNotAskedTwice:
    """pick_for_session took an exclude_ids argument that nothing populated, and
    select_question breaks ties on question id, so the lowest-id question in the
    thinnest category went out every session until a fact happened to land in
    that category."""

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_consecutive_picks_are_all_different(self, make_user):
        from asgiref.sync import sync_to_async
        from engine.elicitation import pick_for_session, record_ask

        user = await sync_to_async(make_user)()
        seen = []
        for _ in range(6):
            q = await pick_for_session(user)
            assert q is not None
            await record_ask(user, q)
            seen.append(q['id'])

        assert len(set(seen)) == len(seen), f'repeated a question: {seen}'

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_a_category_that_yields_nothing_stops_being_chosen(self, make_user):
        """Facts alone cannot tell "never asked" from "asked, and they have no
        pets". Without the ask count that category sits at zero and wins forever."""
        from asgiref.sync import sync_to_async
        from engine.elicitation import pick_for_session, record_ask

        user = await sync_to_async(make_user)()
        first = await pick_for_session(user)
        await record_ask(user, first)

        # Nothing was learned, so held counts are unchanged.
        second = await pick_for_session(user)

        assert second['category'] != first['category']

    def test_ask_counts_break_the_tie_between_empty_categories(self):
        from engine.elicitation import category_gaps

        # No facts anywhere, so every category ties on the first key. pets is the
        # only one that has cost an ask, so it goes last: a category already
        # tried loses to every category that has not been.
        gaps = category_gaps({}, ask_counts={'pets': 3})
        assert gaps[-1] == 'pets'


class TestNoOrphanedGrammarTags:
    """grammar_tags_for_skill() substring-matches bank tags against skill ids and
    fails soft — a question whose tag matches no skill simply never gets picked by
    grammar, with no error. That is correct for deliberately untagged skills, but
    it means a rename can silently detach a question and nothing says so."""

    def test_every_bank_tag_matches_a_real_skill(self):
        import pathlib, yaml
        from engine.elicitation import orphaned_grammar_tags
        skills = yaml.safe_load(pathlib.Path('curriculum/skills.yaml').read_text())
        ids = [s['id'] for s in skills['skills']]
        orphans = orphaned_grammar_tags(ids)
        assert orphans == [], (
            f"elicitation questions tagged with grammar no skill id carries: {orphans}. "
            "A rename probably detached them; they can now only be chosen by gap order."
        )

    def test_a_renamed_skill_is_detected(self):
        from engine.elicitation import orphaned_grammar_tags
        orphans = orphaned_grammar_tags(['a1_greetings', 'a1_numbers_1_5'])
        assert 'subjunctive' in orphans and 'preterite' in orphans

    def test_matching_ids_are_not_reported(self):
        from engine.elicitation import orphaned_grammar_tags
        assert 'preterite' not in orphaned_grammar_tags(['b1_preterite_vs_imperfect'])
