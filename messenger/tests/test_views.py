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


@pytest.mark.django_db(transaction=True)
def test_get_started_postback_creates_user_and_sends_first_message():
    """Get Started button tap routes through dispatch.handle_welcome — the
    transport must not build the greeting itself."""
    from engine.onboarding import FIRST_MESSAGE
    from learner.models import User

    payload = {
        'object': 'page',
        'entry': [{
            'messaging': [{
                'sender': {'id': 'psid_getstarted', 'name': 'Tester'},
                'postback': {'payload': 'GET_STARTED'},
            }],
        }],
    }

    with patch('messenger.views.send_message') as mock_send:
        with patch('messenger.views._valid_signature', return_value=True):
            response = Client().post(
                '/webhook/messenger/',
                data=json.dumps(payload),
                content_type='application/json',
            )

    assert response.status_code == 200
    mock_send.assert_called_once_with('psid_getstarted', FIRST_MESSAGE)
    assert User.objects.filter(messenger_psid='psid_getstarted').exists()


def _non_text_payload(psid: str = 'psid_sticker', name: str = 'Tester') -> dict:
    """What Meta sends for a sticker, photo or voice clip: a message event
    carrying attachments and no 'text' key at all."""
    return {
        'object': 'page',
        'entry': [{
            'messaging': [{
                'sender': {'id': psid, 'name': name},
                'message': {
                    'mid': 'm_1',
                    'attachments': [{'type': 'image', 'payload': {'sticker_id': 369239263222822}}],
                },
            }],
        }],
    }


def _run_coro_inline(coro):
    """Stand-in for _run_async_in_background that runs the coroutine to
    completion, so a test can assert on what the user actually receives."""
    import asyncio
    asyncio.run(coro)


@pytest.mark.django_db(transaction=True)
def test_non_text_message_gets_the_text_only_notice():
    """A thumbs-up sticker is the most common non-text message on Messenger —
    people send it to mean 'ok'. It used to be dropped in silence."""
    from engine.dispatch import NON_TEXT_NOTICE_TEXT
    from learner.models import User

    User.objects.create(messenger_psid='psid_sticker', display_name='Tester')

    with patch('messenger.views.send_message') as mock_send:
        with patch('messenger.views._valid_signature', return_value=True):
            with patch('messenger.views._run_async_in_background', new=_run_coro_inline):
                response = Client().post(
                    '/webhook/messenger/',
                    data=json.dumps(_non_text_payload()),
                    content_type='application/json',
                )

    assert response.status_code == 200
    mock_send.assert_called_once_with('psid_sticker', NON_TEXT_NOTICE_TEXT)


@pytest.mark.django_db(transaction=True)
def test_delivery_and_read_receipts_are_ignored():
    """Meta fires delivery/read events for every message the page sends. They
    carry no 'message' key — routing them to dispatch would answer each one
    with the non-text notice, i.e. reply to our own replies forever."""
    receipts = {
        'object': 'page',
        'entry': [{
            'messaging': [
                {'sender': {'id': 'psid_receipt'}, 'delivery': {'mids': ['m_1'], 'watermark': 1}},
                {'sender': {'id': 'psid_receipt'}, 'read': {'watermark': 1}},
            ],
        }],
    }

    with patch('messenger.views.send_message') as mock_send:
        with patch('messenger.views._valid_signature', return_value=True):
            with patch('messenger.views._run_async_in_background') as bg:
                response = Client().post(
                    '/webhook/messenger/',
                    data=json.dumps(receipts),
                    content_type='application/json',
                )

    assert response.status_code == 200
    bg.assert_not_called()
    mock_send.assert_not_called()


# ── Standalone referral events (messaging_referrals) ─────────────────────────

def _referral_payload(psid: str = 'psid_referral', ref: str = 'web_hero') -> dict:
    """What Meta sends when someone who ALREADY has a thread opens it via an
    m.me?ref= link — the website CTA clicked by a returning student. Per the
    messaging_referrals docs there is no 'message' and no 'postback' key, so
    this event falls through both of the webhook's other branches."""
    return {
        'object': 'page',
        'entry': [{
            'messaging': [{
                'sender': {'id': psid},
                'recipient': {'id': 'page_1'},
                'timestamp': 1458692752478,
                'referral': {'ref': ref, 'source': 'SHORTLINK', 'type': 'OPEN_THREAD'},
            }],
        }],
    }


@pytest.mark.django_db(transaction=True)
def test_referral_event_answers_a_returning_student():
    """Regression: this event used to be dropped, so a returning student who
    clicked the site's only CTA landed in a silent thread."""
    from engine.dispatch import WELCOME_BACK_TEXT
    from learner.models import User

    User.objects.create(
        messenger_psid='psid_referral', display_name='Ana',
        estimated_cefr_level='B1', onboarding_complete=True,
    )

    with patch('messenger.views.send_message') as mock_send:
        with patch('messenger.views._valid_signature', return_value=True):
            response = Client().post(
                '/webhook/messenger/',
                data=json.dumps(_referral_payload()),
                content_type='application/json',
            )

    assert response.status_code == 200
    mock_send.assert_called_once_with('psid_referral', WELCOME_BACK_TEXT)


@pytest.mark.django_db(transaction=True)
def test_referral_event_onboards_a_first_time_visitor():
    """A thread can exist without onboarding (an earlier referral they ignored)."""
    from engine.onboarding import FIRST_MESSAGE
    from learner.models import User

    with patch('messenger.views.send_message') as mock_send:
        with patch('messenger.views._valid_signature', return_value=True):
            Client().post(
                '/webhook/messenger/',
                data=json.dumps(_referral_payload(psid='psid_ref_new')),
                content_type='application/json',
            )

    mock_send.assert_called_once_with('psid_ref_new', FIRST_MESSAGE)
    assert User.objects.filter(messenger_psid='psid_ref_new').exists()


@pytest.mark.django_db(transaction=True)
def test_referral_event_does_not_interrupt_an_open_lesson():
    from learner.models import Session, User

    user = User.objects.create(
        messenger_psid='psid_ref_busy', display_name='Ana',
        estimated_cefr_level='B1', onboarding_complete=True,
    )
    Session.objects.create(user=user, session_type='new_skill')

    with patch('messenger.views.send_message') as mock_send:
        with patch('messenger.views._valid_signature', return_value=True):
            Client().post(
                '/webhook/messenger/',
                data=json.dumps(_referral_payload(psid='psid_ref_busy')),
                content_type='application/json',
            )

    mock_send.assert_not_called()
