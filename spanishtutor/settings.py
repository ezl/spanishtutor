from pathlib import Path
import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'dev-insecure-key-change-in-production')
DEBUG = os.environ.get('DEBUG', 'true').lower() == 'true'
_allowed = os.environ.get('ALLOWED_HOSTS', 'localhost,127.0.0.1')
ALLOWED_HOSTS = [h.strip() for h in _allowed.split(',') if h.strip()] + ['.up.railway.app']

# Derived from ALLOWED_HOSTS rather than listed separately: the two drifting
# apart is a silent failure — the site serves fine and only form POSTs and the
# admin break, on the new domain only. The railway.app wildcard stays as a
# permanent fallback host.
# Bare apex requests get redirected here. Unset (the default) disables the
# redirect entirely, so local dev and any other deployment are unaffected.
CANONICAL_HOST = os.environ.get('CANONICAL_HOST', '')

# Legal name of the entity operating the service. Meta's business
# verification requires the legal business name to appear on the website
# before it will associate the domain with the business.
OPERATOR_LEGAL_NAME = os.environ.get('OPERATOR_LEGAL_NAME', 'Eric Liu')

CSRF_TRUSTED_ORIGINS = ['https://*.up.railway.app'] + [
    f'https://{h.lstrip(".")}' for h in ALLOWED_HOSTS
    if h not in ('localhost', '127.0.0.1') and not h.endswith('up.railway.app')
]
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# HSTS. Env-driven so it can be dialled down without a code change, which
# matters because removing the setting is NOT the undo: browsers keep an
# expired-at-max-age policy of their own. The undo is serving max-age=0 over
# HTTPS until stragglers pick it up.
#
# Shipped at 300 first and confirmed live on all three hostnames before being
# raised to a year. includeSubDomains is safe today because www is the only
# subdomain that resolves and it has its own certificate; it does constrain any
# future subdomain to having HTTPS from the moment it exists.
#
# The real cost of HSTS is that a certificate error stops being click-through
# and becomes a hard failure. That is the security property, but it makes
# renewal load-bearing.
SECURE_HSTS_SECONDS = int(os.environ.get('SECURE_HSTS_SECONDS', '31536000'))
SECURE_HSTS_INCLUDE_SUBDOMAINS = os.environ.get('SECURE_HSTS_INCLUDE_SUBDOMAINS', 'true').lower() == 'true'
SECURE_HSTS_PRELOAD = os.environ.get('SECURE_HSTS_PRELOAD', 'true').lower() == 'true'

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'learner',
    'engine',
    'bot',
    'messenger',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'learner.middleware.CanonicalHostMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'spanishtutor.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'spanishtutor.wsgi.application'

DATABASE_URL = os.environ.get('DATABASE_URL')
if DATABASE_URL:
    import dj_database_url
    DATABASES = {'default': dj_database_url.parse(DATABASE_URL)}
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STORAGES = {
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage',
    },
}
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# API keys
DISCORD_BOT_TOKEN = os.environ.get('DISCORD_BOT_TOKEN', '')
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
ANTHROPIC_MODEL = os.environ.get('ANTHROPIC_MODEL', 'claude-sonnet-4-6')
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY', '')

BASE_URL = os.environ.get('BASE_URL', 'https://spanish.up.railway.app')

# Public entry points. The landing page renders these rather than hardcoding
# them, so a page move or a new invite is an env change, not a deploy.
MESSENGER_LINK = os.environ.get('MESSENGER_LINK', 'https://m.me/61590505591195')
DISCORD_INVITE = os.environ.get('DISCORD_INVITE', 'https://discord.gg/7MB73HYd')
CONTACT_EMAIL = os.environ.get('CONTACT_EMAIL', 'ericzliu@gmail.com')
DEV_MODE = os.environ.get('DEV_MODE', 'true').lower() == 'true'

# Messenger
MESSENGER_VERIFY_TOKEN = os.environ.get('MESSENGER_VERIFY_TOKEN', '')
MESSENGER_PAGE_ACCESS_TOKEN = os.environ.get('MESSENGER_PAGE_ACCESS_TOKEN', '')
MESSENGER_APP_SECRET = os.environ.get('MESSENGER_APP_SECRET', '')

# Bot config
INACTIVITY_TIMEOUT_MINUTES = int(os.environ.get('INACTIVITY_TIMEOUT_MINUTES', '15'))
MAGIC_LINK_EXPIRY_SECONDS = int(os.environ.get('MAGIC_LINK_EXPIRY_SECONDS', '3600'))

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'simple': {
            'format': '%(asctime)s %(levelname)s %(name)s %(message)s',
            'datefmt': '%H:%M:%S',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
        },
    },
    'loggers': {
        'onboarding': {
            'handlers': ['console'],
            'level': 'DEBUG',
            'propagate': False,
        },
        'engine': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}
