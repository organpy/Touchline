import logging
import re
from datetime import datetime
from typing import Optional

import discord
from discord import ui

from db import get_supabase
from db.transactions_repository import TransactionsRepository
from utils.team_player_cache import TeamPlayerCache
from utils.embed_builder import EmbedBuilder
from cogs.score.roster_cache import RosterCache

logger = logging.getLogger(__name__)

class SigningAcceptButton(ui.DynamicItem[ui.Button], template=r'signing:accept:(?P<id>.+)'):
    def __init__(self, signing_id: str):
        super().__init__(
            ui.Button(
                label="Accept Offer",
                style=discord.ButtonStyle.success,
                emoji="✅",
                custom_id=f"signing:accept:{signing_id}",
            )
        )
        self.signing_id = signing_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button, match: re.Match, /):
        return cls(match['id'])

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        supabase = get_supabase()
        repo = TransactionsRepository(supabase)
        
        # Fetch signing
        signing = repo.get_pending_signing(self.signing_id)
        if not signing:
            await interaction.followup.send("❌ Signing offer not found.", ephemeral=True)
            return

        # Check user
        player_record = supabase.table('players').select('*').eq('id', signing['player_id']).single().execute()
        if not player_record.data:
            await interaction.followup.send("❌ Player record not found.", ephemeral=True)
            return
        
        if interaction.user.id != player_record.data['discord_id']:
            await interaction.followup.send("❌ Only the targeted player can respond to this offer.", ephemeral=True)
            return

        if signing['status'] != 'pending':
            await interaction.followup.send(f"⚠️ This offer is already {signing['status']}.", ephemeral=True)
            return

        # Check expiry
        expires_at = signing.get('expires_at')
        if expires_at:
            try:
                expiry_dt = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
                current = datetime.now(expiry_dt.tzinfo)
                if expiry_dt < current:
                    await interaction.followup.send("⚠️ This offer has expired.", ephemeral=True)
                    return
            except ValueError:
                pass

        # Check roster status
        roster_entry = repo.get_active_roster_entry(signing['season_id'], signing['player_id'])
        if roster_entry and roster_entry.get('team_id') == signing['team_id']:
            await interaction.followup.send("ℹ️ You are already on this team.", ephemeral=True)
            repo.update_pending_signing_status(signing['id'], 'accepted')
            return
        
        if roster_entry and roster_entry.get('team_id') != signing['team_id']:
            await interaction.followup.send("❌ You are currently rostered on another team.", ephemeral=True)
            repo.update_pending_signing_status(signing['id'], 'declined')
            return

        # Execute signing
        transaction = repo.insert_transaction({
            'season_id': signing['season_id'],
            'transaction_type': 'sign',
            'player_id': signing['player_id'],
            'team_id': signing['team_id'],
            'metadata': {
                'accepted_at': datetime.utcnow().isoformat() + 'Z',
                'initiated_by': signing.get('created_by'),
                'guild_id': signing.get('guild_id')
            }
        })

        if not transaction:
            await interaction.followup.send("❌ Failed to record transaction.", ephemeral=True)
            return

        repo.update_pending_signing_status(signing['id'], 'accepted')
        repo.cancel_other_pending_signings(signing['player_id'], keep_id=signing['id'])

        # Refresh caches
        RosterCache().invalidate()
        cache = TeamPlayerCache()
        await cache.invalidate_team(signing['team_id'])

        # Update message and post announcement
        try:
            team = supabase.table('teams').select('*').eq('id', signing['team_id']).single().execute().data
            
            # Get league info for styling
            league_code = ''
            division_id = team.get('division_id')
            if division_id:
                try:
                    div_resp = supabase.table('divisions').select('league_type').eq('id', division_id).limit(1).execute()
                    if div_resp.data:
                        league_code = div_resp.data[0].get('league_type', '').upper()
                except Exception:
                    pass
            
            # Get season name
            season_name = 'Unknown Season'
            try:
                season_resp = supabase.table('seasons').select('name').eq('id', signing['season_id']).limit(1).execute()
                if season_resp.data:
                    season_name = season_resp.data[0].get('name', 'Unknown Season')
            except Exception:
                pass
            
            from utils.team_display import format_team_label_from_record
            team_label = format_team_label_from_record(team)
            
            builder = EmbedBuilder(
                title="Signing Accepted",
                description=f"<@{interaction.user.id}> has joined {team_label}!",
                force_color=0x57F287,
                team_code=team.get('code'),
                division_code=league_code,
            )
            builder.add_league_field(league_code)
            builder.add_field("Season", season_name, inline=True)
            builder.add_field("Player", f"<@{interaction.user.id}>", inline=True)
            builder.add_field("Team", team_label, inline=True)
            builder.add_timestamp()
            builder.add_team_image(team.get('code'), position="thumbnail")
            
            embed, files = builder.build_with_files()
            await interaction.message.edit(embed=embed, view=None, attachments=[])
            
            # Post announcement to transactions channel with ping
            guild_id = signing.get('guild_id')
            if guild_id and interaction.guild:
                from utils.config_manager import get_config_manager
                config_mgr = get_config_manager()
                tx_config = await config_mgr.get_transactions_config(int(guild_id))
                channel_id = tx_config.get('channel_id')
                
                if channel_id:
                    try:
                        channel = interaction.guild.get_channel(int(channel_id))
                        if channel and isinstance(channel, discord.TextChannel):
                            # Rebuild embed for channel (in case message files don't carry over)
                            channel_builder = EmbedBuilder(
                                title="✅ Signing Accepted",
                                description=f"<@{interaction.user.id}> has joined {team_label}!",
                                force_color=0x57F287,
                                team_code=team.get('code'),
                                division_code=league_code,
                            )
                            channel_builder.add_league_field(league_code)
                            channel_builder.add_field("Season", season_name, inline=True)
                            channel_builder.add_field("Player", f"<@{interaction.user.id}>", inline=True)
                            channel_builder.add_field("Team", team_label, inline=True)
                            channel_builder.add_timestamp()
                            channel_builder.add_team_image(team.get('code'), position="thumbnail")
                            
                            channel_embed, channel_files = channel_builder.build_with_files()
                            await channel.send(
                                content=f"<@{interaction.user.id}>",
                                embed=channel_embed,
                                files=channel_files or None,
                                allowed_mentions=discord.AllowedMentions(users=[interaction.user]),
                            )
                    except Exception as e:
                        logger.warning(f"Failed to post signing acceptance to transactions channel: {e}")
        except Exception as e:
            logger.error(f"Failed to update message after signing accept: {e}")
            
        await interaction.followup.send("✅ Offer accepted!", ephemeral=True)

