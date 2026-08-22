from django.conf import settings
from django.http import HttpResponsePermanentRedirect


class CanonicalHostMiddleware:
    """Redirect the bare apex to the canonical host.

    Django ships PREPEND_WWW, which is not usable here: it rewrites *any* host
    lacking a "www." prefix, so spanish.up.railway.app would become
    www.spanish.up.railway.app — a hostname that does not exist. That host is
    the deliberate fallback kept working through the domain migration, so this
    rewrites one specific apex and leaves every other host alone.

    Skips the Messenger webhook. Meta does not follow redirects on webhook
    delivery — it treats a 301 as a failed delivery and drops the event. That
    is not hypothetical: a callback URL missing its trailing slash returned 301
    for months and silently discarded every message. If the webhook is ever
    pointed at the apex, this must not resurrect that bug.

    Disabled entirely when CANONICAL_HOST is unset, so local development and
    any other deployment are unaffected.
    """

    # Prefixes that must reach the app on whatever host they arrive at.
    EXEMPT_PREFIXES = ('/webhook/',)

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        canonical = getattr(settings, 'CANONICAL_HOST', '')
        if canonical:
            # Strip any port a proxy may have appended; matching on the raw
            # header would miss and send the client into a redirect loop.
            host = request.get_host().split(':')[0]
            apex = canonical.removeprefix('www.')
            if host == apex and not request.path.startswith(self.EXEMPT_PREFIXES):
                return HttpResponsePermanentRedirect(
                    f'https://{canonical}{request.get_full_path()}'
                )
        return self.get_response(request)
