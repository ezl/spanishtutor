"""
Skill requests — the student asks for a specific lesson.

Product decisions (2026-08-22):
  * A request TERMINATES the current lesson rather than switching inside it.
  * ANY active skill may be requested. There is no prerequisite gate.
  * An ambiguous request asks the student which skill they meant.

Architecture: detection is deterministic and phase-independent, so a request
made at the check-in greeting is caught as readily as one made mid-drill — the
original bug lost two of its three turns because the only classifier lived
inside teach_drill. Resolution is code-owned: `curriculum.find_matching_skills`
decides what exists. The model is never asked to reason about the curriculum,
which is what produced Luz's fabricated "you need solid preterite first"
(b1_subjunctive_formation's real prerequisites are a2_go_verbs and
a2_stem_change_e_ie).
"""
import re

from asgiref.sync import sync_to_async

from .core import call_llm
from .curriculum import _normalize

# Words that mark a message as being ABOUT the lesson rather than IN it. The gate
# runs on every message in every phase, so it is deliberately conservative: a
# false positive would abandon a lesson the student was in the middle of.
REQUEST_CUES = frozenset({
    'learn', 'teach', 'study', 'practice', 'practise', 'lesson', 'lessons',
    'instead', 'switch', 'skip', 'topic',
    'aprender', 'ensename', 'estudiar', 'practicar', 'leccion', 'cambiemos',
    'tema', 'otro',
})


# Markers of confusion or complaint. The cue gate above fires on any message
# ABOUT lessons, and a complaint about a lesson necessarily contains the word
# "lesson" -- so a student saying "why do you keep teaching me this" was handed
# a lesson picker (feedback #24), which produced more confusion, which produced
# more messages containing the word "lesson".
# Anchored with word boundaries on purpose: "why do" must not match inside
# "why don't we do the subjunctive instead", which is a genuine request.
_DISSATISFACTION = [
    re.compile(r'\bwhy (do|does|did|is|are|am)\b', re.IGNORECASE),
    re.compile(r'\b(confusing|unclear|weird|strange)\b', re.IGNORECASE),
    re.compile(r"\b(don't|dont|do not) understand\b", re.IGNORECASE),
    re.compile(r"\b(doesn't|does not) make sense\b", re.IGNORECASE),
    re.compile(r'\bwhat (just )?happened\b', re.IGNORECASE),
    re.compile(r'\bkeep (asking|teaching|doing)\b', re.IGNORECASE),
    re.compile(r'\bno entiendo\b', re.IGNORECASE),
]


def looks_like_dissatisfaction(text: str) -> bool:
    """True when the message reads as confusion or complaint rather than a request.

    Vetoes the request path: someone telling us the lesson is broken must not be
    answered with a menu of lessons.
    """
    return any(p.search(text or '') for p in _DISSATISFACTION)


def looks_like_skill_request(text: str) -> bool:
    """True when the message asks for different material, rather than answering.

    Purely lexical and whole-word: no LLM call on the common path, and a drill
    answer that merely shares letters with a cue is never mistaken for a request.
    """
    if not text or not text.strip():
        return False
    tokens = set(re.findall(r'[a-z0-9]+', _normalize(text)))
    return bool(tokens & REQUEST_CUES)


SKILL_LOOKUP_PROMPT = """A Spanish student sent this message during a lesson. Decide whether they are ASKING FOR DIFFERENT MATERIAL, and if so which skills they mean.

Curriculum:
{listing}

Student's message: "{phrase}"

Not every message that mentions lessons is a request. Confusion, complaints, and questions about what just happened are NOT requests — answering those with a menu of lessons is worse than not answering at all.

Reply with exactly one of:
- NOT_A_REQUEST  — they are not asking for different material
- NONE           — they ARE asking, but nothing in the curriculum matches
- the matching skill ids, comma-separated, most likely first

Never invent an id; every id must appear verbatim in the list above."""

NOT_IN_CURRICULUM = (
    "Mmm, no tengo eso en el programa todavía. 🤔 Puedes escribir `!menu` para ver "
    "lo que sí tengo, o pedirme otro tema."
)

AMBIGUOUS_TEMPLATE = (
    "¡Claro! ¿Cuál de estos quieres?\n\n{options}\n\n"
    "Dime el número o el nombre."
)

# Used when there is no open session to park a pending choice on (the !learn
# command with no lesson running). Stateless: the student re-asks more precisely.
AMBIGUOUS_STATELESS_TEMPLATE = (
    "Tengo varios de esos:\n\n{options}\n\n"
    "Dime cuál con `!learn <nombre>` — por ejemplo `!learn {example}`."
)

UNRESOLVED_TEMPLATE = (
    "No te entendí. ¿Cuál quieres?\n\n{options}\n\nDime el número o el nombre."
)


def _tokens(text: str) -> set:
    return set(re.findall(r'[a-z0-9]+', _normalize(text or '')))


def _format_options(candidates: list[dict]) -> str:
    return "\n".join(f"{i}. {c['name']}" for i, c in enumerate(candidates, 1))


