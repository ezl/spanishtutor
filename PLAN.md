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

## Future Language Expansion

The architecture was designed with expansion in mind. This section documents the recommended path and the decisions required at each stage.

---

### The Three Tiers

**Tier 1 — Regional dialects (same language, surface variation)**

Examples: Colombian vs. Mexican vs. Argentine vs. Spanish Spanish.

The grammar skill ladder is identical across dialects. What varies is surface form: vocabulary (carro/coche/auto), pronouns (tuteo/voseo/vosotros), register norms (Colombian usted-for-everyone vs. Mexican tú-for-friends), and idiomatic expressions.

Implementation:
- Add `dialect` field to the `User` model (e.g. `es-co`, `es-mx`, `es-ar`, `es-es`). Default: `es-co`.
- The `dialect` is passed to the question generation prompt and is included in `question_framework.yaml` as a header field.
- Vocabulary skills can be tagged with dialect-specific entries in `skills.yaml`. Grammar skills are shared.
- The tutor persona (Luz Angela) is dialect-specific. A Mexican Spanish variant would have a different persona name, origin, and expressions — but the same underlying engine.
- No changes to the quiz algorithm, DB schema (beyond the new field), or views.

Effort: ~2 weeks per dialect. Mostly curriculum work (vocabulary lists, sample questions, persona tuning). No architectural changes.

---

**Tier 2 — Other CEFR languages**

Examples: French, German, Italian, Portuguese, Dutch.

The CEFR framework (A1–C2, the same 5 modes) applies to all of these languages. The skill ladders differ — German has cases, French has gendered articles at different levels, etc. — but the structure (a vertical ordered taxonomy × 5 horizontal modes) is identical.

Implementation:
- Add `language` field to the `User` model (e.g. `es`, `fr`, `de`). Default: `es`.
- Namespace all curriculum files: `curriculum/es/skills.yaml`, `curriculum/fr/skills.yaml`, `curriculum/es/question_framework.yaml`, etc.
- The curriculum loader becomes `load_skills(language)` and `load_framework(language)`. All other code is already language-agnostic — the quiz algorithm, DB models, and session engine never reference Spanish specifically.
- Each language needs its own tutor persona (different name, background, speaking style). Persona files: `engine/personas/es_co.py`, `engine/personas/fr.py`, etc.
- The `CEFR_ORDER` list in `views.py` is already a parameterizable list — no change needed there.
- The `estimated_cefr_level` DB field is language-agnostic (A1–C2 applies to all CEFR languages).
- Question generation prompts already take dialect/language as a parameter (via `GENERATION_SYSTEM`). The language just needs to be threaded through.

What you cannot reuse: the skill taxonomy, the question bank, the persona. Everything else — the quiz algorithm, the session engine, the SRS logic, the DB schema, the bot, the web views — is fully reusable without modification.

Effort: ~4–6 weeks per language. Mostly curriculum authoring. One architectural pass to parameterize the curriculum loader (a few hours of code, then repeatable).

---

**Tier 3 — Non-CEFR languages**

Examples: Mandarin (HSK 1–6), Japanese (JLPT N5–N1), Arabic (ACTFL/ILR), Hindi, Swahili, languages with no formal proficiency standard.

These use different proficiency scales, but the underlying structure is universal: every language has a vocabulary ladder, a grammar ladder, discourse and pragmatic skills, and the same 5 modes of use (listening, reading, spoken interaction, spoken production, writing). The quiz algorithm operates on a skill index, not on CEFR labels — it is already fully agnostic.

Implementation:
- Replace the hardcoded `CEFR_ORDER = ['A1','A2','B1','B2','C1','C2']` in `views.py` with a `proficiency_levels` list defined in the language's curriculum config. Example: `["HSK1","HSK2","HSK3","HSK4","HSK5","HSK6"]` for Mandarin, `["N5","N4","N3","N2","N1"]` for Japanese.
- The `estimated_cefr_level` DB field becomes `estimated_proficiency_level` (rename + migration). The field is a free string — no constraint to CEFR.
- The quiz algorithm (`quiz_flow.py`) operates on integer skill indices and is completely agnostic. No changes needed.
- LLM evaluation of student responses requires the LLM to assess answers in the target language. The Anthropic API handles this natively — the evaluator prompt just needs to specify the language.
- Character-based languages (Mandarin, Japanese, Arabic) require Unicode handling throughout. All current string operations are Unicode-safe.

What you cannot reuse: the skill taxonomy, the question bank, the persona, the CEFR-level display labels. Everything else is reusable.

Effort: the first non-CEFR language is 6–8 weeks (architectural pass + curriculum authoring). Each subsequent one is 4–6 weeks.

---

### Recommended Implementation Sequence

1. **Now (V1):** Hardcode `es-co`. Add a dormant `language` field (`default="es-co"`) to the `User` model so the hook exists in the DB. No other changes.
2. **Regional variant:** Add a second dialect (e.g. `es-mx`) when there is demand. This is the lowest-effort expansion and validates the dialect-parameterization pattern.
3. **Second CEFR language:** Pick one with high demand (French or Portuguese). Implement the `load_skills(language)` parameterization. Build the curriculum. This validates the multi-language pattern.
4. **Non-CEFR language:** Only after the second CEFR language is stable. Rename `estimated_cefr_level` to `estimated_proficiency_level` (one migration). Parameterize `proficiency_levels` in the curriculum config. Build the curriculum.

---

### Skill Ladder Adjustments for Multi-Language

The current `skills.yaml` structure is:
```yaml
- skill_id: a1_ser_estar_basic
  name: Ser vs. Estar (basic)
  cefr_level: A1
  order: 1
  description: ...
```

For multi-language, the `cefr_level` field becomes `proficiency_level` (a string whose values depend on the language config). The `skill_id` should be namespaced by language to avoid collisions: `es_a1_ser_estar_basic` vs. `fr_a1_etre_avoir_basic`. The `order` field is already per-file, so no conflict.

The `SkillScore` model links `User → Skill → score`. Since skills are language-specific objects, the language is implicit in the skill. No changes needed to SkillScore.

The `QuizQuestion` model links to `Skill`. Questions are language-specific by inheritance. No changes needed.

---

### What Does NOT Change

Regardless of which expansion tier is implemented:

- The quiz bisection algorithm (`quiz_flow.py`) — fully agnostic, operates on indices
- The SRS decay logic — fully agnostic
- The session engine structure — fully agnostic
- The 5 language modes — universal to all human languages
- The 0–4 scoring scale — universal
- The Discord bot layer — fully agnostic
- The web views and progress grid — parameterized by `proficiency_levels`, otherwise unchanged
- The magic link auth system — fully agnostic
- The DB schema (beyond the `language` field and the `cefr_level` → `proficiency_level` rename)

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
