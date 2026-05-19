# Spanish Tutor — Architecture Plan

## Core Requirement

A conversational, continuously adaptable Spanish learning system that:
- Never requires touching a terminal to update or iterate
- Uses chat as both the learning interface and the update mechanism
- Evolves through usage — the final form is unknown, so adaptability is the top priority

---

## Session Experience

- User opens app → bot is immediately ready to start a lesson, no setup required
- **Bot-driven**: it decides what to work on based on current learner state — user doesn't need to direct it
- Every session balances three things:
  1. **Spaced repetition** — re-test material on a decay schedule to lock in retention
  2. **Boundary pushing** — introduce concepts just beyond current level
  3. **Weakness targeting** — prioritize low-scoring skills/modes
- Long-term arc: A1 → C2 absolute mastery, managed automatically
- User can always override ("drill me on subjunctive", "let's do a story") but default is bot-driven
- **Session continuity**: resumes where it left off by default; user can ask for something new
- **Session prompt**: each session asks whether to review/reinforce recent material or push forward with new concepts — bot caters accordingly
- **Session timing**: all sessions logged with timestamps, duration, time of day — used over time to learn patterns and personalize scheduling/content length

**Interface**: chat is primary. Web is a future option — database is web-accessible by design to support it without a rewrite.

---

## End State Architecture

### The Learner Model (most critical — hardest to change later)

Internal state representing the user across two dimensions:

**Skill taxonomy — CEFR-derived (A1→C2):**
- Grammar components per level: preterite, imperfect, future, subjunctive, object pronouns, reflexives, ser/estar, etc.
- Vocabulary by domain and CEFR level
- Each skill scored individually with decay over time
- CEFR provides the skeleton; extended as gaps emerge through usage

**Language modes — scored independently per skill:**

Based on CEFR's five canonical skill dimensions:
- **Listening** (interpretive — audio/spoken input)
- **Reading** (interpretive — written input)
- **Spoken interaction** (real-time conversation)
- **Spoken production** (monologue, narration)
- **Writing** (constructed output)

