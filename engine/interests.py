import anthropic
import django.conf
import logging
import re
from asgiref.sync import sync_to_async

logger = logging.getLogger('engine')

EXTRACTION_PROMPT = """Here is a conversation between a Spanish tutor (Luz) and a student.

<conversation>
{transcript}
</conversation>

We already know these facts about the student:
<known>
{known}
</known>

What did the STUDENT reveal about their life that we do not already know?

EVIDENCE RULES -- these matter more than finding something:
- Only the STUDENT lines are evidence. TUTOR lines are context for interpreting
  them and are NEVER a source of facts. Luz generates her sentences FROM the
  known list, so treating her words as evidence makes the system invent facts
  by reading its own output back.
- Never attach a name the student did not say. If they said "our friend" without
  naming her, the fact is "has a friend", not a friend with a name borrowed from
  the known list.
- A drill answer is a sentence built to a grammatical spec, not a biographical
  claim. "Mi hijo me lo contó" answering a pronoun exercise does not mean their
  son said anything, and it certainly does not name their son after whoever the
  tutor mentioned.
- If the conversation revealed nothing new, output exactly: NOTHING

CATEGORY must be exactly one of:
{categories}

Output one line per fact, in one of these two forms:

NEW | topic | category | confidence
SUPERSEDES <exact existing topic> | topic | category | confidence

Use SUPERSEDES when the new fact refines, corrects, or contradicts something in
the known list -- "has a wife named Melodie" supersedes "has a friend named
Melodie". Use it rather than adding a second row, so the two beliefs cannot both
survive. Do not restate a known fact in different words: if we already have
"Goes to the gym", "Lifts weights" is not new.

Output ONLY these lines. No reasoning, no preamble, no explanation, no blank
commentary -- just the lines, or the single word NOTHING. Working through it in
prose risks the real output being cut off before it is written.

Examples:
NEW | plays guitar | hobbies | 0.9
NEW | has a dog named Rufo | pets | 0.8
SUPERSEDES has a friend named Melodie | has a wife named Melodie | family | 1.0"""


# Words too common to identify a topic when checking who introduced it.
_ECHO_STOPWORDS = frozenset({
    'the', 'and', 'for', 'has', 'have', 'with', 'from', 'that', 'this', 'they',
    'their', 'his', 'her', 'someone', 'named', 'name', 'likes', 'like', 'enjoys',
    'goes', 'does', 'about', 'into', 'been', 'was', 'are', 'user', 'student',
})


def allowed_categories() -> list:
    """Controlled vocabulary, shared with the elicitation bank so that gaps in
    one are answerable by the other."""
    from .elicitation import load_bank
    return load_bank()['categories']


def _significant_tokens(topic: str) -> set:
    return {w for w in re.findall(r"[a-záéíóúñü]+", (topic or '').lower())
            if len(w) >= 3 and w not in _ECHO_STOPWORDS}


def _looks_echoed(topic: str, tutor_text: str, student_text: str) -> bool:
    """True when this topic is not independent evidence from the student.

    Either the tutor introduced it -- Luz writes sentences from the known list,
    so the student repeating a name back is the system reading its own output --
    or nobody actually said it and it was inferred from context.
    """
    tokens = _significant_tokens(topic)
    if not tokens:
        return True
    tutor = (tutor_text or '').lower()
    student = (student_text or '').lower()
    if any(t in tutor for t in tokens):
        return True
    return not any(t in student for t in tokens)


def _build_transcript(events) -> str:
    """Label both sides explicitly. The prompt treats only STUDENT lines as
    evidence, so the labelling is load-bearing rather than cosmetic."""
    lines = []
    for e in events:
        if e.content:
            lines.append(f"TUTOR: {e.content}")
        if e.user_response:
            lines.append(f"STUDENT: {e.user_response}")
    return "\n".join(lines)


def _split_sides(events) -> tuple:
    tutor = " ".join(e.content or '' for e in events)
    student = " ".join(e.user_response or '' for e in events)
    return tutor, student


