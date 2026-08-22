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


def _guild_message(content='hola', author_id=555, display_name='Eric'):
    message = MagicMock()
    message.author = MagicMock()
    message.author.id = author_id
    message.author.display_name = display_name
    message.content = content
    message.channel = MagicMock(spec=discord.TextChannel)
    message.channel.send = AsyncMock()
    return message


def _bot_user(bot_id=999):
    user = MagicMock()
    user.id = bot_id
    return user


class TestGuildMessagesAreRedirected:
    """A new arrival types in the server because that is the only room they can
    see. Before this, on_message returned early and they got silence."""

    @pytest.mark.asyncio
    async def test_guild_message_gets_a_pointer_to_dms(self):
        from bot.client import on_message, _guild_redirected

        _guild_redirected.clear()
        message = _guild_message()

        with patch('bot.client.client') as fake_client:
            fake_client.user = _bot_user()
            await on_message(message)

        assert message.channel.send.await_count == 1
        sent = message.channel.send.await_args[0][0]
        assert 'Luz' in sent
        # The bot's own profile link is the shortest path to a DM.
        assert 'discord.com/users/999' in sent

    @pytest.mark.asyncio
    async def test_guild_message_never_reaches_the_engine(self):
        """Server chatter must not be scored as a lesson answer."""
        from bot.client import on_message, _guild_redirected

        _guild_redirected.clear()
        message = _guild_message()

        with patch('bot.client.client') as fake_client, \
             patch('bot.client.dispatch_handle', new_callable=AsyncMock) as handle:
            fake_client.user = _bot_user()
            await on_message(message)

        handle.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_same_person_is_only_redirected_once(self):
        """The pointer is a signpost, not a greeter that shouts at every message."""
        from bot.client import on_message, _guild_redirected

        _guild_redirected.clear()
        first = _guild_message(content='hola')
        second = _guild_message(content='anyone here?')

        with patch('bot.client.client') as fake_client:
            fake_client.user = _bot_user()
            await on_message(first)
            await on_message(second)

        assert first.channel.send.await_count == 1
        assert second.channel.send.await_count == 0

    @pytest.mark.asyncio
    async def test_the_bots_own_guild_messages_are_ignored(self):
        from bot.client import on_message, _guild_redirected

        _guild_redirected.clear()
        message = _guild_message()
        bot_user = _bot_user()
        message.author = bot_user

        with patch('bot.client.client') as fake_client:
            fake_client.user = bot_user
            await on_message(message)

        assert message.channel.send.await_count == 0


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
            welcome.return_value = [Reply(text='¡Hola! Soy Luz Angela')]
            await on_member_join(member)

        welcome.assert_awaited_once()
        assert welcome.await_args[0][0] == 'discord'
        assert welcome.await_args[0][1] == '555'
        member.send.assert_awaited()
        assert 'Luz Angela' in member.send.await_args[0][0]

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
        from bot.client import on_message, _guild_redirected

        _guild_redirected.clear()
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
