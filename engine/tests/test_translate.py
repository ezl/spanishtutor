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

    with patch('engine.core.call_llm', new=AsyncMock(return_value='el perro')):
        await handle_translate(user, 'the dog')

    refreshed = await sync_to_async(User.objects.get)(pk=user.pk)
    assert refreshed.translate_mode_entered_at > old_ts


@pytest.mark.django_db(transaction=True)
async def test_handle_translate_returns_llm_text(make_user):
    """handle_translate returns the LLM response as 'text'."""
    from engine.translate import handle_translate
    from asgiref.sync import sync_to_async

    user = await sync_to_async(make_user)(discord_id='u_tr2')

    with patch('engine.core.call_llm', new=AsyncMock(return_value='la casa')):
        result = await handle_translate(user, 'the house')

    assert result['text'] == 'la casa'
    assert result['session_ended'] is False
    assert result['audio_url'] is None


@pytest.mark.django_db(transaction=True)
async def test_handle_translate_uses_system_override(make_user):
    """handle_translate calls call_llm with system_override (not Luz Ángela persona)."""
    from engine.translate import handle_translate
    from asgiref.sync import sync_to_async

    user = await sync_to_async(make_user)(discord_id='u_tr3')

    captured = {}

    async def mock_llm(messages, system_override=None, **kwargs):
        captured['system_override'] = system_override
        return 'quiero ir a la tienda'

    with patch('engine.core.call_llm', new=mock_llm):
        await handle_translate(user, 'I want to go to the store')

    assert captured.get('system_override') is not None
    assert 'Luz Ángela' not in captured['system_override']
