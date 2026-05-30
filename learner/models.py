from django.db import models


class User(models.Model):
    discord_id = models.CharField(max_length=64, unique=True, null=True, blank=True)
    messenger_psid = models.CharField(max_length=64, unique=True, null=True, blank=True)
    display_name = models.CharField(max_length=128, blank=True)
    native_language = models.CharField(max_length=64, blank=True)
    interests = models.TextField(blank=True)
    why_learning = models.TextField(blank=True)
    target_use = models.TextField(blank=True)
    estimated_cefr_level = models.CharField(max_length=4, blank=True)
    reminder_enabled = models.BooleanField(default=True)
    reminder_schedule = models.JSONField(default=dict)
    onboarding_complete = models.BooleanField(default=False)
    instruction_language = models.CharField(
        max_length=10,
        choices=[('auto', 'Auto'), ('english', 'English'), ('spanish', 'Spanish')],
        default='auto',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.display_name} ({self.discord_id})"

    class Meta:
        app_label = 'learner'


class Skill(models.Model):
    skill_id = models.CharField(max_length=64, unique=True)  # e.g. "a1_present_ar"
    name = models.CharField(max_length=128)
    cefr_level = models.CharField(max_length=4)
    description = models.TextField(blank=True)
    order = models.IntegerField(default=0)
    active = models.BooleanField(default=True)
    replaces = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='replaced_by'
    )
    prerequisites = models.ManyToManyField('self', symmetrical=False, blank=True, related_name='unlocks')

    class Meta:
        ordering = ['order']
        app_label = 'learner'

    def __str__(self):
        return f"{self.cefr_level} | {self.name}"


class EvaluationProgress(models.Model):
    PHASES = [
        ('session1', 'Session 1: Quiz + Freeform'),
        ('listening', 'Listening Comprehension'),
        ('speaking', 'Speaking'),
        ('reading', 'Reading Comprehension'),
        ('translation', 'Translation'),
        ('written_production', 'Written Production'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='evaluation_phases')
    phase = models.CharField(max_length=32, choices=PHASES)
    completed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'phase')
        app_label = 'learner'


class SkillScore(models.Model):
    MODES = [
        ('listening', 'Listening'),
        ('reading', 'Reading'),
        ('spoken_interaction', 'Spoken Interaction'),
        ('spoken_production', 'Spoken Production'),
        ('writing', 'Writing'),
    ]
    SCORES = [(0, 'Untested'), (1, 'Beginner'), (2, 'Developing'), (3, 'Confident'), (4, 'Mastered')]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='skill_scores')
    skill = models.ForeignKey(Skill, null=True, blank=True, on_delete=models.CASCADE, related_name='scores')
    mode = models.CharField(max_length=32, choices=MODES)
    score = models.IntegerField(default=0, choices=SCORES)
    last_tested_at = models.DateTimeField(null=True, blank=True)
    next_review_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('user', 'skill', 'mode')
        app_label = 'learner'

    def __str__(self):
        skill_name = self.skill.skill_id if self.skill else 'unknown'
        return f"{self.user} | {skill_name} / {self.mode} = {self.score}"

    @property
    def emoji(self):
        return ['⬜', '🟥', '🟨', '🟦', '🟩'][self.score]


class SkillScoreEvent(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='skill_score_events')
    skill = models.ForeignKey(Skill, on_delete=models.CASCADE, related_name='score_events')
    mode = models.CharField(max_length=32, choices=SkillScore.MODES)
    score = models.IntegerField()
    session = models.ForeignKey('Session', null=True, blank=True, on_delete=models.SET_NULL, related_name='score_events')
    scored_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['scored_at']
        app_label = 'learner'

    def __str__(self):
        return f"{self.user} | {self.skill.skill_id} / {self.mode} = {self.score}"


class Session(models.Model):
    SESSION_TYPES = [
        ('onboarding', 'Onboarding'),
        ('srs_review', 'SRS Review'),
        ('new_skill', 'New Skill'),
        ('vocab_sprint', 'Vocabulary Sprint'),
        ('conversation', 'Conversation'),
        ('reading', 'Reading'),
        ('writing', 'Writing'),
        # legacy
        ('evaluation', 'Evaluation'),
        ('review', 'Review'),
        ('push_forward', 'Push Forward'),
        ('user_directed', 'User Directed'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sessions')
    session_type = models.CharField(max_length=32, choices=SESSION_TYPES, blank=True)
    evaluation_phase = models.CharField(max_length=32, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    summary = models.TextField(blank=True)
    current_phase = models.CharField(max_length=32, blank=True, default='')
    phase_turns_completed = models.IntegerField(default=0)
    target_skill = models.ForeignKey(
        'Skill', null=True, blank=True, on_delete=models.SET_NULL, related_name='targeted_sessions'
    )
    quiz_state = models.JSONField(null=True, blank=True)

    class Meta:
        app_label = 'learner'

    def __str__(self):
        return f"{self.user} | {self.session_type} | {self.started_at:%Y-%m-%d %H:%M}"


class SessionSkill(models.Model):
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name='session_skills')
    skill = models.ForeignKey(Skill, on_delete=models.CASCADE, related_name='session_skills')

    class Meta:
        unique_together = ('session', 'skill')
        app_label = 'learner'

    def __str__(self):
        return f"{self.session} | {self.skill.skill_id}"


class UserInterest(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='user_interests')
    topic = models.CharField(max_length=128)
    category = models.CharField(max_length=64)
    confidence = models.FloatField(default=0.9)
    mention_count = models.IntegerField(default=1)
    first_seen_at = models.DateTimeField(auto_now_add=True)
    last_reinforced_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'topic')
        app_label = 'learner'

    def __str__(self):
        return f"{self.user} | {self.topic} ({self.category})"


class QuizQuestion(models.Model):
    FORMAT_CHOICES = [
        ('multiple_choice',   'Multiple Choice'),
        ('freeform_word',     'Freeform Word'),
        ('freeform_response', 'Freeform Response'),
    ]
    skill = models.ForeignKey('Skill', on_delete=models.CASCADE, related_name='quiz_questions')
    format = models.CharField(max_length=20, choices=FORMAT_CHOICES)
    question_text = models.TextField()
    options = models.JSONField(null=True, blank=True)   # {"a":..,"b":..,"c":..,"d":..}
    correct_answer = models.TextField()                  # "a" / word / exemplar sentence
    rubric = models.TextField(blank=True)                # freeform_response: target structures
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = 'learner'

    def __str__(self):
        return f"{self.skill.skill_id} | {self.format} | {self.question_text[:60]}"


class SessionEvent(models.Model):
    EVENT_TYPES = [
        ('quiz', 'Quiz Question'),
        ('reading', 'Reading'),
        ('listening', 'Listening'),
        ('conversation', 'Conversation'),
        ('written_production', 'Written Production'),
        ('voice', 'Voice'),
        ('correction', 'Error Correction'),
        ('system', 'System'),
        ('menu', 'Menu'),
        ('redirect', 'Redirect'),
        ('meta', 'Meta'),
    ]
    MODES = SkillScore.MODES

    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name='events')
    event_type = models.CharField(max_length=32, choices=EVENT_TYPES)
    dimension = models.CharField(max_length=32, choices=MODES, blank=True)
    skill_id = models.CharField(max_length=64, blank=True)
    content = models.TextField()
    user_response = models.TextField(blank=True)
    score_delta = models.IntegerField(null=True, blank=True)
    error_logged = models.BooleanField(default=False)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = 'learner'
