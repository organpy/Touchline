-- Optimization Plan (docs/22_OPTIMIZATION_OPPORTUNITIES.md)
-- Issue 1.2: Missing indexes on high-traffic tables
-- Note: player_stats doesn't have season_id directly, so we index the join path via match_id

CREATE INDEX IF NOT EXISTS idx_rosters_season_id ON rosters(season_id);
CREATE INDEX IF NOT EXISTS idx_player_stats_match_id ON player_stats(match_id);
CREATE INDEX IF NOT EXISTS idx_pending_signings_season_id ON pending_signings(season_id);
CREATE INDEX IF NOT EXISTS idx_matches_season_id ON matches(season_id);
CREATE INDEX IF NOT EXISTS idx_teams_division_id ON teams(division_id);
