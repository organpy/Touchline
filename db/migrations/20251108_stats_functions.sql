-- 2025-11-08: Stats aggregation functions
-- Provides SQL-based helpers for leaderboards, averages, totals, comparisons, and summaries
-- These functions are called by Python async wrappers in queries.py

BEGIN;

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
      AND r.left_at IS NULL
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
            AND r.left_at IS NULL
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
SELECT
    t.id,
    t.name,
    COUNT(DISTINCT ps.player_id)::INT,
    COALESCE(SUM(ps.points), 0)::INT,
    COALESCE(SUM(ps.assists), 0)::INT,
    COALESCE(SUM(ps.goals), 0)::INT,
    COALESCE(SUM(ps.saves), 0)::INT,
    COALESCE(SUM(ps.own_goals), 0)::INT,
    COALESCE(SUM(ps.blocks), 0)::INT,
    COALESCE(SUM(ps.powerup_plays), 0)::INT,
    COALESCE(SUM(ps.powerups_used), 0)::INT
FROM teams t
JOIN player_stats ps ON ps.match_id IN (
    SELECT id FROM matches WHERE (team1_id = t.id OR team2_id = t.id) AND season_id = p_season_id
)
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
SELECT
    t.id,
    t.name,
    COUNT(DISTINCT ps.match_id)::INT AS games_played,
    ROUND(AVG(ps.points)::NUMERIC, 2) AS ppg,
    ROUND(AVG(ps.assists)::NUMERIC, 2) AS apg,
    ROUND(AVG(ps.goals)::NUMERIC, 2) AS gpg,
    ROUND(AVG(ps.saves)::NUMERIC, 2) AS spg,
    ROUND(AVG(ps.own_goals)::NUMERIC, 2) AS ogpg,
    ROUND(AVG(ps.blocks)::NUMERIC, 2) AS bpg,
    CASE 
        WHEN SUM(ps.powerups_used) > 0 
        THEN ROUND((SUM(ps.powerup_plays)::NUMERIC / SUM(ps.powerups_used)::NUMERIC) * 100, 2)
        ELSE 0
    END AS pp_efficiency
FROM teams t
JOIN player_stats ps ON ps.match_id IN (
    SELECT id FROM matches WHERE (team1_id = t.id OR team2_id = t.id) AND season_id = p_season_id
)
WHERE t.id = p_team_id
GROUP BY t.id, t.name;
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
          AND r.left_at IS NULL
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
LEFT JOIN teams t ON t.id IN (m.team1_id, m.team2_id) AND 
    (
        (m.team1_id = (SELECT team_id FROM rosters WHERE player_id = p.id AND season_id = p_season_id AND left_at IS NULL LIMIT 1))
        OR
        (m.team2_id = (SELECT team_id FROM rosters WHERE player_id = p.id AND season_id = p_season_id AND left_at IS NULL LIMIT 1))
    )
LEFT JOIN teams opp ON opp.id = CASE
    WHEN m.team1_id = t.id THEN m.team2_id
    ELSE m.team1_id
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
SELECT
    (SELECT COUNT(*) FROM matches WHERE season_id = p_season_id)::INT,
    (SELECT COUNT(DISTINCT team_id) FROM (
        SELECT DISTINCT team1_id AS team_id FROM matches WHERE season_id = p_season_id
        UNION ALL
        SELECT DISTINCT team2_id FROM matches WHERE season_id = p_season_id
    ) t)::INT,
    (SELECT COUNT(DISTINCT player_id) FROM player_stats ps JOIN matches m ON m.id = ps.match_id WHERE m.season_id = p_season_id)::INT,
    (SELECT ROUND(AVG(points)::NUMERIC, 2) FROM player_stats ps JOIN matches m ON m.id = ps.match_id WHERE m.season_id = p_season_id),
    (SELECT p.display_name FROM players p
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
    (SELECT t.name FROM teams t
     JOIN (
        SELECT DISTINCT team1_id AS team_id FROM matches WHERE season_id = p_season_id
        UNION ALL
        SELECT DISTINCT team2_id FROM matches WHERE season_id = p_season_id
     ) mt ON mt.team_id = t.id
     JOIN player_stats ps ON ps.match_id IN (
        SELECT id FROM matches WHERE (team1_id = t.id OR team2_id = t.id) AND season_id = p_season_id
     )
     GROUP BY t.id, t.name
     ORDER BY SUM(ps.points) DESC LIMIT 1),
    (SELECT COALESCE(SUM(ps.points), 0)::INT FROM teams t
     JOIN player_stats ps ON ps.match_id IN (
        SELECT id FROM matches WHERE (team1_id = t.id OR team2_id = t.id) AND season_id = p_season_id
     )
     GROUP BY t.id, t.name
     ORDER BY SUM(ps.points) DESC LIMIT 1);
$$ ;

COMMIT;
