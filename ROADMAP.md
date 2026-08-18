# Roadmap

---

## V1 — Foundation (current)

**Goal:** Luz Angela working end-to-end for a single user. Full evaluation, skill grid populated progressively, daily reminders, progress view — delivered over chat, **text only**.

### This phase is text-only, on a transport-agnostic engine

Two decisions define the shape of V1. They are deliberate, and they hold until Phase 5.

**1. Text only, on every chat platform.** No voice, no images, no attachments of any kind. There is no STT/TTS code in the repo and none is planned for a chat surface — voice is built web-first in Phase 5, where we control capture quality, streaming, and latency. `dispatch.IncomingEvent` therefore has no attachments field, on purpose. A transport that receives a non-text message should tell the user we're text-only, not silently drop it.

**2. The engine is separated from the transport layer.** Luz is an engine; a messaging platform is an implementation detail. Three layers, each aware only of the one below:

```
bot/client.py (Discord)  ┐
                         ├──▶ engine/dispatch.py ──▶ engine/core.py ──▶ Postgres
messenger/views.py (Meta)┘     handle(IncomingEvent)   handle_message()
                                  -> list[Reply]
```

`engine/dispatch.py` owns everything that is not platform-specific: user resolution, the first-message/welcome flow, all commands, error wrapping, and building the reply list. A transport does platform I/O and nothing else — its only engine import is `engine.dispatch`.

**Why this matters for the roadmap:** adding a platform (WhatsApp, SMS, Telegram, in-app web chat) is a new transport module, one `PLATFORM_ID_FIELD` entry, and an identity column on `User`. No engine or dispatch changes, and every existing command works on the new platform the day it ships. Any feature that isn't purely about how a platform sends bytes belongs in dispatch or below — if it gets written into `bot/` or `messenger/`, it will have to be written again for the next platform.

See the Architecture section of `CLAUDE.md` for the detail, and Phase 8 for how this shipped.

See V1 tickets below.

**Explicitly out of V1:**
- Other users / multi-user onboarding
- Payment / Stripe
- Public website / landing page
- Web UI
- **All voice and attachments** — voice arrives in Phase 5, web-first. Chat platforms stay text-only.

---

## Phase 2 — Web UI

- Web interface mirroring Discord experience
- Same conversation engine, new delivery layer (DRF API + frontend)
- Progress dashboard: skill grid, session history, score over time
- Audio in browser (microphone input + audio playback)

---

## Phase 3 — Public Launch

- Landing page + public signup
  - Wire up "Start" button: clicking it should initiate the onboarding sequence (redirect user to Discord DM or trigger bot intro)
- Discord join flow triggers bot DM automatically
- Stripe integration: first 10 lessons free, then subscription or pay-per-lesson
- Paywall enforcement in conversation engine
- User authentication (Django auth)

---

## Phase 4 — Learning Intelligence

- Full SM-2 spaced repetition algorithm (replacing simple decay)
- Pattern detection on recurring minor errors ("a common mistake you make is...")
- Session timing analytics (time of day, duration, retention correlation)
- Evaluatory summaries on demand
- Weekly progress reports via DM
- **Custom vocabulary / phrase sets**: user can add their own vocab or ask Luz to generate a set ("create a set of phrases around ordering food at a restaurant"). Sets are stored per-user and enter the SRS rotation alongside the core curriculum. Luz generates the set, confirms it with the user before saving, and treats each item as a skill cell in the grid.

---

## Phase 5 — Voice (web-first)

**Scope decision, 2026-08-17:** voice gets built on the web app first, not on a chat platform. We have maximal control there — real-time streaming, capture quality, latency, pronunciation scoring. Chat platforms stay **text-only**; `dispatch.IncomingEvent` has no attachments field on purpose. If a platform later gets voice, it becomes a thin interface onto the web version, by which point we'll already have the reps.

