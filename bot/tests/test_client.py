"""Transport tests for the Discord client.

These cover the server-doorway path only. Discord requires a shared guild
before a user may DM a bot, so the landing page's Discord CTA drops people in
the server rather than in a conversation — everything here exists to get them
from that silent room into a DM.
"""
import discord
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from engine.dispatch import Reply


def _dm_message(content='hola', author_id=555, display_name='Eric'):
    message = MagicMock()
    message.author = MagicMock()
    message.author.id = author_id
    message.author.display_name = display_name
    message.content = content
    message.channel = MagicMock(spec=discord.DMChannel)
    message.channel.typing = MagicMock()
    message.channel.typing.return_value.__aenter__ = AsyncMock()
    message.channel.typing.return_value.__aexit__ = AsyncMock()
    return message


def _bot_user(bot_id=999):
    user = MagicMock()
    user.id = bot_id
    return user


class TestJoiningTheServerStartsTheConversation:
    """handle_welcome is dispatch's entry point for a platform's explicit start
    affordance. Accepting the invite is exactly that — there is no user text."""

    @pytest.mark.asyncio
    async def test_join_dms_the_welcome(self):
        from bot.client import on_member_join

        member = MagicMock()
        member.id = 555
        member.display_name = 'Eric'
        member.send = AsyncMock()
        member.bot = False

        with patch('bot.client.dispatch_welcome', new_callable=AsyncMock) as welcome:
            welcome.return_value = [Reply(text='¡Hola! Soy Luz Ángela')]
            await on_member_join(member)

        welcome.assert_awaited_once()
        assert welcome.await_args[0][0] == 'discord'
        assert welcome.await_args[0][1] == '555'
        member.send.assert_awaited()
        assert 'Luz Ángela' in member.send.await_args[0][0]

    @pytest.mark.asyncio
    async def test_a_closed_dm_falls_back_to_the_server(self):
        """Members can block DMs from server members. Silence would strand them
        in the same dead room the join was meant to solve."""
        from bot.client import on_member_join

        member = MagicMock()
        member.id = 555
        member.display_name = 'Eric'
        member.mention = '<@555>'
        member.bot = False
        member.send = AsyncMock(
            side_effect=discord.Forbidden(MagicMock(status=403), 'cannot send')
        )
        member.guild.system_channel.send = AsyncMock()

        with patch('bot.client.dispatch_welcome', new_callable=AsyncMock) as welcome, \
             patch('bot.client.client') as fake_client:
            fake_client.user = _bot_user()
            welcome.return_value = [Reply(text='¡Hola!')]
            await on_member_join(member)

        member.guild.system_channel.send.assert_awaited_once()
        posted = member.guild.system_channel.send.await_args[0][0]
        assert '<@555>' in posted

    @pytest.mark.asyncio
    async def test_other_bots_joining_are_ignored(self):
        from bot.client import on_member_join

        member = MagicMock()
        member.bot = True
        member.send = AsyncMock()

        with patch('bot.client.dispatch_welcome', new_callable=AsyncMock) as welcome:
            await on_member_join(member)

        welcome.assert_not_awaited()
        member.send.assert_not_awaited()


class TestIntents:
    def test_members_intent_is_enabled(self):
        """on_member_join never fires without it — and it also has to be toggled
        on in the Discord developer portal, which no test can assert."""
        from bot.client import intents
        assert intents.members is True


class TestDmsStillWork:
    @pytest.mark.asyncio
    async def test_dm_is_routed_to_the_engine(self):
        from bot.client import on_message

        message = _dm_message(content='anoche fui al gimnasio')

        with patch('bot.client.client') as fake_client, \
             patch('bot.client.dispatch_handle', new_callable=AsyncMock) as handle, \
             patch('bot.client._send', new_callable=AsyncMock) as send:
            fake_client.user = _bot_user()
            handle.return_value = [Reply(text='¡Muy bien!')]
            await on_message(message)

        handle.assert_awaited_once()
        assert handle.await_args[0][0].text == 'anoche fui al gimnasio'
        assert handle.await_args[0][0].platform == 'discord'
        send.assert_awaited()


class TestClosedDmFallbackWithoutASystemChannel:
    """A guild need not have a system channel, and this one did not: production
    had system_channel_id=None while the test below it passed, because the mock
    made member.guild.system_channel truthy. The fallback returned silently and
    the member sat in the server with no idea why nothing happened."""

    def _member(self, system_channel, text_channels):
        member = MagicMock()
        member.id = 555
        member.display_name = 'Eric'
        member.mention = '<@555>'
        member.bot = False
        member.send = AsyncMock(
            side_effect=discord.Forbidden(MagicMock(status=403), 'cannot send'))
        member.guild.system_channel = system_channel
        member.guild.text_channels = text_channels
        return member

    def _channel(self, can_send=True):
        ch = MagicMock()
        ch.send = AsyncMock()
        perms = MagicMock()
        perms.send_messages = can_send
        ch.permissions_for = MagicMock(return_value=perms)
        return ch

    @pytest.mark.asyncio
    async def test_falls_back_to_a_postable_channel(self):
        from bot.client import on_member_join

        first, second = self._channel(can_send=False), self._channel(can_send=True)
        member = self._member(system_channel=None, text_channels=[first, second])

        with patch('bot.client.dispatch_welcome', new_callable=AsyncMock) as welcome, \
             patch('bot.client.client') as fake_client:
            fake_client.user = MagicMock(id=999)
            welcome.return_value = [Reply(text='¡Hola!')]
            await on_member_join(member)

        first.send.assert_not_awaited()
        second.send.assert_awaited_once()
        assert '<@555>' in second.send.await_args[0][0]

    @pytest.mark.asyncio
    async def test_nowhere_to_post_is_logged_not_swallowed(self, engine_caplog):
        """If there is genuinely nowhere to speak, that is worth knowing about
        rather than a silent return."""
        import logging
        from bot.client import on_member_join

        member = self._member(system_channel=None, text_channels=[self._channel(can_send=False)])

        with patch('bot.client.dispatch_welcome', new_callable=AsyncMock) as welcome, \
             patch('bot.client.client') as fake_client, \
             engine_caplog.at_level(logging.WARNING, logger='bot'):
            fake_client.user = MagicMock(id=999)
            welcome.return_value = [Reply(text='¡Hola!')]
            await on_member_join(member)

        assert any('nowhere' in r.getMessage().lower() or 'no channel' in r.getMessage().lower()
                   for r in engine_caplog.records), \
            [r.getMessage() for r in engine_caplog.records]
