import anthropic
import django.conf
import logging
from asgiref.sync import sync_to_async

logger = logging.getLogger('engine')

EXTRACTION_PROMPT = """Here is a conversation between a Spanish tutor and a student.

<conversation>
{transcript}
</conversation>

Did this conversation reveal anything about the student's life, interests, hobbies, career, relationships, or personal context?

If yes, output one line per fact in exactly this format:
TOPIC | CATEGORY | CONFIDENCE

Only include things the student actually revealed or that are obvious inferences (e.g. if they mention Dragon Ball Z, infer shonen anime fan). If nothing personal came up, output exactly: NOTHING

Examples of valid output:
Chicago Cubs | sports | 0.9
has a dog | pets | 0.8
works in tech | career | 0.7
shonen anime fan | anime | 0.5"""


def _build_transcript(events) -> str:
    lines = []
    for e in events:
        if e.user_response:
            lines.append(f"Student: {e.user_response}")
        if e.content:
            lines.append(f"Luz: {e.content}")
    return "\n".join(lines)


def _parse_extraction(raw: str) -> list[dict]:
    raw = raw.strip()
    if raw == 'NOTHING':
        return []
    results = []
    for line in raw.splitlines():
        parts = [p.strip() for p in line.split('|')]
        if len(parts) != 3:
            continue
        topic, category, confidence_str = parts
        try:
            confidence = float(confidence_str)
        except ValueError:
            continue
        if topic and category:
            results.append({'topic': topic, 'category': category, 'confidence': confidence})
    return results


async def _call_extraction_llm(transcript: str) -> str:
    client = anthropic.Anthropic(api_key=django.conf.settings.ANTHROPIC_API_KEY)
    prompt = EXTRACTION_PROMPT.format(transcript=transcript)
    response = await sync_to_async(client.messages.create)(
        model="claude-sonnet-4-6",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


async def _regenerate_interests_prose(user) -> None:
    from learner.models import UserInterest
    interests = await sync_to_async(
        lambda: list(
            UserInterest.objects.filter(user=user)
                               .order_by('-mention_count', '-confidence')[:20]
        )
    )()
    if not interests:
        return
    high = [i for i in interests if i.confidence >= 0.7]
    low = [i for i in interests if i.confidence < 0.7]
    parts = []
    if high:
        parts.append(', '.join(i.topic for i in high))
    if low:
        parts.append('Possible: ' + ', '.join(i.topic for i in low))
    prose = '. '.join(parts) + '.' if parts else ''
    await sync_to_async(
        lambda: type(user).objects.filter(pk=user.pk).update(interests=prose)
    )()


SEEDED_INTERESTS = [
    ("making coffee or tea in the morning", "daily_routine"),
    ("taking a shower", "daily_routine"),
    ("eating breakfast", "daily_routine"),
    ("commuting to work", "daily_routine"),
    ("working at a desk or laptop", "work"),
    ("having lunch during the workday", "work"),
    ("finishing work late and being tired", "work"),
    ("going for a jog", "exercise"),
    ("going to the gym", "exercise"),
    ("taking a walk to clear your head", "exercise"),
    ("cooking dinner at home", "food"),
    ("ordering food delivery", "food"),
    ("trying a new restaurant", "food"),
    ("going grocery shopping", "errands"),
    ("doing laundry", "errands"),
    ("cleaning the apartment", "errands"),
    ("watching Netflix or a show", "entertainment"),
    ("scrolling through social media before bed", "entertainment"),
    ("meeting a friend for coffee", "social"),
    ("having dinner with family", "social"),
    ("calling or texting someone", "social"),
    ("going to a birthday party or celebration", "social"),
    ("making plans that fall through", "social"),
    ("sleeping in on a Saturday", "leisure"),
    ("going on vacation", "leisure"),
]


async def seed_interests(user) -> None:
    """Seed universal interests at onboarding completion. Idempotent."""
    from learner.models import UserInterest
    for topic, category in SEEDED_INTERESTS:
        await sync_to_async(UserInterest.objects.get_or_create)(
            user=user,
            topic=topic,
            defaults={'category': category, 'confidence': 0.4},
        )
    await _regenerate_interests_prose(user)


async def extract_and_store_interests(session, user) -> list[dict]:
    """
    Run post-session interest extraction. Returns list of extracted facts.
    Called synchronously from _close_session before response is sent.
    """
    from learner.models import UserInterest

    events = await sync_to_async(
        lambda: list(session.events.order_by('timestamp'))
    )()

    transcript = _build_transcript(events)
    if not transcript:
        return []

    try:
        raw = await _call_extraction_llm(transcript)
    except Exception as e:
        logger.error(f"Interest extraction failed: {e}")
        return []

    facts = _parse_extraction(raw)

    for fact in facts:
        existing = await sync_to_async(
            lambda: UserInterest.objects.filter(
                user=user, topic__iexact=fact['topic']
            ).first()
        )()
        if existing:
            await sync_to_async(
                lambda: UserInterest.objects.filter(pk=existing.pk).update(
                    mention_count=existing.mention_count + 1,
                    confidence=max(existing.confidence, fact['confidence']),
                )
            )()
            fact['new'] = False
        else:
            await sync_to_async(UserInterest.objects.create)(
                user=user,
                topic=fact['topic'],
                category=fact['category'],
                confidence=fact['confidence'],
            )
            fact['new'] = True

    await _regenerate_interests_prose(user)
    logger.info(f"Interests extracted for {user}: {[f['topic'] for f in facts]}")
    return facts
