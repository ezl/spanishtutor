# Translate Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `!translate` persistent mode to the Discord bot — users type `!translate` to enter a mode where every subsequent message is translated bidirectionally (EN→ES or ES→EN detected automatically), with up to 3 labeled variants when meaningful; mode terminates after 10 minutes of inactivity, at which point the next message returns a termination notice before resuming normal bot behavior.

**Architecture:** A nullable `translate_mode_entered_at` DateTimeField on `User` tracks mode state (DB-backed, stateless per request). `engine/translate.py` contains the handler and mode-check helper. `engine/core.py` routes to translate before onboarding/session. `bot/client.py` handles the `!translate` command trigger, clearing any active session and setting the field.

**Tech Stack:** Django ORM, Anthropic API (`call_llm` with `system_override`), discord.py, pytest-django.

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `learner/models.py` | Modify | Add `translate_mode_entered_at` field to `User` |
| `learner/migrations/0008_user_translate_mode_entered_at.py` | Create | Migration for new field |
| `engine/translate.py` | Create | `_in_translate_mode()`, `handle_translate()`, translation prompt |
| `engine/core.py` | Modify | Route to `handle_translate` when in translate mode |
| `bot/client.py` | Modify | Handle `!translate` command — close session, set field, send confirmation |
| `engine/tests/test_translate.py` | Create | Unit tests for translate handler and mode detection |

---

## Task 1: Add `translate_mode_entered_at` to User model

**Files:**
- Modify: `learner/models.py`
- Create: `learner/migrations/0008_user_translate_mode_entered_at.py`

- [ ] **Step 1: Add field to User model**

In `learner/models.py`, add after the `instruction_language` field block (around line 20):

```python
    translate_mode_entered_at = models.DateTimeField(null=True, blank=True)
```

The full User model field list should now include this after `instruction_language`:

```python
class User(models.Model):
    discord_id = models.CharField(max_length=64, unique=True, null=True, blank=True)
    messenger_psid = models.CharField(max_length=64, unique=True, null=True, blank=True)
    display_name = models.CharField(max_length=128, blank=True)
    native_language = models.CharField(max_length=64, blank=True)
    interests = models.TextField(blank=True)
    why_learning = models.TextField(blank=True)
    target_use = models.TextField(blank=True)
    estimated_cefr_level = models.CharField(max_length=4, blank=True)
    reminder_enabled = models.BooleanField(default=True)
    reminder_schedule = models.JSONField(default=dict)
    onboarding_complete = models.BooleanField(default=False)
    instruction_language = models.CharField(
        max_length=16,
        choices=[('default', 'Default'), ('english', 'English'), ('spanish', 'Spanish')],
        default='default',
    )
    translate_mode_entered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

- [ ] **Step 2: Generate migration**

```bash
cd /home/claude/spanishtutor
python manage.py makemigrations learner --name user_translate_mode_entered_at
```

Expected: creates `learner/migrations/0008_user_translate_mode_entered_at.py`

- [ ] **Step 3: Run migration**

```bash
python manage.py migrate
```

Expected: `Applying learner.0008_user_translate_mode_entered_at... OK`

- [ ] **Step 4: Commit**

```bash
git add learner/models.py learner/migrations/0008_user_translate_mode_entered_at.py
git commit -m "feat: add translate_mode_entered_at field to User"
```

---

## Task 2: Create `engine/translate.py` with mode detection and handler

**Files:**
- Create: `engine/translate.py`
- Create: `engine/tests/test_translate.py`

- [ ] **Step 1: Write failing tests**

Create `engine/tests/test_translate.py`:

```python
"""
Tests for engine/translate.py

Covers: _in_translate_mode (expiry logic), handle_translate routing.
"""
import pytest
from datetime import timedelta
from unittest.mock import patch, AsyncMock
from django.utils import timezone


# ── _in_translate_mode ────────────────────────────────────────────────────────

