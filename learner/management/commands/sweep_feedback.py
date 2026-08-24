"""
Find feedback nobody labelled.

Reads SessionEvent rows rather than hooking a conversation handler, so it
cannot be blind to a session type the way in-handler capture was: feedback
typed during an SRS review on 2026-08-23 had nowhere to go because capture
lived only inside teach_drill.

Deliberately offline. Feedback is reviewed in batches, so nothing needs this
within the turn -- which buys full conversational context for the judgement
and costs the live conversation path nothing. It is also re-runnable: improve
the detection prompt and sweep the backlog again. An inline classifier only
ever sees each message once, which is exactly how three were lost.

Intended for a Railway cron (e.g. hourly), or run on demand before a review:
    railway run python manage.py sweep_feedback --days 7 --dry-run
"""
import asyncio

from django.core.management.base import BaseCommand

from engine.feedback import sweep


class Command(BaseCommand):
    help = 'Detect unlabelled feedback in recent turns and log it'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=3,
                            help='How far back to scan (default: 3)')
        parser.add_argument('--limit', type=int, default=200,
                            help='Max turns to scan in one pass (default: 200)')
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would be logged without writing')

    def handle(self, *args, **options):
        result = asyncio.run(sweep(
            days=options['days'],
            limit=options['limit'],
            dry_run=options['dry_run'],
        ))

        self.stdout.write(f"scanned {result['scanned']} unlogged turns")
        for event_pk, interpretation in result['hits']:
            self.stdout.write(f"  event {event_pk}: {interpretation}")

        if options['dry_run']:
            self.stdout.write(self.style.WARNING(
                f"dry run — would log {result['would_create']}"))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"logged {result['created']} new feedback rows"))
