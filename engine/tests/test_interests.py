"""
Tests for engine/interests.py

Covers: _parse_extraction, extract_and_store_interests dedup logic.
"""
import pytest
from asgiref.sync import sync_to_async
from unittest.mock import patch, AsyncMock


# ── Area 10: _parse_extraction ───────────────────────────────────────────────

class TestParseExtraction:
    def test_parses_valid_lines(self):
        from engine.interests import _parse_extraction
        raw = "Chicago Cubs | sports | 0.9\nhas a dog | pets | 0.8"
        results = _parse_extraction(raw)
        assert len(results) == 2
        assert results[0] == {'topic': 'Chicago Cubs', 'category': 'sports', 'confidence': 0.9}
        assert results[1]['topic'] == 'has a dog'

    def test_nothing_returns_empty_list(self):
        from engine.interests import _parse_extraction
        assert _parse_extraction('NOTHING') == []

    def test_malformed_lines_are_skipped(self):
        from engine.interests import _parse_extraction
        raw = "valid | category | 0.7\nbad line no pipes\nalso | bad"
        results = _parse_extraction(raw)
        assert len(results) == 1
        assert results[0]['topic'] == 'valid'

    def test_non_numeric_confidence_is_skipped(self):
        from engine.interests import _parse_extraction
        raw = "topic | category | not_a_number"
        assert _parse_extraction(raw) == []


# ── extract_and_store_interests: dedup and new interest logic ─────────────────

@pytest.mark.django_db(transaction=True)
async def test_extract_dedup_increments_mention_count(make_user):
    """Same topic seen again → mention_count++ and confidence max-merged."""
    from learner.models import Session, SessionEvent, UserInterest
    from engine.interests import extract_and_store_interests

    user = await sync_to_async(make_user)(discord_id='u_int1')
    session = await sync_to_async(Session.objects.create)(user=user, session_type='conversation')
    await sync_to_async(SessionEvent.objects.create)(
        session=session, event_type='conversation',
        content='Do you like football?', user_response='Yes, I love the Cubs.',
    )

    await sync_to_async(UserInterest.objects.create)(
        user=user, topic='Chicago Cubs', category='sports', confidence=0.6, mention_count=1,
    )

    with patch('engine.interests._call_extraction_llm', AsyncMock(return_value='Chicago Cubs | sports | 0.9')):
        facts = await extract_and_store_interests(session, user)

    ui = await sync_to_async(UserInterest.objects.get)(user=user, topic='Chicago Cubs')
    assert ui.mention_count == 2
    assert ui.confidence == pytest.approx(0.9)
    assert facts[0].get('new') is False


@pytest.mark.django_db(transaction=True)
async def test_extract_creates_new_interest(make_user):
    """New topic → creates UserInterest with new=True."""
    from learner.models import Session, SessionEvent, UserInterest
    from engine.interests import extract_and_store_interests

    user = await sync_to_async(make_user)(discord_id='u_int2')
    session = await sync_to_async(Session.objects.create)(user=user, session_type='conversation')
    await sync_to_async(SessionEvent.objects.create)(
        session=session, event_type='conversation',
        content='What do you do?', user_response='I work in tech.',
    )

    with patch('engine.interests._call_extraction_llm', AsyncMock(return_value='works in tech | career | 0.7')):
        facts = await extract_and_store_interests(session, user)

    assert facts[0]['new'] is True
    count = await sync_to_async(UserInterest.objects.filter(user=user, topic='works in tech').count)()
    assert count == 1


@pytest.mark.django_db(transaction=True)
async def test_extract_returns_empty_when_nothing(make_user):
    """'NOTHING' response → empty list, no UserInterest rows created."""
    from learner.models import Session, SessionEvent, UserInterest
    from engine.interests import extract_and_store_interests

    user = await sync_to_async(make_user)(discord_id='u_int3')
    session = await sync_to_async(Session.objects.create)(user=user, session_type='conversation')
    await sync_to_async(SessionEvent.objects.create)(
        session=session, event_type='conversation',
        content='How are you?', user_response='Fine.',
    )

    with patch('engine.interests._call_extraction_llm', AsyncMock(return_value='NOTHING')):
        facts = await extract_and_store_interests(session, user)

    assert facts == []
    count = await sync_to_async(UserInterest.objects.filter(user=user).count)()
    assert count == 0
