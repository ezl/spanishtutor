# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

A personalized Spanish learning system. An AI tutor named Luz Ángela teaches Spanish through chat DMs (Discord and Facebook Messenger today) — conversation, quizzes, and adaptive content tied to the user's personal interests. The system tracks ability across a skill × mode grid (A1→C2 skills × 5 CEFR language modes) and continuously pushes the boundary of what the user knows.

See `PLAN.md` for full product decisions and `ROADMAP.md` for V1 tickets.

## Tech Stack

| Layer | Choice |
|---|---|
| Framework | Django 5.x |
| Discord | discord.py (`bot/client.py`, long-lived socket) |
| Messenger | Meta Graph API webhook (`messenger/views.py`, `POST /webhook/messenger/`) |
| Discord entry point | `python manage.py run_bot` |
| Database | Postgres via Django ORM |
| Migrations | Django built-in (`manage.py migrate`) |
| Admin | Django Admin |
| LLM | Anthropic API (async) |
| STT | OpenAI Whisper |
| TTS | OpenAI TTS or ElevenLabs |
| Curriculum config | YAML files in `curriculum/` (runtime reload) |
| Hosting | Railway (bot + Postgres + cron jobs) |

## Architecture

Three layers. Each one only knows about the layer directly below it.

```
  TRANSPORT              DISPATCH                    ENGINE
  (platform I/O)         (transport-agnostic)        (teaching logic)

  bot/client.py      ┐
  (Discord socket)   │
                     ├──▶  engine/dispatch.py  ──▶  engine/core.py  ──▶  Postgres
  messenger/views.py │     handle(IncomingEvent)     handle_message()
  (Meta webhook)     ┘         -> list[Reply]
```

**`engine/dispatch.py` is the single entry point every transport calls.** It owns:

- **User resolution** — `resolve_user(platform, external_id, display_name)`. `PLATFORM_ID_FIELD` maps a platform name to the `User` column holding its identity (`discord_id`, `messenger_psid`). Adding a platform means adding one entry here, not a new lookup path.
- **First-message flow** — a brand-new user gets `FIRST_MESSAGE` before the engine ever sees the text.
- **The non-text guard** — voice clips, photos and stickers reach dispatch as empty `text` and get `NON_TEXT_NOTICE_TEXT`. It sits above command and engine routing (an attachment must never be scored as a blank answer) and below the first-message check (a new user sending a sticker gets onboarded, not corrected). Transports pass empty text through; none of them knows what an attachment is.
- **Commands** — the `COMMANDS` table (`!reset`, `!retest`, `!english`, `!spanish`, `!menu`, `!translate`) and `ARG_COMMANDS` for commands taking an argument (`!learn <topic>`), matched on the first word. Commands live here, never in a transport, so every command works on every platform for free.
- **Error wrapping** — any exception below dispatch becomes a friendly `Reply`, so no transport needs its own engine-error handling.
- **Welcome flow** — `handle_welcome(platform, external_id, display_name, ref)` for a platform's explicit "start" affordance (Messenger's Get Started tap, an `m.me?ref=` referral from the website CTA), where there's no user text to route. One website CTA serves everyone, so the reply is level-aware: never-onboarded → `FIRST_MESSAGE`; open session → no reply at all (their thread already shows Luz's pending turn); otherwise `WELCOME_BACK_TEXT`, which starts no session and calls no model so the engine's own returning-user check-in still owns that job. `ref` is logged for attribution only.
- **Normalizing the engine's response dict** into an ordered `list[Reply]`.

**`IncomingEvent`** (`platform`, `external_id`, `display_name`, `text`) and **`Reply`** (`text`, `follow_up`, `session_ended`) are the only two types crossing the transport/dispatch boundary. `IncomingEvent` is text-only by design — see the voice constraint below.

**A transport is pure I/O.** Its entire job is: receive a platform event → build an `IncomingEvent` → `await dispatch.handle(event)` → send each `Reply` back via the native API. Anything a transport does beyond that must be genuinely platform-specific — Discord's 2000-char chunking and markdown escaping, Messenger's HMAC signature check and 20-second webhook SLA. `engine.dispatch` is the only module either transport imports — nothing in `bot/` or `messenger/` touches `engine.core`, `engine.onboarding`, or the models.

**Adding a platform** is: a new transport module doing the I/O, plus one `PLATFORM_ID_FIELD` entry and its `User` identity column. No engine or dispatch changes.

**Channels do not share an account.** `resolve_user` creates one `User` per platform identity, and that is the intended design, not a gap — see the constraint below.

## Project Structure

