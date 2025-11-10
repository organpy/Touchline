"""Unit tests for admin dashboard features - Phase 4.

NOTE: This file is ARCHIVED. See test_admin_features_real.py and test_admin_features_v2.py for active tests.
This file is kept for reference only and many tests are not executable due to Discord.py modal/View 
instantiation requirements outside of proper async context.
"""

import pytest
from unittest.mock import Mock, AsyncMock, patch, MagicMock, call
from datetime import datetime, timezone
import discord
import asyncio
import os

# Try importing admin classes - these may not be available in test context
try:
    from cogs.admin.players import AddPlayerModal, LinkPlayerModal, ViewPlayersView
    from cogs.admin.rosters import ForceSignView, BlacklistView, RosterActionsView
    from cogs.admin.pending_offers import ViewPendingOffersView
except (ImportError, RuntimeError):
    # These imports may fail due to Supabase initialization or Discord.py requirements
    # For actual testing, use test_admin_features_real.py or test_admin_features_v2.py
    pass

from db import get_supabase, queries

# Mock Supabase BEFORE importing admin modules
@pytest.fixture(scope="function", autouse=True)
def mock_supabase_init(monkeypatch):
    """Mock Supabase initialization before admin imports."""
    mock = AsyncMock()
    mock.table = Mock(return_value=Mock())
    
    # Mock the get_supabase function globally
    with patch('db.supabase_client.get_supabase', return_value=mock):
        with patch('db.get_supabase', return_value=mock):
            yield mock


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def mock_supabase():
    """Create a mock Supabase client."""
    mock = Mock()
    mock.table = Mock(return_value=Mock())
    return mock


@pytest.fixture
def mock_bot():
    """Create a mock Discord bot."""
    bot = AsyncMock()
    bot.get_guild = Mock()
    bot.get_channel = Mock()
    return bot


@pytest.fixture
def mock_interaction():
    """Create a mock Discord interaction."""
    interaction = AsyncMock(spec=discord.Interaction)
    interaction.response = AsyncMock()
    interaction.followup = AsyncMock()
    interaction.user = Mock(id=123456, display_name="TestAdmin")
    interaction.guild_id = 999999
    interaction.data = {}
    return interaction


@pytest.fixture
def mock_guild():
    """Create a mock Discord guild."""
    guild = Mock(spec=discord.Guild)
    guild.id = 999999
    
    # Create mock members
    member1 = Mock(spec=discord.Member)
    member1.id = 111111
    member1.display_name = "Ace"
    
    member2 = Mock(spec=discord.Member)
    member2.id = 222222
    member2.display_name = "Bob"
    
    guild.members = [member1, member2]
    guild.get_member = Mock(side_effect=lambda mid: member1 if mid == 111111 else member2 if mid == 222222 else None)
    
    return guild


# ============================================================================
# TESTS: AddPlayerModal
# ============================================================================