def _parse_extraction(raw: str) -> list[dict]:
    """Parse NEW / SUPERSEDES lines. Anything outside the category vocabulary is
    dropped: a free-text category is what made "which category is thin?"
    unanswerable in the first place."""
    raw = (raw or '').strip()
    if raw == 'NOTHING':
        return []
    allowed = set(allowed_categories())
    results = []
    for line in raw.splitlines():
        parts = [p.strip() for p in line.split('|')]
        if len(parts) != 4:
            continue
        head, topic, category, confidence_str = parts
        try:
            confidence = float(confidence_str)
        except ValueError:
            continue
        if not topic or category not in allowed:
            continue
        upper = head.upper()
        if upper == 'NEW':
            action, supersedes = 'new', None
        elif upper.startswith('SUPERSEDES'):
            action, supersedes = 'supersede', head[len('SUPERSEDES'):].strip()
            if not supersedes:
                continue
        else:
            continue
        results.append({'action': action, 'supersedes': supersedes, 'topic': topic,
                        'category': category, 'confidence': confidence})
    return results


async def _call_extraction_llm(transcript: str, known: str = "(nothing yet)") -> str:
    client = anthropic.Anthropic(api_key=django.conf.settings.ANTHROPIC_API_KEY)
    prompt = EXTRACTION_PROMPT.format(
        transcript=transcript,
        known=known,
        categories="\n".join(f"  {c}" for c in allowed_categories()),
    )
    response = await sync_to_async(client.messages.create)(
        model="claude-sonnet-4-6",
        # Headroom: the prompt now carries the known list and asks for a
        # supersede decision, and a truncated response parses to silence.
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


# At most this many facts per category may reach the prompt. Duplicates each
# carried their own mention_count, so one cluster (gym said four ways) crowded
# out every other topic in the field lessons are generated from.
MAX_PER_CATEGORY = 3
MAX_INTEREST_FACTS = 20


def _diverse_selection(rows, per_category=MAX_PER_CATEGORY, limit=MAX_INTEREST_FACTS):
    """Best facts per category, spread across categories rather than ranked
    globally, so the loudest topic cannot dominate."""
    by_category = {}
    for row in sorted(rows, key=lambda r: (-r.mention_count, -r.confidence, r.topic)):
        by_category.setdefault(row.category, []).append(row)
    kept, round_index = [], 0
    while len(kept) < limit and round_index < per_category:
        added = False
        for category in sorted(by_category):
            bucket = by_category[category]
            if round_index < len(bucket) and len(kept) < limit:
                kept.append(bucket[round_index])
                added = True
        if not added:
            break
        round_index += 1
    return kept


async def _regenerate_interests_prose(user) -> None:
    from learner.models import UserInterest
    rows = await sync_to_async(lambda: list(UserInterest.objects.filter(user=user)))()
    interests = _diverse_selection(rows)
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

    known_rows = await sync_to_async(
        lambda: list(UserInterest.objects.filter(user=user).order_by('-mention_count'))
    )()
    known = "\n".join(f"- {r.topic} ({r.category})" for r in known_rows) or "(nothing yet)"

    try:
        raw = await _call_extraction_llm(transcript, known)
    except Exception as e:
        logger.error(f"Interest extraction failed: {e}")
        return []

    facts = _parse_extraction(raw)
    tutor_text, student_text = _split_sides(events)

    for fact in facts:
        topic = fact['topic']

        if fact['action'] == 'supersede':
            superseded = await sync_to_async(
                lambda t=fact['supersedes']: UserInterest.objects.filter(
                    user=user, topic__iexact=t).first()
            )()
            if superseded:
                # Replace in place so the old belief cannot survive alongside the
                # correction -- "friend named Melodie" and "wife named Melodie"
                # were both sitting in the pool and either could reach a lesson.
                await sync_to_async(
                    lambda pk=superseded.pk: UserInterest.objects.filter(pk=pk).update(
                        topic=topic, category=fact['category'],
                        confidence=max(superseded.confidence, fact['confidence']),
                    )
                )()
                fact['new'] = False
                continue

        existing = await sync_to_async(
            lambda t=topic: UserInterest.objects.filter(user=user, topic__iexact=t).first()
        )()
        if existing:
            # Reinforce only what the student actually volunteered. Counting an
            # echo measures the system's own output: Luz writes sentences from
            # the known list, the student repeats a name back, and the topic
            # climbs the ranking on no new evidence.
            if _looks_echoed(topic, tutor_text, student_text):
                fact['new'] = False
                fact['echoed'] = True
                continue
            await sync_to_async(
                lambda pk=existing.pk, c=existing.mention_count, conf=existing.confidence:
                    UserInterest.objects.filter(pk=pk).update(
                        mention_count=c + 1,
                        confidence=max(conf, fact['confidence']),
                    )
            )()
            fact['new'] = False
        else:
            await sync_to_async(UserInterest.objects.create)(
                user=user,
                topic=topic,
                category=fact['category'],
                confidence=fact['confidence'],
            )
            fact['new'] = True

    await _regenerate_interests_prose(user)
    logger.info(f"Interests extracted for {user}: {[f['topic'] for f in facts]}")
    return facts


CLEANUP_PROMPT = """You are consolidating a Spanish tutoring app's memory of one student.

Each row is a fact it believes. The list has accumulated for weeks with only
exact-string deduplication, so the same fact appears several times in different
words, some categories are free text that no longer exists, and a few rows
contradict each other.

<rows>
{rows}
</rows>

CATEGORY must be exactly one of:
{categories}

Return ONLY JSON, no prose:

{{
  "clusters": [
    {{"canonical": "one clear phrasing", "category": "<category>", "members": [<row ids>]}}
  ],
  "contradictions": [
    {{"reason": "why these cannot both be true", "members": [<row ids>],
      "safe": "the weaker fact that is still true, or omit", "category": "<category>"}}
  ],
  "drop": [<row ids>]
}}

- clusters: rows that say the SAME thing. Give one canonical phrasing and the
  right category. A single row that only needs its category remapped is a
  cluster of one.
- contradictions: rows that cannot both be true about the same person -- a
  friend who is also a child, a habit and its opposite. Do NOT try to pick the
  true one; we cannot know, and a wrong fact generates lessons about a person
  who does not exist.
  Where a weaker fact survives the disagreement, give it as "safe": "friend
  named Chris" and "son named Chris" disagree about the relationship, but that
  a Chris exists is not in dispute, so safe is "knows someone named Chris".
  Omit "safe" only when nothing at all survives.
  Facts that merely differ in specificity are NOT contradictions -- "knows
  Melodie" and "has a wife named Melodie" are a cluster, and the more specific
  phrasing is the canonical one.
- drop: rows that are worthless -- generic filler true of everyone, or so vague
  they could not shape a lesson.
- Every row id must appear exactly once, in exactly one of the three lists."""


class CleanupParseError(Exception):
    """The model produced a plan we could not read.

    Distinct from "no plan": returning an empty plan for undecodable JSON made a
    failed run look exactly like a successful no-op, which is how a 120-row
    cleanup silently reported nothing to do.
    """


def _parse_cleanup_plan(raw: str, valid_ids: set) -> dict:
    """Parse the consolidation plan, keeping only ids we actually showed it.

    The model can never remove a row it was not given, and a category outside
    the controlled vocabulary invalidates its cluster -- free-text categories
    are what made the pool unmeasurable to begin with.
    """
    import json as _json
    empty = {'clusters': [], 'contradictions': [], 'drop': []}
    text = re.sub(r'^\s*```(?:json)?|```\s*$', '', (raw or '').strip(),
                  flags=re.MULTILINE).strip()
    match = re.search(r'\{.*\}', text, flags=re.DOTALL)
    if not match:
        # No plan at all -- the model declined, which is a real answer.
        return empty
    try:
        data = _json.loads(match.group(0))
    except (ValueError, TypeError) as exc:
        raise CleanupParseError(f"plan was not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise CleanupParseError("plan was not an object")

    allowed = set(allowed_categories())

    clusters = []
    for cluster in data.get('clusters') or []:
        if not isinstance(cluster, dict) or cluster.get('category') not in allowed:
            continue
        members = [m for m in (cluster.get('members') or []) if m in valid_ids]
        canonical = str(cluster.get('canonical', '')).strip()
        if members and canonical:
            clusters.append({'canonical': canonical, 'category': cluster['category'],
                             'members': members})

    contradictions = []
    for item in data.get('contradictions') or []:
        if not isinstance(item, dict):
            continue
        members = [m for m in (item.get('members') or []) if m in valid_ids]
        if not members:
            continue
        entry = {'reason': str(item.get('reason', '')), 'members': members}
        safe = str(item.get('safe', '') or '').strip()
        if safe and item.get('category') in allowed:
            entry['safe'] = safe
            entry['category'] = item['category']
        contradictions.append(entry)

    drop = [m for m in (data.get('drop') or []) if m in valid_ids]
    return {'clusters': clusters, 'contradictions': contradictions, 'drop': drop}


async def build_cleanup_plan(user) -> tuple:
    """Ask the model how to consolidate this user's interest rows."""
    from learner.models import UserInterest

    rows = await sync_to_async(
        lambda: list(UserInterest.objects.filter(user=user).order_by('pk'))
    )()
    if not rows:
        return {'clusters': [], 'contradictions': [], 'drop': []}, []

    listing = "\n".join(
        f"{r.pk} | {r.topic} | {r.category} | seen {r.mention_count}x" for r in rows
    )
    prompt = CLEANUP_PROMPT.format(
        rows=listing,
        categories="\n".join(f"  {c}" for c in allowed_categories()),
    )
    client = anthropic.Anthropic(api_key=django.conf.settings.ANTHROPIC_API_KEY)
    valid_ids = {r.pk for r in rows}

    last_error = None
    for attempt in range(2):
        response = await sync_to_async(client.messages.create)(
            model="claude-sonnet-4-6",
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt}],
        )
        try:
            return _parse_cleanup_plan(response.content[0].text, valid_ids), rows
        except CleanupParseError as exc:
            last_error = exc
            logger.warning("cleanup plan unreadable (attempt %d/2): %s", attempt + 1, exc)
    raise last_error


