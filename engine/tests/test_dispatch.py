"""Tests for the transport-agnostic dispatch layer."""
import pytest
from asgiref.sync import sync_to_async


class TestDataclasses:
    def test_incoming_event_has_expected_fields(self):
        from engine.dispatch import IncomingEvent
        e = IncomingEvent(
            platform='discord', external_id='u123',
            display_name='Alice', text='hola',
        )
        assert e.platform == 'discord'
        assert e.external_id == 'u123'
        assert e.display_name == 'Alice'
        assert e.text == 'hola'

    def test_incoming_event_has_no_attachments_field(self):
        """Text-only by design (voice lives on web app, not chat platforms).
        No attachments field until we intentionally add multi-modal support."""
        from engine.dispatch import IncomingEvent
        e = IncomingEvent(platform='p', external_id='x', display_name='n', text='t')
        assert not hasattr(e, 'attachments')

    def test_reply_defaults(self):
        from engine.dispatch import Reply
        r = Reply(text='hi')
        assert r.text == 'hi'
        assert r.follow_up is None
        assert r.session_ended is False

    def test_reply_with_all_fields(self):
        from engine.dispatch import Reply
        r = Reply(text='hi', follow_up='and again', session_ended=True)
        assert r.text == 'hi'
        assert r.follow_up == 'and again'
        assert r.session_ended is True


class TestResolveUser:
    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_creates_new_discord_user(self):
        from engine.dispatch import resolve_user
        from learner.models import User

        user, is_new = await resolve_user('discord', 'd_new_1', 'Alice')
        assert is_new is True
        assert user.discord_id == 'd_new_1'
        assert user.messenger_psid is None
        assert user.display_name == 'Alice'

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_finds_existing_discord_user(self):
        from engine.dispatch import resolve_user
        from learner.models import User

        await sync_to_async(User.objects.create)(
            discord_id='d_existing', display_name='Bob',
        )
        user, is_new = await resolve_user('discord', 'd_existing', 'IgnoredName')
        assert is_new is False
        assert user.discord_id == 'd_existing'
        # display_name is only set on create, not update — matches prior behavior.
        assert user.display_name == 'Bob'

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_creates_new_messenger_user(self):
        from engine.dispatch import resolve_user

        user, is_new = await resolve_user('messenger', 'psid_new_1', 'Carol')
        assert is_new is True
        assert user.messenger_psid == 'psid_new_1'
        assert user.discord_id is None
        assert user.display_name == 'Carol'

    @pytest.mark.asyncio
    async def test_unknown_platform_raises(self):
        from engine.dispatch import resolve_user

        with pytest.raises(ValueError, match="Unknown platform"):
            await resolve_user('signal', 'x', 'y')
