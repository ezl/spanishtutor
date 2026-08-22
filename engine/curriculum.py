"""
Curriculum helpers. Skills live in the DB; this module wraps DB queries.
For bootstrapping / testing without a DB, YAML is only read by sync_skills.
"""
import re
import unicodedata

LEVEL_ORDER = ['A1', 'A2', 'B1', 'B2', 'C1', 'C2']

# Filler that carries no topic meaning, English and Spanish. Deliberately small:
# an over-eager stopword list silently swallows real topic words.
_REQUEST_STOPWORDS = frozenset({
    'i', 'a', 'an', 'the', 'to', 'want', 'wanna', 'would', 'like', 'learn',
    'study', 'teach', 'me', 'my', 'do', 'doing', 'lets', 'let', 'can', 'could',
    'we', 'you', 'please', 'about', 'on', 'some', 'practice', 'practise',
    'work', 'next', 'lesson', 'skill', 'vs', 'versus', 'and', 'or', 'of', 'is',
    'quiero', 'aprender', 'ensename', 'vamos', 'hacer', 'el', 'la', 'los',
    'las', 'un', 'una', 'por', 'favor', 'sobre', 'con', 'de', 'quisiera',
    # Words that mark a message AS a request are not part of the topic. Leaving
    # them in makes every naturally-phrased request fail to match, because the
    # matcher requires every content token to appear in the skill.
    'instead', 'switch', 'skip', 'topic', 'this', 'that', 'it', 'otro', 'otra',
    'tema', 'cambiemos', 'cambiar', 'estudiar', 'practicar', 'leccion',
})


def _normalize(text: str) -> str:
    """Casefold and strip accents so 'Acentuacion' matches 'Acentuación'."""
    decomposed = unicodedata.normalize('NFD', text.casefold())
    return ''.join(c for c in decomposed if unicodedata.category(c) != 'Mn')


def _content_tokens(phrase: str) -> list[str]:
    return [w for w in re.findall(r'[a-z0-9]+', _normalize(phrase))
            if w not in _REQUEST_STOPWORDS]


def find_matching_skills(phrase: str) -> list[dict]:
    """Active skills whose id/name/description contain every content token.

    Stage one of skill-request resolution. The code decides what exists in the
    curriculum -- never the model -- so a requested skill_id can't be invented.
    Returns [] for no match, one entry when unambiguous, several when the
    request needs a clarifying question.
    """
    tokens = set(_content_tokens(phrase))
    if not tokens:
        return []
    matches = []
    for skill in all_skills():
        # Whole-word matching, not substring: 'yo' must not match inside 'mayo'.
        # A drill answer that happens to share letters with a skill name must
        # never be mistaken for a request to change lesson.
        haystack = set(re.findall(r'[a-z0-9]+', _normalize(
            f"{skill['id']} {skill['name']} {skill['description']}")))
        if tokens <= haystack:
            matches.append(skill)
    return matches


def _skill_to_dict(skill):
    return {
        'id': skill.skill_id,
        'name': skill.name,
        'cefr_level': skill.cefr_level,
        'description': skill.description,
        'order': skill.order,
    }


def get_skill(skill_id: str) -> dict | None:
    from learner.models import Skill
    try:
        return _skill_to_dict(Skill.objects.get(skill_id=skill_id, active=True))
    except Skill.DoesNotExist:
        return None


def all_skills() -> list[dict]:
    from learner.models import Skill
    return [_skill_to_dict(s) for s in Skill.objects.filter(active=True).order_by('order')]


def next_new_skill(user_level: str, scored_ids: set) -> dict | None:
    """First unscored skill at user's level, then levels above."""
    from learner.models import Skill
    idx = LEVEL_ORDER.index(user_level) if user_level in LEVEL_ORDER else 0
    for level in LEVEL_ORDER[idx:]:
        skill = (
            Skill.objects.filter(active=True, cefr_level=level)
                         .exclude(skill_id__in=scored_ids)
                         .order_by('order')
                         .first()
        )
        if skill:
            return _skill_to_dict(skill)
    return None


def next_new_vocab_skill(user_level: str, scored_ids: set) -> dict | None:
    """First unscored vocab skill at or below user's level."""
    from learner.models import Skill
    idx = LEVEL_ORDER.index(user_level) if user_level in LEVEL_ORDER else 0
    for level in LEVEL_ORDER[:idx + 1]:
        skill = (
            Skill.objects.filter(active=True, cefr_level=level, skill_id__contains='vocab')
                         .exclude(skill_id__in=scored_ids)
                         .order_by('order')
                         .first()
        )
        if skill:
            return _skill_to_dict(skill)
    return None
