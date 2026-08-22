import pytest
from asgiref.sync import sync_to_async


@pytest.fixture
def subjunctive_grid(make_skill):
    """A slice of the real taxonomy: 'subjunctive' is genuinely ambiguous."""
    return [
        make_skill(skill_id='b1_subjunctive_formation',
                   name='Subjunctive — present formation',
                   cefr_level='B1', description='Forming the present subjunctive'),
        make_skill(skill_id='b1_subjunctive_triggers_doubt_emotion',
                   name='Subjunctive — doubt and emotion triggers',
                   cefr_level='B1', description='Dudo que, me alegra que'),
        make_skill(skill_id='b2_subjunctive_impersonal',
                   name='Subjunctive — impersonal expressions',
                   cefr_level='B2', description='Es importante que, es posible que'),
        make_skill(skill_id='b1_preterite_vs_imperfect',
                   name='Preterite vs. imperfect — contrast',
                   cefr_level='B1', description='Completed events vs background'),
    ]


@pytest.mark.django_db
class TestFindMatchingSkills:
    def test_unique_topic_returns_exactly_one(self, subjunctive_grid):
        from engine.curriculum import find_matching_skills
        matches = find_matching_skills("preterite vs imperfect")
        assert [m['id'] for m in matches] == ['b1_preterite_vs_imperfect']

    def test_ambiguous_topic_returns_every_candidate(self, subjunctive_grid):
        """'subjunctive' maps to three skills — this is the case that must ask."""
        from engine.curriculum import find_matching_skills
        matches = find_matching_skills("subjunctive")
        assert len(matches) == 3
        assert all('subjunctive' in m['id'] for m in matches)

    def test_filler_words_are_ignored(self, subjunctive_grid):
        from engine.curriculum import find_matching_skills
        assert (find_matching_skills("I want to learn the subjunctive")
                == find_matching_skills("subjunctive"))

    def test_unknown_topic_returns_nothing(self, subjunctive_grid):
        from engine.curriculum import find_matching_skills
        assert find_matching_skills("quantum mechanics") == []

    def test_empty_phrase_returns_nothing(self, subjunctive_grid):
        from engine.curriculum import find_matching_skills
        assert find_matching_skills("   ") == []

    def test_inactive_skills_are_never_offered(self, make_skill):
        from engine.curriculum import find_matching_skills
        make_skill(skill_id='b1_retired', name='Retired subjunctive thing',
                   description='old', active=False)
        assert find_matching_skills("retired") == []

    def test_matching_is_accent_and_case_insensitive(self, make_skill):
        from engine.curriculum import find_matching_skills
        make_skill(skill_id='a2_acentos', name='Acentuación y tildes',
                   description='Reglas de acentuación')
        assert find_matching_skills("ACENTUACION") != []


@pytest.mark.django_db
class TestMatchingIsWordAccurate:
    """Substring matching would let a short token match inside an unrelated word,
    which matters because a drill answer must never be read as a skill request."""

    def test_short_token_does_not_match_inside_a_word(self, make_skill):
        from engine.curriculum import find_matching_skills
        make_skill(skill_id='a1_months', name='Months of the year',
                   description='enero, febrero, mayo, junio')
        # 'yo' appears inside 'mayo' but is not a word in this skill.
        assert find_matching_skills("yo") == []

    def test_whole_word_still_matches(self, make_skill):
        from engine.curriculum import find_matching_skills
        make_skill(skill_id='a1_pronouns', name='Subject pronouns',
                   description='yo, tú, él, nosotros')
        assert [m['id'] for m in find_matching_skills("yo")] == ['a1_pronouns']


@pytest.mark.django_db
class TestRequestPhrasingIsStripped:
    """Words that mark a message as a request ('instead', 'otro tema') are not
    topic content — leaving them in makes every phrased request fail to match."""

    @pytest.mark.parametrize("phrase", [
        "subjunctive",
        "can we learn subjunctive instead",
        "switch to subjunctive",
        "otro tema: subjunctive",
        "skip this, subjunctive please",
    ])
    def test_phrasing_does_not_break_the_match(self, phrase, make_skill):
        from engine.curriculum import find_matching_skills
        make_skill(skill_id='b1_subjunctive_formation',
                   name='Subjunctive — present formation',
                   description='Forming the present subjunctive')
        assert [m['id'] for m in find_matching_skills(phrase)] == ['b1_subjunctive_formation']