async def apply_cleanup_plan(user, plan: dict, dry_run: bool = False) -> dict:
    """Collapse clusters, delete contradictions and filler. Returns a summary."""
    from learner.models import UserInterest

    summary = {'merged': 0, 'clusters': 0, 'contradictions': 0, 'dropped': 0}

    for cluster in plan.get('clusters') or []:
        members = cluster['members']
        summary['clusters'] += 1
        summary['merged'] += len(members)
        if dry_run:
            continue
        rows = await sync_to_async(
            lambda m=members: list(UserInterest.objects.filter(user=user, pk__in=m)
                                                       .order_by('first_seen_at'))
        )()
        if not rows:
            continue
        keeper = rows[0]
        # Max, not sum: the counts are echo-inflated, and summing would compound
        # the very signal that let one cluster crowd out every other topic.
        await sync_to_async(
            lambda: UserInterest.objects.filter(pk=keeper.pk).update(
                topic=cluster['canonical'],
                category=cluster['category'],
                mention_count=max(r.mention_count for r in rows),
                confidence=max(r.confidence for r in rows),
            )
        )()
        losers = [r.pk for r in rows[1:]]
        if losers:
            await sync_to_async(
                lambda: UserInterest.objects.filter(user=user, pk__in=losers).delete()
            )()

    contradiction_ids = []
    for item in plan.get('contradictions') or []:
        members = item['members']
        summary['contradictions'] += len(members)
        if not item.get('safe'):
            contradiction_ids.extend(members)
            continue
        # Keep what survives the disagreement. Deleting every row would lose
        # that the person exists at all, when only the relationship is contested.
        if dry_run:
            continue
        rows = await sync_to_async(
            lambda m=members: list(UserInterest.objects.filter(user=user, pk__in=m)
                                                       .order_by('first_seen_at'))
        )()
        if not rows:
            continue
        keeper = rows[0]
        await sync_to_async(
            lambda k=keeper, it=item: UserInterest.objects.filter(pk=k.pk).update(
                topic=it['safe'], category=it['category'],
                mention_count=1, confidence=0.6,
            )
        )()
        losers = [r.pk for r in rows[1:]]
        if losers:
            await sync_to_async(
                lambda l=losers: UserInterest.objects.filter(user=user, pk__in=l).delete()
            )()
    drop_ids = list(plan.get('drop') or [])
    summary['dropped'] = len(drop_ids)

    removable = contradiction_ids + drop_ids
    if removable and not dry_run:
        await sync_to_async(
            lambda: UserInterest.objects.filter(user=user, pk__in=removable).delete()
        )()

    if not dry_run:
        await _regenerate_interests_prose(user)
    return summary
