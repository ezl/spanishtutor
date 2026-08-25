"""
Consolidate the accumulated interest pool.

Dedup was exact-string only, so weeks of extraction deposited the same fact in
slightly different words each session (141 rows for roughly 15 real facts, two
thirds of them one topic said four ways). Categories were free text, so the
controlled vocabulary the rest of the system now depends on does not match any
of the historical rows. And a few rows contradict each other outright -- a
friend who is also a spouse, a friend who is also a child.

Fixing extraction (d006521) stops this accumulating. It does not clean what is
already there, and the contradictions in particular can still reach a lesson.

    railway run python manage.py clean_interests --dry-run
    railway run python manage.py clean_interests --apply
"""
import asyncio

from django.core.management.base import BaseCommand

from engine.interests import apply_cleanup_plan, build_cleanup_plan


class Command(BaseCommand):
    help = 'Consolidate duplicate interests, remap categories, drop contradictions'

    def add_arguments(self, parser):
        parser.add_argument('--user-id', type=int, help='Limit to one user')
        parser.add_argument('--apply', action='store_true',
                            help='Write the changes (default is a dry run)')

    def handle(self, *args, **options):
        from learner.models import User, UserInterest

        users = User.objects.all()
        if options['user_id']:
            users = users.filter(pk=options['user_id'])

        dry_run = not options['apply']

        for user in users:
            before = UserInterest.objects.filter(user=user).count()
            if not before:
                continue

            plan, rows = asyncio.run(build_cleanup_plan(user))
            by_id = {r.pk: r for r in rows}

            self.stdout.write(f"\n=== {user} — {before} rows")
            for cluster in plan['clusters']:
                if len(cluster['members']) > 1:
                    self.stdout.write(
                        f"  merge -> {cluster['canonical']}  [{cluster['category']}]")
                    for pk in cluster['members']:
                        row = by_id.get(pk)
                        if row:
                            self.stdout.write(f"      - {row.topic}  ({row.category})")
            for item in plan['contradictions']:
                if item.get('safe'):
                    self.stdout.write(self.style.WARNING(
                        f"  contradiction -> keeping only \"{item['safe']}\""))
                else:
                    self.stdout.write(self.style.WARNING(
                        "  contradiction -> removing all (nothing survives)"))
                self.stdout.write(f"      reason: {item['reason']}")
                for pk in item['members']:
                    row = by_id.get(pk)
                    if row:
                        self.stdout.write(f"      - {row.topic}")
            if plan['drop']:
                self.stdout.write(f"  dropping {len(plan['drop'])} low-value rows")

            summary = asyncio.run(apply_cleanup_plan(user, plan, dry_run=dry_run))
            after = UserInterest.objects.filter(user=user).count()
            self.stdout.write(
                f"  clusters={summary['clusters']} merged_rows={summary['merged']} "
                f"contradictions={summary['contradictions']} dropped={summary['dropped']}")
            if dry_run:
                self.stdout.write(self.style.WARNING(f"  dry run — still {after} rows"))
            else:
                self.stdout.write(self.style.SUCCESS(f"  {before} -> {after} rows"))
