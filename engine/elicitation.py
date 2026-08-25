"""
Choosing what to ask the student about themselves.

Interest extraction was passive: it mined whatever the conversation contained,
and the conversation was generated FROM the interests, so the pool fed itself
and never grew. This module supplies the other half -- deliberate questions,
aimed at whatever the pool is thinnest on.

Two ideas do the work:

  * The question IS the practice. Each entry is tagged with the grammar it
    naturally elicits, so a preterite lesson can ask what you did last weekend
    and a subjunctive lesson can ask what you hope for. Elicitation then costs
    no lesson time.
  * The fact lives in the follow-up. "Do you cook or eat out?" yields "eat
    out", which is worthless; "which place, and what did you order?" yields a
    name and a dish. Every entry is a pair for that reason.

The bank itself is config (curriculum/elicitation.yaml), reloaded at call time
so questions can be tuned without a redeploy.
"""
import pathlib

import yaml

BANK_PATH = pathlib.Path(__file__).resolve().parent.parent / 'curriculum' / 'elicitation.yaml'


def load_bank() -> dict:
    """Read the question bank. Not cached -- config over code, runtime reload."""
    return yaml.safe_load(BANK_PATH.read_text())


def category_gaps(held_counts: dict) -> list:
    """Controlled-vocabulary categories, thinnest first.

    `held_counts` maps category -> how many facts we hold. Categories absent
    from it count as zero, which is the point: a category we have never once
    recorded is the most valuable thing to ask about.
    """
    categories = load_bank()['categories']
    return sorted(categories, key=lambda c: (held_counts.get(c, 0), c))


def select_question(held_counts: dict, grammar_tags=None, exclude_ids=()) -> dict | None:
    """Pick the question to ask, or None when the bank is exhausted.

    Grammar match first so the question doubles as practice for the skill being
    taught; among the matches, the thinnest category, so each answer is worth as
    much as possible. Falls back to pure gap order when the lesson's structure
    has no question attached to it.
    """
    bank = load_bank()
    excluded = set(exclude_ids)
    available = [q for q in bank['questions'] if q['id'] not in excluded]
    if not available:
        return None

    gaps = category_gaps(held_counts)
    rank = {category: i for i, category in enumerate(gaps)}

    tags = set(grammar_tags or ())
    matching = [q for q in available if tags & set(q['grammar'])]
    pool = matching or available

    return min(pool, key=lambda q: (rank.get(q['category'], len(rank)), q['id']))


def grammar_tags_for_skill(skill_id: str) -> list:
    """Grammar tags from the bank that this skill's id mentions.

    Lets a preterite lesson ask what you did last weekend and a subjunctive
    lesson ask what you hope for, so the elicitation turn is also practice.
    """
    sid = (skill_id or '').lower()
    tags = {tag for q in load_bank()['questions'] for tag in q['grammar']}
    return sorted((t for t in tags if t in sid), key=len, reverse=True)


async def held_category_counts(user) -> dict:
    """How many facts we hold per category, for gap-finding."""
    from asgiref.sync import sync_to_async
    from learner.models import UserInterest

    def _counts():
        counts = {}
        for category in UserInterest.objects.filter(user=user).values_list('category', flat=True):
            counts[category] = counts.get(category, 0) + 1
        return counts

    return await sync_to_async(_counts)()


async def pick_for_session(user, skill_id: str = None, exclude_ids=()) -> dict | None:
    """The question to ask this session, or None."""
    counts = await held_category_counts(user)
    return select_question(counts,
                           grammar_tags=grammar_tags_for_skill(skill_id),
                           exclude_ids=exclude_ids)


def build_elicitation_block(question: dict | None, cefr_level: str = 'B1') -> str:
    """Prompt fragment instructing Luz to ask one question about the student.

    Answering must not be gated on their Spanish: the point of the turn is
    information, not production, and a student who can only answer within their
    L2 ceiling will volunteer far less than they know. The drills carry the
    assessment, so nothing here is marked -- being graded on a question about
    your own life makes people answer defensively and briefly, which defeats it.
    """
    if not question:
        return ''
    advanced = (cefr_level or 'A1').upper() in {'B1', 'B2', 'C1', 'C2'}
    opener = question['opener_es'] if advanced else question['opener_en']
    follow_up = question['follow_up_es'] if advanced else question['follow_up_en']
    return (
        "\n\nASK ABOUT THEM (one turn, before anything else):\n"
        f"- Ask this, naturally and in your own voice: \"{opener}\"\n"
        f"- If they answer briefly, follow up once with: \"{follow_up}\" -- the "
        "specifics are the point, a one-word answer tells us nothing.\n"
        "- Tell them they can answer in English if that is easier. This is not a "
        "test and their Spanish should not limit what they can tell you.\n"
        "- Do NOT correct their Spanish on this turn, do NOT mark it ✓ or ✗, and "
        "do NOT treat it as a lesson answer. React like a person who is "
        "interested, then move on.\n"
    )
