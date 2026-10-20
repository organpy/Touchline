-- 2025-11-08: Stats aggregation functions
-- Provides SQL-based helpers for leaderboards, averages, totals, comparisons, and summaries
-- These functions are called by Python async wrappers in queries.py

BEGIN;

DROP FUNCTION IF EXISTS get_player_averages(UUID, UUID);
DROP FUNCTION IF EXISTS get_player_totals(UUID, UUID);
DROP FUNCTION IF EXISTS get_team_totals(UUID, UUID);
DROP FUNCTION IF EXISTS get_team_averages(UUID, UUID);
DROP FUNCTION IF EXISTS get_leaderboard(TEXT, UUID, INT);
DROP FUNCTION IF EXISTS get_player_history(UUID, UUID, INT);
DROP FUNCTION IF EXISTS compare_players(UUID, UUID, UUID);
DROP FUNCTION IF EXISTS get_season_summary(UUID);

-- =====================================================================
-- 1. Player per-game averages for a season
-- =====================================================================
CREATE OR REPLACE FUNCTION get_player_averages(
    p_player_id UUID,
    p_season_id UUID
)
RETURNS TABLE(
    player_id UUID,
    display_name TEXT,
    team_name TEXT,
    games_played INT,
    ppg NUMERIC,
    apg NUMERIC,
    gpg NUMERIC,
    spg NUMERIC,
    ogpg NUMERIC,
    bpg NUMERIC,
    pp_efficiency NUMERIC
)
LANGUAGE SQL
STABLE
AS $$
WITH player_stats_agg AS (
    SELECT
        p.id AS player_id,
        p.display_name,
        COUNT(ps.id)::INT AS games_played,
        COALESCE(SUM(ps.points), 0) AS total_points,
        COALESCE(SUM(ps.assists), 0) AS total_assists,
        COALESCE(SUM(ps.goals), 0) AS total_goals,
        COALESCE(SUM(ps.saves), 0) AS total_saves,
        COALESCE(SUM(ps.own_goals), 0) AS total_own_goals,
        COALESCE(SUM(ps.blocks), 0) AS total_blocks,
        COALESCE(SUM(ps.powerup_plays), 0) AS total_pp_plays,
        COALESCE(SUM(ps.powerups_used), 0) AS total_pp_used
    FROM players p
    JOIN player_stats ps ON ps.player_id = p.id
    JOIN matches m ON m.id = ps.match_id
    WHERE p.id = p_player_id
      AND m.season_id = p_season_id
    GROUP BY p.id, p.display_name
)
SELECT
    psa.player_id,
    psa.display_name,
    COALESCE(ct.team_name, 'Free Agent') AS team_name,
    psa.games_played,
    ROUND((psa.total_points::NUMERIC / NULLIF(psa.games_played, 0)), 2) AS ppg,
    ROUND((psa.total_assists::NUMERIC / NULLIF(psa.games_played, 0)), 2) AS apg,
    ROUND((psa.total_goals::NUMERIC / NULLIF(psa.games_played, 0)), 2) AS gpg,
    ROUND((psa.total_saves::NUMERIC / NULLIF(psa.games_played, 0)), 2) AS spg,
    ROUND((psa.total_own_goals::NUMERIC / NULLIF(psa.games_played, 0)), 2) AS ogpg,
    ROUND((psa.total_blocks::NUMERIC / NULLIF(psa.games_played, 0)), 2) AS bpg,
    CASE 
        WHEN psa.total_pp_used > 0 
        THEN ROUND((psa.total_pp_plays::NUMERIC / psa.total_pp_used::NUMERIC) * 100, 2)
        ELSE 0
    END AS pp_efficiency
FROM player_stats_agg psa
LEFT JOIN LATERAL (
        SELECT t.name AS team_name
        FROM rosters r
        JOIN teams t ON t.id = r.team_id
        WHERE r.player_id = psa.player_id
            AND r.season_id = p_season_id
        ORDER BY r.joined_at DESC NULLS LAST
        LIMIT 1
) ct ON TRUE;
$$ ;

