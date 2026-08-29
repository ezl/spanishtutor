"""
Micro-reading: the reception channel.

Every other session type has the student produce language. None of them has the
student COMPREHEND language they did not produce, which means every word they
meet is one Luz chose to say. That is survivable to B1 and fatal above it.

Three or four sentences of personalised text costs the reader about twenty
seconds and is far lower effort than producing, which suits a distracted user
better than anything else available in a chat format. It carries due vocabulary
in context, supplies the volume of encounters that spaced retrieval alone cannot,
and gives the long input that "summarise this" and "narrate this" need in order
to be teachable at all.

Deliberately shaped as a TASK UNDER A CONSTRAINT rather than a lesson with a
skill_id: no Skill row, no grid cell, no next-unscored progression. Above B2 the
bottleneck stops being "you do not know the form" and becomes range, precision
and comprehension, where "the next unscored skill" has no meaning. Building this
without a skill_id costs nothing now and keeps that option open.
"""
import logging
import re

from asgiref.sync import sync_to_async

from .core import call_llm

logger = logging.getLogger(__name__)

SENTENCES = 4
MAX_WORDS_SEEDED = 6

PASSAGE_PROMPT = """Write {sentences} sentences of connected Spanish prose for a \
CEFR {level} learner.

It must read as one small piece about a real situation, not {sentences} unrelated \
example sentences. Someone should be able to follow it as a story or a scene.

Build it around this person's life:
{interests}

Work these words in naturally, as many as fit without strain:
{words}

Rules:
- {level} grammar only. Do not reach for structures above their level to fit a word in.
- Do not gloss anything inside the passage and do not add a translation.
- No title, no preamble, no commentary. Only the passage.
- If a word will not fit without contorting the sentence, leave it out. A natural \
passage carrying four words beats a stilted one carrying six."""

QUESTION_PROMPT = """Here is a Spanish passage a student has just read:

{passage}

Ask ONE question about it, in {question_language}. The question must be \
answerable only by having understood the passage -- not by pattern-matching a \
word from it.

Keep it to one sentence. No preamble."""


def _question_language(level: str) -> str:
    return 'English' if level in ('A1', 'A2', '') else 'Spanish'


class PassageGenerationFailed(RuntimeError):
    """Raised rather than returning '' so the caller can tell a failed generation
    from a genuinely empty one. An empty string that means both is the shape that
    has produced most of the silent bugs in this codebase."""


def _strip(raw: str) -> str:
    text = re.sub(r'^```.*?$|^```$', '', (raw or '').strip(), flags=re.MULTILINE)
    return text.strip()


async def build_passage(user, words: list) -> str:
    """Words are LexItems to seed. Returns the passage, or raises."""
    interests = (user.interests or '').strip() or 'No interests recorded yet.'
    seeded = ', '.join(w.es for w in words[:MAX_WORDS_SEEDED]) or '(none in particular)'

    raw = await call_llm(
        [{'role': 'user', 'content': PASSAGE_PROMPT.format(
            sentences=SENTENCES,
            level=user.estimated_cefr_level or 'A1',
            interests=interests,
            words=seeded)}],
        user=user,
        max_tokens=600,
    )
    passage = _strip(raw)
    if not passage:
        raise PassageGenerationFailed(
            f'empty passage for user {user.pk} (seeded: {seeded})')
    return passage


async def build_question(user, passage: str) -> str:
    raw = await call_llm(
        [{'role': 'user', 'content': QUESTION_PROMPT.format(
            passage=passage,
            question_language=_question_language(user.estimated_cefr_level or ''))}],
        user=user,
        max_tokens=200,
    )
    question = _strip(raw)
    if not question:
        raise PassageGenerationFailed(f'empty question for user {user.pk}')
    return question


async def open_reading(user) -> dict:
    """A complete micro-reading turn: passage plus one comprehension question.

    Delivered as ONE message. The passage alone is already a complete unit of
    value -- a student who reads it and leaves has had the exposure, and the
    question is a bonus rather than the point. That is what makes this safe at a
    2-turn median: nothing here depends on a second turn arriving.
    """
    from .vocabulary import _select

    selected = await sync_to_async(_select)(user)
    words = selected['due'] + selected['new']

    passage = await build_passage(user, words)
    question = await build_question(user, passage)

    logger.info('micro-reading for user %s seeded with %d words', user.pk, len(words))
    return {
        'passage': passage,
        'question': question,
        'seeded': [w.es for w in words[:MAX_WORDS_SEEDED]],
        'text': f'{passage}\n\n{question}',
    }