class TestInTranslateMode:
    def _make_user(self, entered_at):
        """Return a minimal duck-typed user object."""
        class FakeUser:
            pass
        u = FakeUser()
        u.translate_mode_entered_at = entered_at
        return u

    def test_none_returns_false(self):
        from engine.translate import _in_translate_mode
        user = self._make_user(None)
        assert _in_translate_mode(user) is False

    def test_recent_timestamp_returns_true(self):
        from engine.translate import _in_translate_mode
        user = self._make_user(timezone.now() - timedelta(minutes=5))
        assert _in_translate_mode(user) is True

    def test_expired_timestamp_returns_false(self):
        from engine.translate import _in_translate_mode
        user = self._make_user(timezone.now() - timedelta(minutes=11))
        assert _in_translate_mode(user) is False

    def test_exactly_ten_minutes_returns_false(self):
        """Boundary: exactly 10 minutes is expired."""
        from engine.translate import _in_translate_mode
        user = self._make_user(timezone.now() - timedelta(minutes=10, seconds=1))
        assert _in_translate_mode(user) is False

    def test_just_under_ten_minutes_returns_true(self):
        from engine.translate import _in_translate_mode
        user = self._make_user(timezone.now() - timedelta(minutes=9, seconds=59))
        assert _in_translate_mode(user) is True


# ── handle_translate ──────────────────────────────────────────────────────────

@pytest.mark.django_db(transaction=True)
async def test_handle_translate_refreshes_timestamp(make_user):
    """handle_translate updates translate_mode_entered_at to now (sliding window)."""
    from engine.translate import handle_translate
    from learner.models import User
    from asgiref.sync import sync_to_async

    user = await sync_to_async(make_user)(discord_id='u_tr1')
    old_ts = timezone.now() - timedelta(minutes=5)
    await sync_to_async(User.objects.filter(pk=user.pk).update)(
        translate_mode_entered_at=old_ts
    )
    user.translate_mode_entered_at = old_ts

    with patch('engine.translate.call_llm', new=AsyncMock(return_value='el perro')):
        await handle_translate(user, 'the dog')

    refreshed = await sync_to_async(User.objects.get)(pk=user.pk)
    assert refreshed.translate_mode_entered_at > old_ts


@pytest.mark.django_db(transaction=True)
async def test_handle_translate_returns_llm_text(make_user):
    """handle_translate returns the LLM response as 'text'."""
    from engine.translate import handle_translate
    from asgiref.sync import sync_to_async

    user = await sync_to_async(make_user)(discord_id='u_tr2')

    with patch('engine.translate.call_llm', new=AsyncMock(return_value='la casa')):
        result = await handle_translate(user, 'the house')

    assert result['text'] == 'la casa'
    assert result['session_ended'] is False
    assert result['audio_url'] is None


@pytest.mark.django_db(transaction=True)
async def test_handle_translate_uses_system_override(make_user):
    """handle_translate calls call_llm with system_override (not Luz Angela persona)."""
    from engine.translate import handle_translate
    from asgiref.sync import sync_to_async

    user = await sync_to_async(make_user)(discord_id='u_tr3')

    captured = {}

    async def mock_llm(messages, system_override=None, **kwargs):
        captured['system_override'] = system_override
        return 'quiero ir a la tienda'

    with patch('engine.translate.call_llm', new=mock_llm):
        await handle_translate(user, 'I want to go to the store')

    assert captured.get('system_override') is not None
    assert 'Luz Angela' not in captured['system_override']
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/claude/spanishtutor
python -m pytest engine/tests/test_translate.py -v 2>&1 | head -30
```

Expected: `ModuleNotFoundError` or `ImportError` — `engine.translate` does not exist yet.

- [ ] **Step 3: Create `engine/translate.py`**

```python
from asgiref.sync import sync_to_async
from django.utils import timezone
from .core import call_llm

TRANSLATE_TIMEOUT_MINUTES = 10

