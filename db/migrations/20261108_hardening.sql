-- 2026-11-08: Hardening
-- For databases created from the earlier init.sql. A fresh database needs only init.sql, which already includes
-- everything below.
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/migrations/20261108_hardening.sql

BEGIN;

-- ----------------------------------------------------------------------------
-- Safe names: no control characters, backticks, angle brackets or @everyone/@here
-- ----------------------------------------------------------------------------
-- The constraints are NOT VALID so rows saved earlier do not block the migration; new and changed rows are checked
-- right away. To find and fix older rows, then check them all:
--   SELECT id, name FROM teams WHERE NOT is_safe_name(name);   (same for divisions, division_groups, seasons)
--   SELECT id, display_name FROM players WHERE NOT is_safe_name(display_name) OR length(btrim(display_name)) NOT BETWEEN 1 AND 64;
--   ALTER TABLE teams VALIDATE CONSTRAINT ck_teams_name_safe;   (and the others)

CREATE FUNCTION is_safe_name(p_name text) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
    SELECT p_name !~ '[[:cntrl:]`<>]' AND p_name !~* '@(everyone|here)'
$$;
REVOKE EXECUTE ON FUNCTION is_safe_name(text) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION is_safe_name(text) TO service_role;

ALTER TABLE divisions ADD CONSTRAINT ck_divisions_name_safe CHECK (is_safe_name(name)) NOT VALID;
ALTER TABLE division_groups ADD CONSTRAINT ck_division_groups_name_safe CHECK (is_safe_name(name)) NOT VALID;
ALTER TABLE teams ADD CONSTRAINT ck_teams_name_safe CHECK (is_safe_name(name)) NOT VALID;
ALTER TABLE seasons ADD CONSTRAINT ck_seasons_name_safe CHECK (is_safe_name(name)) NOT VALID;
ALTER TABLE players ADD CONSTRAINT ck_players_name
    CHECK (length(btrim(display_name)) BETWEEN 1 AND 64 AND is_safe_name(display_name)) NOT VALID;

