"""
Guard against the empty-parse wipe.

sync_skills deactivates any active skill absent from the YAML:

    Skill.objects.filter(active=True).exclude(skill_id__in=yaml_ids).update(active=False)

exclude(field__in=[]) excludes NOTHING, so an empty parse deactivates the entire
curriculum — measured at 82 skills against production — and the command still
reports success. Empty file, truncated write, a `skills:` key with nothing under
it: any of them, and the grid is gone with no error.
"""
import pytest
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError


@pytest.mark.django_db
class TestEmptyParseIsRefused:

    def test_empty_skill_list_raises_instead_of_wiping(self, make_skill):
        from learner.models import Skill
        make_skill(skill_id='a1_keepme', name='Keep me')

        with patch('yaml.safe_load', return_value={'skills': []}):
            with pytest.raises(CommandError):
                call_command('sync_skills')

        assert Skill.objects.filter(skill_id='a1_keepme', active=True).exists(), \
            "an empty parse must never deactivate the existing curriculum"

    def test_missing_skills_key_raises(self, make_skill):
        make_skill(skill_id='a1_keepme2', name='Keep me')
        with patch('yaml.safe_load', return_value={}):
            with pytest.raises(CommandError):
                call_command('sync_skills')

    def test_none_from_an_empty_file_raises(self, make_skill):
        make_skill(skill_id='a1_keepme3', name='Keep me')
        with patch('yaml.safe_load', return_value=None):
            with pytest.raises(CommandError):
                call_command('sync_skills')

    def test_a_real_list_still_syncs(self, make_skill):
        from learner.models import Skill
        entry = {'id': 'a1_real', 'name': 'Real', 'cefr_level': 'A1', 'description': 'x'}
        with patch('yaml.safe_load', return_value={'skills': [entry]}):
            call_command('sync_skills')
        assert Skill.objects.filter(skill_id='a1_real', active=True).exists()
