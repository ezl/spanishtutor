# Interest Graph — Build Plan

## What we're building

Luz builds a rich picture of every user through natural conversation — not intake forms. The graph grows session by session. Lesson content is progressively personalized as it deepens.

## Core principles

- No intake form. No opt-in prompt. Luz just learns, like a friend does.
- Teaching is the primary job. Interest extraction is a quiet background job after each session.
- Never surface the graph. Just use what you know as if you've always known it.
- Dynamic context ("how was your weekend") feeds the same graph as static interests. Recurring things accumulate and become permanent.

---

## Decisions

| Decision | Choice | Reason |
|---|---|---|
| Extraction timing | Synchronous at session close | Simple, reliable, no race conditions. ~2s added to goodbye. Acceptable. |
| Cold start | Session 1 is generic, no special handling | One unPersonalized lesson is fine. Graph builds from session 2 onward. |
| Inference rules | Dropped — LLM handles in extraction prompt | YAML config adds complexity with marginal value. LLM already knows that DBZ → shonen anime. |
| Category taxonomy | Free-form, LLM assigns | No predefined list to maintain. Not queried programmatically. |
| Dynamic vs static context | One model, mention_count promotes recurring themes | No separate ephemeral layer. "Went to a Cubs game" → Cubs interest reinforced. |
| Session opener | Teaching-first. Check-in is natural Luz behavior, not scripted. | Forced check-in feels clinical. |
| Extraction mechanism | Structured LLM prompt, always runs, outputs TOPIC\|CATEGORY\|CONFIDENCE or NOTHING | Simple, cheap (~$0.005/session), LLM judges relevance. |
| Dev mode | ENV var DEV_MODE. Extra Discord message after close listing extracted interests. | Debuggable without logs. |
| System prompt | Option B: inject interests + one instruction line to use them in examples. | One sentence costs nothing and meaningfully increases consistency. |
| Translation | Backlog — user can ask Luz to translate something at any time. | Common learner behavior. Not building now. |

---

## Data model

### `UserInterest`

| Field | Type | Notes |
|---|---|---|
| `user` | FK → User | |
| `topic` | str(128) | e.g. "Chicago Cubs", "rock climbing", "has a dog" |
| `category` | str(64) | free-form, LLM-assigned e.g. "sports", "pets", "career" |
| `confidence` | float 0.0–1.0 | explicit mention ≈ 0.9, inference ≈ 0.3–0.5 |
| `mention_count` | int | incremented each session the topic appears |
| `first_seen_at` | datetime | auto |
| `last_reinforced_at` | datetime | auto-updated on upsert |

Unique together: `(user, topic)`.

### `user.interests` (existing TextField)

Auto-regenerated from `UserInterest` after each extraction pass. Ordered by `mention_count DESC, confidence DESC`. High-confidence facts first, low-confidence possible interests at the end. This prose is injected into the system prompt.

---

## Extraction prompt

```
Here is a conversation between a Spanish tutor and a student.

<conversation>
{transcript}
</conversation>

Did this conversation reveal anything about the student's life, interests,
hobbies, career, relationships, or personal context?

If yes, output one line per fact:
TOPIC | CATEGORY | CONFIDENCE

Only include things the student actually revealed or that are obvious
inferences. If nothing personal came up, output exactly: NOTHING
```

---

## System prompt addition (persona.py)

One line added to the Teaching approach section:

```
- When generating examples, use the student's interests as the context wherever natural.
```

---

## Build order

1. `UserInterest` model + migration
2. `engine/interests.py` — extraction, upsert, prose regeneration
3. Hook into `_close_session()` in `engine/session.py`
4. `DEV_MODE` env var in `settings.py` + dev log in result dict
5. `bot/client.py` — handle `dev_log` key
6. System prompt update in `engine/persona.py`