-- =====================================================================
-- 2. Player season totals
-- =====================================================================
CREATE OR REPLACE FUNCTION get_player_totals(
    p_player_id UUID,
    p_season_id UUID
)
RETURNS TABLE(
    player_id UUID,
    display_name TEXT,
    team_name TEXT,
    games_played INT,
    total_points INT,
    total_assists INT,
    total_goals INT,
    total_saves INT,
    total_own_goals INT,
    total_blocks INT,
    total_pp_plays INT,
    total_pp_used INT
)
LANGUAGE SQL
STABLE
AS $$
WITH player_stats_agg AS (
    SELECT
        p.id AS player_id,
        p.display_name,
        COUNT(ps.id)::INT AS games_played,
        COALESCE(SUM(ps.points), 0)::INT AS total_points,
        COALESCE(SUM(ps.assists), 0)::INT AS total_assists,
        COALESCE(SUM(ps.goals), 0)::INT AS total_goals,
        COALESCE(SUM(ps.saves), 0)::INT AS total_saves,
        COALESCE(SUM(ps.own_goals), 0)::INT AS total_own_goals,
        COALESCE(SUM(ps.blocks), 0)::INT AS total_blocks,
        COALESCE(SUM(ps.powerup_plays), 0)::INT AS total_pp_plays,
        COALESCE(SUM(ps.powerups_used), 0)::INT AS total_pp_used
    FROM players p
    JOIN player_stats ps ON ps.player_id = p.id
    JOIN matches m ON m.id = ps.match_id
    WHERE p.id = p_player_id
      AND m.season_id = p_season_id
    GROUP BY p.id, p.display_name
)
SELECT
    psa.player_id,
    psa.display_name,
        COALESCE(ct.team_name, 'Free Agent') AS team_name,
    psa.games_played,
    psa.total_points,
    psa.total_assists,
    psa.total_goals,
    psa.total_saves,
    psa.total_own_goals,
    psa.total_blocks,
    psa.total_pp_plays,
    psa.total_pp_used
FROM player_stats_agg psa
LEFT JOIN LATERAL (
        SELECT t.name AS team_name
        FROM rosters r
        JOIN teams t ON t.id = r.team_id
        WHERE r.player_id = psa.player_id
            AND r.season_id = p_season_id
        ORDER BY r.joined_at DESC NULLS LAST
        LIMIT 1
) ct ON TRUE;
$$ ;

-- =====================================================================
-- 3. Team season totals
-- =====================================================================
CREATE OR REPLACE FUNCTION get_team_totals(
    p_team_id UUID,
    p_season_id UUID
)
RETURNS TABLE(
    team_id UUID,
    team_name TEXT,
    player_count INT,
    games_played INT,
    total_points INT,
    total_assists INT,
    total_goals INT,
    total_saves INT,
    total_own_goals INT,
    total_blocks INT,
    total_pp_plays INT,
    total_pp_used INT
)
LANGUAGE SQL
STABLE
AS $$
WITH team_stat_rows AS (
    SELECT
        ps.player_id,
        ps.match_id,
        ps.points,
        ps.assists,
        ps.goals,
        ps.saves,
        ps.own_goals,
        ps.blocks,
        ps.powerup_plays,
        ps.powerups_used
    FROM player_stats ps
    JOIN matches m ON m.id = ps.match_id
    JOIN LATERAL (
        SELECT r.team_id
        FROM rosters r
        WHERE r.player_id = ps.player_id
          AND r.season_id = p_season_id
          AND r.team_id = p_team_id
          AND (r.joined_at IS NULL OR m.played_at IS NULL OR m.played_at >= r.joined_at)
          AND (r.left_at IS NULL OR m.played_at IS NULL OR m.played_at < r.left_at)
        ORDER BY r.joined_at DESC NULLS LAST
        LIMIT 1
    ) roster ON TRUE
    WHERE m.season_id = p_season_id
)
SELECT
    t.id,
    t.name,
    COUNT(DISTINCT ts.player_id)::INT AS player_count,
    COUNT(DISTINCT ts.match_id)::INT AS games_played,
    COALESCE(SUM(ts.points), 0)::INT AS total_points,
    COALESCE(SUM(ts.assists), 0)::INT AS total_assists,
    COALESCE(SUM(ts.goals), 0)::INT AS total_goals,
    COALESCE(SUM(ts.saves), 0)::INT AS total_saves,
    COALESCE(SUM(ts.own_goals), 0)::INT AS total_own_goals,
    COALESCE(SUM(ts.blocks), 0)::INT AS total_blocks,
    COALESCE(SUM(ts.powerup_plays), 0)::INT AS total_pp_plays,
    COALESCE(SUM(ts.powerups_used), 0)::INT AS total_pp_used
