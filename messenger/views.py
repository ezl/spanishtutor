import asyncio
import hashlib
import hmac
import json
import logging
import threading

from asgiref.sync import sync_to_async
from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt

from engine.onboarding import FIRST_MESSAGE
from messenger.client import send_message

logger = logging.getLogger('messenger')

FALLBACK_ERROR_MESSAGE = 'Lo siento, tuve un problema. 😅 Try again in a moment!'


def _valid_signature(body: bytes, header: str) -> bool:
    if not header.startswith('sha256='):
        return False
    secret = settings.MESSENGER_APP_SECRET
    if not secret:
        return True  # skip validation in dev if secret not set
    mac = hmac.new(secret.encode(), body, hashlib.sha256)
    return hmac.compare_digest(mac.hexdigest(), header[7:])


def _get_or_create_user(psid: str, name: str):
    from learner.models import User
    return User.objects.get_or_create(
        messenger_psid=psid,
        defaults={'display_name': name},
    )


def _run_async_in_background(coro) -> None:
    """Fire an async coroutine in a background daemon thread. Returns
    immediately. The thread creates its own event loop, runs the coro to
    completion, and exits.

    Meta's webhook contract requires a response within ~20 seconds. LLM turns
    can exceed that. This shim lets the webhook return 200 immediately while
    the actual message processing (which may run 5-30s) continues out-of-band.

    Under WSGI (our current deployment), asyncio.create_task inside an async
    view is unreliable — the request's event loop terminates on return.
    Threading sidesteps that by running the coro in a wholly separate loop.
    When we eventually migrate to ASGI, this can be replaced by
    asyncio.create_task with no change to callers."""
    def _runner():
        try:
            asyncio.run(coro)
        except Exception:
            logger.exception('Background messenger task crashed')
    threading.Thread(target=_runner, daemon=True).start()


async def _process_message_async(psid: str, name: str, text: str) -> None:
    """Background worker: resolve user, dispatch to engine, send reply.
    Any exception is caught here and a fallback error message is sent to
    the user, so a silent failure doesn't leave them wondering."""
    from engine.core import handle_message

    try:
        user, is_new = await sync_to_async(_get_or_create_user)(psid, name)

        if is_new:
            await sync_to_async(send_message)(psid, FIRST_MESSAGE)
            return

        result = await handle_message(user, text, [])

        if result.get('text'):
            await sync_to_async(send_message)(psid, result['text'])
        if result.get('follow_up'):
            await sync_to_async(send_message)(psid, result['follow_up'])
    except Exception:
        logger.exception('Error processing Messenger message from %s', psid)
        try:
            await sync_to_async(send_message)(psid, FALLBACK_ERROR_MESSAGE)
        except Exception:
            logger.exception('Failed to send fallback error message to %s', psid)


@csrf_exempt
def webhook(request):
    if request.method == 'GET':
        if request.GET.get('hub.verify_token') == settings.MESSENGER_VERIFY_TOKEN:
            return HttpResponse(request.GET.get('hub.challenge', ''))
        return HttpResponse('Forbidden', status=403)

    if request.method != 'POST':
        return HttpResponse('Method Not Allowed', status=405)

    if not _valid_signature(request.body, request.headers.get('X-Hub-Signature-256', '')):
        logger.warning('Messenger webhook: invalid signature')
        return HttpResponse('Forbidden', status=403)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return HttpResponse('Bad Request', status=400)

    if data.get('object') != 'page':
        return HttpResponse('OK')

    for entry in data.get('entry', []):
        for event in entry.get('messaging', []):
            sender = event.get('sender', {})
            psid = sender.get('id', '')
            if not psid:
                continue

            # Get Started button tap
            postback = event.get('postback', {})
            if postback.get('payload') == 'GET_STARTED':
                try:
                    user, is_new = _get_or_create_user(psid, sender.get('name', ''))
                    send_message(psid, FIRST_MESSAGE)
                except Exception:
                    logger.exception('Error processing GET_STARTED from %s', psid)
                continue

            # Ignore echo events (messages sent by the page itself)
            if event.get('message', {}).get('is_echo'):
                continue
            message = event.get('message', {})
            text = message.get('text', '').strip()
            if not text:
                continue
            name = sender.get('name', '')
            # Fire-and-forget: return 200 to Meta immediately, process the
            # message in a background thread. Reply comes back to the user via
            # send_message when processing completes.
            _run_async_in_background(_process_message_async(psid, name, text))

    return HttpResponse('OK')