TRANSLATE_SYSTEM_PROMPT = """You are a translation assistant between English and Colombian Spanish — specifically the casual register spoken by young professionals in Medellín going out, hanging out, making plans, and talking about their lives. Think: rooftop bar, weekend plans, lunch with coworkers, WhatsApp voice notes. Not textbook Spanish. Not formal. Paisa.

Detect the language of the user's input and translate to the other language (English→Spanish or Spanish→English).

Rules:
- Default to casual Medellín register. Use vos or tú as appropriate for this setting (vos is common in Medellín). Use real expressions: parce, bacano, chimba, ¿qué más?, qué pena, listo, dale, etc. where they fit naturally.
- If the input is clearly something to translate: provide translations. Give exactly 1 translation if unambiguous. Give 2-3 if there are meaningful variants (register differences, regional options, or genuinely different meanings). When giving multiple, briefly label each on the same line in parentheses, e.g. "1. rumba (going out, partying)  2. fiesta (more generic)".
- Format: single translation on one line with no preamble. Multiple translations as a numbered list.
- If the input reads like context or clarification rather than something to translate: ask one short clarifying question.
- If ambiguous: make your best inference and translate. Only ask when you genuinely cannot determine intent.
- No preamble. No "Here is the translation:". No praise. Just the translation."""


def _in_translate_mode(user) -> bool:
    """Return True if the user is in an active (non-expired) translate session."""
    if not user.translate_mode_entered_at:
        return False
    elapsed = (timezone.now() - user.translate_mode_entered_at).total_seconds()
    return elapsed < TRANSLATE_TIMEOUT_MINUTES * 60


async def handle_translate(user, text: str) -> dict:
    """Translate user's English input to Spanish and refresh the sliding timeout."""
    from learner.models import User

    now = timezone.now()
    await sync_to_async(
        User.objects.filter(pk=user.pk).update
    )(translate_mode_entered_at=now)
    user.translate_mode_entered_at = now

    messages = [{"role": "user", "content": text}]
    response_text = await call_llm(messages, system_override=TRANSLATE_SYSTEM_PROMPT)

    return {"text": response_text, "audio_url": None, "session_ended": False}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/claude/spanishtutor
python -m pytest engine/tests/test_translate.py -v
```

Expected: all 7 tests pass.

- [ ] **Step 5: Commit**

```bash
git add engine/translate.py engine/tests/test_translate.py
git commit -m "feat: add translate mode handler and mode detection"
```

---

## Task 3: Route translate mode in `engine/core.py`

**Files:**
- Modify: `engine/core.py`
- Modify: `engine/tests/test_core.py`

- [ ] **Step 1: Write failing test**

Append to `engine/tests/test_core.py`:

```python
@pytest.mark.django_db(transaction=True)
async def test_handle_message_routes_to_translate_when_in_mode(make_user):
    """When user is in translate mode, handle_translate is called instead of handle_session."""
    from engine.core import handle_message
    from learner.models import User
    from asgiref.sync import sync_to_async
    from datetime import timedelta
    from django.utils import timezone

    user = await sync_to_async(make_user)(discord_id='u_tr_route', onboarding_complete=True)
    await sync_to_async(User.objects.filter(pk=user.pk).update)(
        translate_mode_entered_at=timezone.now() - timedelta(minutes=1)
    )
    user.translate_mode_entered_at = timezone.now() - timedelta(minutes=1)

    translate_called = []

    async def mock_translate(u, text):
        translate_called.append(True)
        return {'text': 'el perro', 'audio_url': None, 'session_ended': False}

    with patch('engine.translate.handle_translate', mock_translate):
        result = await handle_message(user, 'the dog')

    assert translate_called, "handle_translate should be called when in translate mode"
    assert result['text'] == 'el perro'


