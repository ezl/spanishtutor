from django.shortcuts import render, get_object_or_404
from .models import User, Skill, SkillScore

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
    grouped = {level: [] for level in CEFR_ORDER}
    for skill in Skill.objects.filter(active=True).order_by('order'):
        grouped[skill.cefr_level].append({'id': skill.skill_id, 'name': skill.name, 'cefr_level': skill.cefr_level})
    return grouped


def landing(request):
    return render(request, 'learner/landing.html')


def progress(request, discord_id=None):
    if discord_id:
        user = get_object_or_404(User, discord_id=discord_id)
    else:
        user = User.objects.order_by('created_at').first()
        if not user:
            return render(request, 'learner/progress.html', {'no_user': True})

    scores = {
        (s.skill.skill_id, s.mode): s.score
        for s in SkillScore.objects.filter(user=user).select_related('skill')
        if s.skill
    }

    grouped_skills = _load_skills()
    cefr_rank = {level: i for i, level in enumerate(CEFR_ORDER)}
    estimated_rank = cefr_rank.get(user.estimated_cefr_level, -1)

    # Build grid data
    levels = []
    total_skills = 0
    skills_at_or_below = 0

    for level in CEFR_ORDER:
        skill_list = grouped_skills.get(level, [])
        level_rank = cefr_rank[level]
        rows = []
        for skill in skill_list:
            total_skills += 1
            if level_rank <= estimated_rank:
                skills_at_or_below += 1
            cells = []
            for mode_id, mode_label, mode_active in MODES:
                if not mode_active:
                    cells.append({'active': False})
                    continue
                score = scores.get((skill['id'], mode_id))
                if score is not None:
                    css_class = SCORE_CLASSES[score]
                    emoji = SCORE_EMOJIS[score]
                elif level_rank < estimated_rank:
                    css_class = 'inferred'
                    emoji = '🔷'
                else:
                    css_class = 'untested'
                    emoji = '⬜'
                cells.append({
                    'active': True,
                    'score': score,
                    'css_class': css_class,
                    'emoji': emoji,
                })
            rows.append({'skill': skill, 'cells': cells})
        levels.append({'level': level, 'rows': rows})

    # Mastery % = fraction of the skill ladder at or below estimated CEFR level
    mastery_pct = round(skills_at_or_below / total_skills * 100) if total_skills else 0

    return render(request, 'learner/progress.html', {
        'user': user,
        'levels': levels,
        'modes': [{'label': label, 'active': active} for _, label, active in MODES],
        'mastery_pct': mastery_pct,
        'skills_at_or_below': skills_at_or_below,
        'total_skills': total_skills,
    })