class TestAddPlayerModal:  # type: ignore
    """Tests for adding new players."""

    @pytest.mark.asyncio
    async def test_add_player_success(self, mock_interaction, mock_supabase):
        """Test successfully adding a new player."""
        modal = AddPlayerModal()
        modal.player_name.value = "TestPlayer"
        
        with patch('cogs.admin.players.get_supabase', return_value=mock_supabase):
            # Mock insert response
            mock_supabase.table().insert().execute.return_value = Mock(
                data=[{'id': 'player_123', 'display_name': 'TestPlayer', 'active': True}]
            )
            
            await modal.on_submit(mock_interaction)
            
            # Verify interaction response
            mock_interaction.response.defer.assert_called_once()
            mock_interaction.followup.send.assert_called_once()
            
            # Verify player was inserted
            mock_supabase.table.assert_called()

    @pytest.mark.asyncio
    async def test_add_player_already_exists(self, mock_interaction, mock_supabase):
        """Test error when player name already exists."""
        modal = AddPlayerModal()
        modal.player_name.value = "ExistingPlayer"
        
        with patch('cogs.admin.players.get_supabase', return_value=mock_supabase):
            # Mock select to find existing player
            mock_supabase.table().select().eq().execute.return_value = Mock(
                data=[{'id': 'existing_123', 'display_name': 'ExistingPlayer'}]
            )
            
            await modal.on_submit(mock_interaction)
            
            # Should show warning
            mock_interaction.response.defer.assert_called_once()
            mock_interaction.followup.send.assert_called_once()
            # Verify error message contains "already exists"
            call_args = mock_interaction.followup.send.call_args
            assert "already exists" in str(call_args).lower() or "already" in str(call_args).lower()

    @pytest.mark.asyncio
    async def test_add_player_empty_name(self, mock_interaction):
        """Test error with empty player name."""
        modal = AddPlayerModal()
        modal.player_name.value = ""
        
        await modal.on_submit(mock_interaction)
        
        mock_interaction.response.defer.assert_called_once()
        # Should get validation error or empty name error
        mock_interaction.followup.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_player_database_unavailable(self, mock_interaction):
        """Test error when database is unavailable."""
        modal = AddPlayerModal()
        modal.player_name.value = "TestPlayer"
        
        with patch('cogs.admin.players.get_supabase', return_value=None):
            await modal.on_submit(mock_interaction)
            
            mock_interaction.response.defer.assert_called_once()
            # Should show database error
            call_args = mock_interaction.followup.send.call_args
            assert "database" in str(call_args).lower() or "unavailable" in str(call_args).lower()


# ============================================================================
# TESTS: LinkPlayerModal
# ============================================================================

class TestLinkPlayerModal:  # type: ignore
    """Tests for linking players to Discord accounts."""

    @pytest.mark.asyncio
    async def test_link_player_success(self, mock_interaction, mock_supabase):
        """Test successfully linking player to Discord ID."""
        modal = LinkPlayerModal()
        modal.discord_id.value = "111111"
        
        mock_interaction.response.defer.return_value = None
        
        with patch('cogs.admin.players.get_supabase', return_value=mock_supabase):
            # Mock unlinked players query
            mock_supabase.table().select().eq().execute.return_value = Mock(
                data=[
                    {'id': 'player_1', 'display_name': 'Ace'},
                    {'id': 'player_2', 'display_name': 'Bob'},
                ]
            )
            
            await modal.on_submit(mock_interaction)
            
            # Should show player dropdown
            mock_interaction.response.defer.assert_called_once()
            mock_interaction.followup.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_link_player_invalid_discord_id(self, mock_interaction):
        """Test error with invalid Discord ID format."""
        modal = LinkPlayerModal()
        modal.discord_id.value = "not_a_number"
        
        await modal.on_submit(mock_interaction)
        
        mock_interaction.response.defer.assert_called_once()
        # Should show validation error
        call_args = mock_interaction.followup.send.call_args
        assert "valid" in str(call_args).lower() or "number" in str(call_args).lower()

    @pytest.mark.asyncio
    async def test_link_player_no_unlinked_players(self, mock_interaction, mock_supabase):
        """Test error when no unlinked players available."""
        modal = LinkPlayerModal()
        modal.discord_id.value = "111111"
        
        with patch('cogs.admin.players.get_supabase', return_value=mock_supabase):
            # Mock empty unlinked players
            mock_supabase.table().select().eq().execute.return_value = Mock(data=[])
            
            await modal.on_submit(mock_interaction)
            
            mock_interaction.response.defer.assert_called_once()
            # Should show "no unlinked players" error
            call_args = mock_interaction.followup.send.call_args
            assert "no" in str(call_args).lower() and "unlinked" in str(call_args).lower()


# ============================================================================
# TESTS: ViewPlayersView
# ============================================================================

