"""
Focused LLM evaluator for placement quiz answers.

Bypasses the Luz Ángela persona via system_override. Returns an integer 1-4.
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
Skill being tested: {skill_name}
Skill description: {skill_description}

Question: {question_text}
Format: {format}
Correct answer: {correct_answer}
{rubric_block}
Student said: "{student_response}"

Score 1-4 based on whether the student correctly demonstrates the skill being tested.
Do not penalize for errors in grammar or vocabulary outside that specific skill.
  4 = skill demonstrated correctly, no meaningful errors in the target area (missing accent ok)
  3 = skill demonstrated, minor error in the target area only
  2 = right idea for the skill, but significant error in how it's applied
  1 = skill not demonstrated, wrong, incomprehensible, or expressed not knowing

Multiple choice: 4 if correct option selected, 1 otherwise.

SCORE: <1|2|3|4>"""


async def evaluate_answer(question: QuizQuestion, student_response: str, skill=None) -> int:
    """Score a student's quiz answer. Returns 1–4; returns 1 on parse failure."""
    rubric_block = _RUBRIC_LINE.format(rubric=question.rubric) if question.rubric else ""
    prompt = _PROMPT.format(
        skill_name=skill.name if skill else "Spanish",
        skill_description=skill.description if skill else "",
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
