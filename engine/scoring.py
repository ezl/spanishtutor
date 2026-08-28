"""
Post-session skill scoring: frontier selection, LLM evaluation, SRS updates.
"""
import json
import logging
from datetime import timedelta

import anthropic
from asgiref.sync import sync_to_async

from .curriculum import LEVEL_ORDER
from django.conf import settings
from django.utils import timezone


logger = logging.getLogger(__name__)

SRS_INTERVALS = {1: 1, 2: 3, 3: 7, 4: 21}  # score -> days until next review

SCORING_PROMPT = """You are evaluating a Spanish tutoring session transcript. Your task is to score only the skills that were meaningfully practiced.

Skills to evaluate (these were the session's target skills):
{skill_list}

CEFR modes: writing (student produced Spanish text), reading (student demonstrated reading comprehension).
This was a {session_type} session, so the mode is: {mode}.

Transcript:
{transcript}

Score each skill that had 2-3+ exchanges directly exercising it. Skip skills with insufficient evidence — a passing mention does not count.

Scores:
  1 = struggled / major errors throughout
  2 = partial understanding / significant errors
  3 = solid / minor errors only
  4 = mastered / no meaningful errors

Respond with a JSON array. Only include skills you observed meaningfully practiced:
[
  {{"skill_id": "a1_present_ar", "score": 3}},
  ...
]

If no skills were sufficiently practiced, return [].
Do not include any text outside the JSON array."""


def _session_mode(session_type: str) -> str:
    if session_type == 'reading':
        return 'reading'
    return 'writing'


async def get_frontier_skills(user) -> list:
    """
    Returns the 9-skill pool: 6 below frontier + 3 above.
    Frontier = first Skill (by order) where writing score > 0 and < 3.
    """
    from learner.models import Skill, SkillScore

    skills = await sync_to_async(lambda: list(Skill.objects.filter(active=True).order_by('order')))()
    scores_qs = await sync_to_async(
        lambda: list(SkillScore.objects.filter(user=user, mode='writing'))
    )()
    score_by_skill = {s.skill_id: s.score for s in scores_qs}

    frontier_idx = None
    for i, skill in enumerate(skills):
        s = score_by_skill.get(skill.id, 0)
        if 0 < s < 3:
            frontier_idx = i
            break

    if frontier_idx is None:
        # No frontier found — check if all mastered
        tested = [s for s in score_by_skill.values() if s > 0]
        if len(tested) == len(skills) and all(s >= 3 for s in tested):
            return []  # win state
        # Cold start or all untested — take first 3
        frontier_idx = 0

    below_start = max(0, frontier_idx - 6)
    below = skills[below_start:frontier_idx]
    above = skills[frontier_idx:frontier_idx + 3]
    return below + above


async def score_session(session, user) -> list:
    """
    Run post-session LLM scoring. Returns list of dicts with skill_id, score.
    Writes SkillScore + SkillScoreEvent records. Returns scored results for dev log.
    """
    from learner.models import Skill, SkillScore, SkillScoreEvent, SessionSkill, SessionEvent

    events = await sync_to_async(
        lambda: list(session.events.order_by('timestamp'))
    )()
    if not events:
        return []

    # Build transcript
    lines = []
    for e in events:
        if e.user_response:
            lines.append(f"Student: {e.user_response}")
        lines.append(f"Luz: {e.content}")
    transcript = "\n".join(lines)

    # Get session skills (written at open)
    session_skill_objs = await sync_to_async(
        lambda: list(SessionSkill.objects.filter(session=session).select_related('skill'))
    )()
    if not session_skill_objs:
        return []

    skill_list = "\n".join(
        f"- {ss.skill.skill_id}: {ss.skill.name} — {ss.skill.description}"
        for ss in session_skill_objs
    )
    mode = _session_mode(session.session_type)

    prompt = SCORING_PROMPT.format(
        skill_list=skill_list,
        session_type=session.session_type,
        mode=mode,
        transcript=transcript,
    )

    client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    try:
        resp = await client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        results = json.loads(raw)
    except Exception as exc:
        logger.error("Scoring LLM call failed: %s", exc)
        return []

    now = timezone.now()
    scored = []

    for item in results:
        skill_id_str = item.get('skill_id')
        score = item.get('score')
        if not skill_id_str or score not in (1, 2, 3, 4):
            continue

        try:
            skill = await sync_to_async(Skill.objects.get)(skill_id=skill_id_str)
        except Skill.DoesNotExist:
            logger.warning("Scoring returned unknown skill_id: %s", skill_id_str)
            continue

        interval_days = SRS_INTERVALS.get(score, 3)
        next_review = now + timedelta(days=interval_days)

        ss, _ = await sync_to_async(SkillScore.objects.get_or_create)(
            user=user, skill=skill, mode=mode,
            defaults={'score': 0},
        )
        await sync_to_async(
            lambda: SkillScore.objects.filter(pk=ss.pk).update(
                score=score,
                last_tested_at=now,
                next_review_at=next_review,
            )
        )()

        await sync_to_async(SkillScoreEvent.objects.create)(
            user=user, skill=skill, mode=mode, score=score, session=session,
        )

        scored.append({'skill_id': skill_id_str, 'skill_name': skill.name, 'score': score})

    # Check for reteach flags (score 1 any session, or score <=2 on last 2 sessions)
    for item in scored:
        skill = await sync_to_async(Skill.objects.get)(skill_id=item['skill_id'])
        await _check_reteach(user, skill, mode)

    return scored