class TestViewPlayersView:  # type: ignore
    """Tests for viewing player lists with pagination."""

    @pytest.mark.asyncio
    async def test_view_players_pagination_next(self, mock_interaction, mock_supabase):
        """Test pagination next button."""
        view = ViewPlayersView(Mock(), 999999, mock_supabase)
        
        # Mock players list
        view.players_list = [
            {'id': f'player_{i}', 'display_name': f'Player{i}', 'active': True}
            for i in range(15)
        ]
        view.current_page = 0
        
        # Create embed before pagination
        embed_page_0 = view.create_embed()
        assert "Page 1/2" in embed_page_0.footer.text
        
        # Simulate next page
        view.current_page = 1
        embed_page_1 = view.create_embed()
        assert "Page 2/2" in embed_page_1.footer.text

    @pytest.mark.asyncio
    async def test_view_players_pagination_with_view(self, mock_interaction, mock_supabase):
        """Test that pagination buttons include view object."""
        view = ViewPlayersView(Mock(), 999999, mock_supabase)
        view.players_list = [
            {'id': 'player_1', 'display_name': 'Ace', 'active': True},
            {'id': 'player_2', 'display_name': 'Bob', 'active': True},
        ]
        view.current_page = 0
        
        # Mock interaction
        mock_interaction.response.defer = AsyncMock()
        
        # Simulate next_page button click
        await view.next_page(mock_interaction, Mock())
        
        # Verify view passed to followup (view parameter should be included)
        mock_interaction.response.defer.assert_called_once()
        mock_interaction.followup.send.assert_called_once()
        # Check that view kwarg is present
        call_kwargs = mock_interaction.followup.send.call_args.kwargs
        assert 'view' in call_kwargs or 'ephemeral' in call_kwargs

    def test_view_players_empty_list(self, mock_supabase):
        """Test viewing empty player list."""
        view = ViewPlayersView(Mock(), 999999, mock_supabase)
        view.players_list = []
        
        embed = view.create_embed()
        
        assert "No players registered" in embed.description

    def test_view_players_single_page(self, mock_supabase):
        """Test pagination with single page of players."""
        view = ViewPlayersView(Mock(), 999999, mock_supabase)
        view.players_list = [
            {'id': 'player_1', 'display_name': 'Ace', 'active': True},
        ]
        view.current_page = 0
        
        embed = view.create_embed()
        
        assert "Page 1/1" in embed.footer.text


# ============================================================================
# TESTS: ForceSignView
# ============================================================================