class SigningDeclineButton(ui.DynamicItem[ui.Button], template=r'signing:decline:(?P<id>.+)'):
    def __init__(self, signing_id: str):
        super().__init__(
            ui.Button(
                label="Decline Offer",
                style=discord.ButtonStyle.danger,
                custom_id=f"signing:decline:{signing_id}",
            )
        )
        self.signing_id = signing_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: ui.Button, match: re.Match, /):
        return cls(match['id'])

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        supabase = get_supabase()
        repo = TransactionsRepository(supabase)
        
        signing = repo.get_pending_signing(self.signing_id)
        if not signing:
            await interaction.followup.send("❌ Offer not found.", ephemeral=True)
            return

        player_record = supabase.table('players').select('*').eq('id', signing['player_id']).single().execute()
        if not player_record.data or interaction.user.id != player_record.data['discord_id']:
             await interaction.followup.send("❌ Only the targeted player can respond.", ephemeral=True)
             return

        if signing['status'] != 'pending':
            await interaction.followup.send(f"⚠️ Offer is {signing['status']}.", ephemeral=True)
            return

        repo.update_pending_signing_status(signing['id'], 'declined')
        
        try:
            team = supabase.table('teams').select('*').eq('id', signing['team_id']).single().execute().data
            builder = EmbedBuilder(
                title="Signing Declined",
                description=f"<@{interaction.user.id}> declined the offer from **{team['name']}**.",
                force_color=0xED4245,
                team_code=team.get('code'),
            )
            embed, files = builder.build_with_files()
            await interaction.message.edit(embed=embed, view=None, attachments=[])
        except Exception as e:
            logger.error(f"Failed to update message after signing decline: {e}")

        await interaction.followup.send("🗂️ Offer declined.", ephemeral=True)
