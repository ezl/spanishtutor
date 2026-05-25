"""
Management command: review_quiz_questions

Reviews QuizQuestion rows against the question framework. For each skill:
  1. Validates that question format matches curriculum/question_framework.yaml (no LLM)
  2. Calls LLM to check appropriateness, fill thin rubrics, and verify subject variety

Usage:
    python manage.py review_quiz_questions --dry-run
    python manage.py review_quiz_questions --skill b1_subjunctive_triggers_want --fill-rubrics
    python manage.py review_quiz_questions --fill-rubrics
"""
import json
import yaml
from pathlib import Path
from django.core.management.base import BaseCommand
from django.conf import settings
import anthropic

FRAMEWORK_PATH = Path(__file__).resolve().parents[3] / 'curriculum' / 'question_framework.yaml'

VOCAB_MARKERS = {'_vocab_', '_greetings', '_numbers', '_dates', '_idiomatic', '_register_shifts'}


def _skill_type(skill_id: str) -> str:
    return 'vocab' if any(m in skill_id for m in VOCAB_MARKERS) else 'grammar'


REVIEW_SYSTEM = (
    "You review Spanish placement quiz questions for a language learning system. "
    "Output valid JSON only — no prose, no markdown fences."
)

REVIEW_PROMPT = """Review these quiz questions for the skill below.

Skill: {skill_id} | {name} | {cefr_level} | type: {skill_type}
Description: {description}

Framework rule for this cell ({cefr_level} {skill_type}):
  Expected format: {expected_format}
  Prompt language: {prompt_language}
{distractor_section}
Questions to review:
{questions_block}

Return a JSON object with two keys:
  "questions": array of objects, one per question in the same order:
    {{
      "id": <integer question pk>,
      "appropriate": true or false,
      "flag_reason": "reason string" or null,
      "rubric": "improved rubric string" or null
    }}
  "subject_variety_ok": true or false

Rules:
- appropriate=false if the question tests something outside the skill description, or if the
  format is wrong for this cell (but format mismatches are already flagged separately — you
  can still flag other appropriateness issues)
- freeform_word rubric: comma-separated list of acceptable answer variants; accent-free forms ok
- freeform_response rubric: pipe-separated list of target structures for the evaluator to look for
- multiple_choice: rubric must be null — return null, do not improve it
- Missing accents are always acceptable — the rubric should never penalize them
- If the current rubric is already good, return it unchanged
- subject_variety_ok: true if the set covers at least 3 of (yo, tú, él/ella, nosotros, ellos)
  for grammar skills; always true for vocab skills

Output only the JSON object."""


def _build_questions_block(questions):
    lines = []
    for q in questions:
        lines.append(f"  id: {q.pk}")
        lines.append(f"  format: {q.format}")
        lines.append(f"  question_text: {q.question_text!r}")
        if q.options:
            lines.append(f"  options: {q.options}")
        lines.append(f"  correct_answer: {q.correct_answer!r}")
        lines.append(f"  current_rubric: {q.rubric!r}")
        lines.append("")
    return "\n".join(lines)