FROM teams t
LEFT JOIN team_stat_rows ts ON TRUE
WHERE t.id = p_team_id
GROUP BY t.id, t.name;
$$ ;

-- =====================================================================
-- 4. Team per-game averages
-- =====================================================================
CREATE OR REPLACE FUNCTION get_team_averages(
    p_team_id UUID,
    p_season_id UUID
)
RETURNS TABLE(
    team_id UUID,
    team_name TEXT,
    games_played INT,
    ppg NUMERIC,
    apg NUMERIC,
    gpg NUMERIC,
    spg NUMERIC,
    ogpg NUMERIC,
    bpg NUMERIC,
    pp_efficiency NUMERIC
)
LANGUAGE SQL
STABLE
AS $$
WITH team_stat_rows AS (
    SELECT
        ps.match_id,
        ps.points,
        ps.assists,
        ps.goals,
        ps.saves,
        ps.own_goals,
        ps.blocks,
        ps.powerup_plays,
        ps.powerups_used
    FROM player_stats ps
    JOIN matches m ON m.id = ps.match_id
    JOIN LATERAL (
        SELECT r.team_id
        FROM rosters r
        WHERE r.player_id = ps.player_id
          AND r.season_id = p_season_id
          AND r.team_id = p_team_id
          AND (r.joined_at IS NULL OR m.played_at IS NULL OR m.played_at >= r.joined_at)
          AND (r.left_at IS NULL OR m.played_at IS NULL OR m.played_at < r.left_at)
        ORDER BY r.joined_at DESC NULLS LAST
        LIMIT 1
    ) roster ON TRUE
    WHERE m.season_id = p_season_id
),
aggregated AS (
    SELECT
        COUNT(DISTINCT match_id)::INT AS games_played,
        COALESCE(SUM(points), 0)::NUMERIC AS total_points,
        COALESCE(SUM(assists), 0)::NUMERIC AS total_assists,
        COALESCE(SUM(goals), 0)::NUMERIC AS total_goals,
        COALESCE(SUM(saves), 0)::NUMERIC AS total_saves,
        COALESCE(SUM(own_goals), 0)::NUMERIC AS total_own_goals,
        COALESCE(SUM(blocks), 0)::NUMERIC AS total_blocks,
        COALESCE(SUM(powerup_plays), 0)::NUMERIC AS total_pp_plays,
        COALESCE(SUM(powerups_used), 0)::NUMERIC AS total_pp_used
    FROM team_stat_rows
)
SELECT
    t.id,
    t.name,
    aggregated.games_played,
    CASE WHEN aggregated.games_played > 0 THEN ROUND(aggregated.total_points / aggregated.games_played, 2) ELSE 0 END AS ppg,
    CASE WHEN aggregated.games_played > 0 THEN ROUND(aggregated.total_assists / aggregated.games_played, 2) ELSE 0 END AS apg,
    CASE WHEN aggregated.games_played > 0 THEN ROUND(aggregated.total_goals / aggregated.games_played, 2) ELSE 0 END AS gpg,
    CASE WHEN aggregated.games_played > 0 THEN ROUND(aggregated.total_saves / aggregated.games_played, 2) ELSE 0 END AS spg,
    CASE WHEN aggregated.games_played > 0 THEN ROUND(aggregated.total_own_goals / aggregated.games_played, 2) ELSE 0 END AS ogpg,
    CASE WHEN aggregated.games_played > 0 THEN ROUND(aggregated.total_blocks / aggregated.games_played, 2) ELSE 0 END AS bpg,
    CASE
        WHEN aggregated.total_pp_used > 0
        THEN ROUND((aggregated.total_pp_plays / aggregated.total_pp_used) * 100, 2)
        ELSE 0
    END AS pp_efficiency
