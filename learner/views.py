import yaml
import os
from django.shortcuts import render, get_object_or_404
from .models import User, SkillScore

SKILLS_PATH = os.path.join(os.path.dirname(__file__), '..', 'curriculum', 'skills.yaml')
MODES = ['listening', 'reading', 'spoken_interaction', 'spoken_production', 'writing']
MODE_LABELS = ['Listening', 'Reading', 'Spoken\nInteraction', 'Spoken\nProduction', 'Writing']
SCORE_CLASSES = ['untested', 'shaky', 'developing', 'confident', 'mastered']
SCORE_EMOJIS = ['⬜', '🟥', '🟨', '🟦', '🟩']
CEFR_ORDER = ['A1', 'A2', 'B1', 'B2', 'C1', 'C2']


def _load_skills():
    with open(SKILLS_PATH) as f:
        data = yaml.safe_load(f)
    skills = data['skills']
    grouped = {level: [] for level in CEFR_ORDER}
    for skill in skills:
        grouped[skill['cefr_level']].append(skill)
    return grouped


def progress(request, discord_id=None):
    if discord_id:
        user = get_object_or_404(User, discord_id=discord_id)
    else:
        user = User.objects.order_by('created_at').first()
        if not user:
            return render(request, 'learner/progress.html', {'no_user': True})

    scores = {
        (s.skill_id, s.mode): s.score
        for s in SkillScore.objects.filter(user=user)
    }

    grouped_skills = _load_skills()

    # Build grid data
    levels = []
    total_cells = 0
    mastered_cells = 0

    for level in CEFR_ORDER:
        skills = grouped_skills.get(level, [])
        rows = []
        for skill in skills:
            cells = []
            for mode in MODES:
                score = scores.get((skill['id'], mode), 0)
                cells.append({
                    'score': score,
                    'css_class': SCORE_CLASSES[score],
                    'emoji': SCORE_EMOJIS[score],
                })
                total_cells += 1
                if score == 4:
                    mastered_cells += 1
            rows.append({'skill': skill, 'cells': cells})
        levels.append({'level': level, 'rows': rows})

    mastery_pct = round(mastered_cells / total_cells * 100) if total_cells else 0

    return render(request, 'learner/progress.html', {
        'user': user,
        'levels': levels,
        'modes': MODE_LABELS,
        'mastery_pct': mastery_pct,
        'mastered_cells': mastered_cells,
        'total_cells': total_cells,
    })
