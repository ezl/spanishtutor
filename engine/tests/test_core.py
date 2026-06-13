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