- Real-time voice conversation in the browser (live STT streaming, real-time response)
- Pronunciation scoring
- Replaces the voice-message approximation from V1
- *(Deferred, likely YAGNI until asked for:* bot joining a Discord voice channel*)*
- **Multi-modal SRS reinforcement**: after a skill is scored in one mode (writing), schedule subsequent reviews to test the same skill in a *different* mode (listening, spoken_interaction, spoken_production). Blocks on voice being available. Currently `_select_session` and `next_new_skill` in `engine/session.py`/`engine/curriculum.py` only look at `mode='writing'` scores — expand to consider the full skill × mode grid once voice input/output is wired up.

## Phase 6 — Personalization

- Dialect/style selector per user (Medellín, Castilian, Mexican, etc.)
- Interest-based content refinement (user voice system — periodic native language conversations to surface interests)
- Adaptive difficulty beyond CEFR (pushes past current level based on performance patterns)
- Goal setting (travel, heritage, work) influencing content type

---

## Phase 7 — Multi-User / Scale

- Full multi-user onboarding flow
- Billing and subscription management
- User dashboard (account, subscription, progress)
- Admin tooling beyond Django admin

---

## Phase 8 — Multi-Platform Transport ✅ *(done — 2026-08-17)*

Originally scoped as a *migration* off Discord onto Messenger. That framing is obsolete: rather than swapping one platform for another, the transport layer was decoupled so both run side by side and a third costs almost nothing.

**What actually shipped** (the `refactor(n/…)` dispatch-layer commit series):
- `engine/dispatch.py` — a transport-agnostic layer between platform I/O and the engine. Transports normalize into `IncomingEvent`, call `dispatch.handle()`, and send back the returned `list[Reply]`.
- Dispatch owns user resolution, the first-message flow, all six commands, and error wrapping. `bot/client.py` (115 LOC) and `messenger/views.py` (141 LOC) are I/O shims whose only engine import is `engine.dispatch`.
- Discord and Messenger both live, sharing one code path. Every command works on every platform automatically.

**How the original plan changed:**

| Originally planned | What we did instead |
|---|---|
| Replace `bot/client.py` with a Messenger webhook | Both transports coexist; neither is privileged |
| Rename `User.discord_id` → `User.external_id` | Obviated — `dispatch.PLATFORM_ID_FIELD` maps platform → identity column, so each platform keeps its own field. The schema allows one `User` row to hold both ids, but no account-linking flow exists yet |
| Remove the `discord.py` dependency | Kept — Discord is still a supported surface |
| Update magic-link auth for Messenger identity | Already platform-agnostic; `make_progress_token` signs `user.pk`, not a platform id |

**Adding a platform now:** write a transport module that does the platform's I/O, add one `PLATFORM_ID_FIELD` entry, add its identity column to `User`. No engine or dispatch changes. See the Architecture section of `CLAUDE.md`.

**Still open:** Meta app review is required before Messenger can serve users beyond the app's test accounts.


---

---

# V1 Tickets

---

## INFRA-1: Railway Deploy Pipeline

**Description:**
Connect GitHub repo to Railway. Any push to `main` triggers automatic redeploy of the learning bot.

**Acceptance criteria:**
- Railway project connected to GitHub repo
- Push to `main` redeploys within 3 minutes
- Environment variables configured in Railway (not in code)
- Deploy success/failure visible in Railway dashboard

---

## INFRA-2: Postgres Schema

**Description:**
Define the initial multi-tenant schema. Must support skill × mode scoring, session history, evaluation phases, and reminder preferences without a rewrite.

**Acceptance criteria:**
- Schema includes:
  - `users` (id, discord_id, native_language, interests, why_learning, target_use, reminder_enabled, reminder_schedule, created_at)
  - `skill_scores` (user_id, skill_id, mode, score 0–4, last_tested_at, next_review_at)
  - `sessions` (user_id, started_at, ended_at, session_type, evaluation_phase)
  - `session_events` (session_id, event_type, content, user_response, score, error_logged, dimension, timestamp)
  - `evaluation_progress` (user_id, phase, completed_at) — tracks which eval phases are done