```
spanishtutor/          # Django project (settings.py, urls.py)
bot/                   # TRANSPORT: Discord
  client.py            # socket client, chunking, markdown escaping
  management/commands/run_bot.py
messenger/             # TRANSPORT: Facebook Messenger
  views.py             # webhook: signature check, background thread, send
  client.py            # Meta Graph API send_message
  setup.py             # one-time page/profile configuration
engine/                # DISPATCH + ENGINE
  dispatch.py          # ← transport boundary: IncomingEvent/Reply, commands
  core.py              # handle_message() — engine entry point
  session.py           # session lifecycle + phase management
  teach_drill.py       # interleaved teach/drill grammar loop
  quiz_flow.py         # placement quiz
  quiz_evaluator.py    # answer evaluation
  scoring.py           # skill x mode scoring
  curriculum.py        # skill selection / next-skill logic
  skill_request.py     # student asks for a specific lesson (detect / resolve / start)
  onboarding.py        # FIRST_MESSAGE + onboarding flow
  translate.py         # !translate mode handler
  interests.py         # interest extraction
  persona.py           # Luz Ángela system prompt
learner/               # models (User, Session, SkillScore...), auth, web views
curriculum/            # skills.yaml, config.yaml (runtime reload)
```

## Key Design Constraints

- **Interface-agnostic engine**: never put conversation logic — or a command handler — in a transport. Transports handle platform I/O and nothing else. If you're about to write an `if text == '!something'` in `bot/` or `messenger/`, it belongs in `dispatch.COMMANDS`.
- **Text-only on chat platforms**: `IncomingEvent` has no attachments field on purpose. Voice gets built on the web app first, where we have full control; if a chat platform ever gets voice, it becomes an interface to the web version. This is a deliberate scope decision (2026-08-17), not an oversight.
- **Stateless per request**: no in-memory user state. Everything read from DB on each message.
- **Multi-tenant from day one**: every DB query scoped to `user_id`.
- **One channel, one user — no account linking** (decided 2026-08-22): a person who uses both Messenger and Discord has two `User` rows, two skill grids, and two placement quizzes, and they never merge. The common case is a single channel, and merging two independently-measured skill grids has no correct answer — you would be discarding someone's history or reconciling scores taken under different conditions. Do not build a linking or merge flow without revisiting this. **Before Stripe ships**, note that billing attaches to a `User`: either Discord stops being a public entry point, or billing has to attach to something that outlives a channel — otherwise a paying Messenger customer hits the paywall again on Discord.
- **The model never reasons about the curriculum**: what skills exist, what they require, and which one a request maps to are decided in code (`curriculum.py`, `skill_request.py`). The model classifies intent and relays the student's words; it is never asked to judge the skill graph. Letting it do so produced a confidently false claim to a student that subjunctive required solid preterite.
- **Config over code**: skill taxonomy, SRS weights, Luz Ángela's system prompt — all in config files. Changes to these don't require a redeploy, just a restart.

## Workflow

**Do not commit or deploy without explicit instruction from the user.** Make code changes locally, then wait to be told to commit and/or push.

**Do not write any code or make any changes without explicit instruction.** When the user reports a bug or asks a question, respond with analysis and proposed solutions only. Wait for explicit instruction ("fix it", "do it", "make that change") before touching any file. Questions and bug reports are openings for discussion — not action items.

## Common Commands

```bash
python manage.py migrate          # Run migrations
python manage.py run_bot          # Start Discord bot
python manage.py createsuperuser  # Create Django admin user
python manage.py shell            # Django shell
```

## Luz Ángela

The tutor persona. Mid-30s, from Laureles, Medellín. Warm, playful, direct. Speaks Colombian Spanish (Medellín dialect). Not sycophantic. Honest about being a bot if asked. Spanish-first, calibrated to user's CEFR level. English escape hatch: user says "English please."

Her persona is defined in `engine/persona.py` as a system prompt passed to every Anthropic API call. It must never be hardcoded in bot handlers.

## Web Auth — Magic Links

No passwords. Discord is the identity layer. When the bot sends a web link, it uses `django.core.signing` to generate a signed token containing `user_id` + expiry. The Django view validates the token and creates a session. Default expiry: 1 hour. Never build a traditional login form.

## Skill Grid

- **Vertical**: A1→C2 skills defined in `curriculum/skills.yaml`
- **Horizontal**: 5 CEFR modes — listening, reading, spoken_interaction, spoken_production, writing
- **Scores**: 0 (⬜ untested) → 1 (🟥) → 2 (🟨) → 3 (🟦) → 4 (🟩 mastered)
- **Atomic unit**: `skill × mode` — each cell has a score + last_tested_at + next_review_at
