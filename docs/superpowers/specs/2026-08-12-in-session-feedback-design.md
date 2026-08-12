# In-Session Feedback Capture — Design

**Status:** Approved (2026-08-12)
**Author:** Eric Liu + Claude
**Scope:** Enable users to give natural-language feedback about the tutor's behavior mid-lesson without special commands. Feedback is classified by the LLM, logged to a dedicated model, and reviewed ad-hoc via Claude Code queries.

---

## Motivation

Today, giving feedback about the tutor requires the user to leave Discord, screenshot or paste the transcript into a Claude Code session, and describe what was wrong. This is high-friction — feedback gets deferred and often lost. And it won't scale beyond the developer once the app has other users.

The user should be able to just say "that cue was ambiguous" or "you're going too fast" naturally in the middle of a lesson. The LLM already reads every message; it can classify meta-feedback as such, log it, and keep the lesson moving. Feedback accumulates in the DB. Later, the developer reviews it (via Claude Code queries) to decide what deserves prompt or code changes.

---

## Goals

1. Zero user knowledge required to give feedback — no commands, no special syntax.
2. Feedback is anchored to the specific turn the user was reacting to.
3. Lesson flow is not interrupted — brief acknowledgment, then continue.
4. Feedback is queryable across sessions and (eventually) across users.
5. Developer (Eric) reviews feedback ad-hoc by asking Claude Code — no dashboard to build.

## Non-goals (V1)

- Auto-adaptation. The bot does not change its own behavior in response to feedback in real time. All prompt/code changes are developer-reviewed and shipped through normal git flow.
- User-facing surfaces beyond acknowledgment. No "your feedback list" command for the user themselves.
- Extending capture to non-teach_drill phases (guided_practice, free_production, srs_review, conversation, reading, writing). MVP is teach_drill only.
- Sentiment scoring, categorization taxonomies, or NLP beyond the LLM's per-message paraphrase.

---

## Mechanism

Every user message runs through a classification decision inside the existing LLM per-turn call for teach_drill. Three outcomes:

| Classification | Bot behavior | Storage |
|---|---|---|
| **Lesson answer** | Normal evaluate + next question | None (existing flow) |
| **Content question** ("wait, is estar always for locations?") | Answer inline, resume lesson naturally | None (existing flow) |
| **Meta-feedback** ("that cue was ambiguous", "you keep asking me the same person") | Emit `<<FEEDBACK>>[one-sentence paraphrase]<<END_FEEDBACK>>` marker + brief acknowledgment; code strips marker and stores a `SessionFeedback` row | New model |

**Marker pattern mirrors existing markers** (`<<LESSON_COMPLETE>>`, `<<REDO_PENDING>>`), which are proven to work reliably.

**Mixed messages** (both an answer AND feedback in the same message): the LLM extracts both — evaluates the answer per the REDO check AND emits the feedback marker. Both handled in the same turn.

**Ambiguity resolution:** when the LLM is uncertain whether a message is a content question or meta-feedback, it defaults to content question (answer inline; no marker). This under-captures — some real feedback slips through as inline content answers — but avoids false positives (a lesson answer accidentally getting logged as feedback, which would pollute the review log).

---

## Data model

New Django model in `learner/models.py`:

```python
class SessionFeedback(models.Model):
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name='feedback')
    anchor_event = models.ForeignKey(
        SessionEvent, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='feedback',
        help_text="The Luz turn the user was reacting to (usually the most recent assistant message)."
    )
    user_message = models.TextField(
        help_text="Raw user message that contained the feedback."
    )
    interpretation = models.TextField(
        help_text="LLM's one-sentence paraphrase of what the user is flagging."
    )
    resolved = models.BooleanField(default=False)
    resolution_note = models.TextField(blank=True, help_text="Optional note when you review/close this.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = 'learner'
        ordering = ['-created_at']
```

**Design choices:**
- `anchor_event` nullable with `SET_NULL` — feedback survives even if the anchoring event gets deleted somehow.
- `interpretation` is written by the LLM in the same call — nearly free compared to the classification decision itself, and gives the developer a scannable summary during review.
- `resolved` + `resolution_note` support the review workflow — developer marks items done as they're addressed, so future queries surface only open items.
- No FK to `User` — reachable via `session.user`.

Migration is standard `python manage.py makemigrations learner`.

Register in `learner/admin.py` for out-of-the-box browsability, though the primary review path is via Claude Code queries.

---

## Prompt changes

Modify `TEACH_DRILL_CONTINUATION_SUFFIX` in `engine/teach_drill.py` to add a SECOND decision after the existing REDO_FIRST_CHECK:

```
SECOND DECISION (after the redo check passes): what IS the student's message?

Classify their message into ONE of:
  (a) Lesson answer — an attempt to answer the question you just asked. Handled by REDO check above.
  (b) Content question — asking about Spanish itself. Examples: "wait, is estar always for locations?", "why is it hizo not hico?", "does poder always mean 'managed to' in preterite?"
      → Answer in 2-3 sentences, then continue with the normal per-turn instruction (teach/retrieval).
  (c) Meta-feedback about the app or lesson — comments about YOU, the pedagogy, the pacing, the cues, the style. Examples: "that cue was ambiguous", "you keep asking me the same person", "this is going too fast", "shouldn't 'I was at the wedding' be estar?" (meta because the student is questioning YOUR choice).
      → Emit the feedback marker on its own line: <<FEEDBACK>>[one-sentence paraphrase of what they're flagging]<<END_FEEDBACK>>
      → Then write one short line acknowledging: "Got it, logged that. Sigamos." or similar.
      → Then continue with the normal per-turn instruction (teach/retrieval) — do NOT skip the lesson step.
  (d) Both answer + feedback in one message — extract both. Evaluate the answer per the REDO check, AND emit the feedback marker.

The classification bar: if you're uncertain whether something is a content question or meta-feedback, prefer content question (answer it inline; no marker). Only emit the feedback marker when the user is clearly commenting on YOUR behavior or the app.
```

