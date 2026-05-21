"""
Curriculum helpers. Skills live in the DB; this module wraps DB queries.
For bootstrapping / testing without a DB, YAML is only read by sync_skills.
"""
LEVEL_ORDER = ['A1', 'A2', 'B1', 'B2', 'C1', 'C2']


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
