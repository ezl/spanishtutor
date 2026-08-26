"""
Sync curriculum/lexicon.yaml into the LexItem table.
Safe to run repeatedly -- upserts by (es, owner=NULL).

Only the shared core catalogue is touched. Words generated from a person's
interests carry an owner and are never created, updated or deactivated here.
"""
import os
import yaml
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings

from learner.models import LexItem


FIELDS = ('en', 'pos', 'gender', 'note', 'cefr_level')


class Command(BaseCommand):
    help = 'Sync lexicon.yaml into the LexItem table'

    def handle(self, *args, **options):
        path = os.path.join(settings.BASE_DIR, 'curriculum', 'lexicon.yaml')
        with open(path) as f:
            data = yaml.safe_load(f)

        # A word may appear in several packs. Collapse to one entry per lemma:
        # union the pack tags, and let the first non-empty value win per field so
        # a later pack can fill a blank without clobbering an earlier answer.
        merged = {}
        for pack in data['packs']:
            for word in pack['words']:
                es = word['es']
                entry = merged.setdefault(es, {'packs': [], 'analyzable': True})
                if pack['id'] not in entry['packs']:
                    entry['packs'].append(pack['id'])
                for field in FIELDS:
                    value = word.get(field) or (pack.get('cefr_level') if field == 'cefr_level' else '')
                    if value and not entry.get(field):
                        entry[field] = value
                if word.get('analyzable') is False:
                    entry['analyzable'] = False

        # An empty parse is never a legitimate state, and the sweep below cannot
        # tell it from a real one: exclude(es__in=[]) excludes NOTHING, so a
        # truncated write or a `packs:` key with nothing under it would deactivate
        # the entire catalogue and report success. Raise rather than guard the
        # update -- guarding would leave an empty file silently doing nothing,
        # which is the same bug one layer up.
        if not merged:
            raise CommandError(
                f'{path} parsed to zero words. Refusing to sync: this would '
                f'deactivate every word in the catalogue.')

        created = updated = 0
        for es, entry in merged.items():
            _, is_new = LexItem.objects.update_or_create(
                es=es, owner=None,
                defaults={
                    'en': entry.get('en', ''),
                    'pos': entry.get('pos', ''),
                    'gender': entry.get('gender', ''),
                    'note': entry.get('note', ''),
                    'cefr_level': entry.get('cefr_level', ''),
                    'packs': ','.join(entry['packs']),
                    'analyzable': entry['analyzable'],
                    'active': True,
                },
            )
            created += is_new
            updated += not is_new

        gone = (LexItem.objects.filter(owner__isnull=True, active=True)
                               .exclude(es__in=merged.keys())
                               .update(active=False))

        self.stdout.write(self.style.SUCCESS(
            f'Lexicon synced: {created} created, {updated} updated, {gone} deactivated '
            f'({len(data["packs"])} packs)'))
