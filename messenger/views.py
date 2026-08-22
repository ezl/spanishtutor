import asyncio
import hashlib
import hmac
import json
import logging
import threading

from asgiref.sync import async_to_sync, sync_to_async
from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt

from engine.dispatch import (
    FALLBACK_ERROR_TEXT,
    IncomingEvent,
    handle as dispatch_handle,
    handle_welcome,
)
from messenger.client import send_message

logger = logging.getLogger('messenger')

# Single source of truth lives in engine.dispatch — the transport only needs it
# for failures in its own send path (dispatch already wraps engine errors).
FALLBACK_ERROR_MESSAGE = FALLBACK_ERROR_TEXT


# Postback payloads that mean "start a conversation". Kept in sync with the
# ice breakers registered in messenger/setup.py — a payload configured there
# and missing here is delivered and then dropped.
WELCOME_POSTBACK_PAYLOADS = frozenset({
    'GET_STARTED',
    'START_ASSESSMENT',
})


def _valid_signature(body: bytes, header: str) -> bool:
    if not header.startswith('sha256='):
        return False
    secret = settings.MESSENGER_APP_SECRET
    if not secret:
        # Fail closed in production. This endpoint's URL is registered with
        # Meta and shows up in logs, so trusting unsigned POSTs would let
        # anyone forge a message from any PSID. Only local dev, which has no
        # app secret and no tunnel, is allowed to skip the check.
        if not settings.DEBUG:
            logger.error(
                'MESSENGER_APP_SECRET is not set — rejecting webhook request. '
                'Set it on the service that serves this endpoint.'
            )
            return False
        return True
    mac = hmac.new(secret.encode(), body, hashlib.sha256)
    return hmac.compare_digest(mac.hexdigest(), header[7:])


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
    """Background worker: build an IncomingEvent, hand to the dispatch layer,
    send each Reply via the platform API.

    Error handling now lives in dispatch.handle — this wrapper only handles
    unexpected failures from the send_message path itself."""
    try:
        event = IncomingEvent(
            platform='messenger', external_id=psid,
            display_name=name, text=text,
        )
        replies = await dispatch_handle(event)
        for reply in replies:
            await sync_to_async(send_message)(psid, reply.text)
            if reply.follow_up:
                await sync_to_async(send_message)(psid, reply.follow_up)
    except Exception:
        # dispatch.handle already returns a Reply for engine-side errors;
        # this catch covers only send_message / transport failures.
        logger.exception('Messenger transport failure for %s', psid)
        try:
            await sync_to_async(send_message)(psid, FALLBACK_ERROR_MESSAGE)
        except Exception:
            logger.exception('Failed to send fallback error message to %s', psid)


def _welcome(psid: str, sender: dict, ref: str) -> None:
    """Run a platform 'start' affordance (Get Started tap or m.me referral)
    through dispatch and send whatever it returns.

    Synchronous, unlike regular messages: handle_welcome does no LLM work, so
    it comfortably fits Meta's ~20s webhook SLA. It may legitimately return no
    replies (user is mid-lesson) — sending nothing is the correct outcome."""
    try:
        replies = async_to_sync(handle_welcome)(
            'messenger', psid, sender.get('name', ''), ref,
        )
        for reply in replies:
            send_message(psid, reply.text)
    except Exception:
        logger.exception('Error processing welcome for %s (ref=%r)', psid, ref)


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

            # Start affordances that arrive as postbacks: the Get Started
            # button and any ice breaker. Both mean "this person is opening a
            # conversation with no text to route", which is what handle_welcome
            # exists for. Matched against an explicit table rather than "any
            # postback" so a future button can't silently re-onboard someone.
            # Get Started additionally carries postback.referral when the user
            # arrived from an m.me?ref= link (i.e. the website CTA).
            postback = event.get('postback', {})
            if postback.get('payload') in WELCOME_POSTBACK_PAYLOADS:
                _welcome(psid, sender, postback.get('referral', {}).get('ref', ''))
                continue

            # Standalone referral (messaging_referrals): someone who ALREADY has
            # a thread reopened it via m.me?ref=. Carries neither 'message' nor
            # 'postback', so without this branch it falls through both checks
            # below and the returning student lands in a silent thread.
            if 'referral' in event:
                _welcome(psid, sender, event['referral'].get('ref', ''))
                continue

            # Ignore echo events (messages sent by the page itself)
            if event.get('message', {}).get('is_echo'):
                continue
            # Only message events reach dispatch. Delivery/read receipts and
            # other postbacks carry no 'message' key — routing those through
            # would answer each one with the non-text notice.
            if 'message' not in event:
                continue
            message = event['message']
            # Stickers, photos and voice clips arrive with no 'text'. They pass
            # through as empty text and dispatch answers them, so this shim
            # never has to know what an attachment is.
            text = message.get('text', '').strip()
            name = sender.get('name', '')
            # Fire-and-forget: return 200 to Meta immediately, process the
            # message in a background thread. Reply comes back to the user via
            # send_message when processing completes.
            _run_async_in_background(_process_message_async(psid, name, text))

    return HttpResponse('OK')
