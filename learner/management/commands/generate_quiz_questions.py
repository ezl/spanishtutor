"""
Management command: generate_quiz_questions

Generates QuizQuestion rows (active=False) for skills that have fewer than
3 active questions. Operator reviews in Django admin and sets active=True
before questions go live.

Usage:
    python manage.py generate_quiz_questions
    python manage.py generate_quiz_questions --skill a1_ser_estar_basic
    python manage.py generate_quiz_questions --dry-run
"""
import json
import asyncio
from django.core.management.base import BaseCommand
from django.conf import settings
import anthropic

GENERATION_SYSTEM = (
    "You generate Spanish placement quiz questions. "
    "Output valid JSON only — no prose, no markdown fences."
)

GENERATION_PROMPT = """Generate {count} placement quiz question(s) for the skill:
  skill_id: {skill_id}
  name: {skill_name}
  cefr_level: {cefr_level}
  description: {description}

Format rules by level:
  A1-A2 vocab: direct translation MC only ("What does X mean?") — never fill-in-blank.
  A1-A2 grammar: short fill-in-blank or MC; all 4 options same grammatical category.
  B1+: freeform_response encouraged (open-ended prompts anyone can answer — no personalization).
  All MC distractors: same semantic/grammatical category as correct answer.
  Missing accents treated as correct — do NOT penalize in rubric.

Return a JSON array of {count} objects, each with:
  "format": one of "multiple_choice", "freeform_word", "freeform_response"
  "question_text": the question shown to the student
  "options": {{"a": ..., "b": ..., "c": ..., "d": ...}} for MC, null otherwise
  "correct_answer": "a"/"b"/"c"/"d" for MC, word/phrase for freeform_word, exemplar sentence for freeform_response
  "rubric": for freeform_response, list target structures; for freeform_word, list acceptable variants (accent-free ok); empty string for MC

Output only the JSON array."""


class Command(BaseCommand):
    help = 'Generate QuizQuestion rows (active=False) for skills lacking questions'

    def add_arguments(self, parser):
        parser.add_argument('--skill', type=str, help='Generate only for this skill_id')
        parser.add_argument('--dry-run', action='store_true', help='Print questions without saving')
        parser.add_argument('--count', type=int, default=5, help='Questions per skill (default 5)')
        parser.add_argument('--min-active', type=int, default=3,
                            help='Skip skills already with this many active questions (default 3)')

    def handle(self, *args, **options):
        from learner.models import Skill, QuizQuestion

        skill_filter = options['skill']
        dry_run = options['dry_run']
        count = options['count']
        min_active = options['min_active']

        qs = Skill.objects.filter(active=True)
        if skill_filter:
            qs = qs.filter(skill_id=skill_filter)
            if not qs.exists():
                self.stderr.write(f"Skill '{skill_filter}' not found.")
                return

        if not dry_run:
            client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

        generated = 0
        skipped = 0
        for skill in qs.order_by('order'):
            active_count = skill.quiz_questions.filter(active=True).count()
            if active_count >= min_active:
                skipped += 1
                continue

            self.stdout.write(f"  {skill.skill_id} ({skill.cefr_level}): {active_count} active → would generate {count}")
            if dry_run:
                generated += count
                continue

            self.stdout.write(f"    generating {count} question(s)...")
            prompt = GENERATION_PROMPT.format(
                count=count,
                skill_id=skill.skill_id,
                skill_name=skill.name,
                cefr_level=skill.cefr_level,
                description=skill.description or skill.name,
            )

            try:
                response = client.messages.create(
                    model='claude-sonnet-4-6',
                    max_tokens=2000,
                    system=GENERATION_SYSTEM,
                    messages=[{'role': 'user', 'content': prompt}],
                )
                raw = response.content[0].text.strip()
                questions = json.loads(raw)
            except (json.JSONDecodeError, IndexError, anthropic.APIError) as exc:
                self.stderr.write(f"    ERROR for {skill.skill_id}: {exc}")
                continue

            for q in questions:
                QuizQuestion.objects.create(
                    skill=skill,
                    format=q['format'],
                    question_text=q['question_text'],
                    options=q.get('options'),
                    correct_answer=q['correct_answer'],
                    rubric=q.get('rubric', ''),
                    active=False,
                )
            generated += len(questions)

        action = 'Would create' if dry_run else 'Created'
        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone. {action} {generated} question(s). "
                f"Skipped {skipped} skill(s) already at ≥{min_active} active questions."
            )
        )
        if not dry_run and generated:
            self.stdout.write(
                "Questions are inactive (active=False). "
                "Review in Django admin and set active=True before they go live."
            )
