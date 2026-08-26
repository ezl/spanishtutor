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
