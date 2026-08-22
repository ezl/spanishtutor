"""Tests for the apex -> www redirect.

Driven through the middleware directly rather than django.test.Client: the
Client instruments template rendering, and Django's Context.__copy__ does
copy(super()), which raises on Python 3.14. Any view that renders a template
blows up before the assertion. RequestFactory skips that machinery.
"""
from django.test import RequestFactory


def _run(path, host, canonical, settings):
    """Send one request through the middleware. Returns its response, or None
    if it passed the request through untouched."""
    from learner.middleware import CanonicalHostMiddleware
    settings.CANONICAL_HOST = canonical
    # Self-contained: request.get_host() raises DisallowedHost otherwise, and
    # relying on the ambient ALLOWED_HOSTS makes these pass alone and fail in
    # the full suite.
    settings.ALLOWED_HOSTS = [
        'minispanish.com', 'www.minispanish.com', 'spanish.up.railway.app',
    ]
    sentinel = object()
    mw = CanonicalHostMiddleware(lambda request: sentinel)
    resp = mw(RequestFactory().get(path, HTTP_HOST=host))
    return None if resp is sentinel else resp


class TestCanonicalHostRedirect:
    """minispanish.com must land on https://www.minispanish.com.

    Namecheap's URL Redirect record used to do this and only spoke HTTP, so
    https://minispanish.com failed the TLS handshake outright. Once the apex
    points at Railway it gets a real certificate and the redirect is ours.
    """

    def test_apex_redirects_to_www(self, settings):
        resp = _run('/privacy/', 'minispanish.com', 'www.minispanish.com', settings)
        assert resp.status_code == 301
        assert resp['Location'] == 'https://www.minispanish.com/privacy/'

    def test_query_string_survives(self, settings):
        """The CTA carries ?ref=; dropping it silently breaks attribution."""
        resp = _run('/?ref=web_hero', 'minispanish.com', 'www.minispanish.com', settings)
        assert resp['Location'] == 'https://www.minispanish.com/?ref=web_hero'

    def test_www_passes_through(self, settings):
        assert _run('/privacy/', 'www.minispanish.com', 'www.minispanish.com', settings) is None

    def test_railway_fallback_host_passes_through(self, settings):
        """Django's built-in PREPEND_WWW would rewrite this to
        www.spanish.up.railway.app, which does not exist — killing the fallback
        host kept working for emergencies. Only the apex is rewritten."""
        assert _run('/privacy/', 'spanish.up.railway.app', 'www.minispanish.com', settings) is None

    def test_webhook_is_never_redirected(self, settings):
        """Meta does not follow redirects on webhook delivery. A 301 here is
        indistinguishable from an outage — the exact bug that kept Messenger
        dead for months."""
        assert _run('/webhook/messenger/', 'minispanish.com', 'www.minispanish.com', settings) is None

    def test_disabled_when_canonical_host_unset(self, settings):
        """Local dev and any other deployment must be unaffected."""
        assert _run('/privacy/', 'minispanish.com', '', settings) is None

    def test_port_in_host_header_is_ignored(self, settings):
        """Railway forwards Host with no port, but a proxy that adds one must
        not defeat the match and cause an infinite redirect."""
        resp = _run('/privacy/', 'minispanish.com:443', 'www.minispanish.com', settings)
        assert resp.status_code == 301