Layered with cognitive depth levels (Bloom's applied to language):
- **Recognition** — can identify correct form when shown options
- **Recall** — can retrieve without prompts
- **Comprehension** — understands meaning in context
- **Production** — can construct correctly from scratch
- **Interaction** — can use fluidly in real-time

Each skill × mode × cognitive level gets its own score. The SRS engine uses this to determine what to drill and at what depth.

**Skill matrix** — the atomic unit of tracking is `skill × mode`:
- e.g. `preterite / comprehension`, `preterite / usage`, `future / comprehension`, `future / usage`
- Not overly granular — just enough resolution to know what to work on
- Each cell gets a simple score + last-tested timestamp for SRS decay

From this model, the system:
- Runs a spaced repetition engine (SRS) — decides what to review vs. introduce based on scores + decay
- Manages progression: re-testing known things while introducing new concepts at the boundary of current level
- Recommends what to work on next
- Generates an evaluatory summary on demand ("here are your strengths/weaknesses")

### Content Generation Layer

LLM-generated content targeted at specific `skill × mode` cells, wrapped almost entirely in the user's personal interests and voice.

**The 75% rule**: ~75% of all content is built around the user's actual interests, life, and topics — not arbitrary textbook sentences ("Roberto compró un carro"). The target language is the vehicle; the user's world is the content.

**Content types:**

**(A) Conversation practice**
- Chat-based in v1
- Audio (speech-to-text / text-to-speech) in final version
- LLM evaluates freeform responses, maps to `skill × mode` scores
- Tests: fluency, grammar in context, vocabulary recall, real-time production

**(B) Reading comprehension**
- Generated articles, news, or stories in user's interest domains
- Followed by quizzes: multiple choice AND freeform response
- Tests: understanding, spelling, typing accuracy, grammatical awareness
- LLM evaluates freeform; deterministic scoring for multiple choice

**(C) Quizzes**
- Multiple choice or fill-in-the-blank
- Targeted at specific `skill × mode` cells
- Deterministic scoring

**User voice system** — the system continuously learns the user:
- Periodically has conversations in the user's native language to surface interests, current topics, tone, knowledge domains
- Extracts and stores: interest tags, current topics, communication style, vocabulary they use
- This profile feeds directly into content generation — lessons feel personal, not generic
- Starting in native language removes the language barrier from the interest-discovery process
- Over time the system knows enough to generate content that sounds like it was written for this specific person

**Content generation inputs:**
1. Target `skill × mode` from SRS engine
2. User interest profile (pickleball, video games, current life topics)
3. User's CEFR level (controls complexity, vocab range)
4. Session context (review vs. push forward)
5. Target dialect/style profile (see below)

**Dialect and style targeting:**
- User selects a target dialect, register, and demographic — e.g. "Medellín young professional" vs "Spain 60-year-old" vs "Mexican street slang"
- Affects: vocabulary choices, idioms, formality (tuteo/voseo/usted), slang, pronunciation targets (for audio), cultural references
- Stored as part of user profile, changeable at any time
- Content generation and conversation evaluation both respect the selected style

### Error Handling During Conversation

- **Significant errors** → inline correction in a consistent format:
  > "You said ___. This would be more natural: ___. Type it back once to reinforce, then we'll move on."
  - One reinforcement rep, then conversation continues immediately
- **Minor errors** (accents, minor agreement, small typos) → silently logged, never interrupt flow
- **Pattern detection**: minor errors accumulate and surface in progress reports:
  > "A common error you make is ___"
- User can override strictness at any time ("be strict with me" / "just let me talk")

### Interface Layer

**Discord** (primary interface, multiple channels):
- `#lessons` — active learning, quizzes, drills
- `#curriculum` — current plan, what's queued, progression view
- `#feedback` — instruct the bot to change behavior, curriculum, or config
- `#progress` — stats, skill scores, history, evaluatory summaries
- `#admin` — trigger deploys, update config, system changes

Bot behavior varies by channel.

### Update / Deploy Layer

Goal: changes flow from phone → production with no terminal.

```
Phone (Discord/chat) → agent edits code or config → GitHub push → auto-deploy → bot updated
```

- **Config/curriculum changes** (no redeploy needed): vocab, skill weights, lesson emphasis, weak-area flags — all in editable YAML/JSON files loaded dynamically at runtime
- **Code changes** (redeploy needed): new drill types, behavior changes, bug fixes — triggered via `#admin` channel, handled by a dev agent

---

## Open Questions

### Skill Taxonomy Source
**Decided: CEFR-derived.** A1→C2 breakdown as the skeleton, extended as gaps emerge through usage. Each skill scored across 5 language modes × cognitive depth levels (recognition → interaction).

### Update Mechanism
What handles code edits and deploys from chat?
- OpenClaw (discussed in planning docs as the operator layer)
- Claude Code with remote access
- Other agent tooling

*Not yet decided.*

### Hosting
Where does the bot run?
- Railway / Render (auto-deploy from GitHub push — likely choice)

*Not yet decided.*

---

## Multi-User Design

The system is multi-tenant from day one:
- Every DB record scoped to a user ID
- Postgres (not SQLite) to handle concurrent users
- Stateless bot — no in-memory user state, everything read from DB per request
- Each user gets their own learner profile, skill scores, session history, interests, and generated curriculum
- The learning engine is identical per user — only the data differs

Onboarding flow not yet defined, but architecture supports: placement assessment, interest collection, goal setting → initial learner profile creation.

---

## Design Principles

- **Data-driven over code-driven**: vocab, weights, curriculum, weak-area emphasis live in config files — not in code. Changes to these should never require a redeploy.
- **Adaptability first**: v1 must not box in the learner model. The data schema is the one thing to get right early.
- **No terminal ever**: the update loop (chat → change → deploy) is as important as the learning features.

---

## V1 Strategy

Goal: smallest thing that gives a working feedback loop — learning features second, update loop first.

**Must have in v1:**
- Working Discord bot with at least `#lessons` and `#feedback` channels
- Learner model schema that can grow without a rewrite
- At least one content type (quiz or drill)
- Config-driven curriculum (YAML) so iteration doesn't require redeploys
- Chat → deploy pipeline working end to end

**Explicitly not in v1:**
- Full A1-C2 skill taxonomy
- All content types (stories, conversations)
- Polished spaced repetition algorithm
- Multi-mode tracking

---

## Tech Stack (Tentative)

| Component | Choice |
|---|---|
| Learning interface | Discord bot |
| Backend language | TBD |
| Database | Postgres (multi-tenant from day one) |
| LLM | Claude API |
| Curriculum/config | YAML + JSON files |
| Hosting | Railway or Render |
| Source of truth | GitHub |
| Dev/deploy agent | TBD (OpenClaw candidate) |
