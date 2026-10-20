-- Fix duplicate games in get_player_history function
-- Problem: LEFT JOIN on rosters can create duplicates if player has multiple roster entries
-- Solution: Use player_stats as the source of truth (one row per player per match)
-- and use subquery to get player's team WITHOUT creating duplicates

DROP FUNCTION IF EXISTS get_player_history(UUID, UUID, INT, INT);
DROP FUNCTION IF EXISTS get_player_history(UUID, UUID, INT);

CREATE OR REPLACE FUNCTION get_player_history(
    p_player_id UUID,
    p_season_id UUID,
    p_limit INT DEFAULT 50,
    p_offset INT DEFAULT 0
)
RETURNS TABLE(
    match_id UUID,
    match_date TIMESTAMPTZ,
    opponent_name TEXT,
    points INT,
    assists INT,
    goals INT,
    saves INT,
    own_goals INT,
    blocks INT,
    pp_plays INT,
    pp_used INT
)
LANGUAGE SQL
STABLE
AS $$
WITH player_team AS (
    -- Get player's team for this season (latest entry)
    SELECT DISTINCT ON (player_id, season_id) team_id
    FROM rosters
    WHERE player_id = p_player_id AND season_id = p_season_id
    ORDER BY player_id, season_id, joined_at DESC
)
SELECT
    m.id,
    m.played_at,
    CASE 
        WHEN (SELECT team_id FROM player_team) = m.team1_id THEN t2.name
        WHEN (SELECT team_id FROM player_team) = m.team2_id THEN t1.name
        ELSE 'Unknown'
    END,
    COALESCE(ps.points, 0)::INT,
    COALESCE(ps.assists, 0)::INT,
    COALESCE(ps.goals, 0)::INT,
    COALESCE(ps.saves, 0)::INT,
    COALESCE(ps.own_goals, 0)::INT,
    COALESCE(ps.blocks, 0)::INT,
    COALESCE(ps.powerup_plays, 0)::INT,
    COALESCE(ps.powerups_used, 0)::INT
FROM player_stats ps
JOIN matches m ON ps.match_id = m.id
JOIN teams t1 ON m.team1_id = t1.id
JOIN teams t2 ON m.team2_id = t2.id
WHERE ps.player_id = p_player_id
    AND m.season_id = p_season_id
ORDER BY m.played_at DESC NULLS LAST
LIMIT p_limit OFFSET p_offset;
$$;

-- Note: Run this in Supabase SQL Editor to fix duplicates
