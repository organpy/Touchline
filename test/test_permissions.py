"""Tests for the shared admin check."""

from unittest.mock import AsyncMock, Mock, patch

import discord
import pytest
from discord import app_commands

from utils import permissions
from utils.permissions import admin_only, is_admin

ADMIN_ROLE = 555


def _member(*, administrator=False, role_ids=()):
    member = Mock(spec=discord.Member)
    member.guild_permissions = discord.Permissions(administrator=administrator)
    member.roles = [Mock(id=role_id) for role_id in role_ids]
    return member


def _interaction(member):
    interaction = Mock()
    interaction.user = member
    interaction.guild = Mock(id=1)
    interaction.client = Mock()
    return interaction


@pytest.fixture
def config():
    with patch.object(permissions.ConfigUtility, "get_guild_config", AsyncMock(return_value={"admin_role_id": str(ADMIN_ROLE)})) as get:
        yield get


@pytest.mark.asyncio
async def test_server_administrator_is_admin_without_loading_config(config):
    assert await is_admin(_interaction(_member(administrator=True))) is True
    config.assert_not_awaited()


@pytest.mark.asyncio
async def test_member_with_the_admin_role_is_admin(config):
    assert await is_admin(_interaction(_member(role_ids=[1, ADMIN_ROLE]))) is True


@pytest.mark.asyncio
async def test_ordinary_member_is_not_admin(config):
    assert await is_admin(_interaction(_member(role_ids=[1, 2]))) is False


@pytest.mark.asyncio
async def test_manage_server_alone_does_not_make_an_admin(config):
    member = _member()
    member.guild_permissions = discord.Permissions(manage_guild=True)
    assert await is_admin(_interaction(member)) is False


@pytest.mark.asyncio
async def test_no_admin_role_configured_means_only_administrators(config):
    config.return_value = {"admin_role_id": ""}
    assert await is_admin(_interaction(_member(role_ids=[ADMIN_ROLE]))) is False


@pytest.mark.asyncio
async def test_outside_a_server_nobody_is_admin(config):
    interaction = _interaction(Mock(spec=discord.User))
    assert await is_admin(interaction) is False
    interaction.guild = None
    assert await is_admin(interaction) is False


@pytest.mark.asyncio
async def test_admin_only_decorator_checks_is_admin(config):
    @admin_only()
    async def command(interaction):
        pass

    check = command.__discord_app_commands_checks__[0]
    assert await check(_interaction(_member(administrator=True))) is True
    assert await check(_interaction(_member())) is False


@pytest.mark.asyncio
async def test_dashboard_access_hides_the_admin_channel_from_non_admins(config):
    from cogs.dashboard import dashboard

    interaction = _interaction(_member(role_ids=[1]))
    interaction.response.send_message = AsyncMock()
    with patch.object(dashboard.ConfigUtility, "send_permission_denied_message", AsyncMock()) as denied, \
         patch.object(dashboard.ConfigUtility, "check_admin_channel_restriction", AsyncMock()) as channel:
        assert await dashboard.check_dashboard_access(Mock(), interaction) is False
    denied.assert_awaited_once()
    channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_dashboard_access_still_enforces_the_admin_channel_for_admins(config):
    from cogs.dashboard import dashboard

    interaction = _interaction(_member(administrator=True))
    interaction.response.send_message = AsyncMock()
    wrong_channel = (False, discord.Embed(title="Wrong Channel"))
    with patch.object(dashboard.ConfigUtility, "check_admin_channel_restriction", AsyncMock(return_value=wrong_channel)):
        assert await dashboard.check_dashboard_access(Mock(), interaction) is False
    assert interaction.response.send_message.await_args.kwargs["embed"].title == "Wrong Channel"
