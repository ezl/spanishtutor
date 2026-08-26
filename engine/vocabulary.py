"""
Selecting vocabulary for a lesson and rendering it into the system prompt.

Vocabulary used to be 21 one-line skill descriptions with the actual words
improvised at lesson time, so nothing was curated, nothing recurred, and a word
taught in lesson 8 had no way back into lesson 40. See docs/vocabulary.md.

There is no review debt here, and it needs no mechanism: selection always takes
the most overdue words that fit and simply ignores the rest. Nothing counts a
backlog, nothing displays one, and nothing has to be cleared -- which is the
failure mode that makes people quit Anki after a missed week. A student who
disappears for a fortnight comes back to an ordinary lesson.
"""
import os
import yaml
from datetime import timedelta
from asgiref.sync import sync_to_async
from django.conf import settings as django_settings
from django.db.models import Q
from django.utils import timezone

DEFAULTS = {
    'new_words_per_lesson': 2,
    'recycled_words_per_lesson': 5,
    'new_words_per_vocab_lesson': 8,
    'graduation_productions': 3,
}

_cache = {}


def load_settings() -> dict:
    """Runtime-reloadable, like the rest of curriculum/."""
    path = os.path.join(django_settings.BASE_DIR, 'curriculum', 'lexicon.yaml')
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return dict(DEFAULTS)
    if _cache.get('mtime') != mtime:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        merged = dict(DEFAULTS)
        merged.update(data.get('settings') or {})
        _cache.update({'mtime': mtime, 'settings': merged})
    return _cache['settings']


def pack_for_skill(skill_id: str):
    """The pack a lesson teaches, if it is one. Only A1 lessons have one."""
    path = os.path.join(django_settings.BASE_DIR, 'curriculum', 'lexicon.yaml')
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    for pack in data.get('packs') or []:
        if pack.get('skill') == skill_id:
            return pack['id']
    return None


def _open_session_skill(user):
    from learner.models import Session
    session = (Session.objects.filter(user=user, ended_at__isnull=True)
                              .select_related('target_skill')
                              .order_by('-started_at').first())
    return session.target_skill.skill_id if session and session.target_skill else None


def _select(user, skill_id=None) -> dict:
    from learner.models import LexItem, UserWord

    conf = load_settings()
    if skill_id is None:
        skill_id = _open_session_skill(user)
    pack = pack_for_skill(skill_id) if skill_id else None
    new_budget = conf['new_words_per_vocab_lesson'] if pack else conf['new_words_per_lesson']

    # Graduated words ('known') have stopped consuming slots. That is what keeps
    # the active set bounded while the corpus grows without limit.
    due = list(
        UserWord.objects.filter(user=user, next_due_at__lte=timezone.now())
                        .exclude(state='known')
                        .select_related('item')
                        .order_by('next_due_at')[:conf['recycled_words_per_lesson']]
    )

    taught = UserWord.objects.filter(user=user).values_list('item_id', flat=True)
    candidates = LexItem.objects.filter(active=True).exclude(id__in=taught)
    # NOT owner__in=[None, user.pk]: SQL `IN (NULL, 5)` never matches NULL, so
    # that silently excludes the entire core catalogue. Same NULL semantics that
    # forced the partial unique constraints on LexItem.
    candidates = (candidates.filter(packs__contains=pack) if pack
                  else candidates.filter(Q(owner__isnull=True) | Q(owner=user)))
    new = list(candidates.order_by('cefr_level', 'es')[:new_budget])

    return {'due': [uw.item for uw in due], 'new': new}


def render_block(due: list, new: list) -> str:
    """The prompt fragment. Empty string when there is nothing to say."""
    if not due and not new:
        return ''
    lines = ['\n## Vocabulary for this lesson']
    if new:
        lines.append('Introduce these words naturally. Do not list them; use them.')
        for item in new:
            lines.append(f'- {item.display_es} = {item.en}'
                         + (f' ({item.note})' if item.note else '')
                         + ('  [FIXED CHUNK: teach whole, never break into parts]'
                            if not item.analyzable else ''))
    if due:
        lines.append('Weave these already-taught words back in wherever they fit '
                     'naturally. Do not announce that you are reviewing them.')
        lines.append('- ' + ', '.join(i.es for i in due))
    return '\n'.join(lines)


async def block_for(user, skill_id=None) -> str:
    selected = await sync_to_async(_select)(user, skill_id)
    return render_block(selected['due'], selected['new'])


# ── Recording exposure ────────────────────────────────────────────────────────

import re
import unicodedata

REVIEW_INTERVALS_DAYS = [1, 3, 7, 16, 35, 90]


def _normalize(text: str) -> str:
    """Lowercase, strip diacritics, reduce punctuation to spaces.

    Diacritics come off BOTH sides because beginners type "como estas" and mean
    "¿cómo estás?". Refusing that match would under-record exactly the students
    who most need the reinforcement.
    """
    text = unicodedata.normalize('NFD', (text or '').lower())
    text = ''.join(c for c in text if unicodedata.category(c) != 'Mn')
    return ' ' + re.sub(r'[^a-z0-9ñ]+', ' ', text).strip() + ' '


def _appears(needle: str, haystack: str) -> bool:
    """Whole-word (or whole-phrase) match. 'casa' must not match 'casado'."""
    n = _normalize(needle).strip()
    return bool(n) and f' {n} ' in haystack


def _schedule(word) -> tuple:
    """Interval ladder. Producing a word counts double -- saying it is stronger
    evidence than seeing it. Returns (next_due_at, state)."""
    conf = load_settings()
    strength = word.times_produced * 2 + word.times_seen
    days = REVIEW_INTERVALS_DAYS[min(strength, len(REVIEW_INTERVALS_DAYS) - 1)]

    if word.times_produced >= conf['graduation_productions']:
        # Graduated: stops consuming slots, keeps its row and its history. This
        # is what bounds the active set while the corpus grows without limit.
        return None, 'known'
    return timezone.now() + timedelta(days=days), (
        'practiced' if word.times_produced else 'introduced')


def _record(session, user) -> dict:
    """Write what this session's transcript shows about the user's vocabulary.

    No model call: matching a known word list against a transcript is string
    work. Only ONE increment per word per session, which is what makes
    "produced correctly in N separate sessions" mean what it says.

    Raises on failure rather than returning an empty result. "No words appeared"
    and "the matcher blew up" must not look identical at the call site -- that
    ambiguity has produced at least six bugs in this repo already.
    """
    from learner.models import LexItem, UserWord
    from .interests import _split_sides

    events = list(session.events.order_by('timestamp'))
    tutor_text, student_text = _split_sides(events)
    tutor, student = _normalize(tutor_text), _normalize(student_text)

    candidates = LexItem.objects.filter(active=True).filter(
        Q(owner__isnull=True) | Q(owner=user))

    seen = produced = 0
    for item in candidates:
        in_student = _appears(item.es, student)
        in_tutor = _appears(item.es, tutor)
        if not (in_student or in_tutor):
            continue

        word, _ = UserWord.objects.get_or_create(user=user, item=item)
        if in_student:
            word.times_produced += 1
            produced += 1
        else:
            word.times_seen += 1
            seen += 1
        word.last_seen_at = timezone.now()
        word.next_due_at, word.state = _schedule(word)
        word.save()

    return {'seen': seen, 'produced': produced}


async def record_exposure(session, user) -> dict:
    return await sync_to_async(_record)(session, user)
