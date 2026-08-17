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


class TestDispatchHandle:
    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_new_user_gets_first_message_reply(self):
        from engine.dispatch import IncomingEvent, handle
        from engine.onboarding import FIRST_MESSAGE
        from learner.models import User

        assert not await sync_to_async(User.objects.filter(discord_id='d_dispatch_new').exists)()

        event = IncomingEvent(
            platform='discord', external_id='d_dispatch_new',
            display_name='NewOne', text='hi',
        )
        replies = await handle(event)

        assert len(replies) == 1
        assert replies[0].text == FIRST_MESSAGE
        # User row was created.
        assert await sync_to_async(User.objects.filter(discord_id='d_dispatch_new').exists)()

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_existing_user_routes_to_engine_and_returns_reply(self):
        from unittest.mock import patch, AsyncMock
        from engine.dispatch import IncomingEvent, handle
        from learner.models import User

        await sync_to_async(User.objects.create)(
            discord_id='d_existing_2', display_name='Ex',
        )

        fake_result = {'text': 'engine reply', 'audio_url': None, 'session_ended': False}
        with patch('engine.core.handle_message', new=AsyncMock(return_value=fake_result)):
            event = IncomingEvent(
                platform='discord', external_id='d_existing_2',
                display_name='Ex', text='hola',
            )
            replies = await handle(event)

        assert len(replies) == 1
        assert replies[0].text == 'engine reply'
        assert replies[0].follow_up is None

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_engine_follow_up_carried_on_reply(self):
        from unittest.mock import patch, AsyncMock
        from engine.dispatch import IncomingEvent, handle
        from learner.models import User

        await sync_to_async(User.objects.create)(
            discord_id='d_followup', display_name='F',
        )

        fake_result = {
            'text': 'intro', 'follow_up': 'lesson body',
            'audio_url': None, 'session_ended': False,
        }
        with patch('engine.core.handle_message', new=AsyncMock(return_value=fake_result)):
            event = IncomingEvent(
                platform='discord', external_id='d_followup',
                display_name='F', text='hi',
            )
            replies = await handle(event)

        assert len(replies) == 1
        assert replies[0].text == 'intro'
        assert replies[0].follow_up == 'lesson body'

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_dev_log_becomes_separate_reply(self):
        """When the engine returns dev_log (scoring transcript at session
        close), it lands as an additional Reply — transport sends both."""
        from unittest.mock import patch, AsyncMock
        from engine.dispatch import IncomingEvent, handle
        from learner.models import User

        await sync_to_async(User.objects.create)(
            discord_id='d_devlog', display_name='D',
        )
        fake_result = {
            'text': 'close message', 'audio_url': None, 'session_ended': True,
            'dev_log': 'DEV: [scored a1_greetings=4]',
        }
        with patch('engine.core.handle_message', new=AsyncMock(return_value=fake_result)):
            event = IncomingEvent(
                platform='discord', external_id='d_devlog',
                display_name='D', text='adios',
            )
            replies = await handle(event)

        assert len(replies) == 2
        assert replies[0].text == 'close message'
        assert replies[1].text == 'DEV: [scored a1_greetings=4]'

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_command_dispatch_short_circuits_engine(self):
        """When the incoming text matches a registered command, dispatch
        runs the command handler and does NOT call the engine."""
        from unittest.mock import patch, AsyncMock
        from engine.dispatch import IncomingEvent, handle
        from learner.models import User

        await sync_to_async(User.objects.create)(
            discord_id='d_cmd', display_name='C',
            instruction_language='auto',
        )

        with patch('engine.core.handle_message', new=AsyncMock()) as mock_engine:
            event = IncomingEvent(
                platform='discord', external_id='d_cmd',
                display_name='C', text='!english',
            )
            replies = await handle(event)

        mock_engine.assert_not_called()
        assert len(replies) == 1
        assert 'English' in replies[0].text

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_command_case_insensitive(self):
        """Commands match on lower-cased stripped text."""
        from unittest.mock import patch, AsyncMock
        from engine.dispatch import IncomingEvent, handle
        from learner.models import User

        await sync_to_async(User.objects.create)(
            discord_id='d_case', display_name='C',
        )

        with patch('engine.core.handle_message', new=AsyncMock()) as mock_engine:
            event = IncomingEvent(
                platform='discord', external_id='d_case',
                display_name='C', text='  !ENGLISH  ',
            )
            replies = await handle(event)

        mock_engine.assert_not_called()
        assert len(replies) == 1

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_reset_command_wipes_user_and_sends_first_message(self):
        from engine.dispatch import IncomingEvent, handle
        from engine.onboarding import FIRST_MESSAGE
        from learner.models import User

        original = await sync_to_async(User.objects.create)(
            discord_id='d_reset', display_name='OriginalName',
            estimated_cefr_level='B1', onboarding_complete=True,
        )

        event = IncomingEvent(
            platform='discord', external_id='d_reset',
            display_name='OriginalName', text='!reset',
        )
        replies = await handle(event)

        assert len(replies) == 1
        assert replies[0].text == FIRST_MESSAGE
        # New row with same external_id but reset state.
        new_user = await sync_to_async(User.objects.get)(discord_id='d_reset')
        assert new_user.pk != original.pk
        assert new_user.estimated_cefr_level == ''
        assert new_user.onboarding_complete is False

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_english_command_updates_instruction_language(self):
        from engine.dispatch import IncomingEvent, handle
        from learner.models import User

        await sync_to_async(User.objects.create)(
            discord_id='d_lang', display_name='L',
            instruction_language='spanish',
        )

        event = IncomingEvent(
            platform='discord', external_id='d_lang',
            display_name='L', text='!english',
        )
        replies = await handle(event)

        assert len(replies) == 1
        user = await sync_to_async(User.objects.get)(discord_id='d_lang')
        assert user.instruction_language == 'english'

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_commands_work_uniformly_from_any_platform(self):
        """The whole point: !english typed on Messenger works exactly like
        !english typed on Discord — one implementation, all platforms."""
        from engine.dispatch import IncomingEvent, handle
        from learner.models import User

        await sync_to_async(User.objects.create)(
            messenger_psid='psid_lang', display_name='L',
            instruction_language='spanish',
        )

        event = IncomingEvent(
            platform='messenger', external_id='psid_lang',
            display_name='L', text='!english',
        )
        replies = await handle(event)

        assert len(replies) == 1
        user = await sync_to_async(User.objects.get)(messenger_psid='psid_lang')
        assert user.instruction_language == 'english'

    @pytest.mark.asyncio
    @pytest.mark.django_db(transaction=True)
    async def test_engine_exception_returns_fallback_reply(self):
        """Engine failures must not surface to transport — dispatch wraps
        them into a Reply so the user gets a message either way."""
        from unittest.mock import patch, AsyncMock
        from engine.dispatch import IncomingEvent, handle, FALLBACK_ERROR_TEXT
        from learner.models import User

        await sync_to_async(User.objects.create)(
            discord_id='d_boom', display_name='B',
        )

        with patch('engine.core.handle_message',
                   new=AsyncMock(side_effect=RuntimeError('kaboom'))):
            event = IncomingEvent(
                platform='discord', external_id='d_boom',
                display_name='B', text='hola',
            )
            replies = await handle(event)

        assert len(replies) == 1
        assert replies[0].text == FALLBACK_ERROR_TEXT
