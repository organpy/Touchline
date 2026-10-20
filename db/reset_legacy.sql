-- Removes the old NBL/MBL/NCBA schema so db/migrations/init.sql can be applied.
--
-- THIS DELETES ALL LEAGUE DATA: divisions, teams, players, seasons, rosters, games, stats, picks,
-- trades and transaction history. bot_config (server settings such as the admin role and channels)
-- is kept.
--
-- Back up first:
--   pg_dump "$DATABASE_URL" --format=custom --file=nbl-backup-$(date +%Y%m%d).dump
-- Then:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/reset_legacy.sql
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/migrations/init.sql

BEGIN;

DROP SCHEMA IF EXISTS api CASCADE;
DROP VIEW IF EXISTS view_standings CASCADE;

DROP FUNCTION IF EXISTS execute_trade_proposal(uuid, bigint) CASCADE;
DROP FUNCTION IF EXISTS get_player_averages(uuid, uuid) CASCADE;
DROP FUNCTION IF EXISTS get_player_totals(uuid, uuid) CASCADE;
DROP FUNCTION IF EXISTS get_team_totals(uuid, uuid) CASCADE;
DROP FUNCTION IF EXISTS get_team_averages(uuid, uuid) CASCADE;
DROP FUNCTION IF EXISTS get_leaderboard(text, uuid, integer) CASCADE;
DROP FUNCTION IF EXISTS get_leaderboard(text, uuid, integer, integer) CASCADE;
DROP FUNCTION IF EXISTS get_player_history(uuid, uuid, integer) CASCADE;
DROP FUNCTION IF EXISTS get_player_history(uuid, uuid, integer, integer) CASCADE;
DROP FUNCTION IF EXISTS compare_players(uuid, uuid, uuid) CASCADE;
DROP FUNCTION IF EXISTS get_season_summary(uuid) CASCADE;
DROP FUNCTION IF EXISTS recompute_match_scores(uuid) CASCADE;

DROP TABLE IF EXISTS
    transaction_blocker_entries,
    transaction_events,
    trade_assets,
    trade_proposals,
    pending_signings,
    draft_picks,
    transactions,
    player_stats,
    matches,
    schedules,
    rosters,
    guild_divisions,
    teams,
    players,
    seasons,
    divisions
CASCADE;

DROP FUNCTION IF EXISTS ensure_final_scores() CASCADE;
DROP FUNCTION IF EXISTS ensure_match_teams_in_division() CASCADE;
DROP FUNCTION IF EXISTS compute_powerup_pct() CASCADE;
DROP FUNCTION IF EXISTS apply_transaction_to_roster() CASCADE;

-- The old games-per-matchup setting now lives on each division.
UPDATE bot_config SET config = config - 'games_limits' WHERE config ? 'games_limits';

COMMIT;
