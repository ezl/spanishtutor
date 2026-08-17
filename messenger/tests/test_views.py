import json

import pytest
from asgiref.sync import sync_to_async
from django.test import Client
from unittest.mock import AsyncMock, patch


def _messenger_payload(psid: str = 'psid_test', name: str = 'Tester',
                       text: str = 'hola') -> dict:
    return {
        'object': 'page',
        'entry': [{
            'messaging': [{
                'sender': {'id': psid, 'name': name},
                'message': {'text': text},
            }],
        }],
    }


@pytest.mark.django_db(transaction=True)
def test_webhook_returns_200_immediately_and_fires_background_task():
    """The webhook MUST return 200 without waiting on LLM processing.
    Meta expects a response within ~20s and LLM turns can exceed that."""
    client = Client()
    payload = _messenger_payload()

    with patch('messenger.views._valid_signature', return_value=True):
        with patch('messenger.views._run_async_in_background') as bg:
            resp = client.post(
                '/webhook/messenger/',
                data=json.dumps(payload),
                content_type='application/json',
            )

    assert resp.status_code == 200
    # Background task was fired with the async coroutine.
    bg.assert_called_once()


@pytest.mark.django_db(transaction=True)
def test_webhook_does_not_call_handle_message_inline():
    """Regression: previous implementation called async_to_sync(handle_message)
    inline in the webhook, blocking the response on the full LLM turn.
    The fix moves that work to a background thread — verify the sync path is gone."""
    client = Client()
    payload = _messenger_payload()

    with patch('messenger.views._valid_signature', return_value=True):
        with patch('messenger.views._run_async_in_background'):
            with patch('engine.core.handle_message', new=AsyncMock()) as handle:
                client.post(
                    '/webhook/messenger/',
                    data=json.dumps(payload),
                    content_type='application/json',
                )

    # handle_message must NOT have been called inline — the background task
    # was mocked out, so if handle_message ran, it ran synchronously in the view.
    handle.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_process_message_async_creates_new_user_and_sends_first_message(make_user):
    """New user (no prior messenger_psid row) → get_or_create + FIRST_MESSAGE."""
    from engine.onboarding import FIRST_MESSAGE
    from learner.models import User
    from messenger.views import _process_message_async

    with patch('messenger.views.send_message') as mock_send:
        await _process_message_async('new_psid_1', 'Tester', 'hola')

    mock_send.assert_called_once()
    args = mock_send.call_args[0]
    assert args[0] == 'new_psid_1'
    assert args[1] == FIRST_MESSAGE

    exists = await sync_to_async(User.objects.filter(messenger_psid='new_psid_1').exists)()
    assert exists


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_process_message_async_dispatches_existing_user_to_engine(make_user):
    """Existing user → handle_message() → send_message with the reply."""
    from learner.models import User
    from messenger.views import _process_message_async

    await sync_to_async(User.objects.create)(
        messenger_psid='psid_existing', display_name='Existing',
    )

    fake_result = {'text': 'reply from engine', 'audio_url': None, 'session_ended': False}
    with patch('messenger.views.send_message') as mock_send:
        with patch('engine.core.handle_message', new=AsyncMock(return_value=fake_result)):
            await _process_message_async('psid_existing', 'Existing', 'hola')

    mock_send.assert_called_once()
    args = mock_send.call_args[0]
    assert args[0] == 'psid_existing'
    assert args[1] == 'reply from engine'


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_process_message_async_sends_fallback_on_exception(make_user):
    """If handle_message raises, user must still get a fallback message rather
    than silence — otherwise they wonder if their message got through."""
    from learner.models import User
    from messenger.views import _process_message_async, FALLBACK_ERROR_MESSAGE

    await sync_to_async(User.objects.create)(
        messenger_psid='psid_err', display_name='X',
    )

    with patch('messenger.views.send_message') as mock_send:
        with patch('engine.core.handle_message', new=AsyncMock(side_effect=Exception('boom'))):
            await _process_message_async('psid_err', 'X', 'hola')

    mock_send.assert_called_once()
    args = mock_send.call_args[0]
    assert args[0] == 'psid_err'
    assert args[1] == FALLBACK_ERROR_MESSAGE


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_process_message_async_sends_follow_up_when_present(make_user):
    """When handle_message returns a follow_up (e.g., onboarding scripted intro
    plus lesson content), both messages get sent."""
    from learner.models import User
    from messenger.views import _process_message_async

    await sync_to_async(User.objects.create)(
        messenger_psid='psid_followup', display_name='F',
    )

    fake_result = {
        'text': 'intro line',
        'follow_up': 'lesson content',
        'audio_url': None, 'session_ended': False,
    }
    with patch('messenger.views.send_message') as mock_send:
        with patch('engine.core.handle_message', new=AsyncMock(return_value=fake_result)):
            await _process_message_async('psid_followup', 'F', 'hola')

    assert mock_send.call_count == 2
    calls = [c[0] for c in mock_send.call_args_list]
    assert calls[0] == ('psid_followup', 'intro line')
    assert calls[1] == ('psid_followup', 'lesson content')
