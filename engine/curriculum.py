"""
Utilities for loading the skill taxonomy and finding the next skill to teach.
"""
import os
import yaml
from functools import lru_cache
import django.conf

LEVEL_ORDER = ['A1', 'A2', 'B1', 'B2', 'C1', 'C2']


@lru_cache(maxsize=1)
def _load():
    path = os.path.join(django.conf.settings.BASE_DIR, 'curriculum', 'skills.yaml')
    with open(path) as f:
        return yaml.safe_load(f)


def all_skills():
    return _load()['skills']


def get_skill(skill_id):
    for s in all_skills():
        if s['id'] == skill_id:
            return s
    return None


def next_new_skill(user_level, scored_ids):
    """First unscored skill at user's level, then the next level up."""
    idx = LEVEL_ORDER.index(user_level) if user_level in LEVEL_ORDER else 0
    for level in LEVEL_ORDER[idx:]:
        for skill in all_skills():
            if skill['cefr_level'] == level and skill['id'] not in scored_ids:
                return skill
    return None


def next_new_vocab_skill(user_level, scored_ids):
    """First unscored vocab skill at or below user's level."""
    idx = LEVEL_ORDER.index(user_level) if user_level in LEVEL_ORDER else 0
    for level in LEVEL_ORDER[:idx + 1]:
        for skill in all_skills():
            if skill['cefr_level'] == level and 'vocab' in skill['id'] and skill['id'] not in scored_ids:
                return skill
    return None
