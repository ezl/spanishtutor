"""
Generating vocabulary packs from what a person actually talks about.

Core packs are hand-curated and shared; these are generated per user and owned
by them. Mechanically they are the same object -- a LexItem with `owner` set --
so a generated gym word is scheduled, recorded and recycled by exactly the same
rules as a curated one. There is no parallel system.

Generated at session close rather than once at onboarding, because onboarding is
the placement quiz and collects no interests at all. Interests accumulate from
elicitation and extraction, so the pack for someone's strongest interest cannot
exist until they have mentioned it.
"""
import json
import logging
import re
import unicodedata

from asgiref.sync import sync_to_async

from .core import call_llm

logger = logging.getLogger(__name__)

# One topic per session close. Generation is an LLM call on a latency-sensitive
# path, and interests accumulate slowly enough that there is nothing to catch up.
TOPICS_PER_RUN = 1
MIN_MENTIONS = 2
WORDS_PER_PACK = 7

PROMPT = """A Spanish learner at CEFR level {level} has this in their life:

{topic}

List the {n} Spanish words or short fixed phrases they would most need to talk \
about it. Judge usefulness by how often the word would come up when THIS person \
describes THIS part of their life, not by how common the word is in general.

Rules:
- Mixed parts of speech. Verbs and adjectives, not only nouns. A list of nouns \
cannot be made into a sentence.
- Words that naturally co-occur, so the set could be written as a short \
connected paragraph. Do NOT return a category of interchangeable words (six \
colours, six sports); those interfere with each other when learned together.
- Nouns: give the bare lemma without an article, plus its gender.
- Skip anything a learner at {level} would already know.

Return ONLY a JSON array, no commentary:
[{{"es": "pesas", "en": "weights", "pos": "noun", "gender": "f"}}, ...]

pos is one of: noun, verb, adjective, adverb, chunk. gender is "m", "f", or "" \
for non-nouns."""


def slugify(topic: str) -> str:
    t = unicodedata.normalize('NFD', topic.lower())
    t = ''.join(c for c in t if unicodedata.category(c) != 'Mn')
    return re.sub(r'[^a-z0-9]+', '_', t).strip('_')[:40]


def _parse(raw: str) -> list:
    """Raise on unparseable output rather than returning [].

    An empty list and a failed call must not look the same at the call site --
    "this interest yielded no words" and "the model returned prose" would
    otherwise be indistinguishable, and the caller would quietly mark the topic
    done and never retry it.
    """
    cleaned = re.sub(r'^```(?:json)?\s*|\s*```$', '', (raw or '').strip(), flags=re.MULTILINE)
    match = re.search(r'\[.*\]', cleaned, flags=re.DOTALL)
    if not match:
        raise ValueError(f'no JSON array in model output: {cleaned[:200]!r}')
    return json.loads(match.group(0))


def _pending_topics(user, limit: int) -> list:
    """Interests worth a pack that do not have one yet."""
    from learner.models import LexItem, UserInterest

    done = set()
    for packs in LexItem.objects.filter(owner=user).values_list('packs', flat=True):
        done.update(p for p in (packs or '').split(',') if p.startswith('interest:'))

    out = []
    for interest in (UserInterest.objects.filter(user=user, mention_count__gte=MIN_MENTIONS)
                                         .order_by('-mention_count', '-confidence')):
        tag = f'interest:{slugify(interest.topic)}'
        if tag not in done:
            out.append((interest, tag))
        if len(out) >= limit:
            break
    return out


def _store(user, tag: str, words: list) -> int:
    from learner.models import LexItem

    created = 0
    for w in words:
        es = (w.get('es') or '').strip()
        if not es:
            continue
        item, is_new = LexItem.objects.get_or_create(
            es=es, owner=user,
            defaults={
                'en': (w.get('en') or '').strip(),
                'pos': (w.get('pos') or '').strip(),
                'gender': (w.get('gender') or '').strip()[:1],
                # Deliberately unlevelled: generated for this person on purpose,
                # so the level window must never filter them out.
                'cefr_level': '',
                'packs': tag,
            },
        )
        if not is_new and tag not in item.pack_list:
            item.packs = ','.join(item.pack_list + [tag])
            item.save(update_fields=['packs'])
        created += is_new
    return created


async def generate_for(user, limit: int = TOPICS_PER_RUN) -> dict:
    pending = await sync_to_async(_pending_topics)(user, limit)
    if not pending:
        return {'topics': 0, 'words': 0}

    topics = words = 0
    for interest, tag in pending:
        raw = await call_llm(
            [{'role': 'user', 'content': PROMPT.format(
                level=user.estimated_cefr_level or 'A1',
                topic=interest.topic,
                n=WORDS_PER_PACK)}],
            system_override='You return only JSON. No commentary.',
            max_tokens=1024,
        )
        parsed = _parse(raw)
        words += await sync_to_async(_store)(user, tag, parsed)
        topics += 1
        logger.info('generated interest pack %s for user %s (%d words)',
                    tag, user.pk, len(parsed))
    return {'topics': topics, 'words': words}
