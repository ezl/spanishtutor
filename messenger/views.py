import hashlib
import hmac
import json
import logging

from asgiref.sync import async_to_sync
from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt

from engine.onboarding import FIRST_MESSAGE
from messenger.client import send_message

logger = logging.getLogger('messenger')


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


def _process_message(psid: str, name: str, text: str):
    from engine.core import handle_message

    user, is_new = _get_or_create_user(psid, name)

    if is_new:
        send_message(psid, FIRST_MESSAGE)
        return

    result = async_to_sync(handle_message)(user, text, [])

    if result.get('text'):
        send_message(psid, result['text'])
    if result.get('follow_up'):
        send_message(psid, result['follow_up'])


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
            # Ignore echo events (messages sent by the page itself)
            if event.get('message', {}).get('is_echo'):
                continue
            message = event.get('message', {})
            text = message.get('text', '').strip()
            if not text:
                continue
            name = sender.get('name', '')
            try:
                _process_message(psid, name, text)
            except Exception:
                logger.exception('Error processing Messenger message from %s', psid)
                send_message(psid, 'Lo siento, tuve un problema. 😅 Try again in a moment!')

    return HttpResponse('OK')