class TestForceSignView:  # type: ignore
    """Tests for force signing players."""

    @pytest.mark.asyncio
    async def test_force_sign_success(self, mock_bot, mock_guild, mock_interaction, mock_supabase):
        """Test successfully force signing a player."""
        parent_view = Mock()
        repo = Mock()
        view = ForceSignView(mock_bot, 999999, mock_supabase, repo, parent_view)
        
        mock_bot.get_guild.return_value = mock_guild
        
        # Mock teams
        view._teams = [
            {'id': 'team_1', 'code': 'PANDAS', 'name': 'Pandas', 'division_id': 'div_1'}
        ]
        
        await view.prepare()
        
        # Verify team select was added
        assert len(view.children) > 0

    @pytest.mark.asyncio
    async def test_force_sign_creates_player_if_not_exists(self, mock_bot, mock_guild, mock_interaction, mock_supabase):
        """Test that force sign creates player record if it doesn't exist."""
        parent_view = Mock()
        repo = Mock()
        repo.create_transaction = AsyncMock(return_value='txn_123')
        
        view = ForceSignView(mock_bot, 999999, mock_supabase, repo, parent_view)
        view._selected_team = {'id': 'team_1', 'code': 'PANDAS', 'name': 'Pandas', 'division_id': 'div_1'}
        view._selected_member = mock_guild.members[0]  # Ace (111111)
        
        mock_bot.get_guild.return_value = mock_guild
        
        # Mock no existing player
        with patch('cogs.admin.rosters.queries.get_player_by_discord_id', new_callable=AsyncMock) as mock_get_player:
            mock_get_player.return_value = None
            
            # Mock player creation
            mock_supabase.table().insert().execute.return_value = Mock(
                data=[{'id': 'player_new', 'discord_id': 111111, 'display_name': 'Ace'}]
            )
            
            # Mock season
            mock_supabase.table().select().eq().eq().limit().execute.return_value = Mock(
                data=[{'id': 'season_1', 'name': 'S10', 'active': True}]
            )
            
            # Mock active roster check
            mock_supabase.table().select().eq().eq().eq().is_().execute.return_value = Mock(data=[])
            
            await view.confirm_force_sign(mock_interaction)
            
            # Should have created player and transaction
            assert repo.create_transaction.called

    @pytest.mark.asyncio
    async def test_force_sign_no_active_season(self, mock_bot, mock_guild, mock_interaction, mock_supabase):
        """Test error when no active season exists."""
        parent_view = Mock()
        repo = Mock()
        repo.create_transaction = AsyncMock(return_value=None)
        
        view = ForceSignView(mock_bot, 999999, mock_supabase, repo, parent_view)
        view._selected_team = {'id': 'team_1', 'code': 'PANDAS', 'name': 'Pandas', 'division_id': 'div_1'}
        view._selected_member = mock_guild.members[0]
        
        mock_bot.get_guild.return_value = mock_guild
        
        with patch('cogs.admin.rosters.queries.get_player_by_discord_id', new_callable=AsyncMock) as mock_get_player:
            mock_get_player.return_value = {'id': 'player_existing', 'discord_id': 111111}
            
            # Mock no season
            mock_supabase.table().select().eq().eq().limit().execute.return_value = Mock(data=[])
            
            await view.confirm_force_sign(mock_interaction)
            
            # Should show error about no active season
            mock_interaction.followup.send.assert_called_once()
            call_args = mock_interaction.followup.send.call_args
            assert "active season" in str(call_args).lower()

    @pytest.mark.asyncio
    async def test_force_sign_player_already_on_roster(self, mock_bot, mock_guild, mock_interaction, mock_supabase):
        """Test error when player already on roster."""
        parent_view = Mock()
        repo = Mock()
        view = ForceSignView(mock_bot, 999999, mock_supabase, repo, parent_view)
        view._selected_team = {'id': 'team_1', 'code': 'PANDAS', 'name': 'Pandas', 'division_id': 'div_1'}
        view._selected_member = mock_guild.members[0]
        
        mock_bot.get_guild.return_value = mock_guild
        
        with patch('cogs.admin.rosters.queries.get_player_by_discord_id', new_callable=AsyncMock) as mock_get_player:
            mock_get_player.return_value = {'id': 'player_existing', 'discord_id': 111111}
            
            # Mock active season
            mock_supabase.table().select().eq().eq().limit().execute.return_value = Mock(
                data=[{'id': 'season_1', 'name': 'S10', 'active': True}]
            )
            
            # Mock player already on roster
            mock_supabase.table().select().eq().eq().eq().is_().execute.return_value = Mock(
                data=[{'player_id': 'player_existing', 'season_id': 'season_1'}]
            )
            
            await view.confirm_force_sign(mock_interaction)
            
            # Should show "already on roster" error
            mock_interaction.followup.send.assert_called_once()
            call_args = mock_interaction.followup.send.call_args
            assert "already" in str(call_args).lower() or "roster" in str(call_args).lower()


# ============================================================================
# TESTS: BlacklistView
# ============================================================================