-- ----------------------------------------------------------------------------
-- Match rules under a lock: the games-per-matchup limit and series status are checked inside the database, one
-- reporter at a time per pair / series (the bot's own check is only for a friendlier message)
-- ----------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION validate_match() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    v_phase text;
    v_series series%ROWTYPE;
    v_bracket brackets%ROWTYPE;
    v_div1 uuid;
    v_div2 uuid;
    v_limit integer;
    v_played integer;
BEGIN
    SELECT phase INTO v_phase FROM seasons WHERE id = NEW.season_id;

    IF TG_OP = 'UPDATE' THEN
        IF NEW.season_id IS DISTINCT FROM OLD.season_id
           OR NEW.team1_id IS DISTINCT FROM OLD.team1_id
           OR NEW.team2_id IS DISTINCT FROM OLD.team2_id
           OR NEW.series_id IS DISTINCT FROM OLD.series_id
           OR NEW.stage IS DISTINCT FROM OLD.stage
           OR NEW.division_id IS DISTINCT FROM OLD.division_id THEN
            RAISE EXCEPTION 'a game''s teams, season and series cannot change; void it and report it again'
                USING HINT = 'MATCH_IMMUTABLE';
        END IF;
        IF NEW.status IS DISTINCT FROM OLD.status
           OR NEW.team1_score IS DISTINCT FROM OLD.team1_score
           OR NEW.team2_score IS DISTINCT FROM OLD.team2_score THEN
            IF NEW.stage = 'regular' AND v_phase <> 'regular' THEN
                RAISE EXCEPTION 'regular-season results are locked once the regular season has ended'
                    USING HINT = 'MATCH_LOCKED';
            END IF;
            IF NEW.stage <> 'regular' AND v_phase <> 'postseason' THEN
                RAISE EXCEPTION 'playoff results are locked once the postseason has ended'
                    USING HINT = 'MATCH_LOCKED';
            END IF;
            IF NEW.status = 'void' AND OLD.status <> 'void' THEN
                NEW.voided_at := COALESCE(NEW.voided_at, now());
            END IF;
        END IF;
        NEW.updated_at := now();
        RETURN NEW;
    END IF;

    IF NEW.status <> 'final' THEN
        RAISE EXCEPTION 'new games must be final' USING HINT = 'MATCH_INVALID';
    END IF;

    IF NEW.series_id IS NULL THEN
        NEW.stage := 'regular';
        IF v_phase IS DISTINCT FROM 'regular' THEN
            RAISE EXCEPTION 'regular-season games can only be reported during the regular season'
                USING HINT = 'MATCH_NOT_REGULAR_PHASE';
        END IF;
        SELECT division_id INTO v_div1 FROM season_teams WHERE season_id = NEW.season_id AND team_id = NEW.team1_id;
        SELECT division_id INTO v_div2 FROM season_teams WHERE season_id = NEW.season_id AND team_id = NEW.team2_id;
        IF v_div1 IS NULL OR v_div2 IS NULL THEN
            RAISE EXCEPTION 'both teams must be in the season' USING HINT = 'MATCH_TEAM_NOT_IN_SEASON';
        END IF;
        IF v_div1 <> v_div2 OR (NEW.division_id IS NOT NULL AND NEW.division_id <> v_div1) THEN
            RAISE EXCEPTION 'both teams must be in the same division' USING HINT = 'MATCH_DIVISION_MISMATCH';
        END IF;
        NEW.division_id := v_div1;

        -- One report at a time per pair: a second reporter waits here, then sees the first game when counting,
        -- so two people can never both squeeze in under the division's games-per-matchup limit.
        PERFORM pg_advisory_xact_lock(hashtextextended(
            NEW.season_id::text || ':' || LEAST(NEW.team1_id, NEW.team2_id)::text || ':'
            || GREATEST(NEW.team1_id, NEW.team2_id)::text, 0));
        SELECT COALESCE((settings ->> 'games_per_matchup')::integer, 2) INTO v_limit FROM divisions WHERE id = v_div1;
        SELECT count(*) INTO v_played
        FROM matches
        WHERE season_id = NEW.season_id AND series_id IS NULL AND status = 'final'
          AND LEAST(team1_id, team2_id) = LEAST(NEW.team1_id, NEW.team2_id)
          AND GREATEST(team1_id, team2_id) = GREATEST(NEW.team1_id, NEW.team2_id);
        IF v_played >= v_limit THEN
            RAISE EXCEPTION 'all % games between these teams are already recorded', v_limit USING HINT = 'MATCH_LIMIT';
        END IF;
    ELSE
        -- Lock the series so concurrent reports line up and the status check below sees the latest result.
        SELECT * INTO v_series FROM series WHERE id = NEW.series_id FOR UPDATE;
        SELECT * INTO v_bracket FROM brackets WHERE id = v_series.bracket_id;
        IF v_bracket.season_id IS DISTINCT FROM NEW.season_id THEN
            RAISE EXCEPTION 'series % is not in season %', NEW.series_id, NEW.season_id
                USING HINT = 'MATCH_SERIES_SEASON';
        END IF;
        IF v_phase IS DISTINCT FROM 'postseason' THEN
            RAISE EXCEPTION 'playoff games can only be reported during the postseason'
                USING HINT = 'MATCH_NOT_POSTSEASON';
        END IF;
        IF v_series.status <> 'ready' THEN
            RAISE EXCEPTION 'series % is %', NEW.series_id, v_series.status USING HINT = 'MATCH_SERIES_NOT_READY';
        END IF;
        IF NOT ((NEW.team1_id = v_series.team1_id AND NEW.team2_id = v_series.team2_id)
             OR (NEW.team1_id = v_series.team2_id AND NEW.team2_id = v_series.team1_id)) THEN
            RAISE EXCEPTION 'these teams are not playing series %', NEW.series_id
                USING HINT = 'MATCH_SERIES_TEAMS';
        END IF;
        NEW.stage := v_bracket.kind;
        NEW.division_id := CASE WHEN v_bracket.kind = 'promo_rel' THEN NULL ELSE v_bracket.division_id END;
    END IF;
    RETURN NEW;
END;
$$;

-- ----------------------------------------------------------------------------
-- Save a reported game and its stats in one transaction; a repeated report returns the saved game
-- ----------------------------------------------------------------------------

ALTER TABLE matches ADD COLUMN report_id uuid;
CREATE UNIQUE INDEX ux_matches_report_id ON matches (report_id);

CREATE FUNCTION record_match(p_match jsonb, p_stats jsonb DEFAULT '[]'::jsonb, p_report_id uuid DEFAULT NULL)
RETURNS matches
LANGUAGE plpgsql AS $$
DECLARE
    v_match matches%ROWTYPE;
    v_constraint text;
BEGIN
    IF p_report_id IS NOT NULL THEN
        SELECT * INTO v_match FROM matches WHERE report_id = p_report_id;
        IF FOUND THEN
            RETURN v_match;
        END IF;
    END IF;

    BEGIN
        INSERT INTO matches (season_id, division_id, series_id, team1_id, team2_id, team1_score, team2_score,
                             recorded_by, metadata, report_id)
        SELECT m.season_id, m.division_id, m.series_id, m.team1_id, m.team2_id, m.team1_score, m.team2_score,
               m.recorded_by, m.metadata, p_report_id
        FROM jsonb_to_record(p_match) AS m(season_id uuid, division_id uuid, series_id uuid, team1_id uuid,
                                           team2_id uuid, team1_score integer, team2_score integer,
                                           recorded_by bigint, metadata jsonb)
        RETURNING * INTO v_match;
    EXCEPTION WHEN unique_violation THEN
        -- A concurrent copy of this same report committed first.
        GET STACKED DIAGNOSTICS v_constraint = CONSTRAINT_NAME;
        IF v_constraint = 'ux_matches_report_id' THEN
            SELECT * INTO v_match FROM matches WHERE report_id = p_report_id;
            RETURN v_match;
        END IF;
        RAISE;
    END;

    INSERT INTO player_stats (match_id, player_id, team_id, goals, points, own_goals, assists, saves, blocks,
                              powerup_plays, powerups_used)
    SELECT v_match.id, s.player_id, s.team_id, COALESCE(s.goals, 0), COALESCE(s.points, 0), COALESCE(s.own_goals, 0),
           COALESCE(s.assists, 0), COALESCE(s.saves, 0), COALESCE(s.blocks, 0), COALESCE(s.powerup_plays, 0),
           COALESCE(s.powerups_used, 0)
    FROM jsonb_to_recordset(COALESCE(p_stats, '[]'::jsonb)) AS s(player_id uuid, team_id uuid, goals integer,
                                                                 points integer, own_goals integer, assists integer,
                                                                 saves integer, blocks integer,
                                                                 powerup_plays integer, powerups_used integer);

    RETURN v_match;
END;
$$;
REVOKE EXECUTE ON FUNCTION record_match(jsonb, jsonb, uuid) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION record_match(jsonb, jsonb, uuid) TO service_role;

-- ----------------------------------------------------------------------------
-- Player card: one row per season of a player's career
-- ----------------------------------------------------------------------------

CREATE FUNCTION get_player_career(p_player_id uuid)
RETURNS TABLE(season_id uuid, season_number integer, season_name text, team_name text, games_played integer,
              goals integer, assists integer, saves integer, blocks integer, points integer)
LANGUAGE sql STABLE AS $$
SELECT s.id, s.number, s.name, player_season_team_name(p_player_id, s.id),
       count(sl.id)::integer,
       COALESCE(sum(sl.goals), 0)::integer, COALESCE(sum(sl.assists), 0)::integer,
       COALESCE(sum(sl.saves), 0)::integer, COALESCE(sum(sl.blocks), 0)::integer,
       COALESCE(sum(sl.points), 0)::integer
FROM stat_lines sl
JOIN seasons s ON s.id = sl.season_id
WHERE sl.player_id = p_player_id
GROUP BY s.id, s.number, s.name
ORDER BY s.number
$$;
REVOKE EXECUTE ON FUNCTION get_player_career(uuid) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION get_player_career(uuid) TO service_role;

COMMIT;
