# Making the skill grid mean something

Five changes to how skills are scored and selected. Agreed 2026-08-29, not yet
built. Written for review before implementation.

## The problem, stated once

The system does two jobs: deliver instruction in sequence, and verify the skill
landed. Practice happens elsewhere -- this is the learning and evaluation
surface, not the sole surface of time spent.

It is decent at the first job and bad at the second. Of 14 skills currently
marked mastered for the primary user, **nine have been scored zero times across
zero sessions**. They are green because a placement quiz asked one question
months ago. The grid then decides what gets taught next, so a guess made once
propagates into every subsequent lesson choice.

Evidence, from the live grid:

| skill | score | scoring events |
|---|---|---|
| `b1_future_regular` | 4 | 0 |
| `b1_subjunctive_triggers_doubt_emotion` | 4 | 0 |
| `b1_vocab_work_career` | 4 | 0 |
| `b1_preterite_irreg_high_freq` | 4 | 1 |
| `b1_vocab_media_tech` | 1 | 0 |
| `b2_subjunctive_impersonal` | 1 | 0 |

## 0. Three fixes from session 68

Filed by the primary user within one minute, all on 2026-08-29.

**"This lesson feels remedial. And it's more English than before."** Luz was
drilling *nosotros*, *ellos*, *trabajar* -- A1 pack words -- at a B1 student,
because new-word selection had no level filter and the lexicon was 100% A1. A1
content also trips the persona's English-primary rule, which is the extra
English. Fixed by the level window (`_level_window`: the student's level plus
one below) and by writing packs A1-C1 so there is level-appropriate vocabulary
to offer.

**"It just assumed I didn't know the answer"** and **"broken -- after logging it
didn't keep going, so this is essentially a stuck state"** are one bug.
`looks_like_explicit_feedback` fires and writes the row, so the system has
ALREADY determined the message is feedback. Then inside `teach_drill` it
discards that and asks the model to re-derive it -- and the model read
"Feedback: broken. After logging it didn't keep going" as a student who did not
know, and did what you do for a student who does not know: supplied the answer.
Every other path returns FEEDBACK_ACK; `teach_drill` is the one that does not,
so nothing confirmed the capture either.

Fixed by passing what the code knows into the prompt rather than hoping it is
re-derived: a ContextVar sets a deterministic override telling the classifier
this is META-FEEDBACK, not to evaluate it, not to supply the answer, not to
advance, and to re-ask the same pending question. A ContextVar rather than a
module global so concurrent sessions cannot see each other's flag.

## 1. Exclude only score 4 from new-skill selection

**Today:** `session.py:743` builds `scored_ids` from every `SkillScore` row with
no filter on value, and `next_new_skill` excludes all of them. The quiz writes
`score = 1` when the student answers "I don't know".

**So saying "I don't know" in the placement quiz means that skill is never
taught.** It can only return as SRS review -- retrieval practice on something
never taught in the first place. Four skills are in that state right now.

**Change:** exclude only score 4. A 1 means "asked, failed, still needs
teaching", which is what it should always have meant.

## 2. The placement quiz caps at 3 near the placed level

**Today:** one quiz question writes a 0-4 straight into the grid
(`onboarding.py:535`). A single lucky answer at the frontier reads as mastery.

**Change:** the quiz may write 4 freely, EXCEPT at the placed level and the one
below it, where it caps at 3.

**Why one level and not two:** a single answer certifies mastery only when the
learner is demonstrably two or more levels above the material. Adjacent CEFR
levels overlap heavily and partial knowledge is normal there; two levels down is
genuinely consolidated. This also keeps a genuine B2's A1 and A2 grid clean, so
they are never retested on material they obviously have.

Above the placed level nothing is needed: the quiz found the boundary by the
learner getting things wrong, so those scores are already low.

## 3. Correct answers advance one step

**Today:** `scoring.py:161` does `score=score`. One session's judgement replaces
whatever was there. A single good session takes a skill from untested to
mastered.

**Change:** a correct answer advances one step. Mastery is reachable only by
repeated confirmation.

**Why:** the evidence is asymmetric. Succeeding once is weak -- you might have
it, or guessed, or the question was easy, or the model was generous. Replacement
treats one right answer as proof.

## 4. Incorrect answers drop the score

**Change:** unchanged from today in direction, and kept deliberately. If you
cannot do it now, you do not know it now. Recency wins on the way down.

Replacement is right descending and wrong ascending. That is the whole
asymmetry, and it is already how `UserWord` works: advance a rung on a correct
production, drop to the bottom on failure, graduate only on net evidence across
separate sessions. The word-level model is more rigorous than the skill-level
one; this brings them into line.

## 5. A dropped score routes to re-teaching, not re-review

**Today:** `_check_reteach` does not reteach. It sets `next_review_at` to now, so
the skill surfaces immediately as REVIEW. Its own docstring says "No-op for now".

So the loop is: fail a review, get reviewed again, fail again, get reviewed
again. A skill the student cannot do is tested repeatedly and never taught.

Retrieval practice only works on something already encoded. Testing an unlearned
item is not spaced repetition; it is failing on a schedule.

It compounds: SRS review is the highest-priority branch when 3+ skills are
overdue, and `_bump_to_now` manufactures overdue skills. A handful of weak
skills can pin the student in review and pre-empt new teaching entirely -- which
presents as "this lesson feels remedial".

**Change:** on a drop below 3, clear the review bump and return the skill to the
teaching queue so the next new-skill session picks it up.

## How the five compose

- Quiz says "don't know" → 1 → **taught**
- Quiz correct at the frontier → 3 → **taught once**, confirms to 4, then
  excluded on a long interval
- Quiz correct two levels down → 4 → never taught, light periodic review
- Review passes → advances, interval lengthens
- Review fails → drops, and is **re-taught** rather than re-tested

Interval stays purely score-driven, unchanged. With advance-one-step in place,
score already encodes evidence: you cannot reach 4 without repeated
confirmation, so a score-driven interval is an evidence-driven interval. No new
field is needed.

## Not doing

**Rebasing the existing grid.** The new rules correct it through normal testing.
A bulk rewrite of scores earned under different rules is hard to reason about
later. The four score-1 skills unblock automatically under change 1.

**A channel for outside practice.** Considered and rejected: SRS recurrence is
already the evidence-gathering mechanism. If another surface grooved a skill,
the review passes and the interval expands on its own. Self-reported knowledge
would substitute a claim for a measurement.