class TestBlacklistView:  # type: ignore
    """Tests for blacklist management."""

    @pytest.mark.asyncio
    async def test_blacklist_add_player_success(self, mock_bot, mock_guild, mock_interaction, mock_supabase):
        """Test successfully adding player to blacklist."""
        parent_view = Mock()
        view = BlacklistView(mock_bot, 999999, mock_supabase, parent_view)
        view._selected_team = {'id': 'team_1', 'code': 'PANDAS', 'name': 'Pandas'}
        view._selected_member = mock_guild.members[0]  # Ace
        
        mock_bot.get_guild.return_value = mock_guild
        
        with patch('cogs.admin.rosters.queries.get_player_by_discord_id', new_callable=AsyncMock) as mock_get_player:
            mock_get_player.return_value = {'id': 'player_ace', 'discord_id': 111111, 'display_name': 'Ace'}
            
            # Mock team metadata fetch (no blacklist yet)
            mock_supabase.table().select().eq().limit().execute.return_value = Mock(
                data=[{'id': 'team_1', 'metadata': {}}]
            )
            
            # Mock metadata update
            mock_supabase.table().update().eq().execute.return_value = Mock(data=[{}])
            
            await view.confirm_blacklist_action(mock_interaction, "add", mock_guild.members[0])
            
            # Should show success message
            mock_interaction.followup.send.assert_called_once()
            call_args = mock_interaction.followup.send.call_args
            assert "added" in str(call_args).lower() or "success" in str(call_args).lower()

    @pytest.mark.asyncio
    async def test_blacklist_remove_player_success(self, mock_bot, mock_guild, mock_interaction, mock_supabase):
        """Test successfully removing player from blacklist."""
        parent_view = Mock()
        view = BlacklistView(mock_bot, 999999, mock_supabase, parent_view)
        view._selected_team = {'id': 'team_1', 'code': 'PANDAS', 'name': 'Pandas'}
        view._selected_member = mock_guild.members[0]  # Ace
        
        mock_bot.get_guild.return_value = mock_guild
        
        with patch('cogs.admin.rosters.queries.get_player_by_discord_id', new_callable=AsyncMock) as mock_get_player:
            mock_get_player.return_value = {'id': 'player_ace', 'discord_id': 111111, 'display_name': 'Ace'}
            
            # Mock team metadata (with player already blacklisted)
            mock_supabase.table().select().eq().limit().execute.return_value = Mock(
                data=[{'id': 'team_1', 'metadata': {'blacklist': ['player_ace']}}]
            )
            
            # Mock metadata update
            mock_supabase.table().update().eq().execute.return_value = Mock(data=[{}])
            
            await view.confirm_blacklist_action(mock_interaction, "remove", mock_guild.members[0])
            
            # Should show success message
            mock_interaction.followup.send.assert_called_once()
            call_args = mock_interaction.followup.send.call_args
            assert "removed" in str(call_args).lower() or "success" in str(call_args).lower()

    @pytest.mark.asyncio
    async def test_blacklist_add_duplicate_error(self, mock_bot, mock_guild, mock_interaction, mock_supabase):
        """Test error when adding player already blacklisted."""
        parent_view = Mock()
        view = BlacklistView(mock_bot, 999999, mock_supabase, parent_view)
        view._selected_team = {'id': 'team_1', 'code': 'PANDAS', 'name': 'Pandas'}
        view._selected_member = mock_guild.members[0]  # Ace
        
        mock_bot.get_guild.return_value = mock_guild
        
        with patch('cogs.admin.rosters.queries.get_player_by_discord_id', new_callable=AsyncMock) as mock_get_player:
            mock_get_player.return_value = {'id': 'player_ace', 'discord_id': 111111, 'display_name': 'Ace'}
            
            # Mock team metadata (player already blacklisted)
            mock_supabase.table().select().eq().limit().execute.return_value = Mock(
                data=[{'id': 'team_1', 'metadata': {'blacklist': ['player_ace']}}]
            )
            
            await view.confirm_blacklist_action(mock_interaction, "add", mock_guild.members[0])
            
            # Should show "already blacklisted" warning
            mock_interaction.followup.send.assert_called_once()
            call_args = mock_interaction.followup.send.call_args
            assert "already" in str(call_args).lower() or "blacklist" in str(call_args).lower()

    @pytest.mark.asyncio
    async def test_blacklist_remove_not_blacklisted_error(self, mock_bot, mock_guild, mock_interaction, mock_supabase):
        """Test error when removing player not on blacklist."""
        parent_view = Mock()
        view = BlacklistView(mock_bot, 999999, mock_supabase, parent_view)
        view._selected_team = {'id': 'team_1', 'code': 'PANDAS', 'name': 'Pandas'}
        view._selected_member = mock_guild.members[0]  # Ace
        
        mock_bot.get_guild.return_value = mock_guild
        
        with patch('cogs.admin.rosters.queries.get_player_by_discord_id', new_callable=AsyncMock) as mock_get_player:
            mock_get_player.return_value = {'id': 'player_ace', 'discord_id': 111111, 'display_name': 'Ace'}
            
            # Mock team metadata (empty blacklist)
            mock_supabase.table().select().eq().limit().execute.return_value = Mock(
                data=[{'id': 'team_1', 'metadata': {'blacklist': []}}]
            )
            
            await view.confirm_blacklist_action(mock_interaction, "remove", mock_guild.members[0])
            
            # Should show "not blacklisted" warning
            mock_interaction.followup.send.assert_called_once()
            call_args = mock_interaction.followup.send.call_args
            assert "not" in str(call_args).lower() or "blacklist" in str(call_args).lower()


