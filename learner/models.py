from django.db import models


class User(models.Model):
    discord_id = models.CharField(max_length=64, unique=True)
    display_name = models.CharField(max_length=128, blank=True)
    native_language = models.CharField(max_length=64, blank=True)
    interests = models.TextField(blank=True)
    why_learning = models.TextField(blank=True)
    target_use = models.TextField(blank=True)
    estimated_cefr_level = models.CharField(max_length=4, blank=True)  # A1, A2, B1, B2, C1, C2
    reminder_enabled = models.BooleanField(default=True)
    reminder_schedule = models.JSONField(default=dict)  # {days: [0-6], time: "HH:MM"}
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
    skill_id = models.CharField(max_length=64)  # matches id in skills.yaml
    mode = models.CharField(max_length=32, choices=MODES)
    score = models.IntegerField(default=0, choices=SCORES)
    last_tested_at = models.DateTimeField(null=True, blank=True)
    next_review_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('user', 'skill_id', 'mode')
        app_label = 'learner'

    def __str__(self):
        return f"{self.user} | {self.skill_id} / {self.mode} = {self.score}"

    @property
    def emoji(self):
        return ['⬜', '🟥', '🟨', '🟦', '🟩'][self.score]


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

    class Meta:
        app_label = 'learner'

    def __str__(self):
        return f"{self.user} | {self.session_type} | {self.started_at:%Y-%m-%d %H:%M}"


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