class Command(BaseCommand):
    help = 'Review QuizQuestion rows for appropriateness, rubric quality, and framework compliance'

    def add_arguments(self, parser):
        parser.add_argument('--skill', type=str, help='Review only this skill_id')
        parser.add_argument('--dry-run', action='store_true',
                            help='Print proposed changes without writing to DB')
        parser.add_argument('--fill-rubrics', action='store_true',
                            help='Write improved rubrics to DB (default: report only)')
        parser.add_argument('--inactive-only', action='store_true',
                            help='Review only inactive questions (default: review all)')

    def handle(self, *args, **options):
        from learner.models import Skill, QuizQuestion

        framework = yaml.safe_load(FRAMEWORK_PATH.read_text())

        skill_filter = options['skill']
        dry_run = options['dry_run']
        fill_rubrics = options['fill_rubrics']
        inactive_only = options['inactive_only']

        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

        qs = Skill.objects.filter(active=True)
        if skill_filter:
            qs = qs.filter(skill_id=skill_filter)
            if not qs.exists():
                self.stderr.write(f"Skill '{skill_filter}' not found.")
                return

        n_flagged = 0
        n_rubrics = 0
        n_ok = 0
        n_format_errors = 0

        for skill in qs.order_by('order'):
            questions_qs = skill.quiz_questions.all()
            if inactive_only:
                questions_qs = questions_qs.filter(active=False)
            questions = list(questions_qs)

            if not questions:
                continue

            skill_type = _skill_type(skill.skill_id)
            cell = framework.get(skill.cefr_level, {}).get(skill_type)
            if cell is None:
                self.stderr.write(
                    f"  WARNING: no framework entry for {skill.cefr_level}/{skill_type} "
                    f"(skill: {skill.skill_id}) — skipping"
                )
                continue

            self.stdout.write(f"\n{skill.skill_id} ({skill.cefr_level}/{skill_type}) — {len(questions)} question(s)")

            # 1. Format check — no LLM needed, authoritative from YAML
            expected_format = cell['format']
            for q in questions:
                if q.format != expected_format:
                    self.stdout.write(
                        self.style.ERROR(
                            f"  ✗ q{q.pk}: format={q.format!r}, expected={expected_format!r}"
                        )
                    )
                    n_format_errors += 1

            # 2. LLM review — appropriateness, rubric fill, subject variety
            distractor_section = ''
            if cell.get('distractor_rule') and 'N/A' not in cell['distractor_rule']:
                distractor_section = f"  Distractor rule: {cell['distractor_rule'].strip()}\n"

            prompt = REVIEW_PROMPT.format(
                skill_id=skill.skill_id,
                name=skill.name,
                cefr_level=skill.cefr_level,
                skill_type=skill_type,
                description=skill.description or skill.name,
                expected_format=expected_format,
                prompt_language=cell['prompt_language'],
                distractor_section=distractor_section,
                questions_block=_build_questions_block(questions),
            )

            try:
                response = client.messages.create(
                    model='claude-sonnet-4-6',
                    max_tokens=2000,
                    system=REVIEW_SYSTEM,
                    messages=[{'role': 'user', 'content': prompt}],
                )
                raw = response.content[0].text.strip()
                review = json.loads(raw)
            except (json.JSONDecodeError, IndexError, anthropic.APIError) as exc:
                self.stderr.write(f"  ERROR calling LLM for {skill.skill_id}: {exc}")
                continue

            question_map = {q.pk: q for q in questions}
            skill_ok = True

            for result in review.get('questions', []):
                q = question_map.get(result['id'])
                if q is None:
                    continue

                if not result.get('appropriate', True):
                    self.stdout.write(
                        self.style.ERROR(f"  ✗ q{q.pk}: {result.get('flag_reason', 'flagged')}")
                    )
                    n_flagged += 1
                    skill_ok = False

                new_rubric = result.get('rubric')
                if new_rubric and new_rubric != q.rubric and q.format != 'multiple_choice':
                    self.stdout.write(f"  ⚠ q{q.pk} rubric {'would be' if dry_run else ''} updated")
                    self.stdout.write(f"    before: {q.rubric!r}")
                    self.stdout.write(f"    after:  {new_rubric!r}")
                    if fill_rubrics and not dry_run:
                        q.rubric = new_rubric
                        q.save(update_fields=['rubric'])
                    n_rubrics += 1

            if skill_type == 'grammar' and not review.get('subject_variety_ok', True):
                self.stdout.write(
                    self.style.WARNING(
                        f"  ⚠ {skill.skill_id}: subject variety insufficient — question set skewed toward one person"
                    )
                )
                skill_ok = False

            if skill_ok and not n_format_errors:
                self.stdout.write(f"  ✓ ok")
                n_ok += 1

        action = "(dry run)" if dry_run else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"\nDone {action}. {n_format_errors} format error(s), "
                f"{n_flagged} flagged, {n_rubrics} rubric(s) {'would be ' if dry_run else ''}filled, "
                f"{n_ok} skill(s) ok."
            )
        )
        if fill_rubrics and dry_run:
            self.stdout.write("Re-run without --dry-run to write rubric improvements.")
        elif n_rubrics and not fill_rubrics:
            self.stdout.write("Re-run with --fill-rubrics to write rubric improvements.")