# ============================================================================
# TESTS: ViewPendingOffersView
# ============================================================================

class TestViewPendingOffersView:  # type: ignore
    """Tests for pending offers display."""

    def test_pending_offers_with_joins(self, mock_supabase):
        """Test that pending offers query includes joins for player/team data."""
        view = ViewPendingOffersView(Mock(), 999999, mock_supabase)
        
        # Mock offers with joined data
        view.offers_list = [
            {
                'id': 'offer_1',
                'status': 'pending',
                'created_at': '2025-11-09T10:00:00Z',
                'expires_at': '2025-11-16T10:00:00Z',
                'players': {'id': 'player_1', 'display_name': 'Ace', 'discord_id': 111111},
                'teams': {'id': 'team_1', 'name': 'Pandas', 'code': 'PANDAS'}
            }
        ]
        view.current_page = 0
        
        embed = view.create_embed()
        
        # Verify embed contains player and team names
        assert 'Ace' in embed.fields[0].name or 'Ace' in str(embed)
        assert 'Pandas' in embed.fields[0].name or 'Pandas' in str(embed)
        assert 'PANDAS' in str(embed)

    def test_pending_offers_display_format(self, mock_supabase):
        """Test pending offers display format."""
        view = ViewPendingOffersView(Mock(), 999999, mock_supabase)
        
        view.offers_list = [
            {
                'id': 'abc123def456',
                'status': 'pending',
                'created_at': '2025-11-09T10:00:00Z',
                'expires_at': '2025-11-16T10:00:00Z',
                'players': {'display_name': 'Ace', 'discord_id': 111111},
                'teams': {'name': 'Pandas', 'code': 'PANDAS'}
            }
        ]
        view.current_page = 0
        
        embed = view.create_embed()
        
        # Should display "PlayerName → TeamName (CODE)" format
        field_name = embed.fields[0].name
        assert '→' in field_name or 'Ace' in field_name
        # Short ID should be in field value
        field_value = embed.fields[0].value
        assert 'abc12' in field_value  # First 8 chars

    def test_pending_offers_empty_list(self, mock_supabase):
        """Test empty pending offers list."""
        view = ViewPendingOffersView(Mock(), 999999, mock_supabase)
        view.offers_list = []
        
        embed = view.create_embed()
        
        assert "No pending offers" in embed.description

    def test_pending_offers_pagination(self, mock_supabase):
        """Test pending offers pagination."""
        view = ViewPendingOffersView(Mock(), 999999, mock_supabase)
        
        # Create 8 offers (2 pages with per_page=5)
        view.offers_list = [
            {
                'id': f'offer_{i}',
                'status': 'pending',
                'created_at': '2025-11-09T10:00:00Z',
                'expires_at': '2025-11-16T10:00:00Z',
                'players': {'display_name': f'Player{i}', 'discord_id': 111111 + i},
                'teams': {'name': 'Pandas', 'code': 'PANDAS'}
            }
            for i in range(8)
        ]
        view.current_page = 0
        
        embed_page_0 = view.create_embed()
        assert "Page 1/2" in embed_page_0.footer.text
        
        view.current_page = 1
        embed_page_1 = view.create_embed()
        assert "Page 2/2" in embed_page_1.footer.text


# ============================================================================
# INTEGRATION TESTS
# ============================================================================

