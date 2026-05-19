# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

A personalized Spanish learning system. An AI tutor named Luz Angela teaches Spanish through Discord DMs — conversation, quizzes, voice messages, and adaptive content tied to the user's personal interests. The system tracks ability across a skill × mode grid (A1→C2 skills × 5 CEFR language modes) and continuously pushes the boundary of what the user knows.

See `PLAN.md` for full product decisions and `ROADMAP.md` for V1 tickets.

## Tech Stack

| Layer | Choice |
|---|---|
| Framework | Django 5.x |
| Discord | discord.py (Cogs) |
| Bot entry point | `python manage.py run_bot` |
| Database | Postgres via Django ORM |
| Migrations | Django built-in (`manage.py migrate`) |
| Admin | Django Admin |
| LLM | Anthropic API (async) |
| STT | OpenAI Whisper |
| TTS | OpenAI TTS or ElevenLabs |
| Curriculum config | YAML files in `curriculum/` (runtime reload) |
| Hosting | Railway (bot + Postgres + cron jobs) |

## Architecture

The conversation engine is interface-agnostic. All message handling goes through `engine.handle_message(user_id, text, attachments)`. The Discord bot and future web API both call this function — Luz Angela never knows which surface she's on.

```
Discord DM  ──┐
              ├──▶  engine.handle_message()  ──▶  Postgres
Web UI      ──┘         (Django)
```

## Project Structure (planned)

```
spanishtutor/          # Django project root
  settings.py
  urls.py
bot/                   # Discord bot (Cogs)
  management/
    commands/
      run_bot.py       # Entry point: manage.py run_bot
  cogs/
    lessons.py
    progress.py
    reminders.py
engine/                # Interface-agnostic conversation engine
  core.py              # handle_message()
  evaluation.py        # Adaptive quiz, phase management
  srs.py               # Spaced repetition engine
  content.py           # LLM content generation
  persona.py           # Luz Angela system prompt
curriculum/
  skills.yaml          # Full A1→C2 skill taxonomy
  config.yaml          # SRS weights, timeouts, defaults
```

## Key Design Constraints

- **Interface-agnostic engine**: never put conversation logic in the Discord bot layer. The bot only handles Discord I/O.
- **Stateless per request**: no in-memory user state. Everything read from DB on each message.
- **Multi-tenant from day one**: every DB query scoped to `user_id`.
- **Config over code**: skill taxonomy, SRS weights, Luz Angela's system prompt — all in config files. Changes to these don't require a redeploy, just a restart.

## Common Commands

```bash
python manage.py migrate          # Run migrations
python manage.py run_bot          # Start Discord bot
python manage.py createsuperuser  # Create Django admin user
python manage.py shell            # Django shell
```

## Luz Angela

The tutor persona. Mid-30s, from Laureles, Medellín. Warm, playful, direct. Speaks Colombian Spanish (Medellín dialect). Not sycophantic. Honest about being a bot if asked. Spanish-first, calibrated to user's CEFR level. English escape hatch: user says "English please."

Her persona is defined in `engine/persona.py` as a system prompt passed to every Anthropic API call. It must never be hardcoded in bot handlers.

## Skill Grid

- **Vertical**: A1→C2 skills defined in `curriculum/skills.yaml`
- **Horizontal**: 5 CEFR modes — listening, reading, spoken_interaction, spoken_production, writing
- **Scores**: 0 (⬜ untested) → 1 (🟥) → 2 (🟨) → 3 (🟦) → 4 (🟩 mastered)
- **Atomic unit**: `skill × mode` — each cell has a score + last_tested_at + next_review_at