@pytest.mark.django_db(transaction=True)
async def test_handle_message_returns_termination_on_expiry(make_user):
    """Expired translate_mode_entered_at returns termination message and clears the field.
    handle_session is NOT called — the next message resumes normal routing."""
    from engine.core import handle_message
    from learner.models import User
    from asgiref.sync import sync_to_async
    from datetime import timedelta
    from django.utils import timezone

    user = await sync_to_async(make_user)(discord_id='u_tr_exp', onboarding_complete=True)
    old_ts = timezone.now() - timedelta(minutes=15)
    await sync_to_async(User.objects.filter(pk=user.pk).update)(
        translate_mode_entered_at=old_ts
    )
    user.translate_mode_entered_at = old_ts

    session_called = []

    async def mock_session(u, text, attachments):
        session_called.append(True)
        return {'text': 'session', 'audio_url': None, 'session_ended': False}

    with patch('engine.session.handle_session', mock_session):
        result = await handle_message(user, 'hola')

    assert not session_called, "handle_session should NOT be called on the expiry message"
    assert 'Translation session ended' in result['text']
    assert '!translate' in result['text']
    refreshed = await sync_to_async(User.objects.get)(pk=user.pk)
    assert refreshed.translate_mode_entered_at is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/claude/spanishtutor
python -m pytest engine/tests/test_core.py::test_handle_message_routes_to_translate_when_in_mode engine/tests/test_core.py::test_handle_message_clears_expired_translate_mode -v
```

Expected: FAIL — `handle_message` does not yet check translate mode.

- [ ] **Step 3: Update `engine/core.py`**

Replace the `handle_message` function body:

```python
async def handle_message(user, text: str, attachments: list = None) -> dict:
    """
    Interface-agnostic message handler. Called by Discord bot and future web API.

    Returns: {
        "text": str,           # Luz Angela's response text
        "audio_url": str|None, # TTS audio URL if applicable
        "session_ended": bool,
    }
    """
    from learner.models import Session
    from .onboarding import handle_onboarding
    from .session import handle_session
    from .translate import handle_translate, _in_translate_mode

    if _in_translate_mode(user):
        return await handle_translate(user, text)

    if user.translate_mode_entered_at is not None:
        # Mode expired — clear field and return termination notice.
        # The user's next message will resume normal session routing.
        from asgiref.sync import sync_to_async
        from learner.models import User as _User
        await sync_to_async(_User.objects.filter(pk=user.pk).update)(translate_mode_entered_at=None)
        user.translate_mode_entered_at = None
        return {
            "text": (
                "Translation session ended.\n"
                "To start another, use `!translate`.\n"
                "To start a lesson, just send me a message!"
            ),
            "audio_url": None,
            "session_ended": False,
        }

    if not user.onboarding_complete:
        return await handle_onboarding(user, text, attachments)

    return await handle_session(user, text, attachments)
```

The import at the top of the file is unchanged. The only change is adding the translate routing block before the onboarding check.

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /home/claude/spanishtutor
python -m pytest engine/tests/test_core.py -v
```

Expected: all tests pass (including the 2 existing routing tests).

- [ ] **Step 5: Commit**

```bash
git add engine/core.py engine/tests/test_core.py
git commit -m "feat: route translate mode in handle_message"
```

---

## Task 4: Handle `!translate` command in `bot/client.py`

**Files:**
- Modify: `bot/client.py`

Note: `bot/client.py` is Discord-facing glue code. The logic it calls is already tested via `engine/tests/`. No separate unit tests for this task — it's thin routing code.

- [ ] **Step 1: Add `!translate` command handler in `on_message`**

In `bot/client.py`, add the `!translate` block after the `!menu` handler and before the `uid = str(message.author.id)` line (around line 157). The new block:

