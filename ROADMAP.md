# Product Roadmap

---

## Phase 1 — V1: Foundation
*Goal: working deploy loop + one complete learning session end-to-end*

See tickets below.

---

## Phase 2 — Content Expansion
- User voice system (native language conversations to learn interests/tone)
- Weekly progress reports
- Freeform response quizzes (in addition to multiple choice / fill-in-the-blank)

---

## Phase 3 — Learning Intelligence
- Full CEFR A1→C2 skill taxonomy
- Sophisticated SRS (SM-2 algorithm or equivalent)
- Pattern detection on minor errors ("a common mistake you make is...")
- Session timing analytics (time of day, duration patterns)
- Evaluatory summaries on demand ("here are your current strengths/weaknesses")
- Curriculum view in `#curriculum` channel

---

## Phase 4 — Personalization
- Dialect/style selector (Medellín young professional, Castilian, Mexican, etc.) per user
- Interest-based content (75% of content wrapped in user's personal interests)
- Adaptive difficulty (pushes boundaries based on current level, not fixed curriculum)
- Goal setting (travel, heritage, work) influencing content type and vocabulary

---

## Phase 5 — Audio
- Text-to-speech for bot responses (hear native-dialect pronunciation)
- Speech-to-text for user responses (speak instead of type)
- Real listening comprehension (replaces timed-text approximation from v1)
- Real spoken interaction and production (replaces text approximation from v1)
- Pronunciation scoring

---

## Phase 6 — Web Interface
- Database already web-accessible by design
- Web chat interface mirroring Discord experience
- Progress dashboard (skill scores, session history, heatmap)
- Curriculum management UI

---

## Phase 7 — Multi-User / Public
- Onboarding flow for new users (placement assessment, interest collection)
- User authentication
- Isolated learner profiles per user
- Billing / subscription layer

---

---

# V1 Tickets

---

## INFRA-1: GitHub Repository + Railway Auto-Deploy

**Description:**
Set up the GitHub repository with a Railway project connected to it. Any push to `main` triggers an automatic redeploy of the learning bot.

**Acceptance criteria:**
- GitHub repo exists with a basic README and project structure
- Railway project is connected to the GitHub repo
- A push to `main` automatically triggers a redeploy within 3 minutes
- Deploy success/failure is visible in Railway dashboard
- Environment variables (DB connection, Discord token, Anthropic API key) are configured in Railway and not in code

---

## INFRA-2: Postgres Database + Schema

**Description:**
Provision a Postgres database and define the initial multi-tenant schema. This schema must support multiple users, skill × mode scoring, and session history without a rewrite.

**Acceptance criteria:**
- Postgres instance provisioned and accessible from Railway
- Schema includes at minimum:
  - `users` table (id, discord_id, native_language, created_at)
  - `user_profiles` table (user_id, interests[], dialect_preference, estimated_level)
  - `skill_scores` table (user_id, skill, mode, score, last_tested_at)
  - `sessions` table (user_id, started_at, ended_at, session_type)
  - `session_events` table (session_id, event_type, content, response, score, error_logged, timestamp)
- All tables have user_id as a foreign key (multi-tenant from day one)
- Migrations are version-controlled and repeatable
- Local development can run against a local Postgres instance

---

## INFRA-3: VPS + Claude Code CLI Setup

**Description:**
Provision a VPS with Claude Code CLI installed and configured to listen for instructions from Discord. This is the coding agent that makes codebase changes on demand.

**Acceptance criteria:**
- VPS provisioned and running
- Claude Code CLI installed and authenticated
- GitHub repo cloned on the VPS
- A script or service runs persistently and listens for messages from Discord dev server
- A test message in `#dev` ("add a comment to README") results in Claude Code making the change, pushing to GitHub, and confirming in `#logs`
- VPS auto-restarts the listener service on reboot

---

## INFRA-4: Discord Dev Server

**Description:**
Set up the developer Discord server with correct channels and a bot that routes messages to the Claude Code agent on the VPS.

**Acceptance criteria:**
- Discord server created with channels: `#features`, `#dev`, `#deploy`, `#bugs`, `#logs`
- Bot is present in the server
- Messages in `#dev` and `#deploy` and `#bugs` are forwarded to the VPS agent
- Agent responses are posted back to the appropriate channel
- `#logs` receives automatic posts from the agent (deploy confirmations, test results) without user prompting
- Bot ignores messages from itself to prevent loops

---

## APP-1: Discord Learning Server + Bot Skeleton

**Description:**
Set up the learning Discord server with the correct channels and a bot that responds to messages. No learning logic yet — just the skeleton.

**Acceptance criteria:**
- Discord server created with channels: `#lessons`, `#progress`, `#feedback`, `#curriculum`
- Bot is present and responds to a ping (`!ping` → "pong") in any channel
- Bot behavior varies by channel (different response or acknowledgment per channel)
- Bot is deployed via Railway and restarts automatically on crash
- Bot connects to the Postgres database on startup

---

## APP-2: User Onboarding

**Description:**
When a new user sends their first message in `#lessons`, the bot runs an onboarding flow to collect interests, set dialect preference, and estimate starting level.

**Acceptance criteria:**
- First message from a new user in `#lessons` triggers onboarding
- Bot asks for: native language, interests (freeform), why learning Spanish
- Bot asks for dialect preference with examples ("Medellín Colombia", "Madrid Spain", "Mexico City") — defaults to Colombian Spanish if skipped
- Bot runs a two-part placement assessment to estimate starting CEFR level:
  - **Part A — Adaptive quiz**: multiple choice questions that adjust difficulty based on answers. Correct → harder, incorrect → easier. Terminates early once level is estimated with confidence. Target: 10-15 questions max, ~5 minutes.
  - **Part B — Conversational**: short freeform chat in Spanish (~10 min). LLM evaluates grammar, vocabulary, and production ability in real time. Surfaces mode gaps (e.g. recognizes grammar rules but struggles to produce). Bot tells user upfront: "I'm going to chat with you briefly to get a sense of your level."
  - Part A runs first to establish a structural baseline. Part B refines it with production data.
  - Combined estimate saved as initial CEFR level per skill × mode across all 5 dimensions (not a single overall score)
  - Placement includes a timed reading passage (approximates listening), a freeform writing prompt (approximates spoken production), and a short conversation (spoken interaction) to seed all 5 dimensions from day one
- All collected data saved to `users` and `user_profiles` tables
- Returning users skip onboarding and go straight to session start
- Onboarding can be re-triggered with `!onboard` command

---

## APP-3: Session Start + Resume

**Description:**
Every time a user messages in `#lessons`, the bot either resumes an incomplete session or starts a new one with a review/push-forward prompt.

**Acceptance criteria:**
- If a session was left incomplete (no explicit end), bot offers to resume: "Welcome back — want to pick up where we left off, or start something new?"
- If starting fresh, bot asks: "Do you want to review recent material or push forward with something new?"
- User response is stored as `session_type` (review / new / resume) in `sessions` table
- Session start timestamp is recorded
- Bot acknowledges and moves immediately into content — no further setup questions

---

## APP-4: Skill Taxonomy (A1-B1 Subset)

**Description:**
Define the initial skill taxonomy covering A1-B1 grammar and vocabulary. This is the data that drives what the bot teaches and tests.

**Acceptance criteria:**
- Skill taxonomy defined in a YAML config file (not hardcoded)
- Covers A1-B1 skills at minimum: present tense, preterite, ser/estar, gender/agreement, basic vocab domains, object pronouns
- Each skill has: id, name, cefr_level, modes[] (comprehension / usage)
- Taxonomy is loaded by the app at runtime (no redeploy needed to add a skill)
- A new skill can be added by editing the YAML file and restarting — no schema change required

---

## APP-5: Content Generation — All 5 Skill Dimensions

**Description:**
The bot generates content targeting all five CEFR skill dimensions. Each dimension has a distinct content format appropriate for text-based v1. All content is contextually tied to the user's interests.

**The 5 dimensions and their v1 format:**

**Listening** (approximated — real audio in Phase 5)
- Bot presents a passage marked as a listening simulation, displayed for a time-limited window
- Followed by comprehension questions (multiple choice or freeform)
- Slot exists in learner model; real audio replaces timed text in Phase 5

**Reading**
- Bot generates a short article, story, or dialogue in the user's interest domain
- Followed by comprehension questions: multiple choice and freeform response
- Tests understanding, vocabulary in context, inference

**Spoken interaction** (approximated via text chat)
- Bot initiates a freeform conversation on a topic tied to user's interests
- LLM evaluates grammar, vocabulary, fluency in real time
- Error correction applies per APP-7 rules

**Spoken production** (approximated via prompted freeform writing)
- Bot gives a prompt: "Describe what you did last weekend" / "Tell me about your favorite sport"
- User writes a freeform response; LLM evaluates structure, tense usage, vocabulary
- Real spoken production (voice) added in Phase 5

**Writing**
- Multiple choice, fill-in-the-blank, and short freeform written responses
- Targets specific grammar skill × writing mode

**Acceptance criteria:**
- All 5 dimension types can be generated and delivered in `#lessons`
- Each content type is clearly labeled so the user knows what mode they're in
- Content references the user's interests (pulled from user profile) for all types
- Bot waits for user response before evaluating
- All generated content and responses stored in `session_events` with dimension type recorded
- No dimension type repeats more than twice in a single session
- SRS engine can target any of the 5 dimensions independently

---

## APP-6: Quiz Evaluation + Scoring

**Description:**
Bot evaluates user responses to quizzes, updates skill scores, and applies spaced repetition decay.

**Acceptance criteria:**
- Multiple choice: deterministic scoring (correct / incorrect)
- Fill-in-the-blank: LLM evaluates response, allows for minor spelling variation if meaning is clear
- Correct answer → skill score increases, next review interval extended
- Incorrect answer → skill score decreases, item queued for sooner review
- Score update written to `skill_scores` table immediately after evaluation
- Bot confirms correct/incorrect and moves to next question without lengthy explanation unless user asks
- Score delta is logged in `session_events`

---

## APP-7: Inline Error Correction

**Description:**
During quizzes, significant errors trigger an inline correction with one reinforcement rep before moving on. Minor errors are silently logged.

**Acceptance criteria:**
- LLM classifies each error as significant or minor
- Significant error triggers response in this format:
  > "You said ___. This would be more natural: ___. Type it back once to reinforce, then we'll move on."
- Bot waits for the user to type back the correction before continuing
- Minor errors are logged to `session_events` with `error_logged: true` but produce no bot response
- Correction format is consistent every time (not paraphrased)
- User can type `!strict` or `!relax` to change correction sensitivity for the session

---

## APP-8: SRS Engine

**Description:**
Simple spaced repetition engine that determines what to review vs. introduce next based on skill scores and time since last tested.

**Acceptance criteria:**
- For "review" sessions: selects skills with lowest scores or longest time since last tested
- For "push forward" sessions: selects the next untested or lowest-scored skill at the boundary of the user's current level
- SRS decisions are based on data in `skill_scores` table, not hardcoded
- Engine returns a ranked list of `skill × mode` targets for the session
- A skill not yet tested is treated as highest priority for introduction
- Engine never returns the same skill × mode twice in a single session

---

## APP-9: Basic Progress View

**Description:**
User can ask for a progress summary in `#progress` and receive a snapshot of their current skill scores and recent session history.

**Acceptance criteria:**
- Any message in `#progress` triggers a summary response
- Summary includes: estimated current CEFR level, top 3 strengths, top 3 weaknesses, number of sessions completed, last session date
- Skill scores displayed as simple levels (beginner / developing / solid / strong) not raw numbers
- Summary generated from live database data, not cached
- Response is readable on mobile (no wide tables or complex formatting)
