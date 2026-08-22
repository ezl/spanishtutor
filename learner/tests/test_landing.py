"""Landing page tests.

The page is the only public entry point to the product, so these guard the
things that silently break a signup: the platform links, and per-CTA
attribution.
"""
import re
import pytest
from django.test import RequestFactory
from django.urls import reverse

from learner.views import landing


def _render():
    """Call the view directly.

    Django's test client copies the template Context on render, which raises on
    Python 3.14 (Context.__copy__ touches super().dicts). Every other view test
    in this repo POSTs JSON and never renders, so it has not bitten before.
    """
    return landing(RequestFactory().get('/')).content.decode()


@pytest.mark.django_db
def test_landing_renders():
    response = landing(RequestFactory().get('/'))
    assert response.status_code == 200


@pytest.mark.django_db
def test_platform_links_come_from_settings(settings):
    settings.MESSENGER_LINK = 'https://m.me/test-page'
    settings.DISCORD_INVITE = 'https://discord.gg/test-invite'

    body = _render()

    assert 'https://m.me/test-page' in body
    assert 'https://discord.gg/test-invite' in body


@pytest.mark.django_db
def test_every_cta_carries_a_distinct_ref():
    """One CTA repeats five times. Without a per-position ref every signup
    attributes to the hero and the page cannot be read."""
    body = _render()

    refs = re.findall(r'data-open-picker data-ref="([^"]+)"', body)

    assert len(refs) >= 4, f'expected several CTAs, found {refs}'
    assert len(set(refs)) == len(refs), f'duplicate refs: {refs}'


@pytest.mark.django_db
def test_legal_and_contact_links_are_real(settings):
    settings.CONTACT_EMAIL = 'someone@example.com'

    body = _render()

    assert reverse('privacy') in body
    assert reverse('terms') in body
    assert 'mailto:someone@example.com' in body


@pytest.mark.django_db
def test_no_placeholders_survived():
    body = _render()

    for placeholder in ('[LEGAL ENTITY]', '[YOUR PRICE]', '[portrait]', 'lorem'):
        assert placeholder.lower() not in body.lower()


@pytest.mark.django_db
def test_no_pricing_claims():
    """Testing with friends only; the page must not promise a price or a trial."""
    body = _render().lower()

    assert '$' not in body
    assert 'free' not in body


@pytest.mark.django_db
def test_social_image_url_is_absolute(settings):
    """Scrapers do not resolve relative URLs. A relative og:image silently
    yields no preview at all."""
    settings.BASE_URL = 'https://example.test'

    body = _render()

    assert 'content="https://example.test/static/learner/img/og.png"' in body
    assert 'property="og:image:width" content="1200"' in body
    assert 'name="twitter:card" content="summary_large_image"' in body


@pytest.mark.django_db
def test_favicon_ships_a_separate_16px_cut():
    """The wave inside the bubble does not resolve at 16px, so the small size
    is its own simplified file rather than the same one scaled."""
    body = _render()

    assert 'favicon-16.png' in body
    assert 'favicon-32.png' in body
    assert 'favicon.svg' in body
    assert 'apple-touch-icon.png' in body


@pytest.mark.django_db
def test_platform_chips_are_live_links(settings):
    """The chips in "Learn via chat" start that platform directly. Clicking a
    labelled logo IS the choice, so it skips the picker."""
    settings.MESSENGER_LINK = 'https://m.me/test-page'
    settings.DISCORD_INVITE = 'https://discord.gg/test-invite'

    body = _render()

    assert 'href="https://m.me/test-page?ref=web_chips"' in body
    assert 'href="https://discord.gg/test-invite"' in body


@pytest.mark.django_db
def test_whatsapp_chip_is_not_a_link():
    """No transport exists yet, so it must not be clickable."""
    body = _render()

    whatsapp = body[body.index('ms-chip--inactive'):]
    whatsapp = whatsapp[:whatsapp.index('</span>')]
    assert 'href' not in whatsapp


@pytest.mark.django_db
def test_messenger_link_with_a_baked_in_ref_does_not_double_up(settings):
    """The deployed MESSENGER_LINK carries ?ref=web_hero. Appending a second
    ?ref produced m.me/...?ref=web_hero?ref=web_chips in production: a malformed
    URL, and every per-position ref silently lost."""
    settings.MESSENGER_LINK = 'https://m.me/test-page?ref=baked_in'

    body = _render()

    for url in re.findall(r'https://m\.me/[^"\']*', body):
        assert url.count('?') <= 1, f'malformed: {url}'
        assert 'baked_in' not in url, f'stale ref survived: {url}'
