"""
Feedback capture.

Three layers write into SessionFeedback, and they overlap on purpose:

1. Deterministic literal capture (`looks_like_explicit_feedback`), run above
   handler routing so it works in every session type. The developer prefixes
   feedback with the literal word; on 2026-08-22/23 three such messages were
   lost because capture lived only inside the teach_drill classifier.
2. The inline classifier in teach_drill, which is free -- that turn already
   makes an LLM call -- and catches unprefixed feedback during a lesson.
3. The offline sweep (`manage.py sweep_feedback`), which reads SessionEvent
   rows and so cannot be blind to a session type. This is the layer that
   catches a real user complaining without ever using the word "feedback".

All three funnel through record_feedback(), which is idempotent, so the same
message seen by two layers produces one row.
"""
import json
import re

from asgiref.sync import sync_to_async

from .core import call_llm

# Whole word only. "feedbacks are feedbacking" is not a feedback report, and a
# drill answer must never be logged as one.
_EXPLICIT = re.compile(r'\bfeedback\b', re.IGNORECASE)

# Written by the deterministic layer, which guarantees capture but cannot explain
# what was said. A later layer that CAN explain -- the inline classifier, or the
# sweep -- overwrites it. Carried as a sentinel string rather than a model field
# so this needs no migration; other sessions are adding migrations concurrently.
PROVISIONAL_INTERPRETATION = (
    "Captured verbatim: the student labelled this feedback. Not yet interpreted."
)

FEEDBACK_ACK = "Got it — that's logged and a human will read it. ¡Gracias! 🙏"


def looks_like_explicit_feedback(text: str) -> bool:
    """True when the message announces itself as feedback.

    Deliberately over-inclusive: a spurious row costs a scroll, a missed one
    costs the signal entirely.
    """
    if not text or not text.strip():
        return False
    return bool(_EXPLICIT.search(text))


async def record_feedback(session, anchor_event, user_message: str,
                          interpretation: str, provisional: bool = False) -> bool:
    """Write one SessionFeedback row. Returns True if a row was created.

    Idempotent on (session, user_message): the deterministic layer and the
    inline classifier both see the same turn, and neither knows about the other.

    A provisional row -- capture guaranteed, meaning not yet known -- is UPGRADED
    when a real interpretation arrives, rather than the second write being
    dropped. Plain dedup kept whichever landed first, and the deterministic layer
    always lands first, so the log filled with placeholders while the classifier's
    actual analysis was discarded.
    """
    from learner.models import SessionFeedback

    message = (user_message or '').strip()
    if not message:
        return False

    existing = await sync_to_async(
        lambda: SessionFeedback.objects.filter(
            session=session, user_message=message).first()
    )()
    if existing is not None:
        upgradable = (existing.interpretation == PROVISIONAL_INTERPRETATION
                      and not provisional)
        if upgradable:
            await sync_to_async(
                lambda: SessionFeedback.objects.filter(pk=existing.pk).update(
                    interpretation=interpretation)
            )()
        return False

    await sync_to_async(SessionFeedback.objects.create)(
        session=session,
        anchor_event=anchor_event,
        user_message=message,
        interpretation=interpretation,
    )
    return True


SWEEP_MAX_TOKENS = 4096

SWEEP_PROMPT = """You are reviewing a Spanish tutoring transcript to find feedback the student gave about the product.

You are looking for anything that is a COMPLAINT, FRUSTRATION, CONFUSION about how the lesson works, a SUGGESTION, or a BUG REPORT. Most students will never use the word "feedback" — judge it from the content.

Counts as feedback:
- "this is too hard", "this is boring", "why do you keep asking me the same thing"
- "you already asked me that", "that doesn't make sense", "this is going too fast"
- "it would be better if...", "can you stop doing X"
- confusion about the SYSTEM (scheduling, repetition, what it remembers), not about Spanish

Does NOT count:
- an attempt at the Spanish exercise, right or wrong
- "no sé" / "I don't know" as an answer to a drill
- a question ABOUT SPANISH ("why is it hizo not hico?") — that is the lesson working
- acknowledgements: "ok", "listo", "got it"

Turns (each is what the tutor said, then what the student replied):

{turns}

Return ONLY a JSON array, one object per turn that IS feedback:
[{{"n": <turn number>, "interpretation": "<one sentence describing what they are flagging>"}}]

Return [] if none qualify. Never invent a turn number."""


async def sweep_candidates(days: int = 3, limit: int = 200) -> list:
    """Student turns in the window that no layer has logged yet."""
    from django.utils import timezone
    from datetime import timedelta
    from learner.models import SessionEvent, SessionFeedback

    since = timezone.now() - timedelta(days=days)

    def _load():
        logged = set(
            SessionFeedback.objects.values_list('user_message', flat=True)
        )
        events = (SessionEvent.objects
                  .filter(timestamp__gte=since)
                  .exclude(user_response='')
                  .select_related('session')
                  .order_by('timestamp')[:limit])
        return [e for e in events if e.user_response.strip() not in logged]

    return await sync_to_async(_load)()


async def sweep(days: int = 3, limit: int = 200, dry_run: bool = False) -> dict:
    """Find feedback nobody labelled, using content rather than a keyword.

    Deliberately offline: feedback is reviewed in batches, so nothing needs this
    within the turn. That buys full conversational context for the judgement and
    costs the live path nothing.
    """
    candidates = await sweep_candidates(days=days, limit=limit)
    if not candidates:
        return {'scanned': 0, 'created': 0, 'would_create': 0, 'hits': []}

    turns = "\n\n".join(
        f"[{i}] tutor: {e.content[:300]}\n    student: {e.user_response[:300]}"
        for i, e in enumerate(candidates)
    )
    raw = await call_llm(
        [{"role": "user", "content": SWEEP_PROMPT.format(turns=turns)}],
        max_tokens=SWEEP_MAX_TOKENS,
    )

    match = re.search(r'\[.*\]', (raw or '').strip(), flags=re.DOTALL)
    if not match:
        return {'scanned': len(candidates), 'created': 0, 'would_create': 0, 'hits': []}
    try:
        flagged = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return {'scanned': len(candidates), 'created': 0, 'would_create': 0, 'hits': []}

    created, hits = 0, []
    for item in flagged:
        if not isinstance(item, dict):
            continue
        n = item.get('n')
        if not isinstance(n, int) or not (0 <= n < len(candidates)):
            continue
        event = candidates[n]
        interpretation = str(item.get('interpretation', '')).strip() or 'Flagged by sweep.'
        hits.append((event, interpretation))
        if not dry_run:
            if await record_feedback(event.session, event, event.user_response, interpretation):
                created += 1

    return {
        'scanned': len(candidates),
        'created': created,
        'would_create': len(hits),
        'hits': [(e.pk, i) for e, i in hits],
    }
