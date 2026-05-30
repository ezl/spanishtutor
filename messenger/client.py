import logging
import requests
from django.conf import settings

logger = logging.getLogger('messenger')

_GRAPH_URL = 'https://graph.facebook.com/v19.0/me/messages'
_MAX_CHARS = 2000


def _chunks(text: str) -> list[str]:
    chunks = []
    while len(text) > _MAX_CHARS:
        split = text.rfind('\n', 0, _MAX_CHARS)
        if split == -1:
            split = _MAX_CHARS
        chunks.append(text[:split])
        text = text[split:].lstrip('\n')
    if text:
        chunks.append(text)
    return chunks


def send_message(psid: str, text: str) -> None:
    token = settings.MESSENGER_PAGE_ACCESS_TOKEN
    if not token:
        logger.warning('MESSENGER_PAGE_ACCESS_TOKEN not set — cannot send message')
        return
    for chunk in _chunks(text):
        try:
            resp = requests.post(
                _GRAPH_URL,
                params={'access_token': token},
                json={'recipient': {'id': psid}, 'message': {'text': chunk}},
                timeout=10,
            )
            if not resp.ok:
                logger.error('Messenger send failed (%s): %s', resp.status_code, resp.text)
        except requests.RequestException as exc:
            logger.error('Messenger send error: %s', exc)