```python
    if text.lower() == '!translate':
        uid = str(message.author.id)
        if uid not in _user_locks:
            _user_locks[uid] = asyncio.Lock()
        async with _user_locks[uid]:
            from learner.models import User
            from django.utils import timezone
            user_obj = await sync_to_async(User.objects.filter(discord_id=uid).first)()
            if not user_obj:
                await message.channel.send("Start a session first before using translate mode!")
                return
            # Close any active session silently
            from learner.models import Session
            active_session = await sync_to_async(
                lambda: Session.objects.filter(user=user_obj, ended_at__isnull=True)
                                       .exclude(session_type='onboarding')
                                       .first()
            )()
            if active_session:
                from engine.session import _close_session_record
                await _close_session_record(active_session, user_obj)
            # Enter translate mode
            now = timezone.now()
            await sync_to_async(
                User.objects.filter(pk=user_obj.pk).update
            )(translate_mode_entered_at=now)
        await message.channel.send(
            "Translation mode on. Send me anything in English and I'll give you the Spanish, "
            "or Spanish and I'll give you the English. Times out after 10 minutes of inactivity."
        )
        return
```

- [ ] **Step 2: Verify the existing `!menu` command lists `!translate`**

Update the menu string in the `!menu` handler to include the new command. Find the `menu = (` block and add a line:

```python
        menu = (
            f"**Current level:** {level}\n"
            f"{grid_line}\n"
            f"**Commands:**\n"
            f"`!translate` - translate between English and Spanish (times out after 10 min)\n"
            f"`!retest` - retake the placement quiz\n"
            f"`!english` - force English instructions\n"
            f"`!spanish` - force Spanish instructions\n"
            f"`!reset` - wipe everything and start over\n"
        )
```

- [ ] **Step 3: Smoke-test manually**

Start the bot locally:
```bash
python manage.py run_bot
```

In Discord DM:
1. Send `!translate` → expect: "Translation mode on. Send me anything in English and I'll give you the Spanish, or Spanish and I'll give you the English..."
2. Send `let's go out tonight` → expect casual Medellín Spanish, e.g. `¿Salimos esta noche?` or `Dale, vamos esta noche` — not a stiff textbook translation
3. Send `¿qué más, parce?` → expect English, e.g. `What's up, man?` / `How's it going, buddy?`
4. Send `bank` → expect 2 translations with labels: `banco` (financial) + `orilla` or similar for riverbank
5. Send `I'm so hungover` → expect casual slang translation, not formal
6. Wait 10 minutes idle → send any message → expect termination notice: "Translation session ended. To start another, use `!translate`. To start a lesson, just send me a message!"
7. Send another message → expect normal session (not translate mode)

- [ ] **Step 4: Commit**

```bash
git add bot/client.py
git commit -m "feat: add !translate command to Discord bot"
```

---

## Self-Review

**Spec coverage check:**
- ✅ Persistent mode triggered by `!translate` — Task 4
- ✅ Bidirectional EN↔ES translation (auto-detect) — Task 2 (TRANSLATE_SYSTEM_PROMPT)
- ✅ Medellín casual register, young professional going-out voice — Task 2 (TRANSLATE_SYSTEM_PROMPT)
- ✅ Infer translation intent vs. context — Task 2 (prompt instructs this)
- ✅ Minimal clarifying questions — Task 2 (prompt: "only ask when you genuinely cannot determine intent")
- ✅ Up to 3 translations for nuance/variants with labels — Task 2 (prompt rule + numbered list format)
- ✅ 10-min inactivity timeout — Task 2 (`_in_translate_mode`), Task 3 (expiry detection)
- ✅ Termination message on expiry — Task 3 (`handle_message` returns message, not session)
- ✅ Sliding window (refreshed on each message) — Task 2 (`handle_translate` updates timestamp)
- ✅ Interrupts and terminates active session — Task 4 (`_close_session_record`)
- ✅ Confirmation message mentions both directions — Task 4
- ✅ `!menu` updated — Task 4, Step 2

**Placeholder scan:** No TBDs, no "implement later", all code is complete.

**Type consistency:**
- `_in_translate_mode(user)` defined in Task 2, imported in Task 3 — matches.
- `handle_translate(user, text)` defined in Task 2, routed in Task 3 — matches.
- `translate_mode_entered_at` field added in Task 1, used in Tasks 2, 3, 4 — consistent.
- `_close_session_record(session, user)` is existing function in `engine/session.py` — signature confirmed from reading the file.
