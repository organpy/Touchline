-- Multi-league schema for NBL, MBL, NCBA (Supabase/Postgres)
-- Single database supporting three divisions with separate teams, standings, and match histories.
-- Pre-computed player stats (powerup %, goals, assists, etc.)
-- Auto-computed standings from matches.

BEGIN;

-- Enable uuid extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Divisions (leagues)
CREATE TABLE IF NOT EXISTS divisions (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  league_type text NOT NULL UNIQUE,  -- 'NBL', 'MBL', 'NCBA'
  name text,
  created_at timestamptz DEFAULT now()
);

-- Seasons (shared across all divisions, but can have different start dates)
CREATE TABLE IF NOT EXISTS seasons (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  name text NOT NULL,  -- e.g., "S10"
  start_date date,
  end_date date,
  active boolean DEFAULT false,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);

-- Teams (per division)
CREATE TABLE IF NOT EXISTS teams (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  division_id uuid NOT NULL REFERENCES divisions(id) ON DELETE CASCADE,
  code text NOT NULL UNIQUE,  -- e.g., "pandas"
  name text NOT NULL,
  emoji text,
  owner_discord_id bigint,
  metadata jsonb,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_teams_division ON teams(division_id);

-- Players (global, can play in different teams due to loans)
CREATE TABLE IF NOT EXISTS players (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  display_name text NOT NULL,
  discord_id bigint,
  metadata jsonb,
  created_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_players_discord_id ON players(discord_id);

-- Rosters: flexible team membership (handles loans mid-season)
CREATE TABLE IF NOT EXISTS rosters (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  season_id uuid NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
  team_id uuid NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  player_id uuid NOT NULL REFERENCES players(id) ON DELETE CASCADE,
  joined_at timestamptz DEFAULT now(),
  left_at timestamptz,  -- NULL = still active; set when player leaves/traded
  UNIQUE(season_id, team_id, player_id, joined_at)  -- allows player to rejoin
);
CREATE INDEX IF NOT EXISTS idx_rosters_season_team ON rosters(season_id, team_id);
CREATE INDEX IF NOT EXISTS idx_rosters_player ON rosters(player_id);

-- Schedules (pre-generated match schedule per season)
CREATE TABLE IF NOT EXISTS schedules (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  season_id uuid NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
  division_id uuid NOT NULL REFERENCES divisions(id) ON DELETE CASCADE,
  team1_id uuid NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  team2_id uuid NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  scheduled_date timestamptz,
  status text DEFAULT 'scheduled',  -- 'scheduled', 'completed', 'cancelled'
  created_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_schedules_season_division ON schedules(season_id, division_id);

-- Matches (actual match results)
CREATE TABLE IF NOT EXISTS matches (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  season_id uuid NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
  division_id uuid NOT NULL REFERENCES divisions(id) ON DELETE CASCADE,
  schedule_id uuid REFERENCES schedules(id) ON DELETE SET NULL,
  team1_id uuid NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  team2_id uuid NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  team1_score integer,  -- sum of player points/goals
  team2_score integer,
  final_turn integer,
  recorded_by bigint,  -- Discord ID
  status text DEFAULT 'final',  -- 'final', 'pending', etc.
  played_at timestamptz,
  created_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_matches_season_division ON matches(season_id, division_id);
CREATE INDEX IF NOT EXISTS idx_matches_played_at ON matches(played_at);

-- Player stats (per-player, per-match stats with pre-computed powerup %)
CREATE TABLE IF NOT EXISTS player_stats (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  match_id uuid NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  player_id uuid NOT NULL REFERENCES players(id) ON DELETE CASCADE,
  goals integer DEFAULT 0,
  own_goals integer DEFAULT 0,
  assists integer DEFAULT 0,
  saves integer DEFAULT 0,
  blocks integer DEFAULT 0,
  powerup_plays integer DEFAULT 0,
  powerups_used integer DEFAULT 0,
  powerup_percentage decimal(5,2),  -- pre-computed
  points integer,  -- NULL for MBL; used for NBL/NCBA
  created_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_player_stats_match ON player_stats(match_id);
CREATE INDEX IF NOT EXISTS idx_player_stats_player ON player_stats(player_id);

-- Transactions (sign, release, loan, loan return)
CREATE TABLE IF NOT EXISTS transactions (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  season_id uuid REFERENCES seasons(id) ON DELETE SET NULL,
  type text NOT NULL,  -- 'sign', 'release', 'loan', 'loan_return'
  player_id uuid REFERENCES players(id) ON DELETE SET NULL,
  from_team_id uuid REFERENCES teams(id) ON DELETE SET NULL,
  to_team_id uuid REFERENCES teams(id) ON DELETE SET NULL,
  executed_by bigint,  -- Discord ID
  details jsonb,
  created_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_transactions_player ON transactions(player_id);

-- Bot configuration per guild
CREATE TABLE IF NOT EXISTS bot_config (
  guild_id bigint PRIMARY KEY,
  division_id uuid REFERENCES divisions(id) ON DELETE SET NULL,
  config jsonb DEFAULT '{}'::jsonb,
  updated_at timestamptz DEFAULT now()
);

-- ============================================================================
-- MATERIALIZED VIEWS FOR STANDINGS
-- ============================================================================

-- Standings for NBL/NCBA (point-based)
CREATE MATERIALIZED VIEW IF NOT EXISTS view_standings_nbl_ncba AS
SELECT
  s.id as season_id,
  d.id as division_id,
  d.league_type,
  t.id as team_id,
  t.code,
  t.name,
  COUNT(m.id) FILTER (WHERE m.status = 'final') as gp,
  SUM(CASE 
    WHEN m.team1_id = t.id AND m.team1_score > m.team2_score THEN 1
    WHEN m.team2_id = t.id AND m.team2_score > m.team1_score THEN 1
    ELSE 0 END) FILTER (WHERE m.status = 'final') as wins,
  SUM(CASE
    WHEN m.team1_id = t.id AND m.team1_score = m.team2_score THEN 1
    WHEN m.team2_id = t.id AND m.team2_score = m.team1_score THEN 1
    ELSE 0 END) FILTER (WHERE m.status = 'final') as ties,
  SUM(CASE
    WHEN (m.team1_id = t.id AND m.team1_score < m.team2_score) OR
         (m.team2_id = t.id AND m.team2_score < m.team1_score) THEN 1
    ELSE 0 END) FILTER (WHERE m.status = 'final') as losses,
  COALESCE(SUM(CASE WHEN m.team1_id = t.id THEN m.team1_score 
                     WHEN m.team2_id = t.id THEN m.team2_score END) FILTER (WHERE m.status = 'final'), 0) as points_for,
  COALESCE(SUM(CASE WHEN m.team1_id = t.id THEN m.team2_score 
                     WHEN m.team2_id = t.id THEN m.team1_score END) FILTER (WHERE m.status = 'final'), 0) as points_against,
  COALESCE(SUM(CASE WHEN m.team1_id = t.id THEN m.team1_score 
                     WHEN m.team2_id = t.id THEN m.team2_score END) FILTER (WHERE m.status = 'final'), 0) -
  COALESCE(SUM(CASE WHEN m.team1_id = t.id THEN m.team2_score 
                     WHEN m.team2_id = t.id THEN m.team1_score END) FILTER (WHERE m.status = 'final'), 0) as point_diff
FROM seasons s
CROSS JOIN divisions d
CROSS JOIN teams t
LEFT JOIN matches m ON m.season_id = s.id AND m.division_id = d.id AND (m.team1_id = t.id OR m.team2_id = t.id)
WHERE d.league_type IN ('NBL', 'NCBA') AND t.division_id = d.id AND s.active = true
GROUP BY s.id, d.id, d.league_type, t.id, t.code, t.name;

-- Standings for MBL (goal-based with win points)
CREATE MATERIALIZED VIEW IF NOT EXISTS view_standings_mbl AS
SELECT
  s.id as season_id,
  d.id as division_id,
  d.league_type,
  t.id as team_id,
  t.code,
  t.name,
  COUNT(m.id) FILTER (WHERE m.status = 'final') as gp,
  SUM(CASE
    WHEN m.team1_id = t.id AND m.team1_score > m.team2_score THEN 1
    WHEN m.team2_id = t.id AND m.team2_score > m.team1_score THEN 1
    ELSE 0 END) FILTER (WHERE m.status = 'final') as wins,
  SUM(CASE
    WHEN m.team1_id = t.id AND m.team1_score = m.team2_score THEN 1
    WHEN m.team2_id = t.id AND m.team2_score = m.team1_score THEN 1
    ELSE 0 END) FILTER (WHERE m.status = 'final') as ties,
  SUM(CASE
    WHEN (m.team1_id = t.id AND m.team1_score < m.team2_score) OR
         (m.team2_id = t.id AND m.team2_score < m.team1_score) THEN 1
    ELSE 0 END) FILTER (WHERE m.status = 'final') as losses,
  -- MBL points: 3 per win, 1 per tie, 0 per loss
  COALESCE(SUM(CASE
    WHEN m.team1_id = t.id AND m.team1_score > m.team2_score THEN 3
    WHEN m.team2_id = t.id AND m.team2_score > m.team1_score THEN 3
    WHEN (m.team1_id = t.id OR m.team2_id = t.id) AND m.team1_score = m.team2_score THEN 1
    ELSE 0 END) FILTER (WHERE m.status = 'final'), 0) as competition_points,
  COALESCE(SUM(CASE WHEN m.team1_id = t.id THEN m.team1_score
                     WHEN m.team2_id = t.id THEN m.team2_score END) FILTER (WHERE m.status = 'final'), 0) as goals_for,
  COALESCE(SUM(CASE WHEN m.team1_id = t.id THEN m.team2_score
                     WHEN m.team2_id = t.id THEN m.team1_score END) FILTER (WHERE m.status = 'final'), 0) as goals_against,
  COALESCE(SUM(CASE WHEN m.team1_id = t.id THEN m.team1_score
                     WHEN m.team2_id = t.id THEN m.team2_score END) FILTER (WHERE m.status = 'final'), 0) -
  COALESCE(SUM(CASE WHEN m.team1_id = t.id THEN m.team2_score
                     WHEN m.team2_id = t.id THEN m.team1_score END) FILTER (WHERE m.status = 'final'), 0) as goal_diff
FROM seasons s
CROSS JOIN divisions d
CROSS JOIN teams t
LEFT JOIN matches m ON m.season_id = s.id AND m.division_id = d.id AND (m.team1_id = t.id OR m.team2_id = t.id)
WHERE d.league_type = 'MBL' AND t.division_id = d.id AND s.active = true
GROUP BY s.id, d.id, d.league_type, t.id, t.code, t.name;

COMMIT;

-- Notes:
-- - Materialized views combine standings for readability. Refresh after writes with: REFRESH MATERIALIZED VIEW view_standings_nbl_ncba; REFRESH MATERIALIZED VIEW view_standings_mbl;
-- - powerup_percentage in player_stats is pre-computed on insert: CASE WHEN powerups_used > 0 THEN (powerup_plays::decimal / powerups_used) ELSE 0 END
-- - Teams are tied to divisions via division_id.
-- - Rosters support loans: set left_at when player leaves; rejoin creates new roster row with new joined_at.
-- - Schedules are generated at season start; matches are recorded as games are played.