async def _check_reteach(user, skill, mode):
    """Flag skills needing reteach by scheduling them next. No-op for now — decision tree handles priority."""
    from learner.models import SkillScoreEvent

    recent = await sync_to_async(
        lambda: list(
            SkillScoreEvent.objects.filter(user=user, skill=skill, mode=mode)
                                   .order_by('-scored_at')[:2]
        )
    )()

    if not recent:
        return

    if recent[0].score == 1:
        # Score 1 any session — mark next_review to now so it surfaces immediately
        await _bump_to_now(user, skill, mode)
    elif len(recent) >= 2 and recent[0].score <= 2 and recent[1].score <= 2:
        # Two consecutive sessions at score 2 or below
        await _bump_to_now(user, skill, mode)


async def _bump_to_now(user, skill, mode):
    from learner.models import SkillScore
    await sync_to_async(
        lambda: SkillScore.objects.filter(user=user, skill=skill, mode=mode).update(
            next_review_at=timezone.now()
        )
    )()


MILESTONE_SCORE = 3  # "you can use this" — 4 is reserved for SRS scheduling.


async def check_win_state(user) -> bool:
    """True if every ACTIVE skill has a writing score >= MILESTONE_SCORE.

    The active filter is load-bearing: this used to compare a count of the
    user's scores against a count of active skills without filtering the scores
    the same way, so a score on a retired skill counted toward completing the
    live grid. Six skills were deactivated in one afternoon during the A1
    rebuild, any of which would have inflated it.
    """
    from learner.models import Skill, SkillScore

    total = await sync_to_async(lambda: Skill.objects.filter(active=True).count())()
    mastered = await sync_to_async(
        lambda: SkillScore.objects.filter(
            user=user, mode='writing', score__gte=MILESTONE_SCORE, skill__active=True
        ).count()
    )()
    return total > 0 and mastered >= total


async def level_progress(user) -> dict:
    """{CEFR level: (skills at MILESTONE_SCORE or above, active skills)}.

    Per-level rather than one total so milestones arrive six times instead of
    once, and so adding skills to B2 cannot push an A2 learner's next milestone
    further away.
    """
    from learner.models import Skill, SkillScore

    def _counts():
        totals, done = {}, {}
        for level in Skill.objects.filter(active=True).values_list('cefr_level', flat=True):
            totals[level] = totals.get(level, 0) + 1
        rows = SkillScore.objects.filter(
            user=user, mode='writing', score__gte=MILESTONE_SCORE, skill__active=True
        ).select_related('skill')
        for r in rows:
            if r.skill:
                done[r.skill.cefr_level] = done.get(r.skill.cefr_level, 0) + 1
        return {lvl: (done.get(lvl, 0), total) for lvl, total in totals.items()}

    return await sync_to_async(_counts)()


async def completed_levels(user) -> list:
    """Levels where every active skill has reached MILESTONE_SCORE, in order.

    A level with no active skills is never complete -- otherwise an empty C2
    would be "won" on day one.
    """
    progress = await level_progress(user)
    return [lvl for lvl in LEVEL_ORDER
            if lvl in progress and progress[lvl][1] > 0
            and progress[lvl][0] >= progress[lvl][1]]