def pick_candidate(candidates: list[dict], reply: str) -> dict | None:
    """Resolve the student's answer to a clarifying question. None if unclear."""
    stripped = (reply or '').strip()
    # The clarifying question is a numbered list, so a bare number is a choice.
    if stripped.isdigit():
        index = int(stripped) - 1
        return candidates[index] if 0 <= index < len(candidates) else None

    tokens = _tokens(reply)
    if not tokens:
        return None
    hits = [c for c in candidates if tokens & _tokens(f"{c['id']} {c['name']}")]
    return hits[0] if len(hits) == 1 else None


async def _model_candidates(phrase: str) -> list[dict] | None:
    """Fallback when no skill matches literally — e.g. the request is in Spanish
    and the taxonomy is in English. The model may only choose FROM the real
    curriculum, and every id it returns is validated against the DB, so it can
    never invent a skill or reason about prerequisites it cannot see.

    Returns None when the message was not a request at all -- distinct from an
    empty list, which means they asked for something we do not teach."""
    from .curriculum import all_skills, get_skill

    skills = await sync_to_async(all_skills)()
    listing = "\n".join(f"{s['id']}: {s['name']}" for s in skills)
    raw = await call_llm([{"role": "user", "content": SKILL_LOOKUP_PROMPT.format(
        listing=listing, phrase=phrase)}])

    if (raw or '').strip().upper().startswith('NOT_A_REQUEST'):
        return None

    resolved = []
    for candidate_id in re.split(r'[,\s]+', (raw or '').strip()):
        if not candidate_id:
            continue
        skill = await sync_to_async(get_skill)(candidate_id)
        if skill and skill not in resolved:
            resolved.append(skill)
    return resolved


async def _start(user, session, skill: dict) -> dict:
    """Grade the current lesson, end it, and open a new one on the requested skill.

    The grade is the point. Every other path that ends a session calls
    score_session first; this one did not, so asking for a different skill threw
    away whatever the student had just demonstrated. One real case: seventeen
    correct exchanges of ir + a + infinitivo, then a question about pronouns, and
    the skill was offered again days later because the grid had never heard of it.

    Best-effort: a student who asked for a different skill must not be blocked
    because grading raised.
    """
    import logging
    from django.utils import timezone
    from learner.models import Session
    from .scoring import score_session
    from .session import _open_session

    if session is not None:
        try:
            await score_session(session, user)
        except Exception as exc:
            logging.getLogger(__name__).error(
                'score_session failed for session %s on skill switch: %s', session.pk, exc)
        await sync_to_async(
            lambda: Session.objects.filter(pk=session.pk, ended_at__isnull=True)
                                   .update(ended_at=timezone.now())
        )()
    return await _open_session(user, text='', forced_skill=skill)


async def _park(session, candidates: list[dict], phrase: str, template: str) -> dict:
    from learner.models import Session

    state = dict(session.quiz_state or {})
    state['skill_request'] = {'candidates': [c['id'] for c in candidates],
                              'phrase': phrase}
    await sync_to_async(
        lambda: Session.objects.filter(pk=session.pk).update(
            quiz_state=state, current_phase='skill_request')
    )()
    session.quiz_state = state
    session.current_phase = 'skill_request'
    return {"text": template.format(options=_format_options(candidates)),
            "audio_url": None, "session_ended": False}


async def handle_skill_request(user, session, phrase: str) -> dict | None:
    """Act on a request for a specific lesson.

    One match starts it, several ask which, none says so honestly rather than
    inventing a pedagogical reason to refuse. Returns None when the message was
    not a request, so the turn routes normally.
    """
    from .curriculum import find_matching_skills

    candidates = await sync_to_async(find_matching_skills)(phrase)
    if not candidates:
        candidates = await _model_candidates(phrase)
        if candidates is None:
            # Not a request. Return None so the caller lets the turn route
            # normally instead of answering a complaint with a lesson menu.
            return None

    if not candidates:
        return {"text": NOT_IN_CURRICULUM, "audio_url": None, "session_ended": False}
    if len(candidates) == 1:
        return await _start(user, session, candidates[0])
    if session is None:
        # Nowhere to park the pending choice, so ask them to re-request precisely
        # rather than guess a skill on their behalf.
        return {"text": AMBIGUOUS_STATELESS_TEMPLATE.format(
                    options=_format_options(candidates),
                    example=candidates[0]['name']),
                "audio_url": None, "session_ended": False}
    return await _park(session, candidates, phrase, AMBIGUOUS_TEMPLATE)


async def resolve_pending_request(user, session, text: str) -> dict:
    """The student is answering the clarifying question."""
    from .curriculum import get_skill

    parked = (session.quiz_state or {}).get('skill_request') or {}
    candidates = []
    for candidate_id in parked.get('candidates') or []:
        skill = await sync_to_async(get_skill)(candidate_id)
        if skill:
            candidates.append(skill)

    if not candidates:
        return await handle_skill_request(user, session, text)

    picked = pick_candidate(candidates, text)
    if picked is None:
        return await _park(session, candidates, parked.get('phrase', ''),
                           UNRESOLVED_TEMPLATE)
    return await _start(user, session, picked)
