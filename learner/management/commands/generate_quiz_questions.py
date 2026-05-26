"""
Management command: generate_quiz_questions

Generates QuizQuestion rows (active=False) for skills that have fewer than
--min-active active questions. Uses curriculum/question_framework.yaml to
give the LLM precise, level-specific format and content constraints.

Operator reviews in Django admin and sets active=True before questions go live.

Usage:
    python manage.py generate_quiz_questions
    python manage.py generate_quiz_questions --skill a1_ser_estar_basic
    python manage.py generate_quiz_questions --dry-run
"""
import json
import os
import yaml
from pathlib import Path
from django.core.management.base import BaseCommand
from django.conf import settings
import anthropic

FRAMEWORK_PATH = Path(__file__).resolve().parents[3] / 'curriculum' / 'question_framework.yaml'

VOCAB_MARKERS = {'_vocab_', '_greetings', '_numbers', '_dates', '_idiomatic', '_register_shifts'}


def _skill_type(skill_id: str) -> str:
    return 'vocab' if any(m in skill_id for m in VOCAB_MARKERS) else 'grammar'


GENERATION_SYSTEM = (
    "You generate Colombian Spanish placement quiz questions for a tutor set in Medellín, Colombia. "
    "All Spanish must reflect Colombian usage: ustedes (never vosotros), usted for formal address, "
    "Colombian vocabulary (carro, celular, apartamento, computador, jugo, tinto). "
    "Output valid JSON only — no prose, no markdown fences."
)

GENERATION_PROMPT = """Generate {count} placement quiz question(s) for the skill:
  skill_id: {skill_id}
  name: {skill_name}
  cefr_level: {cefr_level}
  description: {description}
  skill_type: {skill_type}

=== FRAMEWORK RULES FOR THIS SKILL ({cefr_level} {skill_type}) ===
Expected format: {expected_format}
Prompt language: {prompt_language}
{distractor_section}
Guidance:
{guidance}

Sample questions at this quality level:
{samples}

=== GLOBAL RULES (apply to every question) ===
- DIALECT: Colombian Spanish (Medellín). NEVER use vosotros — use ustedes for all second-person plural.
  Use Colombian vocabulary: carro, celular, apartamento, computador, jugo, tinto, etc.
- Missing accents are ALWAYS acceptable — never penalize for them in the rubric or correct_answer
- Never embed the correct answer in the question stem
- For multiple_choice: all 4 options must be real, valid Spanish (or English for English-prompt questions)
- Vary grammatical subjects (yo, tú, él/ella, nosotros, ellos) across the question set — do not skew toward "yo"
- Fill-in-blank questions: put ________ in question_text; store as format "freeform_word"
- For freeform_response rubric: use pipe-separated target structures for the evaluator
- For freeform_word rubric: comma-separated list of acceptable answer variants (accent-free ok)
- For multiple_choice: rubric must be null
- Lines beginning with [NOTE:] in the samples are spec annotations — NEVER include them in question_text
- (Translation: ...) lines in samples ARE student-facing — include them in question_text exactly as shown

Return a JSON array of {count} objects, each with:
  "format": one of "multiple_choice", "freeform_word", "freeform_response"
  "question_text": the question shown to the student
  "options": {{"a": ..., "b": ..., "c": ..., "d": ...}} for MC, null otherwise
  "correct_answer": "a"/"b"/"c"/"d" for MC, word/phrase for freeform_word, exemplar for freeform_response
  "rubric": pipe-separated structures for freeform_response; comma-separated variants for freeform_word; null for MC

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

        framework = yaml.safe_load(FRAMEWORK_PATH.read_text())

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

            skill_type = _skill_type(skill.skill_id)
            cell = framework.get(skill.cefr_level, {}).get(skill_type)
            if cell is None:
                self.stderr.write(
                    f"  WARNING: no framework entry for {skill.cefr_level}/{skill_type} "
                    f"(skill: {skill.skill_id}) — skipping"
                )
                continue

            self.stdout.write(
                f"  {skill.skill_id} ({skill.cefr_level}/{skill_type}): "
                f"{active_count} active → generating {count}"
            )
            if dry_run:
                generated += count
                continue

            distractor_section = ''
            if cell.get('distractor_rule') and 'N/A' not in cell['distractor_rule']:
                distractor_section = f"Distractor rule: {cell['distractor_rule'].strip()}"

            prompt = GENERATION_PROMPT.format(
                count=count,
                skill_id=skill.skill_id,
                skill_name=skill.name,
                cefr_level=skill.cefr_level,
                description=skill.description or skill.name,
                skill_type=skill_type,
                expected_format=cell['format'],
                prompt_language=cell['prompt_language'],
                distractor_section=distractor_section,
                guidance=cell['guidance'].strip(),
                samples=cell['samples'].strip(),
            )

            try:
                response = client.messages.create(
                    model='claude-sonnet-4-6',
                    max_tokens=3000,
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
                    rubric=q.get('rubric') or '',
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
