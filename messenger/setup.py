"""
One-time setup for the Messenger profile (Get Started button, ice breakers).

Run with:
    PYTHONPATH=. MESSENGER_PAGE_ACCESS_TOKEN=... python messenger/setup.py

Note on the greeting: Meta removed `greeting` from the Messenger Profile API.
It is no longer in the accepted-params list, and sending it produces
{"result": "success"} while the field is silently discarded — which is how this
script reported success for months without ever setting a greeting. The
pre-conversation greeting text is now configured by hand in Meta Business Suite
(Inbox -> Automations -> Greeting), not from here. Ice breakers are the
supported way to give a new visitor something actionable before they type.

Every payload registered here must also appear in
`messenger.views.WELCOME_POSTBACK_PAYLOADS`, or Meta will deliver the tap and
the webhook will drop it.
"""
import requests
from django.conf import settings

GRAPH_URL = 'https://graph.facebook.com/v21.0/me'

PROFILE = {
    'get_started': {'payload': 'GET_STARTED'},
    'ice_breakers': [
        {
            'locale': 'default',
            'call_to_actions': [
                {
                    # Deliberately trivial to tap — the point is to get a
                    # first message sent at all, since Meta won't let the Page
                    # speak first. The payload routes to handle_welcome, which
                    # answers with FIRST_MESSAGE and explains the assessment.
                    'question': '¡Hola!',
                    'payload': 'START_ASSESSMENT',
                },
            ],
        },
    ],
}


def setup_messenger_profile():
    token = settings.MESSENGER_PAGE_ACCESS_TOKEN
    if not token:
        raise SystemExit('MESSENGER_PAGE_ACCESS_TOKEN is not set')

    resp = requests.post(
        f'{GRAPH_URL}/messenger_profile',
        params={'access_token': token},
        json=PROFILE,
        timeout=10,
    )
    print('POST', resp.status_code, resp.json())
    resp.raise_for_status()

    # Read back and confirm every field we sent actually persisted. Meta
    # accepts a payload wholesale and drops unknown keys without complaint, so
    # a 200 proves nothing on its own.
    fields = ','.join(PROFILE)
    check = requests.get(
        f'{GRAPH_URL}/messenger_profile',
        params={'access_token': token, 'fields': fields},
        timeout=10,
    )
    data = check.json().get('data', [{}])
    stored = data[0] if data else {}

    missing = [f for f in PROFILE if f not in stored]
    for f in PROFILE:
        print(f'  {f}: {"OK" if f in stored else "MISSING — silently discarded"}')
    if missing:
        raise SystemExit(f'Fields not stored by Meta: {", ".join(missing)}')
    print('All profile fields verified.')


if __name__ == '__main__':
    import django, os
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'spanishtutor.settings')
    django.setup()
    setup_messenger_profile()
