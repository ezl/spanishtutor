import asyncio
import django.conf
from django.core.management.base import BaseCommand
from bot.client import run


class Command(BaseCommand):
    help = 'Run the Luz Angela Discord bot'

    def handle(self, *args, **options):
        asyncio.run(run())