FROM teams t
CROSS JOIN aggregated
WHERE t.id = p_team_id;
$$ ;

-- =====================================================================
-- 5. Leaderboard (top N by metric) — OPTIMIZED
-- Track team from rosters table at season level (most recent team)
-- =====================================================================
CREATE OR REPLACE FUNCTION get_leaderboard(
    p_metric TEXT,
    p_season_id UUID,
    p_limit INT DEFAULT 10
)
RETURNS TABLE(
    rank INT,
    player_name TEXT,
    team_name TEXT,
    value NUMERIC,
    games_played INT
)
LANGUAGE SQL
STABLE
AS $$
WITH season_player_stats AS (
    SELECT
        p.id AS player_id,
        p.display_name,
        COALESCE(ct.team_name, 'Free Agent') AS team_name,
        SUM(ps.points) AS total_points,
        SUM(ps.assists) AS total_assists,
        SUM(ps.goals) AS total_goals,
        SUM(ps.saves) AS total_saves,
        SUM(ps.blocks) AS total_blocks,
        SUM(ps.own_goals) AS total_own_goals,
        SUM(ps.powerup_plays) AS total_pp_plays,
        COUNT(DISTINCT ps.match_id) AS game_count
    FROM player_stats ps
    JOIN players p ON p.id = ps.player_id
    JOIN matches m ON m.id = ps.match_id
        LEFT JOIN LATERAL (
                SELECT t.name AS team_name
                FROM rosters r
                JOIN teams t ON t.id = r.team_id
                WHERE r.player_id = p.id
                    AND r.season_id = p_season_id
                ORDER BY r.joined_at DESC NULLS LAST
                LIMIT 1
        ) ct ON TRUE
    WHERE m.season_id = p_season_id
    GROUP BY p.id, p.display_name, ct.team_name
)
SELECT
    ROW_NUMBER() OVER (ORDER BY 
        CASE p_metric
            WHEN 'points' THEN total_points
            WHEN 'assists' THEN total_assists
            WHEN 'goals' THEN total_goals
            WHEN 'saves' THEN total_saves
            WHEN 'blocks' THEN total_blocks
            WHEN 'own_goals' THEN total_own_goals
            WHEN 'pp_plays' THEN total_pp_plays
            ELSE 0
        END DESC
    )::INT AS rank,
    display_name,
    team_name,
    CASE p_metric
        WHEN 'points' THEN total_points::NUMERIC
        WHEN 'assists' THEN total_assists::NUMERIC
        WHEN 'goals' THEN total_goals::NUMERIC
        WHEN 'saves' THEN total_saves::NUMERIC
        WHEN 'blocks' THEN total_blocks::NUMERIC
        WHEN 'own_goals' THEN total_own_goals::NUMERIC
        WHEN 'pp_plays' THEN total_pp_plays::NUMERIC
        ELSE 0::NUMERIC
    END AS value,
    game_count::INT AS games_played
FROM season_player_stats
ORDER BY value DESC
LIMIT p_limit;
$$ ;


