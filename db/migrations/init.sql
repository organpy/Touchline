-- init_multi_league.sql
-- Multi-league (NBL, MBL, NCBA) schema for Supabase / PostgreSQL
-- Includes tables, indexes, constraints, triggers, helper functions, and a unified standings view.
-- Date: 2025-11-07

BEGIN;

-- ----------------------------
-- Extensions
-- ----------------------------
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ----------------------------
-- Core tables
-- ----------------------------

-- Divisions / Leagues
CREATE TABLE IF NOT EXISTS divisions (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  league_type text NOT NULL,  -- 'NBL', 'MBL', 'NCBA', etc.
  name text,
  metadata jsonb,
  created_at timestamptz DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_divisions_league_type ON divisions(league_type);

-- Seasons (per-division)
CREATE TABLE IF NOT EXISTS seasons (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  division_id uuid NOT NULL REFERENCES divisions(id) ON DELETE CASCADE,
  name text NOT NULL,  -- e.g., "S10" or "Season 5"
  start_date date,
  end_date date,
  active boolean DEFAULT false,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);
-- Ensure season name unique per division
CREATE UNIQUE INDEX IF NOT EXISTS ux_seasons_division_name
  ON seasons(division_id, name);
-- Allow only one active season per division
CREATE UNIQUE INDEX IF NOT EXISTS ux_seasons_division_active
  ON seasons(division_id)
  WHERE active = true;

-- Teams (per-division); code globally unique per your requirement
CREATE TABLE IF NOT EXISTS teams (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  division_id uuid NOT NULL REFERENCES divisions(id) ON DELETE CASCADE,
  code text NOT NULL,  -- e.g., "pandas" (globally unique)
  name text NOT NULL,
  emoji text,
  owner_discord_id bigint,
  metadata jsonb,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_teams_code_unique ON teams(code);
CREATE INDEX IF NOT EXISTS idx_teams_division ON teams(division_id);

-- Players (global across leagues)
CREATE TABLE IF NOT EXISTS players (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  display_name text NOT NULL,
  discord_id bigint UNIQUE,         -- one Discord account -> one player
  metadata jsonb,
  created_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_players_discord_id ON players(discord_id);

-- Rosters: flexible team membership (handles loans / transfers)
CREATE TABLE IF NOT EXISTS rosters (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  season_id uuid NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
  team_id uuid NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  player_id uuid NOT NULL REFERENCES players(id) ON DELETE CASCADE,
  joined_at timestamptz DEFAULT now(),
  left_at timestamptz,  -- NULL = currently in team
  role text,            -- optional: starter/sub/bench/captain
  metadata jsonb,
  created_at timestamptz DEFAULT now(),
  UNIQUE(season_id, team_id, player_id, joined_at)
);
CREATE INDEX IF NOT EXISTS idx_rosters_season_team ON rosters(season_id, team_id);
CREATE INDEX IF NOT EXISTS idx_rosters_player ON rosters(player_id);

-- Pre-generated schedule per season (fixtures)
CREATE TABLE IF NOT EXISTS schedules (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  season_id uuid NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
  division_id uuid NOT NULL REFERENCES divisions(id) ON DELETE CASCADE,
  team1_id uuid NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  team2_id uuid NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  scheduled_date timestamptz,
  status text DEFAULT 'scheduled',  -- 'scheduled', 'completed', 'cancelled'
  metadata jsonb,
  created_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_schedules_season_division ON schedules(season_id, division_id);

-- Matches (one row per actual game)
CREATE TABLE IF NOT EXISTS matches (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  season_id uuid NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
  division_id uuid NOT NULL REFERENCES divisions(id) ON DELETE CASCADE,
  schedule_id uuid REFERENCES schedules(id) ON DELETE SET NULL,
  team1_id uuid NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  team2_id uuid NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  team1_score integer,  -- For MBL: goals; For NBL/NCBA: player-scored points aggregated
  team2_score integer,
  final_turn integer,
  recorded_by bigint,    -- Discord ID of who recorded
  status text DEFAULT 'pending',  -- 'pending', 'final', 'cancelled'
  played_at timestamptz,
  metadata jsonb,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_matches_season_division ON matches(season_id, division_id);
CREATE INDEX IF NOT EXISTS idx_matches_played_at ON matches(played_at);

-- Player stats (per player per match)
CREATE TABLE IF NOT EXISTS player_stats (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  match_id uuid NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
  player_id uuid NOT NULL REFERENCES players(id) ON DELETE CASCADE,
  goals integer DEFAULT 0,         -- for MBL (or keep zero for NBL if not used)
  points integer DEFAULT 0,        -- for NBL/NCBA (player scoring)
  own_goals integer DEFAULT 0,
  assists integer DEFAULT 0,
  saves integer DEFAULT 0,
  blocks integer DEFAULT 0,
  powerup_plays integer DEFAULT 0,
  powerups_used integer DEFAULT 0,
  powerup_percentage numeric(5,2) DEFAULT 0,  -- computed trigger: percentage (plays / used) * 100
  metadata jsonb,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_player_stats_match ON player_stats(match_id);
CREATE INDEX IF NOT EXISTS idx_player_stats_player ON player_stats(player_id);

-- Transactions (sign, release, transfer, loan, loan_return)
CREATE TABLE IF NOT EXISTS transactions (
  id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
  season_id uuid REFERENCES seasons(id) ON DELETE SET NULL,
  type text NOT NULL,  -- 'sign', 'release', 'loan', 'loan_return', 'transfer'
  player_id uuid REFERENCES players(id) ON DELETE SET NULL,
  from_team_id uuid REFERENCES teams(id) ON DELETE SET NULL,
  to_team_id uuid REFERENCES teams(id) ON DELETE SET NULL,
  executed_by bigint,  -- Discord ID
  details jsonb,
  created_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_transactions_player ON transactions(player_id);

-- Bot configuration (per guild)
CREATE TABLE IF NOT EXISTS bot_config (
  guild_id bigint PRIMARY KEY,
  config jsonb DEFAULT '{}'::jsonb,
  updated_at timestamptz DEFAULT now()
);

-- Guild <> Division mapping (one guild can manage multiple divisions)
CREATE TABLE IF NOT EXISTS guild_divisions (
  guild_id bigint NOT NULL,
  division_id uuid NOT NULL REFERENCES divisions(id) ON DELETE CASCADE,
  created_at timestamptz DEFAULT now(),
  PRIMARY KEY (guild_id, division_id)
);

-- ----------------------------
-- Constraints & Triggers / Helper functions
-- ----------------------------

-- 1) Prevent draws when match.status = 'final'
-- A check to allow null scores pre-final, but disallow equal non-null scores.
ALTER TABLE matches
  ADD CONSTRAINT chk_matches_no_draw
    CHECK (
      team1_score IS NULL OR team2_score IS NULL
      OR team1_score <> team2_score
    );

-- Trigger function to enforce final state rules
CREATE OR REPLACE FUNCTION ensure_final_scores() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status = 'final' THEN
    IF NEW.team1_score IS NULL OR NEW.team2_score IS NULL THEN
      RAISE EXCEPTION 'final match must have both team1_score and team2_score set';
    END IF;
    IF NEW.team1_score = NEW.team2_score THEN
      RAISE EXCEPTION 'final match cannot be a draw (team1_score <> team2_score required)';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER trg_matches_final_check
  BEFORE INSERT OR UPDATE ON matches
  FOR EACH ROW EXECUTE FUNCTION ensure_final_scores();

-- 2) Ensure teams belong to the match's division
CREATE OR REPLACE FUNCTION ensure_match_teams_in_division() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  t1_div uuid;
  t2_div uuid;
BEGIN
  SELECT division_id INTO t1_div FROM teams WHERE id = NEW.team1_id;
  SELECT division_id INTO t2_div FROM teams WHERE id = NEW.team2_id;
  IF t1_div IS NULL OR t2_div IS NULL THEN
    RAISE EXCEPTION 'one or both teams do not exist';
  END IF;
  IF t1_div <> NEW.division_id OR t2_div <> NEW.division_id THEN
    RAISE EXCEPTION 'team division mismatch: both teams must belong to match.division_id';
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER trg_matches_teams_division
  BEFORE INSERT OR UPDATE ON matches
  FOR EACH ROW EXECUTE FUNCTION ensure_match_teams_in_division();

-- 3) Compute powerup_percentage on insert/update for player_stats
CREATE OR REPLACE FUNCTION compute_powerup_pct() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (NEW.powerups_used IS NOT NULL AND NEW.powerups_used > 0) THEN
    NEW.powerup_percentage := round((NEW.powerup_plays::numeric / NEW.powerups_used::numeric) * 100::numeric, 2);
  ELSE
    NEW.powerup_percentage := 0;
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER trg_player_stats_powerup_pct
  BEFORE INSERT OR UPDATE ON player_stats
  FOR EACH ROW EXECUTE FUNCTION compute_powerup_pct();

-- 4) Apply transactions to rosters automatically (basic example)
-- NOTE: This is a simple implementation — adapt rules (loan durations, rollback, etc.) in application logic if needed.
CREATE OR REPLACE FUNCTION apply_transaction_to_roster() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.type IN ('sign','transfer','loan') AND NEW.to_team_id IS NOT NULL THEN
    -- mark previous active roster rows for this player in same season as left
    UPDATE rosters
      SET left_at = COALESCE(NEW.created_at, now())
      WHERE player_id = NEW.player_id
        AND season_id = NEW.season_id
        AND left_at IS NULL;
    -- insert new roster row
    INSERT INTO rosters (season_id, team_id, player_id, joined_at)
      VALUES (NEW.season_id, NEW.to_team_id, NEW.player_id, COALESCE(NEW.created_at, now()));
  ELSIF NEW.type = 'release' THEN
    UPDATE rosters
      SET left_at = COALESCE(NEW.created_at, now())
      WHERE player_id = NEW.player_id
        AND season_id = NEW.season_id
        AND team_id = NEW.from_team_id
        AND left_at IS NULL;
  ELSIF NEW.type = 'loan_return' THEN
    -- mark the loaned-out roster as left; application should create new roster for original team if needed
    UPDATE rosters
      SET left_at = COALESCE(NEW.created_at, now())
      WHERE player_id = NEW.player_id
        AND season_id = NEW.season_id
        AND team_id = NEW.from_team_id
        AND left_at IS NULL;
  END IF;
  RETURN NEW;
END;
$$;

CREATE TRIGGER trg_transactions_apply_roster
  AFTER INSERT ON transactions
  FOR EACH ROW EXECUTE FUNCTION apply_transaction_to_roster();

-- ----------------------------
-- Unified live standings view
-- ----------------------------
-- This view supports:
--  - MBL: 3 points per win, 1 per tie (although you said draws aren't possible; function kept for completeness)
--  - NBL/NCBA: competition_points = sum of team-scored points (team1_score/team2_score), i.e., basketball-like scoring
-- The main tiebreaker is point_diff (points_for - points_against).
CREATE OR REPLACE VIEW view_standings AS
SELECT
  s.id AS season_id,
  d.id AS division_id,
  d.league_type,
  t.id AS team_id,
  t.code,
  t.name,
  COUNT(m.id) FILTER (WHERE m.status = 'final') AS gp,
  SUM(CASE
    WHEN m.team1_id = t.id AND m.team1_score > m.team2_score THEN 1
    WHEN m.team2_id = t.id AND m.team2_score > m.team1_score THEN 1
    ELSE 0 END) FILTER (WHERE m.status = 'final') AS wins,
  SUM(CASE
    WHEN m.team1_id = t.id AND m.team1_score < m.team2_score THEN 1
    WHEN m.team2_id = t.id AND m.team2_score < m.team1_score THEN 1
    ELSE 0 END) FILTER (WHERE m.status = 'final') AS losses,
  -- competition/league points
  CASE
    WHEN d.league_type = 'MBL' THEN
      COALESCE(SUM(CASE
        WHEN m.team1_id = t.id AND m.team1_score > m.team2_score THEN 3
        WHEN m.team2_id = t.id AND m.team2_score > m.team1_score THEN 3
        WHEN (m.team1_id = t.id OR m.team2_id = t.id) AND m.team1_score = m.team2_score THEN 1
        ELSE 0 END) FILTER (WHERE m.status = 'final'), 0)
    ELSE
      -- For NBL/NCBA, competition points are the sum of the team's scored points in matches (team1_score/team2_score)
      COALESCE(SUM(CASE WHEN m.team1_id = t.id THEN m.team1_score
                        WHEN m.team2_id = t.id THEN m.team2_score END) FILTER (WHERE m.status = 'final'), 0)
  END AS competition_points,
  COALESCE(SUM(CASE WHEN m.team1_id = t.id THEN m.team1_score
                   WHEN m.team2_id = t.id THEN m.team2_score END) FILTER (WHERE m.status = 'final'), 0) AS points_for,
  COALESCE(SUM(CASE WHEN m.team1_id = t.id THEN m.team2_score
                   WHEN m.team2_id = t.id THEN m.team1_score END) FILTER (WHERE m.status = 'final'), 0) AS points_against,
  (COALESCE(SUM(CASE WHEN m.team1_id = t.id THEN m.team1_score
                   WHEN m.team2_id = t.id THEN m.team2_score END) FILTER (WHERE m.status = 'final'), 0) -
   COALESCE(SUM(CASE WHEN m.team1_id = t.id THEN m.team2_score
                   WHEN m.team2_id = t.id THEN m.team1_score END) FILTER (WHERE m.status = 'final'), 0)
  ) AS point_diff
FROM divisions d
JOIN teams t ON t.division_id = d.id
JOIN seasons s ON s.division_id = d.id
LEFT JOIN matches m ON m.season_id = s.id AND m.division_id = d.id AND (m.team1_id = t.id OR m.team2_id = t.id)
WHERE s.active = true
GROUP BY s.id, d.id, d.league_type, t.id, t.code, t.name;

-- ----------------------------
-- Convenience functions (optional)
-- ----------------------------

-- Helper: Recalculate team scores for a match from player_stats
-- Use if you want to compute team score (sum of player_stats.points or goals) instead of manual match score input.
-- Example usage (from application): SELECT recompute_match_scores('<match-uuid>');
CREATE OR REPLACE FUNCTION recompute_match_scores(_match_id uuid) RETURNS void LANGUAGE plpgsql AS $$
DECLARE
  t1 uuid;
  t2 uuid;
  pts1 integer;
  pts2 integer;
  div_type text;
BEGIN
  SELECT team1_id, team2_id, division_id INTO t1, t2, div_type FROM matches WHERE id = _match_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'match not found';
  END IF;

  -- Determine league_type
  SELECT league_type INTO div_type FROM divisions WHERE id = (SELECT division_id FROM matches WHERE id = _match_id);

  IF div_type = 'MBL' THEN
    -- goals
    SELECT COALESCE(SUM(goals),0) INTO pts1 FROM player_stats ps JOIN players p ON p.id = ps.player_id WHERE ps.match_id = _match_id AND ps.player_id IS NOT NULL AND EXISTS(SELECT 1 FROM matches m WHERE m.id = _match_id AND (m.team1_id = m.team1_id));
    -- The above is a placeholder; usually you need a player -> team mapping per match or store team_id on player_stats.
    -- For robust recompute, it's better if player_stats includes team_id or you deduce via rosters at match time.
  ELSE
    -- points
    SELECT COALESCE(SUM(points),0) INTO pts1 FROM player_stats WHERE match_id = _match_id;
    -- NOTE: recompute needs to split per team. This helper is intentionally basic; prefer application-level compute.
  END IF;
  -- Not performing automatic updates by default to avoid incorrect assumptions.
  RETURN;
END;
$$;

-- ----------------------------
-- Finalize
-- ----------------------------

COMMIT;

-- NOTES / REMINDERS:
-- 1) player_stats currently does not store team_id. If you want to reliably aggregate player stats to team totals automatically,
--    consider adding a "team_id" column to player_stats at insert-time, or ensure your application determines player->team mapping (from rosters) at the time of match recording.
-- 2) recompute_match_scores is intentionally minimal because aggregating player stats into team totals requires a reliable mapping of which team a player represented in that match.
-- 3) If you have a very large dataset and standings become slow, consider switching view_standings to a MATERIALIZED VIEW and refreshing it on-demand or via triggers/background job.
-- 4) Adapt transaction -> roster logic to match your exact business rules (loan durations, rolling-back transfers, approvals, etc.).
