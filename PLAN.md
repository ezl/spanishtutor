# Spanish Tutor — Product Plan

## What This Is

A personalized Spanish learning system delivered primarily through Discord. An AI tutor named Luz Angela teaches you Spanish through conversation, quizzes, voice messages, and adaptive content — all tied to your personal interests. The system tracks your ability across a full skill × mode grid and continuously pushes the boundary of what you know.

---

## Luz Angela

The tutor persona. Mid-30s, from Laureles, Medellín. Warm, playful, direct, lightly flirty in the way Colombian women naturally are. Speaks Medellín Spanish. Not sycophantic — she tells you when your Spanish is wrong and makes you want to try harder anyway. If asked whether she's a bot, she says yes, straightforwardly.

The persona guides tone, dialect, and style. There is no fictional backstory. She is an AI.

Her first message to every new user:

> *¡Hola! Soy Luz Angela, tu profesora de español. 🌟*
>
> *Vamos a empezar con una evaluación rápida para entender dónde estás — no te preocupes, no es un examen formal. Solo quiero conocer tu español.*
>
> *Primero: ¿cuál es tu lengua nativa, y por qué estás aprendiendo español?*
>
> *(If you ever want instructions in English, just say "English please" — I've got you.)*

---

## Interface

**Primary**: Discord DMs. User messages the bot directly. No shared channels, no per-user servers. Scales naturally and feels personal.

**Future**: Web UI with identical functionality. Both surfaces call the same conversation engine — Discord is a delivery layer, not where the logic lives.

**Voice**: User sends voice messages (Discord voice note feature). Bot downloads, transcribes via Whisper STT, processes, responds with TTS audio. No voice channels — DM voice messages only.

**Language**: Spanish-first, calibrated to the user's current level. English escape hatch always available: user says "English please" and Luz Angela accommodates. Instructions in onboarding make this clear upfront.

---

## Architecture

```
Discord DM  ──┐
              ├──▶  Conversation Engine (Django)  ──▶  Postgres
Web UI      ──┘         (Luz Angela lives here)
```

The conversation engine is interface-agnostic. `engine.handle_message(user_id, text, attachments)` returns a response regardless of surface. Discord bot and web API both call this function. Luz Angela never knows which surface she's on.

**Stack:**
| Layer | Choice |
|---|---|
| Framework | Django 5.x |
| Discord | discord.py (Cogs) |
| Bot entry point | Django management command (`manage.py run_bot`) |
| Database | Postgres via Django ORM |
| Migrations | Django built-in |
| Admin | Django Admin |
| LLM | Anthropic API (async) |
| STT | OpenAI Whisper |
| TTS | OpenAI TTS or ElevenLabs |
| Curriculum config | YAML files (runtime reload, no redeploy) |
| Hosting | Railway (bot + Postgres + cron jobs) |
| Future web layer | Django views + Django REST Framework |

---

## The Skill Grid

The core data model. Every learner has a grid:

**Vertical axis — skill taxonomy (A1→C2, defined in YAML):**
Skills are CEFR-derived grammar and vocabulary components. Examples: present tense regular verbs, preterite irregular, subjunctive, ser/estar, object pronouns, idiomatic expressions. Full A1→C2 defined in `curriculum/skills.yaml`.

**Horizontal axis — the 5 CEFR language modes:**
1. Listening
2. Reading
3. Spoken interaction
4. Spoken production
5. Writing

**Each cell (skill × mode) has:**
- Score (0–4)
- Last tested timestamp
- SRS decay curve

**4-point scoring:**
| Score | Grid display | Luz Angela says |
|---|---|---|
| 0 | ⬜ untested | — |
| 1 | 🟥 beginner | *Apenas empezando* |
| 2 | 🟨 developing | *En camino* |
| 3 | 🟦 confident | *Ya casi* |
| 4 | 🟩 mastered | *¡Lo tienes!* |

---

## Evaluation

The grid is populated progressively — not in a single evaluation session.

**Session 1 (onboarding + initial estimate):**
1. Three onboarding questions: native language, interests, why learning Spanish / where they want to use it
2. Adaptive quiz — bisects the A1→C2 skill axis. Correct → harder, wrong → easier. Converges on estimated CEFR level in 5–7 questions. User's self-reported level is just the starting point, not trusted.
3. Freeform written response — "Tell me a little about yourself in Spanish." Reveals grammar in production, sentence complexity, tense usage, vocabulary in use.
4. Initial grid populated with estimates. Untested cells remain ⬜.

**Subsequent sessions:**
Each session adds one additional evaluation phase until the full grid is covered. Phases: listening (TTS audio + comprehension), speaking (voice message response), reading comprehension, translation (EN→ES and ES→EN), written production. Luz Angela is transparent: she's still calibrating.

**Binary search principle:** if a user aces B2, jump to C1. If they fail C1, probe B2 in depth. Converges without exhaustive questioning.

---

## Session Flow

**Opening:**
> *"Hola! Last time we worked on preterite tense — you're solid on recognition but production is your edge. It's been 4 days, so some of it may have decayed. I recommend reviewing before we push forward. ¿Revisamos o seguimos?"*

Recommendation is calculated from: skill × mode scores, last tested timestamps, predicted SRS decay, current edge of ability. Not a guess.

**During session:**
- Bot-driven. Luz Angela decides what to work on. User can override ("drill me on subjunctive", "let's do a story").
- Content tied to user's interests (~75% of content wrapped in personal context).
- All 5 language modes covered across sessions. No mode repeats more than twice per session.
- Error correction: significant errors get inline correction ("You said ___. More natural: ___. Type it back once."), then move on. Minor errors silently logged.
- User can say `!strict` or `!relax` to adjust correction sensitivity.

**Closing:**
- Explicit: user says "bye" (or similar) → immediate summary in Spanish.
- Inactivity: after timeout, Luz Angela sends the summary unprompted.

Summary format (in Spanish, calibrated to user's level):
> *¡Buena sesión, Eric! Hoy trabajamos en ____ y ____. Repasamos ____ y ____. Has aprendido ____ habilidades nuevas. Para la próxima sesión, te recomiendo ____. ¡Que tengas un buen día!*

---

## Reminders

Default: daily at noon, all 7 days, via DM.

Content is personalized — which skills are decaying, how long it'll take to recover. Not generic push notifications.

Configurable via natural language at any time:
- "Remind me daily at 2pm"
- "Tuesdays and Thursdays at 9am and Saturday at 3pm"
- "No reminders on weekends"

Implemented as a Railway cron job: runs daily, checks each user's last session and reminder preferences, sends DMs accordingly.

---

## Content Generation

All content LLM-generated. Inputs to every generation call:
1. Target `skill × mode` from SRS engine
2. User interest profile (captured in onboarding, refined over time)
3. User's current CEFR level
4. Session type (review vs. push forward)
5. Luz Angela's persona system prompt (Medellín dialect, tone, style)

Content types:
- **Quiz**: multiple choice, fill-in-the-blank, targeted at specific skill × mode cells
- **Reading**: short passage in user's interest domain + comprehension questions
- **Listening**: TTS audio clip + comprehension questions
- **Conversation**: freeform exchange, LLM evaluates grammar/vocab/fluency in real time
- **Written production**: open prompt ("describe your weekend"), LLM evaluates structure and tense
- **Voice**: user sends voice message, Whisper transcribes, LLM evaluates spoken production

---

## Progress View

User requests progress in `#progress` (or equivalent in web UI). Luz Angela responds with:
- Current skill grid snapshot (colored squares)
- Grid at any past timestamp for comparison
- Top 3 strengths, top 3 weaknesses
- Sessions completed, last session date
- "In the past month, here's what moved" — cells that changed score

Readable on mobile. No wide tables.

---

## Web UI Authentication — Magic Links

The web UI requires no passwords or signup forms. Discord is the identity layer.

**Flow:**
1. User requests something in Discord that has a web view ("show my scorecard", "open settings")
2. Bot generates a signed, time-limited URL using `django.core.signing`
3. Bot sends the link in DM: *"Aquí está tu progreso → [ver mi scorecard](url) (válido por 1 hora)"*
4. User clicks → Django validates signature and expiry → sets a session → shows the page

**Implementation:**
- Token encodes `user_id` + expiry timestamp, signed with Django's secret key
- Default expiry: 1 hour (configurable)
- On click: Django view validates token, creates a session, redirects to the requested page
- No passwords, no email verification, no "forgot password" — ever

This applies to: skill grid / scorecard, settings (reminder schedule, dialect preference), session history, progress charts.

---

## Multi-User Design

Multi-tenant from day one:
- Every DB record scoped to `user_id`
- No in-memory user state — everything read from DB per request
- Stateless conversation engine
- Each user has isolated profile, skill grid, session history, interests

V1 is single-user (the developer). Other users, payment, and public signup are future phases.

---

## What Requires Redeploy vs. What Doesn't

| Change | Approach |
|---|---|
| Add/edit skills in taxonomy | Edit `curriculum/skills.yaml`, restart |
| Adjust SRS weights | Edit config file, restart |
| Change Luz Angela's system prompt | Edit config file, restart |
| Add a new content type | Code change → redeploy |
| Bug fix | Code change → redeploy |
| New Discord command | Code change → redeploy |