-- =====================================================================
-- 6. Player game-by-game history
-- =====================================================================
CREATE OR REPLACE FUNCTION get_player_history(
    p_player_id UUID,
    p_season_id UUID,
    p_limit INT DEFAULT 50
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
SELECT
    m.id,
    m.played_at,
    opp.name AS opponent_name,
    ps.points,
    ps.assists,
    ps.goals,
    ps.saves,
    ps.own_goals,
    ps.blocks,
    ps.powerup_plays,
    ps.powerups_used
FROM player_stats ps
JOIN players p ON p.id = ps.player_id
JOIN matches m ON m.id = ps.match_id
LEFT JOIN LATERAL (
    SELECT r.team_id
    FROM rosters r
    WHERE r.player_id = p.id
      AND r.season_id = p_season_id
      AND (r.joined_at IS NULL OR m.played_at IS NULL OR m.played_at >= r.joined_at)
      AND (r.left_at IS NULL OR m.played_at IS NULL OR m.played_at < r.left_at)
    ORDER BY r.joined_at DESC NULLS LAST
    LIMIT 1
) roster ON TRUE
LEFT JOIN teams opp ON opp.id = CASE
    WHEN roster.team_id = m.team1_id THEN m.team2_id
    WHEN roster.team_id = m.team2_id THEN m.team1_id
    ELSE NULL
END
WHERE p.id = p_player_id
  AND m.season_id = p_season_id
ORDER BY m.played_at DESC
LIMIT p_limit;
$$ ;

-- =====================================================================
-- 7. Compare two players
-- =====================================================================
CREATE OR REPLACE FUNCTION compare_players(
    p_player_a_id UUID,
    p_player_b_id UUID,
    p_season_id UUID
)
RETURNS TABLE(
    metric TEXT,
    player_a_value NUMERIC,
    player_b_value NUMERIC,
    diff NUMERIC
)
LANGUAGE SQL
STABLE
AS $$
WITH stats_a AS (
    SELECT
        COUNT(ps.id)::INT AS games,
        COALESCE(SUM(ps.points), 0) AS points,
        COALESCE(SUM(ps.assists), 0) AS assists,
        COALESCE(SUM(ps.goals), 0) AS goals,
        COALESCE(SUM(ps.saves), 0) AS saves,
        COALESCE(SUM(ps.blocks), 0) AS blocks,
        COALESCE(SUM(ps.powerup_plays), 0) AS pp_plays,
        CASE WHEN SUM(ps.powerups_used) > 0 THEN (SUM(ps.powerup_plays)::NUMERIC / SUM(ps.powerups_used)::NUMERIC * 100) ELSE 0 END AS pp_eff
    FROM player_stats ps
    JOIN matches m ON m.id = ps.match_id
    WHERE ps.player_id = p_player_a_id AND m.season_id = p_season_id
),
stats_b AS (
    SELECT
        COUNT(ps.id)::INT AS games,
        COALESCE(SUM(ps.points), 0) AS points,
        COALESCE(SUM(ps.assists), 0) AS assists,
        COALESCE(SUM(ps.goals), 0) AS goals,
        COALESCE(SUM(ps.saves), 0) AS saves,
        COALESCE(SUM(ps.blocks), 0) AS blocks,
        COALESCE(SUM(ps.powerup_plays), 0) AS pp_plays,
        CASE WHEN SUM(ps.powerups_used) > 0 THEN (SUM(ps.powerup_plays)::NUMERIC / SUM(ps.powerups_used)::NUMERIC * 100) ELSE 0 END AS pp_eff
    FROM player_stats ps
    JOIN matches m ON m.id = ps.match_id
    WHERE ps.player_id = p_player_b_id AND m.season_id = p_season_id
)
SELECT 'Points'::TEXT, (SELECT points FROM stats_a)::NUMERIC, (SELECT points FROM stats_b)::NUMERIC, 
        ((SELECT points FROM stats_a) - (SELECT points FROM stats_b))::NUMERIC
UNION ALL
SELECT 'Assists'::TEXT, (SELECT assists FROM stats_a)::NUMERIC, (SELECT assists FROM stats_b)::NUMERIC,
        ((SELECT assists FROM stats_a) - (SELECT assists FROM stats_b))::NUMERIC
UNION ALL
SELECT 'Goals'::TEXT, (SELECT goals FROM stats_a)::NUMERIC, (SELECT goals FROM stats_b)::NUMERIC,
        ((SELECT goals FROM stats_a) - (SELECT goals FROM stats_b))::NUMERIC
UNION ALL
SELECT 'Saves'::TEXT, (SELECT saves FROM stats_a)::NUMERIC, (SELECT saves FROM stats_b)::NUMERIC,
        ((SELECT saves FROM stats_a) - (SELECT saves FROM stats_b))::NUMERIC
UNION ALL
SELECT 'Blocks'::TEXT, (SELECT blocks FROM stats_a)::NUMERIC, (SELECT blocks FROM stats_b)::NUMERIC,
        ((SELECT blocks FROM stats_a) - (SELECT blocks FROM stats_b))::NUMERIC
UNION ALL
SELECT 'PP Efficiency %'::TEXT, (SELECT pp_eff FROM stats_a)::NUMERIC, (SELECT pp_eff FROM stats_b)::NUMERIC,
        ((SELECT pp_eff FROM stats_a) - (SELECT pp_eff FROM stats_b))::NUMERIC;
$$ ;

-- =====================================================================
-- 8. Season summary stats
-- =====================================================================
CREATE OR REPLACE FUNCTION get_season_summary(p_season_id UUID)
RETURNS TABLE(
    total_matches INT,
    total_teams INT,
    total_players INT,
    avg_points_per_match NUMERIC,
    top_scorer_name TEXT,
    top_scorer_points INT,
    top_team_name TEXT,
    top_team_points INT
)
LANGUAGE SQL
STABLE
AS $$
WITH season_stat_rows AS (
    SELECT
        ps.player_id,
        ps.points,
        ps.assists,
        ps.goals,
        ps.saves,
        ps.own_goals,
        ps.blocks,
        ps.powerup_plays,
        ps.powerups_used,
        roster.team_id
    FROM player_stats ps
    JOIN matches m ON m.id = ps.match_id
    LEFT JOIN LATERAL (
        SELECT r.team_id
        FROM rosters r
        WHERE r.player_id = ps.player_id
          AND r.season_id = p_season_id
          AND (r.joined_at IS NULL OR m.played_at IS NULL OR m.played_at >= r.joined_at)
          AND (r.left_at IS NULL OR m.played_at IS NULL OR m.played_at < r.left_at)
        ORDER BY r.joined_at DESC NULLS LAST
        LIMIT 1
    ) roster ON TRUE
    WHERE m.season_id = p_season_id
),
team_totals AS (
    SELECT
        team_id,
        COALESCE(SUM(points), 0)::INT AS total_points
    FROM season_stat_rows
    WHERE team_id IS NOT NULL
    GROUP BY team_id
),
ordered_teams AS (
    SELECT
        t.id,
        t.name,
        tt.total_points,
        ROW_NUMBER() OVER (ORDER BY tt.total_points DESC) AS rn
    FROM team_totals tt
    JOIN teams t ON t.id = tt.team_id
)
SELECT
    (SELECT COUNT(*) FROM matches WHERE season_id = p_season_id)::INT,
    (SELECT COUNT(*) FROM ordered_teams)::INT,
    (SELECT COUNT(DISTINCT player_id) FROM player_stats ps JOIN matches m ON m.id = ps.match_id WHERE m.season_id = p_season_id)::INT,
    (SELECT ROUND(AVG(points)::NUMERIC, 2) FROM player_stats ps JOIN matches m ON m.id = ps.match_id WHERE m.season_id = p_season_id),
    (SELECT COALESCE(p.display_name, 'N/A') FROM players p
     JOIN player_stats ps ON ps.player_id = p.id
     JOIN matches m ON m.id = ps.match_id
     WHERE m.season_id = p_season_id
     GROUP BY p.id, p.display_name
     ORDER BY SUM(ps.points) DESC LIMIT 1),
    (SELECT COALESCE(SUM(ps.points), 0)::INT
     FROM player_stats ps
     JOIN players p ON ps.player_id = p.id
     JOIN matches m ON m.id = ps.match_id
     WHERE m.season_id = p_season_id
     GROUP BY p.id, p.display_name
     ORDER BY SUM(ps.points) DESC LIMIT 1),
    (SELECT COALESCE(name, 'N/A') FROM ordered_teams WHERE rn = 1),
    (SELECT COALESCE(total_points, 0) FROM ordered_teams WHERE rn = 1);
$$ ;

COMMIT;
