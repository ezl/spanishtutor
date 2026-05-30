"""
One-time setup for the Messenger profile (Get Started button, greeting text).
Run with: python manage.py shell < messenger/setup.py
Or call setup_messenger_profile() from the shell.
"""
import requests
from django.conf import settings

GRAPH_URL = 'https://graph.facebook.com/v19.0/me'


def setup_messenger_profile():
    token = settings.MESSENGER_PAGE_ACCESS_TOKEN

    payload = {
        'get_started': {'payload': 'GET_STARTED'},
        'greeting': [
            {
                'locale': 'default',
                'text': 'Hola! Soy Luz Angela, tu profesora de español. Toca "Comenzar" para empezar. 🌟',
            }
        ],
    }

    resp = requests.post(
        f'{GRAPH_URL}/messenger_profile',
        params={'access_token': token},
        json=payload,
        timeout=10,
    )
    print(resp.status_code, resp.json())


if __name__ == '__main__':
    import django, os
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'spanishtutor.settings')
    django.setup()
    setup_messenger_profile()