- All tables scoped to user_id
- Django migrations, version-controlled and repeatable

---

## INFRA-3: Skill Taxonomy YAML

**Description:**
Define the full A1→C2 skill taxonomy as a YAML config file. This is the vertical axis of the skill grid.

**Acceptance criteria:**
- All skills defined in `curriculum/skills.yaml`
- Each skill has: id, name, cefr_level, description
- Covers A1 through C2
- Loaded at runtime — new skills added by editing YAML and restarting, no schema change required
- 5 language modes defined as constants: listening, reading, spoken_interaction, spoken_production, writing

---

## APP-1: Bot Skeleton + Railway Deploy

**Description:**
Discord bot running on Railway, connected to Postgres. Responds to DMs. Foundation for all app features.

**Acceptance criteria:**
- Bot online, responds to DMs
- Connected to Postgres on startup
- Deployed via Railway, auto-restarts on crash
- `manage.py run_bot` is the entry point
- Django admin accessible
- Push to `main` triggers redeploy

---

## APP-2: Luz Angela Persona + Conversation Engine

**Description:**
Interface-agnostic conversation engine. All message handling goes through `engine.handle_message(user_id, text, attachments)`. Luz Angela's persona lives here as a system prompt.

**Acceptance criteria:**
- `engine.handle_message()` callable from Discord bot and future web API
- Luz Angela system prompt defined (Medellín dialect, warm/direct/playful, Spanish-first, honest about being a bot)
- "English please" triggers English mode for that response, then returns to Spanish
- Response always calibrated to user's current estimated CEFR level

---

## APP-3: Onboarding + Initial Evaluation

**Description:**
New user DMs the bot → Luz Angela runs onboarding + session 1 evaluation → initial skill grid populated.

**Acceptance criteria:**
- First DM from new user triggers onboarding
- Luz Angela asks: native language, interests, why learning / where they want to use it
- Adaptive quiz bisects A1→C2 skill axis. User's self-report is starting difficulty only. Correct → harder, wrong → easier. Terminates when level estimated with confidence (~5–7 questions).
- Freeform written response collected and evaluated
- Initial CEFR estimate saved, skill grid seeded with estimates, untested cells marked ⬜
- `evaluation_progress` records session 1 complete
- Returning users skip onboarding entirely

---

## APP-4: Voice Message Support (STT + TTS)

**Description:**
User sends voice messages → transcribed via Whisper → evaluated. Luz Angela responds with TTS audio for listening exercises.

**Acceptance criteria:**
- Bot detects voice message attachments in DMs
- Downloads audio, sends to Whisper STT, receives transcript
- Transcript processed by conversation engine identically to text input
- For listening exercises: Luz Angela generates TTS audio (OpenAI TTS or ElevenLabs), uploads as voice message
- Error handling if audio is inaudible or transcription fails

---

## APP-5: Listening & Speaking Session Types

**Depends on:** APP-4 (STT + TTS infrastructure)

**Description:**
Add listening and speaking session types to cover the two modes the current system can't reach without voice. Scope is evaluation and rough scoring only — accent/pronunciation coaching is out of scope. The transcript is sufficient to score grammar and vocabulary accuracy; a pronunciation API or GPT-4o audio input is used to score fluency and rhythm. We do not attempt to coach phoneme-level articulation through a chat interface — the feedback loop is too weak to be reliable.

**Session types added:**

*Listening:* Luz generates a short passage and sends it as TTS audio. Student replies in text with comprehension answers and then produces sentences using target structures. Same comprehension (4 turns) + production (2 turns) structure as reading sessions. Scores `listening` mode.

*Spoken interaction:* Student sends voice messages; Luz responds with TTS audio. Structured like a conversation session (turn-limited). Transcript evaluated for grammar/vocab accuracy. Fluency scored via pronunciation API or GPT-4o audio. Brief qualitative feedback at close — no phoneme drilling. Scores `spoken_interaction` mode.

