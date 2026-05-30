from django.shortcuts import render, redirect, get_object_or_404
from .models import User, Skill, SkillScore
from .auth import validate_progress_token

MODES = [
    ('writing',              'Writing',            True),
    ('listening',            'Listening',          False),
    ('reading',              'Reading',            False),
    ('spoken_interaction',   'Spoken\nInteraction', False),
    ('spoken_production',    'Spoken\nProduction',  False),
]
SCORE_CLASSES = ['untested', 'shaky', 'developing', 'confident', 'mastered']
SCORE_EMOJIS = ['⬜', '🟥', '🟨', '🟦', '🟩']
CEFR_ORDER = ['A1', 'A2', 'B1', 'B2', 'C1', 'C2']


def _load_skills():
    return list(Skill.objects.filter(active=True).order_by('order'))


def _bracket_infer(skill_ids, raw_scores, mode_id):
    """
    For each skill in order, return (score, is_inferred).
    If the nearest tested skill below AND above an untested skill have the same score,
    infer that score. Mixed brackets or one-sided → (None, False) = untested.
    """
    tested = {sid: raw_scores[(sid, mode_id)] for sid in skill_ids if (sid, mode_id) in raw_scores}
    n = len(skill_ids)
    nearest_below = [None] * n
    nearest_above = [None] * n

    last = None
    for i, sid in enumerate(skill_ids):
        nearest_below[i] = last
        if sid in tested:
            last = tested[sid]

    last = None
    for i in range(n - 1, -1, -1):
        sid = skill_ids[i]
        nearest_above[i] = last
        if sid in tested:
            last = tested[sid]

    result = {}
    for i, sid in enumerate(skill_ids):
        if sid in tested:
            result[sid] = (tested[sid], False)
        elif nearest_below[i] is not None and nearest_above[i] == nearest_below[i]:
            result[sid] = (nearest_below[i], True)
        else:
            result[sid] = (None, False)
    return result


def landing(request):
    return render(request, 'learner/landing.html')


def auth_link(request, token):
    user_pk = validate_progress_token(token)
    if user_pk is None:
        return render(request, 'learner/link_expired.html', status=403)
    request.session['progress_user_pk'] = user_pk
    return redirect('progress')


def progress(request):
    user_pk = request.session.get('progress_user_pk')
    if not user_pk:
        return render(request, 'learner/link_expired.html', status=403)
    user = get_object_or_404(User, pk=user_pk)

    all_skills = _load_skills()
    skill_ids = [s.skill_id for s in all_skills]

    raw_scores = {
        (s.skill.skill_id, s.mode): s.score
        for s in SkillScore.objects.filter(user=user).select_related('skill')
        if s.skill
    }

    # Precompute bracket-interpolated scores per active mode
    active_mode_ids = [mode_id for mode_id, _, active in MODES if active]
    mode_display = {mode_id: _bracket_infer(skill_ids, raw_scores, mode_id) for mode_id in active_mode_ids}

    # Group skills by CEFR level for display
    grouped = {level: [] for level in CEFR_ORDER}
    for skill in all_skills:
        grouped[skill.cefr_level].append({'id': skill.skill_id, 'name': skill.name, 'description': skill.description})

    levels = []
    total_skills = 0
    skills_confident = 0  # tested or inferred score >= 3

    for level in CEFR_ORDER:
        rows = []
        for skill in grouped.get(level, []):
            total_skills += 1
            cells = []
            skill_confident = False
            for mode_id, mode_label, mode_active in MODES:
                if not mode_active:
                    cells.append({'active': False})
                    continue
                score, is_inferred = mode_display[mode_id].get(skill['id'], (None, False))
                if score is not None:
                    css_class = SCORE_CLASSES[score] + (' inferred' if is_inferred else '')
                    emoji = SCORE_EMOJIS[score]
                    if score >= 3:
                        skill_confident = True
                else:
                    css_class = 'untested'
                    emoji = '⬜'
                cells.append({
                    'active': True,
                    'score': score,
                    'is_inferred': is_inferred,
                    'css_class': css_class,
                    'emoji': emoji,
                })
            if skill_confident:
                skills_confident += 1
            rows.append({'skill': skill, 'cells': cells})
        levels.append({'level': level, 'rows': rows})

    mastery_pct = round(skills_confident / total_skills * 100) if total_skills else 0

    return render(request, 'learner/progress.html', {
        'user': user,
        'levels': levels,
        'modes': [{'label': label, 'active': active} for _, label, active in MODES],
        'mastery_pct': mastery_pct,
        'skills_confident': skills_confident,
        'total_skills': total_skills,
    })