No changes needed to `build_teach_instruction` or `build_retrieval_only_instruction` — the suffix handles all three classifications generically. The teach/retrieval steps at the end of each instruction are the "continue with normal flow" that the classification paths point to.

---

## Code changes

**`engine/teach_drill.py`:**

1. Add module-level constants:
   ```python
   FEEDBACK_MARKER_OPEN = '<<FEEDBACK>>'
   FEEDBACK_MARKER_CLOSE = '<<END_FEEDBACK>>'
   ```

2. Add helper:
   ```python
   def _strip_feedback_marker(text: str) -> tuple:
       """Return (cleaned_text, feedback_interpretation | None).
       Extracts the LLM's paraphrase from between the markers and removes the
       marker block from the visible text."""
       # regex match FEEDBACK_MARKER_OPEN(.*?)FEEDBACK_MARKER_CLOSE, strip it,
       # return the captured group as interpretation or None if no match.
   ```

3. Update `TEACH_DRILL_CONTINUATION_SUFFIX` per the prompt changes section above.

4. Update `handle_teach_drill_turn`:
   - After `_strip_redo_pending_marker` call, add `_strip_feedback_marker` call.
   - If feedback was captured: write a `SessionFeedback` row with `session=session`, `anchor_event=<most recent existing SessionEvent for this session>`, `user_message=text` (the raw user message that triggered the LLM call), `interpretation=<captured group>`.
   - State updates for teach/retrieval proceed normally — feedback doesn't interfere with the lesson flow.

**`learner/models.py`:** add `SessionFeedback` per data model section.

**`learner/admin.py`:** register `SessionFeedback` with the admin site.

**Migration:** `learner/migrations/0010_sessionfeedback.py` generated by `makemigrations`.

---

## Testing

Unit tests in `engine/tests/test_teach_drill.py`:

1. `_strip_feedback_marker` extracts interpretation from a response with `<<FEEDBACK>>...<<END_FEEDBACK>>` markers.
2. `_strip_feedback_marker` returns None when no marker is present, text unchanged.
3. `handle_teach_drill_turn` creates a `SessionFeedback` row when the LLM response contains the feedback marker.
4. `handle_teach_drill_turn` does NOT create a `SessionFeedback` row when the LLM response has no marker.
5. Feedback + REDO_PENDING in the same response: both should be honored (feedback logged; redo state applies).
6. Suffix content check: `TEACH_DRILL_CONTINUATION_SUFFIX` contains "meta-feedback" and the marker names.

Integration test verifying the `anchor_event` FK points to the most recent SessionEvent when feedback is captured.

---

## Review workflow (for the developer)

The workflow is: developer opens Claude Code, says something like "show me the recent feedback across all sessions" or "what feedback did I give in my last session on preterite irregulars?" Claude Code queries the DB directly (via `manage.py shell` or a small management command) and summarizes.

**Optional convenience:** a management command `python manage.py feedback --unresolved --since '2026-08-01'` that dumps feedback as a summary. Nice-to-have but not required for MVP — Claude Code can query directly.

**Marking resolved:** developer can flip `resolved=True` via Django admin, or via Claude Code (`SessionFeedback.objects.filter(id__in=[...]).update(resolved=True)`), or via the optional management command.

---

## Risks and trade-offs

**Classification errors** — the LLM might misclassify a lesson answer as feedback ("estuve en la boda" flagged as complaint about a cue) or vice versa (a real complaint interpreted as a language question). Mitigations:
- Bias toward "content question" on ambiguous cases (already in the prompt design).
- Clear examples in the classification prompt.
- Post-launch: review the actual `SessionFeedback` rows to see how the classifier is behaving in practice. If it's noisy, tighten the prompt.

**Prompt bloat** — adding classification to `TEACH_DRILL_CONTINUATION_SUFFIX` makes it longer, which increases per-turn token cost slightly and may reduce the LLM's attention to earlier rules. Mitigation: keep the classification block concise and integrated with the existing REDO_FIRST_CHECK structure.

**Under-capture** — biasing toward "content question" means some real feedback won't be logged (the LLM will answer it inline as if it were a Spanish grammar question). Acceptable trade-off — better to lose some feedback than pollute the log with false positives.

**Not extending to other phases yet** — feedback given in guided_practice, free_production, or SRS review won't be captured in V1. If those turn out to be the main sources of user complaints (unlikely — teach_drill is where users spend most of their time and where UX friction lives), extend later.

**Adding a new marker to an already-marker-heavy pattern** — we now have `<<LESSON_COMPLETE>>`, `<<REDO_PENDING>>`, and `<<FEEDBACK>>...<<END_FEEDBACK>>`. The LLM has to juggle three orthogonal signals per turn. Not a bug yet, but worth watching — if we add more, at some point structured JSON emission might be more reliable than free-form markers.

---

## Future extensions (out of scope)

- Extend feedback capture to `guided_practice`, `free_production`, `srs_review`, `conversation`, `reading`, `writing` phases.
- Auto-adaptation loop: apply low-risk feedback in real time (e.g., "slow down" adjusts pace immediately), with review-and-revert workflow.
- Multi-user aggregation: cluster similar feedback across users to surface common issues.
- Sentiment/priority scoring: LLM tags each feedback with severity (bug / suggestion / nice-to-have) at capture time.
- User-facing "your feedback" surface, once there are actual multiple users.
- Feedback resolution linking: FK from `SessionFeedback` to the git commit that addressed it.
