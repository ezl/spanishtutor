# Skill Scoring Plan

## What we're building

After each session closes, an LLM pass evaluates the transcript and updates
SkillScore records for skills that were practiced. This feeds the SRS queue
so the decision tree has real data to work with.

## Decisions

### Prerequisite chains
Skills have explicit prerequisites defined in YAML and stored as M2M on Skill:

  prerequisites = ManyToManyField('self', symmetrical=False, blank=True)

When a skill is leeching (3 consecutive score 1s):
  1. Check prerequisites for the failing skill
  2. Score each — is any weak (score <= 2)?
  3. If yes → prioritize the weak prerequisite in the session queue
  4. Once prerequisite hits score 3 → return to original skill
  5. If no weak prerequisites → snooze 14 days, move forward

One-time setup cost to map the chains. Fixed taxonomy means it's done once
and used forever. Grammar skills have clear chains; vocabulary is mostly
independent.

### Skill model — move from YAML to DB
Skills live in the database, not runtime YAML. YAML becomes a seed definition
file synced via a `sync_skills` management command.

  class Skill(models.Model):
      skill_id   = CharField(unique=True)  # stable key, e.g. "a1_present_ar"
      name       = CharField
      cefr_level = CharField
      description = TextField
      order      = IntegerField  # explicit ordering, reorderable without rename
      active     = BooleanField(default=True)  # soft-delete
      replaces   = ForeignKey('self', null=True, blank=True)  # for splits

SkillScore and SkillScoreEvent reference Skill via FK instead of raw CharField.
Orphaned references are impossible. Taxonomy can be reordered or reorganized
without rotting historical data.

### Attribution (Challenge 1)
Store target skills on the session via a through table:

  class SessionSkill(models.Model):
      session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name='session_skills')
      skill_id = models.CharField(max_length=64)

Written at session open by the decision tree. Queryable for future analytics
and progress views.

Skill selection for all session types: identify the student's frontier using
spoken_production scores only (the most reliable signal — scored every session).
Frontier = first skill in taxonomy order where spoken_production score > 0 and < 3.
Score 0 (untested) is excluded — no signal. Untested skills above the frontier
are pulled in naturally via the "3 above" selection.
Take 6 skills below the frontier (reinforcement) and 3 above (push).
Candidate pool = 9 skills max. If fewer than 6 exist below, take what's there.
Edge cases:
  - Frontier at skill #1 → 0 below, 3 above.
  - No frontier (all skills mastered) → send win state message: "You won! You've
    gone as far as we can take you. We consider you at mastery for core Spanish
    skills." Then ask: "Would you like to do a full deep review of all skills?
    We'll run a comprehensive assessment covering every skill and mode." If yes,
    run a special mastery_review session covering all 66 skills across all active
    modes. Route to conversation indefinitely afterward.

Skills are selected from this pool for content generation AND passed to the
scoring prompt. No mastered skills from long ago, no skills never seen.
Wide pool gives Luz variety across sessions; scoring pass only scores what
actually came up in the transcript. Scoring prompt explicitly instructs: only
score a skill if it was meaningfully practiced (2-3+ exchanges directly
exercising it). Skip if insufficient evidence — a passing mention does not count.

The same constrained list serves double duty: tells Luz what to weave into
content, tells the scoring pass what to evaluate.

### When to score (Challenge 2)
Post-session LLM pass at close. Same pattern as interests extraction — runs
synchronously before the goodbye message is sent. Sessions are 4-8 minutes;
real-time per-exchange scoring is unnecessary complexity.

Failure handling: wrap scoring in try/except with timeout. If it fails, log
the error and send the goodbye anyway. Never block the user on a scoring failure.

Session close triggers:
  1. User says goodbye (explicit)
  2. 60-minute inactivity timeout — checked lazily on next message. If an
     active session's last event is 60+ minutes old, close it (run scoring)
     and open a new session for the incoming message. New session picks up
     naturally via the decision tree — SRS and frontier put them back on track.

### Score drops and reteaching (Challenge 6)
Scores can drop on review — forgetting is real.

Reteach triggers (treat as new_skill session instead of srs_review):
  - Score 1 after any session, including first introduction
  - Score 2 on 2+ consecutive sessions on the same skill

New skill sessions must end with an explicit assessment — Luz tests the skill
directly before closing so the transcript has clear scoring signal. A score of
1 immediately after first introduction means reteach next session.

Implementation: full SkillScoreEvent history table (append-only):
  - user, skill_id, mode, score, session FK, scored_at (auto)
  - One insert per scored skill per session close. Never updated.
  - consecutive_low_count derived from last N events, not stored as a counter.
  - Reteach check: query last 2 SkillScoreEvents for this skill/mode, if both score <= 2 → reteach.

### Modes (Challenge 5)
Score only modes where we have real data. Unscored modes stay at 0 with no
next_review_at — they're invisible to the decision tree until voice is built.

  writing            — scored every session (student produces Spanish in text)
  reading            — scored for reading sessions only
  spoken_production  — stub only, future phase (requires voice)
  spoken_interaction — stub only, future phase (requires voice)
  listening          — stub only, future phase (requires voice)

Mode is determined by session_type, not inferred from transcript.
Text chat maps to writing per CEFR spec. Voice modes are 0 until voice ships.

### SRS intervals (Challenge 4)
Simple lookup table, tune with real data later:
  1 = review in 1 day
  2 = review in 3 days
  3 = review in 7 days
  4 = review in 21 days

### Score scale (Challenge 3)
Direct scoring. The LLM sees the full transcript and sets the score outright.
No delta math. Scores 1-4:
  1 = struggled / major errors throughout
  2 = partial understanding / significant errors
  3 = solid / minor errors only
  4 = mastered / no meaningful errors
