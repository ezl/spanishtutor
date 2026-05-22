"""
Sync curriculum/skills.yaml into the Skill table.
Safe to run repeatedly — upserts by skill_id, updates prerequisites M2M.
"""
import os
import yaml
from django.core.management.base import BaseCommand
from django.conf import settings
from learner.models import Skill


class Command(BaseCommand):
    help = 'Sync skills.yaml into the Skill table'

    def handle(self, *args, **options):
        path = os.path.join(settings.BASE_DIR, 'curriculum', 'skills.yaml')
        with open(path) as f:
            data = yaml.safe_load(f)

        skills_data = data['skills']
        created = updated = 0

        for order, entry in enumerate(skills_data):
            obj, is_new = Skill.objects.update_or_create(
                skill_id=entry['id'],
                defaults={
                    'name': entry['name'],
                    'cefr_level': entry['cefr_level'],
                    'description': entry.get('description', ''),
                    'order': order,
                    'active': entry.get('active', True),
                },
            )
            if is_new:
                created += 1
            else:
                updated += 1

        # Deactivate skills removed from YAML
        yaml_ids = {entry['id'] for entry in skills_data}
        deactivated = Skill.objects.filter(active=True).exclude(skill_id__in=yaml_ids).update(active=False)
        if deactivated:
            self.stdout.write(self.style.WARNING(f'Deactivated {deactivated} skills no longer in YAML'))

        # Second pass: wire up prerequisites (all skills must exist first)
        for entry in skills_data:
            prereq_ids = entry.get('prerequisites', []) or []
            if not prereq_ids:
                continue
            skill = Skill.objects.get(skill_id=entry['id'])
            prereqs = Skill.objects.filter(skill_id__in=prereq_ids)
            missing = set(prereq_ids) - set(prereqs.values_list('skill_id', flat=True))
            if missing:
                self.stderr.write(f"WARNING: {entry['id']} references unknown prerequisites: {missing}")
            skill.prerequisites.set(prereqs)

        self.stdout.write(
            self.style.SUCCESS(f'Done. Created: {created}, Updated: {updated}, Total: {len(skills_data)}')
        )
