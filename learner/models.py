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
    translate_mode_entered_at = models.DateTimeField(null=True, blank=True)
    resume_pending = models.BooleanField(default=False)
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
        ('conversation', 'Conversation'),
        # Planned, not yet built: reading and writing are two of the five CEFR
        # modes in the skill grid, so a session type for each is a stub someone
        # left on purpose, not an abandoned one.
        ('reading', 'Reading'),
        ('writing', 'Writing'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sessions')
    session_type = models.CharField(max_length=32, choices=SESSION_TYPES, blank=True)
    evaluation_phase = models.CharField(max_length=32, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    summary = models.TextField(blank=True)
    current_phase = models.CharField(max_length=32, blank=True, default='')
    phase_turns_completed = models.IntegerField(default=0)
    # Comprehension questions actually asked this phase. Distinct from
    # phase_turns_completed (student replies) because a correction or a
    # "ready?" turn spends a reply without asking anything.
    phase_questions_asked = models.IntegerField(default=0)
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


class ElicitationAsk(models.Model):
    """One question, asked once, to one student.

    Without this the picker had no memory: pick_for_session took an exclude_ids
    argument that nothing ever populated, and select_question breaks ties by
    question id, so the lowest-id question in the thinnest category was chosen
    every single session until a fact happened to land in that category.

    It also separates "never asked" from "asked, nothing came back", which raw
    fact counts cannot. Someone with no pets would otherwise be asked about pets
    forever, since the category stays permanently at zero.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='elicitation_asks')
    question_id = models.CharField(max_length=64)
    category = models.CharField(max_length=32)
    asked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'question_id')
        app_label = 'learner'

    def __str__(self):
        return f"{self.user} asked {self.question_id}"


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


class SessionFeedback(models.Model):
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name='feedback')
    anchor_event = models.ForeignKey(
        SessionEvent, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='feedback',
        help_text="The Luz turn the user was reacting to (usually the most recent assistant message).",
    )
    user_message = models.TextField(
        help_text="Raw user message that contained the feedback.",
    )
    interpretation = models.TextField(
        help_text="LLM's one-sentence paraphrase of what the user is flagging.",
    )
    resolved = models.BooleanField(default=False)
    resolution_note = models.TextField(blank=True, help_text="Optional note when reviewing/closing this.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = 'learner'
        ordering = ['-created_at']

    def __str__(self):
        return f"Feedback #{self.pk} ({self.created_at:%Y-%m-%d %H:%M})"


class LexItem(models.Model):
    """One vocabulary item: a single word, or a fixed multi-word chunk.

    Chunks are first-class because A1 teaches "me llamo" and "voy a" as
    unanalyzed wholes (Processability Theory Stage 1 -- see docs/vocabulary.md).
    `analyzable=False` is what stops a lesson from breaking one apart eleven
    lessons later, when the description that said not to is long out of scope.

    Packs are tags, not rows: pack metadata lives in curriculum/lexicon.yaml, so
    a word can sit in several packs without ever being duplicated. One row, one
    UserWord, one review schedule -- which is the whole point of making words
    first-class rather than prose inside a skill description.

    `owner` is null for the shared core catalogue and set for words generated
    from one person's interests. Interest vocabulary is therefore ordinary
    vocabulary with an owner, not a parallel system.
    """
    GENDERS = [('m', 'Masculine'), ('f', 'Feminine')]

    es = models.CharField(max_length=128)
    en = models.CharField(max_length=256)
    pos = models.CharField(max_length=32, blank=True)
    gender = models.CharField(max_length=1, blank=True, choices=GENDERS)
    analyzable = models.BooleanField(default=True)
    note = models.TextField(blank=True)              # teaching hint passed to the model
    packs = models.CharField(max_length=256, blank=True)   # comma-separated pack ids
    cefr_level = models.CharField(max_length=4, blank=True)
    owner = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.CASCADE, related_name='lex_items')
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ['cefr_level', 'es']
        app_label = 'learner'
        constraints = [
            # Postgres treats NULLs as distinct, so a plain unique_together on
            # (es, owner) would not actually constrain the core catalogue, where
            # owner is always NULL. Two partial constraints instead.
            models.UniqueConstraint(
                fields=['es'], condition=models.Q(owner__isnull=True),
                name='unique_core_lexitem'),
            models.UniqueConstraint(
                fields=['es', 'owner'], condition=models.Q(owner__isnull=False),
                name='unique_owned_lexitem'),
        ]

    def __str__(self):
        return f"{self.es} ({self.en})"

    @property
    def pack_list(self):
        return [p for p in (self.packs or '').split(',') if p]

    @property
    def display_es(self):
        """casa -> la casa. Articles are rendered, never stored."""
        if self.pos == 'noun' and self.gender:
            return f"{'la' if self.gender == 'f' else 'el'} {self.es}"
        return self.es


class UserWord(models.Model):
    """What one person has been taught, and when it is due again.

    Scheduling is exposure-driven rather than test-driven: meeting a word in
    context and understanding it counts as a rep, so a word does not need a quiz
    question to stay alive. Producing one is worth substantially more than
    seeing one, which is why the two counters are kept apart.
    """
    STATES = [('introduced', 'Introduced'), ('practiced', 'Practiced'), ('known', 'Known')]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='words')
    item = models.ForeignKey(LexItem, on_delete=models.CASCADE, related_name='user_words')
    state = models.CharField(max_length=16, choices=STATES, default='introduced')
    times_seen = models.IntegerField(default=0)
    times_produced = models.IntegerField(default=0)
    times_failed = models.IntegerField(default=0)
    # Explicit rather than derived from the counters: seeing a word refreshes it
    # without promoting it, so position on the ladder is no longer a function of
    # how many times it was met.
    interval_index = models.IntegerField(default=0)
    first_taught_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    next_due_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('user', 'item')
        ordering = ['next_due_at']
        app_label = 'learner'

    def __str__(self):
        return f"{self.user} | {self.item.es} ({self.state})"
