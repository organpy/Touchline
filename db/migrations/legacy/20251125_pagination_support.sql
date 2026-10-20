-- 2025-11-25: Add pagination offset support to stats functions
-- Updates get_leaderboard and get_player_history to support server-side pagination

-- Recreate get_leaderboard with offset parameter
DROP FUNCTION IF EXISTS get_leaderboard(TEXT, UUID, INT);

CREATE OR REPLACE FUNCTION get_leaderboard(
    p_metric TEXT,
    p_season_id UUID,
    p_limit INT DEFAULT 10,
    p_offset INT DEFAULT 0
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
),
ranked_stats AS (
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
)
SELECT * FROM ranked_stats
ORDER BY rank
LIMIT p_limit OFFSET p_offset;
$$ ;


-- Recreate get_player_history with offset parameter
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
SELECT
    m.id,
    m.played_at,
    COALESCE(
        CASE WHEN m.team1_id = r.team_id THEN t2.name ELSE t1.name END,
        'Unknown'
    ),
    COALESCE(ps.points, 0)::INT,
    COALESCE(ps.assists, 0)::INT,
    COALESCE(ps.goals, 0)::INT,
    COALESCE(ps.saves, 0)::INT,
    COALESCE(ps.own_goals, 0)::INT,
    COALESCE(ps.blocks, 0)::INT,
    COALESCE(ps.powerup_plays, 0)::INT,
    COALESCE(ps.powerups_used, 0)::INT
FROM matches m
JOIN teams t1 ON m.team1_id = t1.id
JOIN teams t2 ON m.team2_id = t2.id
LEFT JOIN rosters r ON r.season_id = m.season_id AND r.player_id = p_player_id
LEFT JOIN player_stats ps ON ps.match_id = m.id AND ps.player_id = p_player_id
WHERE m.season_id = p_season_id
    AND (ps.player_id = p_player_id OR r.player_id = p_player_id)
ORDER BY m.played_at DESC NULLS LAST
LIMIT p_limit OFFSET p_offset;
$$ ;
