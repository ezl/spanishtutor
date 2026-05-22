"""
Tests for engine/core.py

Covers: handle_message routing gate (onboarding vs session).
"""
import pytest
from asgiref.sync import sync_to_async
from unittest.mock import patch, AsyncMock


# ── Area 11: handle_message routing ──────────────────────────────────────────

@pytest.mark.django_db(transaction=True)
async def test_handle_message_routes_to_onboarding(make_user):
    """onboarding_complete=False → handle_onboarding is called, not handle_session."""
    from engine.core import handle_message

    user = await sync_to_async(make_user)(
        discord_id='u_rt1', onboarding_complete=False, display_name='Test'
    )

    onboarding_called = []

    async def mock_onboarding(u, text, attachments):
        onboarding_called.append(True)
        return {'text': 'onboarding', 'audio_url': None, 'session_ended': False}

    with patch('engine.onboarding.handle_onboarding', mock_onboarding):
        result = await handle_message(user, 'hello')

    assert onboarding_called, "handle_onboarding should be called when onboarding is incomplete"
    assert result['text'] == 'onboarding'


@pytest.mark.django_db(transaction=True)
async def test_handle_message_routes_to_session(make_user):
    """onboarding_complete=True → handle_session is called, not handle_onboarding."""
    from engine.core import handle_message

    user = await sync_to_async(make_user)(discord_id='u_rt2', onboarding_complete=True)

    session_called = []

    async def mock_session(u, text, attachments):
        session_called.append(True)
        return {'text': 'session', 'audio_url': None, 'session_ended': False}

    with patch('engine.session.handle_session', mock_session):
        result = await handle_message(user, 'hola')

    assert session_called, "handle_session should be called when onboarding is complete"
    assert result['text'] == 'session'
