# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Status

This repository is in the **planning/pre-implementation phase**. The two existing files (`claude_convo.md`, `foo.md`) contain architectural design discussions. No source code exists yet.

## What This Project Is

A personalized Spanish language learning system with two distinct layers:

1. **Learning Bot** — Discord-based tutor (quizzes, corrections, spaced repetition, progress tracking, vocab ingestion)
2. **Dev/Admin Agent** — Chat-controlled operator that can modify codebase, update configs, and redeploy without the owner touching a terminal

The core requirement is not just a Spanish app — it's a **chat-controlled development and deployment workflow** that allows continuous iteration from Discord/chat.

## Planned Architecture

### Layer 1: Learning Runtime

- **Interface**: Discord bot (primary)
- **Database**: SQLite or Postgres (learner memory, progress, mistakes)
- **LLM**: Claude/Anthropic APIs
- **Curriculum**: YAML/Markdown files loaded dynamically at runtime
- **Learner state**: JSON profiles

Capabilities: quizzes, corrections, spaced repetition, weak-area tracking, adaptive difficulty, content ingestion, review scheduling.

### Layer 2: Dev/Admin Agent (OpenClaw or equivalent)

Handles code edits, prompt modifications, new drill types, test runs, deployments, and log inspection — all triggered from Discord without terminal access.

## Critical Design Principle

**Most learning adaptation must be data-driven, not code-driven.**

- Vocab changes, lesson weighting, weak-area emphasis, drill frequency, curriculum tweaks → editable config files, no redeploy
- Only true structural/behavioral changes → code edit + redeploy

Example files to load dynamically at runtime:
```
curriculum.yaml       # lesson rules and sequencing
skills.yaml           # skill definitions and weights
mistake_patterns.json # tracked error patterns
learner_profile.json  # user state and weak areas
quiz_templates/       # quiz generation templates
```

The bot reloads these at runtime so curriculum evolution never requires a redeploy.

## What Requires a Redeploy vs. What Doesn't

| Change | Approach |
|---|---|
| Vocab list update | Edit data file, no redeploy |
| Adjust drill frequency | Edit learner profile/config |
| Add weak-area emphasis | Edit learner profile/config |
| New drill type (code logic) | Code edit → redeploy |
| Bug fix | Code edit → redeploy |
| New Discord command | Code edit → redeploy |

## Key Interaction Channels (Planned)

- Learning channel — quizzes, explanations, corrections
- Feedback/admin channel — curriculum changes, system updates
- Progress channel — review results, weak areas
