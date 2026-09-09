"""Tests for match-result reporting access (team owners and admins only)."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from cogs.score import access as access_module
from cogs.score.access import NOT_ALLOWED_MESSAGE, ReporterAccess, resolve_reporter_access


def _make_interaction(*, administrator: bool = False, role_ids=(), in_guild: bool = True):
    interaction = Mock()
    interaction.guild = Mock(id=987654321) if in_guild else None
    interaction.user = Mock(id=42)
    interaction.user.guild_permissions = Mock(administrator=administrator)
    interaction.user.roles = [Mock(id=role_id) for role_id in role_ids]
    interaction.response = Mock()
    interaction.response.send_message = AsyncMock()
    return interaction


def _patch_config(config=None):
    return patch.object(
        access_module.ConfigUtility, "get_guild_config", AsyncMock(return_value=config or {})
    )


def _patch_owned_teams(teams):
    return patch.object(access_module.queries, "get_teams_by_owner", AsyncMock(return_value=teams))


class TestReporterAccess:
    def test_admin_can_report_any_match(self):
        assert ReporterAccess(is_admin=True).can_report("a", "b")

    def test_owner_can_report_own_match_from_either_side(self):
        access = ReporterAccess(is_admin=False, owned_team_ids=frozenset({"mine"}))
        assert access.can_report("mine", "other")
        assert access.can_report("other", "mine")

    def test_owner_cannot_report_other_teams_match(self):
        access = ReporterAccess(is_admin=False, owned_team_ids=frozenset({"mine"}))
        assert not access.can_report("other1", "other2")


class TestResolveReporterAccess:
    @pytest.mark.asyncio
    async def test_admin_permission_grants_admin_access(self):
        with _patch_config(), _patch_owned_teams([]):
            access = await resolve_reporter_access(Mock(), Mock(), _make_interaction(administrator=True))
        assert access == ReporterAccess(is_admin=True)

    @pytest.mark.asyncio
    async def test_configured_admin_role_grants_admin_access(self):
        with _patch_config({"admin_role_id": "777"}), _patch_owned_teams([]):
            access = await resolve_reporter_access(Mock(), Mock(), _make_interaction(role_ids=(777,)))
        assert access is not None and access.is_admin

    @pytest.mark.asyncio
    async def test_team_owner_gets_owned_teams(self):
        with _patch_config(), _patch_owned_teams([{"id": "t1"}, {"id": "t2"}]):
            access = await resolve_reporter_access(Mock(), Mock(), _make_interaction())
        assert access == ReporterAccess(is_admin=False, owned_team_ids=frozenset({"t1", "t2"}))

    @pytest.mark.asyncio
    async def test_regular_member_is_denied(self):
        with _patch_config(), _patch_owned_teams([]):
            access = await resolve_reporter_access(Mock(), Mock(), _make_interaction())
        assert access is None

    @pytest.mark.asyncio
    async def test_dm_is_denied(self):
        access = await resolve_reporter_access(Mock(), Mock(), _make_interaction(in_guild=False))
        assert access is None


class TestReportCommandsAreGated:
    """Every report command must refuse regular members before starting the flow."""

    @pytest.fixture
    def cogs(self):
        from cogs.score import bulk_stats, stat_registration

        with patch.object(bulk_stats, "get_supabase", return_value=Mock()), patch.object(
            stat_registration, "get_supabase", return_value=Mock()
        ):
            yield {
                "report-stats-bulk": (bulk_stats.BulkStatsCog(Mock()), bulk_stats.BulkStatsCog.report_stats_bulk),
                "report-score": (bulk_stats.BulkStatsCog(Mock()), bulk_stats.BulkStatsCog.report_score),
                "report-stats": (
                    stat_registration.StatRegistrationCog(Mock()),
                    stat_registration.StatRegistrationCog.report_stats,
                ),
            }

    @pytest.mark.asyncio
    @pytest.mark.parametrize("command_name", ["report-stats-bulk", "report-score", "report-stats"])
    async def test_member_is_refused(self, cogs, command_name):
        cog, command = cogs[command_name]
        interaction = _make_interaction()

        with _patch_config(), _patch_owned_teams([]):
            await command.callback(cog, interaction)

        interaction.response.send_message.assert_awaited_once_with(NOT_ALLOWED_MESSAGE, ephemeral=True)