*Spoken production:* Student records a response to a prompt (text or audio). Single voice message. Transcript evaluated for accuracy; fluency scored via audio analysis. Luz surfaces 1-2 errors and asks for a single re-recording. Scores `spoken_production` mode.

**Session selection logic:**
- Listening: triggers when listening score lags reading score (same gap heuristic as reading vs writing)
- Spoken interaction: B1+ only, inserted into rotation alongside conversation
- Spoken production: triggers when spoken_production score lags writing score

**Acceptance criteria:**
- `_select_session` extended with rules for all three new types
- Listening sessions use TTS for passage delivery, text for student responses
- Speaking sessions accept voice message input via APP-4 STT pipeline
- All three types score the appropriate mode in `skill_scores`
- Close summary gives brief qualitative feedback; no pronunciation coaching content

**Explicitly out of scope:**
- Phoneme-level articulation coaching
- Prosody drilling or shadowing exercises
- Tracking pronunciation improvement over time at phoneme granularity

---

## APP-6: Session Flow (Open, Run, Close)

**Description:**
Every post-onboarding session has a defined open, content loop, and close.

**Acceptance criteria:**
- **Open**: Luz Angela recaps last session + recommends review vs. push forward based on SRS decay and current skill edge. One-question check-in: "¿Revisamos o seguimos?"
- **Content**: bot-driven, SRS selects skill × mode targets, content generated per APP-7. No mode repeats more than twice per session.
- **Close (explicit)**: user says "bye" or equivalent → immediate summary in Spanish
- **Close (inactivity)**: after configurable timeout → Luz Angela sends summary unprompted
- Summary format: worked on ___, reviewed ___, N skills improved, recommendation for next session
- Session start/end timestamps recorded

---

## APP-7: Content Generation

**Description:**
LLM-generated content for all 5 language modes, personalized to user interests.

**Acceptance criteria:**
- All 5 content types deliverable in DMs: quiz (MC + fill-in-blank), reading passage + questions, listening (TTS + questions), conversation (freeform exchange), written production (open prompt), voice (voice message prompt)
- Each type clearly labeled so user knows which mode they're in
- Content references user interests (from onboarding profile)
- Content complexity calibrated to user's current CEFR estimate
- All generated content and responses stored in `session_events` with mode recorded
- Inline error correction: significant errors → "You said ___. More natural: ___. Type it back once." Minor errors → silently logged
- `!strict` / `!relax` toggle per session

---

## APP-8: SRS Engine

**Description:**
Spaced repetition engine determines what to review vs. introduce each session.

**Acceptance criteria:**
- For review sessions: selects skill × mode cells with lowest scores or most overdue for review
- For push-forward sessions: selects next untested or lowest-scored skill at the edge of current level
- Predicted decay calculated from last_tested_at and score
- Never returns same skill × mode twice in one session
- Untested cells treated as highest priority for introduction
- Drives both session opening recommendation and content selection

---

## APP-9: Daily Reminders

**Description:**
Cron job sends personalized daily reminders via DM based on user preferences.

**Acceptance criteria:**
- Default: daily at noon, all 7 days
- Reminder content: which skills are decaying, how long a session would take to recover them
- Configurable via natural language at any time ("remind me Tuesdays and Thursdays at 9am")
- Natural language parsed and saved to `users.reminder_schedule`
- Railway cron job runs daily, evaluates each user's schedule and last session, sends DMs
- Users informed of default during onboarding and how to change it

---

## APP-10: Progress View

**Description:**
User requests progress summary → Luz Angela responds with skill grid snapshot and history.

**Acceptance criteria:**
- Triggered by user request in DM ("show my progress", "cómo voy", etc.)
- Displays current skill grid as colored squares (⬜🟥🟨🟦🟩)
- Shows top 3 strengths and top 3 weaknesses
- Shows progress over any time horizon ("last month", "since I started")
- Sessions completed, last session date
- Readable on mobile — no wide tables
- Generated from live DB data
