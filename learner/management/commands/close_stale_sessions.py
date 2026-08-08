"""
Close sessions with no activity for > INACTIVITY_TIMEOUT_MINUTES.

Runs the same close path as engine/session.py's lazy inactivity check, so
scoring and interest extraction still fire. Intended for a Railway cron
(e.g. every 10 minutes) so the DB doesn't accumulate long-open sessions
between user visits.
"""
import asyncio
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import F, Max
from django.db.models.functions import Coalesce
from django.utils import timezone

from engine.session import INACTIVITY_TIMEOUT_MINUTES, _close_session_record
from learner.models import Session


class Command(BaseCommand):
    help = f'Close sessions inactive for > {INACTIVITY_TIMEOUT_MINUTES} minutes'

    def handle(self, *args, **options):
        threshold = timezone.now() - timedelta(minutes=INACTIVITY_TIMEOUT_MINUTES)

        stale = list(
            Session.objects
                .filter(ended_at__isnull=True)
                .exclude(session_type='onboarding')
                .annotate(last_activity=Coalesce(Max('events__timestamp'), F('started_at')))
                .filter(last_activity__lt=threshold)
                .select_related('user')
        )

        if not stale:
            self.stdout.write('No stale sessions.')
            return

        for session in stale:
            age_min = int((timezone.now() - session.last_activity).total_seconds() // 60)
            self.stdout.write(
                f'Closing session {session.id} '
                f'(user={session.user}, type={session.session_type}, idle={age_min}m)'
            )
            try:
                asyncio.run(_close_session_record(session, session.user))
            except Exception as exc:
                self.stderr.write(f'  failed: {exc!r}')

        self.stdout.write(self.style.SUCCESS(f'Closed {len(stale)} session(s).'))
