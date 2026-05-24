"""
Focused LLM evaluator for placement quiz answers.

Bypasses the Luz Angela persona via system_override. Returns an integer 1-4.
"""
import logging
from learner.models import QuizQuestion
from engine.core import call_llm

logger = logging.getLogger('engine')

EVALUATOR_SYSTEM = (
    "You are scoring a Spanish placement quiz answer. "
    "Follow the output format exactly. Output only: SCORE: <1|2|3|4>"
)

_RUBRIC_LINE = "Rubric / target structures: {rubric}"

_PROMPT = """\
Question: {question_text}
Format: {format}
Correct answer: {correct_answer}
{rubric_block}
Student said: "{student_response}"

Score 1-4:
  4 = correct, natural, no meaningful errors (missing accent ok)
  3 = correct meaning/structure, minor error only
  2 = right idea, significant error (wrong form, key word missing)
  1 = wrong, incomprehensible, or expressed not knowing

Multiple choice: 4 if correct option selected, 1 otherwise.

SCORE: <1|2|3|4>"""


async def evaluate_answer(question: QuizQuestion, student_response: str) -> int:
    """Score a student's quiz answer. Returns 1–4; returns 1 on parse failure."""
    rubric_block = _RUBRIC_LINE.format(rubric=question.rubric) if question.rubric else ""
    prompt = _PROMPT.format(
        question_text=question.question_text,
        format=question.format,
        correct_answer=question.correct_answer,
        rubric_block=rubric_block,
        student_response=student_response,
    )
    try:
        response = await call_llm(
            messages=[{"role": "user", "content": prompt}],
            system_override=EVALUATOR_SYSTEM,
        )
        return _parse_score(response)
    except Exception:
        logger.exception("evaluate_answer LLM call failed; defaulting to 1")
        return 1


def _parse_score(text: str) -> int:
    """Extract 'SCORE: N' from evaluator response. Returns 1 on failure."""
    for line in text.strip().splitlines():
        line = line.strip()
        if line.startswith("SCORE:"):
            try:
                n = int(line.split(":", 1)[1].strip())
                if n in (1, 2, 3, 4):
                    return n
            except (ValueError, IndexError):
                pass
    logger.warning("Could not parse evaluator score from: %r", text)
    return 1