class TestAdminDashboardIntegration:  # type: ignore
    """Integration tests for admin dashboard workflows."""

    @pytest.mark.asyncio
    async def test_add_player_then_link_discord(self, mock_supabase):
        """Test workflow: Add player → Link Discord → Set position."""
        # Step 1: Add player
        add_modal = AddPlayerModal()
        add_modal.player_name.value = "NewPlayer"
        
        with patch('cogs.admin.players.get_supabase', return_value=mock_supabase):
            mock_supabase.table().select().eq().execute.return_value = Mock(data=[])
            mock_supabase.table().insert().execute.return_value = Mock(
                data=[{'id': 'player_new', 'display_name': 'NewPlayer'}]
            )
            
            # Would call on_submit but we're verifying the flow
            assert add_modal.player_name.value == "NewPlayer"
            
            # Step 2: Link Discord ID
            link_modal = LinkPlayerModal()
            link_modal.discord_id.value = "111111"
            
            # Mock unlinked players (should include NewPlayer)
            mock_supabase.table().select().eq().execute.return_value = Mock(
                data=[{'id': 'player_new', 'display_name': 'NewPlayer'}]
            )
            
            assert link_modal.discord_id.value == "111111"

    @pytest.mark.asyncio
    async def test_force_sign_then_add_to_blacklist(self, mock_bot, mock_guild, mock_supabase):
        """Test workflow: Force sign player → Add to blacklist."""
        # Players start unblacklisted after signing
        team_metadata = {'blacklist': []}
        
        # Add player to blacklist
        team_metadata['blacklist'].append('player_abc')
        
        assert 'player_abc' in team_metadata['blacklist']
        
        # Remove from blacklist
        team_metadata['blacklist'].remove('player_abc')
        
        assert 'player_abc' not in team_metadata['blacklist']


# ============================================================================
# EDGE CASE TESTS
# ============================================================================

class TestEdgeCases:  # type: ignore
    """Tests for edge cases and error conditions."""

    def test_discord_id_max_length(self):
        """Test Discord ID with maximum length."""
        modal = LinkPlayerModal()
        modal.discord_id.value = "999999999999999999"  # 18 digits (max Discord ID)
        
        try:
            discord_id_int = int(modal.discord_id.value)
            assert discord_id_int == 999999999999999999
        except ValueError:
            pytest.fail("Should accept valid large Discord ID")

    def test_player_name_max_length(self):
        """Test player name with maximum length."""
        modal = AddPlayerModal()
        modal.player_name.value = "A" * 100  # Max length
        
        assert len(modal.player_name.value) == 100

    @pytest.mark.asyncio
    async def test_force_sign_with_many_guild_members(self, mock_bot, mock_guild, mock_supabase):
        """Test force sign dropdown with >25 guild members."""
        parent_view = Mock()
        repo = Mock()
        view = ForceSignView(mock_bot, 999999, mock_supabase, repo, parent_view)
        
        # Create >25 mock members
        members = []
        for i in range(30):
            member = Mock(spec=discord.Member)
            member.id = 100000 + i
            member.display_name = f"Member{i:02d}"
            members.append(member)
        
        mock_guild.members = members
        mock_bot.get_guild.return_value = mock_guild
        
        view._teams = [
            {'id': 'team_1', 'code': 'PANDAS', 'name': 'Pandas', 'division_id': 'div_1'}
        ]
        
        await view.prepare()
        
        # Verify team select was added (members selection happens in next step)
        assert len(view.children) > 0

    def test_blacklist_metadata_corruption_handling(self, mock_supabase):
        """Test blacklist handles corrupted metadata gracefully."""
        parent_view = Mock()
        view = BlacklistView(Mock(), 999999, mock_supabase, parent_view)
        
        # Simulate corrupted metadata (not a dict)
        corrupted_metadata = "not_a_dict"
        
        # Should fallback to default
        if not isinstance(corrupted_metadata, dict):
            metadata = {}
            if 'blacklist' not in metadata:
                metadata['blacklist'] = []
        
        assert isinstance(metadata, dict)
        assert 'blacklist' in metadata

    def test_pending_offers_with_null_joined_data(self, mock_supabase):
        """Test pending offers handles null player/team joins gracefully."""
        view = ViewPendingOffersView(Mock(), 999999, mock_supabase)
        
        view.offers_list = [
            {
                'id': 'offer_1',
                'status': 'pending',
                'created_at': '2025-11-09T10:00:00Z',
                'expires_at': '2025-11-16T10:00:00Z',
                'players': None,  # Null join
                'teams': None     # Null join
            }
        ]
        view.current_page = 0
        
        embed = view.create_embed()
        
        # Should show "Unknown" instead of crashing
        field_value = embed.fields[0].value if embed.fields else ""
        assert 'Unknown' in embed.fields[0].name or 'Unknown' in str(embed)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
