-- Test script for the league rules in db/migrations/init.sql: season phases, division assignments,
-- one team per player, match validation, series progression for every bracket kind, finalize and
-- rollover, and the public-access lockdown.
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/tests/league_schema_test.sql
-- (On plain Postgres run db/tests/local_roles.sql and init.sql first.)
-- Everything runs inside one transaction and is rolled back. Success prints "ALL LEAGUE TESTS PASSED".

BEGIN;

CREATE FUNCTION pg_temp.expect_hint(p_sql text, p_hint text) RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    v_hint text;
    v_msg text;
BEGIN
    BEGIN
        EXECUTE p_sql;
    EXCEPTION WHEN others THEN
        GET STACKED DIAGNOSTICS v_hint = PG_EXCEPTION_HINT, v_msg = MESSAGE_TEXT;
        IF COALESCE(v_hint, '') <> COALESCE(p_hint, '') THEN
            RAISE EXCEPTION 'expected hint % for [%], got % (%)', p_hint, p_sql, v_hint, v_msg;
        END IF;
        RETURN;
    END;
    RAISE EXCEPTION 'expected hint % for [%], but it succeeded', p_hint, p_sql;
END;
$$;

-- Report a game: team a beats team b (scores a_score / b_score).
CREATE FUNCTION pg_temp.game(p_season uuid, p_a uuid, p_b uuid, p_a_score int, p_b_score int, p_series uuid DEFAULT NULL)
RETURNS uuid LANGUAGE sql AS $$
    INSERT INTO matches (season_id, series_id, team1_id, team2_id, team1_score, team2_score)
    VALUES (p_season, p_series, p_a, p_b, p_a_score, p_b_score) RETURNING id;
$$;

DO $$
DECLARE
    d1 uuid; d2 uuid; d3 uuid; g_east uuid; g_west uuid;
    s1 uuid; s2 uuid;
    t uuid[] := ARRAY[]::uuid[];
    v_team uuid;
    p1 uuid; p2 uuid;
    v_match uuid;
    v_count integer;
    v_plan jsonb;
    b_champ uuid; b_rel uuid; b_promo uuid; b_cross uuid;
    sf1 uuid; sf2 uuid; fin uuid;
    r1a uuid; r1b uuid; r2 uuid;
    pr1 uuid; cr1 uuid;
    v_series series%ROWTYPE;
    i integer;
BEGIN
    -- ------------------------------------------------------------ divisions and tiers
    INSERT INTO divisions (code, name, tier) VALUES ('ZZ1', 'Test Premier', 901) RETURNING id INTO d1;
    INSERT INTO divisions (code, name, tier) VALUES ('ZZ2', 'Test Championship', 902) RETURNING id INTO d2;
    INSERT INTO divisions (code, name, tier) VALUES ('ZZ3', 'Test League One', 903) RETURNING id INTO d3;
    PERFORM pg_temp.expect_hint($q$INSERT INTO divisions (code, name, tier) VALUES ('zz1', 'Dup code', 950)$q$, NULL);

    -- Swapping two tiers in one call works (deferred exclusion constraint).
    PERFORM reorder_divisions(ARRAY[d2, d1, d3]);
    SET CONSTRAINTS ex_divisions_tier IMMEDIATE;
    ASSERT (SELECT tier FROM divisions WHERE id = d2) = 1, 'reorder should put d2 first';
    PERFORM reorder_divisions(ARRAY[d1, d2, d3]);
    SET CONSTRAINTS ex_divisions_tier IMMEDIATE;
    ASSERT (SELECT tier FROM divisions WHERE id = d1) = 1 AND (SELECT tier FROM divisions WHERE id = d3) = 3, 'reorder back';
    SET CONSTRAINTS ex_divisions_tier DEFERRED;

    INSERT INTO division_groups (division_id, name, sort_order) VALUES (d1, 'East', 0) RETURNING id INTO g_east;
    INSERT INTO division_groups (division_id, name, sort_order) VALUES (d1, 'West', 1) RETURNING id INTO g_west;

    -- ------------------------------------------------------------ teams (12: four per division)
    FOR i IN 1..12 LOOP
        INSERT INTO teams (code, name, owner_discord_id) VALUES (format('zz-team-%s', i), format('ZZ Team %s', i), 7000 + i)
        RETURNING id INTO v_team;
        t := t || v_team;
    END LOOP;
    PERFORM pg_temp.expect_hint($q$INSERT INTO teams (code, name) VALUES ('Bad Code', 'Bad')$q$, NULL);
    PERFORM pg_temp.expect_hint($q$INSERT INTO teams (code, name, owner_discord_id) VALUES ('zz-dup-owner', 'Dup owner', 7001)$q$, NULL);

    -- ------------------------------------------------------------ season 1 setup
    INSERT INTO seasons (number, name, active) VALUES (901, 'ZZ Season 1', true) RETURNING id INTO s1;
    FOR i IN 1..4 LOOP
        INSERT INTO season_teams (season_id, team_id, division_id, group_id)
        VALUES (s1, t[i], d1, CASE WHEN i <= 2 THEN g_east ELSE g_west END);
    END LOOP;
    FOR i IN 5..8 LOOP INSERT INTO season_teams (season_id, team_id, division_id) VALUES (s1, t[i], d2); END LOOP;
    FOR i IN 9..12 LOOP INSERT INTO season_teams (season_id, team_id, division_id) VALUES (s1, t[i], d3); END LOOP;

    PERFORM pg_temp.expect_hint(format(
        $q$UPDATE season_teams SET group_id = %L WHERE season_id = %L AND team_id = %L$q$, g_east, s1, t[5]),
        'GROUP_DIVISION_MISMATCH');

    -- One team per player per season; rosters need a team in the season.
    INSERT INTO players (display_name, discord_id) VALUES ('ZZ P1', 8001) RETURNING id INTO p1;
    INSERT INTO players (display_name, discord_id) VALUES ('ZZ P2', 8002) RETURNING id INTO p2;
    INSERT INTO rosters (season_id, team_id, player_id) VALUES (s1, t[1], p1);
    INSERT INTO rosters (season_id, team_id, player_id) VALUES (s1, t[5], p2);
    PERFORM pg_temp.expect_hint(format(
        $q$INSERT INTO rosters (season_id, team_id, player_id) VALUES (%L, %L, %L)$q$, s1, t[2], p1), NULL);
    -- A signing cannot take a player off another team; a transfer moves them.
    PERFORM pg_temp.expect_hint(format(
        $q$INSERT INTO transactions (season_id, type, player_id, to_team_id) VALUES (%L, 'sign', %L, %L)$q$, s1, p1, t[2]),
        'ROSTER_PLAYER_ON_TEAM');
    INSERT INTO transactions (season_id, type, player_id, from_team_id, to_team_id) VALUES (s1, 'transfer', p1, t[1], t[2]);
    SELECT count(*) INTO v_count FROM rosters WHERE season_id = s1 AND player_id = p1 AND left_at IS NULL;
    ASSERT v_count = 1, 'a transfer should leave exactly one active roster row';
    ASSERT (SELECT team_id FROM rosters WHERE season_id = s1 AND player_id = p1 AND left_at IS NULL) = t[2], 'p1 now on t2';

    -- ------------------------------------------------------------ regular-season games
    PERFORM pg_temp.expect_hint(format($q$SELECT pg_temp.game(%L, %L, %L, 10, 5)$q$, s1, t[1], t[2]), 'MATCH_NOT_REGULAR_PHASE');
    PERFORM pg_temp.expect_hint(format($q$UPDATE seasons SET phase = 'postseason' WHERE id = %L$q$, s1), 'SEASON_BAD_TRANSITION');
    UPDATE seasons SET phase = 'regular' WHERE id = s1;
    ASSERT (SELECT started_at FROM seasons WHERE id = s1) IS NOT NULL, 'started_at set';

    PERFORM pg_temp.expect_hint(format($q$SELECT pg_temp.game(%L, %L, %L, 10, 5)$q$, s1, t[1], t[5]), 'MATCH_DIVISION_MISMATCH');
    PERFORM pg_temp.expect_hint(format($q$SELECT pg_temp.game(%L, %L, %L, 5, 5)$q$, s1, t[1], t[2]), NULL);

    v_match := pg_temp.game(s1, t[1], t[2], 10, 5);
    ASSERT (SELECT division_id FROM matches WHERE id = v_match) = d1, 'division filled from season_teams';
    -- player_stats team is filled from the roster (p1 is on t2 now).
    INSERT INTO player_stats (match_id, player_id, points, powerup_plays, powerups_used) VALUES (v_match, p1, 5, 1, 4);
    ASSERT (SELECT team_id FROM player_stats WHERE match_id = v_match AND player_id = p1) = t[2], 'stat team from roster';
    ASSERT (SELECT powerup_percentage FROM player_stats WHERE match_id = v_match AND player_id = p1) = 25.00, 'pp pct';

    PERFORM pg_temp.expect_hint(format($q$UPDATE matches SET team2_id = %L WHERE id = %L$q$, t[3], v_match), 'MATCH_IMMUTABLE');
    -- Teams with games cannot change division.
    PERFORM pg_temp.expect_hint(format(
        $q$UPDATE season_teams SET division_id = %L, group_id = NULL WHERE season_id = %L AND team_id = %L$q$, d2, s1, t[1]),
        'SEASON_TEAM_HAS_MATCHES');

    -- A small round: everyone in each division plays once, team with the lower index wins.
    PERFORM pg_temp.game(s1, t[1], t[3], 9, 3);  PERFORM pg_temp.game(s1, t[1], t[4], 9, 2);
    PERFORM pg_temp.game(s1, t[2], t[3], 8, 4);  PERFORM pg_temp.game(s1, t[2], t[4], 8, 1);
    PERFORM pg_temp.game(s1, t[3], t[4], 7, 6);
    PERFORM pg_temp.game(s1, t[5], t[6], 9, 3);  PERFORM pg_temp.game(s1, t[7], t[8], 9, 3);
    PERFORM pg_temp.game(s1, t[9], t[10], 9, 3); PERFORM pg_temp.game(s1, t[11], t[12], 9, 3);

    ASSERT (SELECT wins FROM team_records WHERE season_id = s1 AND team_id = t[1]) = 3, 't1 3 wins';
    ASSERT (SELECT losses FROM team_records WHERE season_id = s1 AND team_id = t[4]) = 3, 't4 3 losses';
    ASSERT (SELECT points_for FROM team_records WHERE season_id = s1 AND team_id = t[1]) = 28, 't1 points for';

    -- Voiding a regular game during the regular season is allowed and removes it from records.
    v_match := pg_temp.game(s1, t[4], t[3], 20, 1);
    UPDATE matches SET status = 'void', voided_by = 1 WHERE id = v_match;
    ASSERT (SELECT voided_at FROM matches WHERE id = v_match) IS NOT NULL, 'voided_at set';
    ASSERT (SELECT wins FROM team_records WHERE season_id = s1 AND team_id = t[4]) = 0, 'void ignored in records';

    -- ------------------------------------------------------------ postseason plan
    -- Championship (D1): semis 1v4, 2v3 (bo1), final bo3.
    -- Relegation (D1 bottom 2 -> loser relegated): t3 v t4, single series, loser feeds nothing.
    --   (relegation teams overlap the championship here only to exercise both brackets in one test)
    -- Promotion (D2): t5 v t6 then winner v t7 (bye for t7), 1 spot.
    -- Cross (D2 <-> D3): t8 (D2) v t9 (D3); winner gets the D2 spot.
    v_plan := jsonb_build_object('brackets', jsonb_build_array(
        jsonb_build_object('kind', 'championship', 'division_id', d1, 'spots', 1,
            'entries', jsonb_build_array(
                jsonb_build_object('team_id', t[1], 'seed', 1, 'source_division_id', d1),
                jsonb_build_object('team_id', t[2], 'seed', 2, 'source_division_id', d1),
                jsonb_build_object('team_id', t[3], 'seed', 3, 'source_division_id', d1),
                jsonb_build_object('team_id', t[4], 'seed', 4, 'source_division_id', d1)),
            'series', jsonb_build_array(
                jsonb_build_object('key', 'c-1-0', 'round', 1, 'position', 0, 'team1_id', t[1], 'team2_id', t[4],
                    'team1_seed', 1, 'team2_seed', 4, 'best_of', 1, 'winner_to', 'c-2-0', 'winner_slot', 1),
                jsonb_build_object('key', 'c-1-1', 'round', 1, 'position', 1, 'team1_id', t[2], 'team2_id', t[3],
                    'team1_seed', 2, 'team2_seed', 3, 'best_of', 1, 'winner_to', 'c-2-0', 'winner_slot', 2),
                jsonb_build_object('key', 'c-2-0', 'round', 2, 'position', 0, 'best_of', 3))),
        jsonb_build_object('kind', 'relegation', 'division_id', d1, 'spots', 1,
            'entries', jsonb_build_array(
                jsonb_build_object('team_id', t[3], 'seed', 1, 'source_division_id', d1),
                jsonb_build_object('team_id', t[4], 'seed', 2, 'source_division_id', d1)),
            'series', jsonb_build_array(
                jsonb_build_object('key', 'r-1-0', 'round', 1, 'position', 0, 'team1_id', t[3], 'team2_id', t[4],
                    'team1_seed', 1, 'team2_seed', 2, 'best_of', 1))),
        jsonb_build_object('kind', 'promotion', 'division_id', d2, 'spots', 1,
            'entries', jsonb_build_array(
                jsonb_build_object('team_id', t[7], 'seed', 1, 'source_division_id', d2),
                jsonb_build_object('team_id', t[5], 'seed', 2, 'source_division_id', d2),
                jsonb_build_object('team_id', t[6], 'seed', 3, 'source_division_id', d2)),
            'series', jsonb_build_array(
                jsonb_build_object('key', 'p-1-0', 'round', 1, 'position', 0, 'team1_id', t[5], 'team2_id', t[6],
                    'team1_seed', 2, 'team2_seed', 3, 'best_of', 1, 'winner_to', 'p-2-0', 'winner_slot', 2),
                jsonb_build_object('key', 'p-2-0', 'round', 2, 'position', 0, 'team1_id', t[7], 'team1_seed', 1, 'best_of', 1))),
        jsonb_build_object('kind', 'promo_rel', 'division_id', d2, 'lower_division_id', d3, 'spots', 1,
            'entries', jsonb_build_array(
                jsonb_build_object('team_id', t[8], 'seed', 1, 'source_division_id', d2),
                jsonb_build_object('team_id', t[9], 'seed', 2, 'source_division_id', d3)),
            'series', jsonb_build_array(
                jsonb_build_object('key', 'x-1-0', 'round', 1, 'position', 0, 'team1_id', t[8], 'team2_id', t[9],
                    'team1_seed', 1, 'team2_seed', 2, 'best_of', 1)))
    ));

    INSERT INTO standings (season_id, team_id, division_id, rank) VALUES (s1, t[1], d1, 1);
    ASSERT create_postseason(s1, v_plan) = 4, 'four brackets created';
    ASSERT (SELECT phase FROM seasons WHERE id = s1) = 'postseason', 'phase postseason';
    ASSERT (SELECT final FROM standings WHERE season_id = s1 AND team_id = t[1]), 'standings locked';
    PERFORM pg_temp.expect_hint(format($q$SELECT create_postseason(%L, '{}')$q$, s1), 'SEASON_WRONG_PHASE');

    SELECT id INTO b_champ FROM brackets WHERE season_id = s1 AND kind = 'championship';
    SELECT id INTO b_rel FROM brackets WHERE season_id = s1 AND kind = 'relegation';
    SELECT id INTO b_promo FROM brackets WHERE season_id = s1 AND kind = 'promotion';
    SELECT id INTO b_cross FROM brackets WHERE season_id = s1 AND kind = 'promo_rel';
    SELECT id INTO sf1 FROM series WHERE bracket_id = b_champ AND round = 1 AND position = 0;
    SELECT id INTO sf2 FROM series WHERE bracket_id = b_champ AND round = 1 AND position = 1;
    SELECT id INTO fin FROM series WHERE bracket_id = b_champ AND round = 2;
    SELECT id INTO r1a FROM series WHERE bracket_id = b_rel;
    SELECT id INTO pr1 FROM series WHERE bracket_id = b_promo AND round = 1;
    SELECT id INTO r2 FROM series WHERE bracket_id = b_promo AND round = 2;
    SELECT id INTO cr1 FROM series WHERE bracket_id = b_cross;

    ASSERT (SELECT status FROM series WHERE id = sf1) = 'ready', 'semi ready';
    ASSERT (SELECT status FROM series WHERE id = fin) = 'waiting', 'final waiting';
    ASSERT (SELECT status FROM series WHERE id = r2) = 'waiting', 'promo final waits for round 1 winner';

    -- Regular games are closed now; series games must match the series.
    PERFORM pg_temp.expect_hint(format($q$SELECT pg_temp.game(%L, %L, %L, 10, 5)$q$, s1, t[1], t[2]), 'MATCH_NOT_REGULAR_PHASE');
    PERFORM pg_temp.expect_hint(format($q$SELECT pg_temp.game(%L, %L, %L, 10, 5, %L)$q$, s1, t[1], t[2], sf1), 'MATCH_SERIES_TEAMS');
    PERFORM pg_temp.expect_hint(format($q$SELECT pg_temp.game(%L, %L, %L, 10, 5, %L)$q$, s1, t[1], t[2], fin), 'MATCH_SERIES_NOT_READY');
    PERFORM pg_temp.expect_hint(format($q$UPDATE matches SET status = 'void' WHERE season_id = %L AND stage = 'regular'$q$, s1), 'MATCH_LOCKED');

    -- Seeds can be swapped before the bracket starts.
    PERFORM swap_bracket_seeds(b_champ, t[3], t[4]);
    ASSERT (SELECT team2_id FROM series WHERE id = sf1) = t[3], 'swap moved t3 into the 1v4 slot';
    ASSERT (SELECT seed FROM bracket_entries WHERE bracket_id = b_champ AND team_id = t[3]) = 4, 't3 now seed 4';
    PERFORM swap_bracket_seeds(b_champ, t[3], t[4]);

    -- Semis: t1 beats t4, t2 beats t3 -> final t1 v t2.
    v_match := pg_temp.game(s1, t[4], t[1], 3, 9, sf1);
    ASSERT (SELECT stage FROM matches WHERE id = v_match) = 'championship', 'stage from bracket';
    ASSERT (SELECT division_id FROM matches WHERE id = v_match) = d1, 'division from bracket';
    PERFORM pg_temp.expect_hint(format($q$SELECT swap_bracket_seeds(%L, %L, %L)$q$, b_champ, t[1], t[2]), 'BRACKET_STARTED');
    PERFORM pg_temp.game(s1, t[2], t[3], 8, 7, sf2);
    SELECT * INTO v_series FROM series WHERE id = fin;
    ASSERT v_series.team1_id = t[1] AND v_series.team2_id = t[2] AND v_series.status = 'ready', 'final fed';
    ASSERT v_series.team1_seed = 1 AND v_series.team2_seed = 2, 'seeds fed';

    -- Final is best of 3: t2 wins once, t1 twice.
    PERFORM pg_temp.game(s1, t[2], t[1], 10, 9, fin);
    ASSERT (SELECT status FROM series WHERE id = fin) = 'ready', 'final still open at 0-1';
    PERFORM pg_temp.game(s1, t[1], t[2], 10, 9, fin);
    v_match := pg_temp.game(s1, t[1], t[2], 11, 9, fin);
    SELECT * INTO v_series FROM series WHERE id = fin;
    ASSERT v_series.winner_id = t[1] AND v_series.team1_wins = 2 AND v_series.team2_wins = 1, 'final won 2-1';
    ASSERT (SELECT status FROM brackets WHERE id = b_champ) = 'completed', 'championship completed';
    PERFORM pg_temp.expect_hint(format($q$SELECT pg_temp.game(%L, %L, %L, 10, 5, %L)$q$, s1, t[1], t[2], fin), 'MATCH_SERIES_NOT_READY');

    -- Voiding the deciding game reopens the final and the bracket.
    UPDATE matches SET status = 'void' WHERE id = v_match;
    ASSERT (SELECT status FROM series WHERE id = fin) = 'ready', 'final reopened';
    ASSERT (SELECT status FROM brackets WHERE id = b_champ) = 'active', 'bracket reopened';
    PERFORM pg_temp.game(s1, t[1], t[2], 11, 9, fin);

    -- A semi result cannot be voided while the final has games.
    PERFORM pg_temp.expect_hint(format($q$UPDATE matches SET status = 'void' WHERE series_id = %L$q$, sf1), 'SERIES_DOWNSTREAM_PLAYED');

    -- Relegation: t4 loses -> relegated.
    PERFORM pg_temp.game(s1, t[3], t[4], 9, 8, r1a);
    ASSERT (SELECT loser_id FROM series WHERE id = r1a) = t[4], 'relegation loser t4';

    -- Promotion with a bye: t6 upsets t5, then t6 beats t7.
    PERFORM pg_temp.game(s1, t[6], t[5], 9, 8, pr1);
    ASSERT (SELECT team2_id FROM series WHERE id = r2) = t[6] AND (SELECT status FROM series WHERE id = r2) = 'ready', 'promo final fed';
    PERFORM pg_temp.game(s1, t[6], t[7], 9, 8, r2);
    ASSERT (SELECT winner_id FROM series WHERE id = r2) = t[6], 't6 wins promotion';

    -- Cross: forfeit forced by an admin, t9 (D3) takes the D2 spot.
    PERFORM pg_temp.expect_hint(format($q$SELECT finalize_season(%L, %L, '[]')$q$, s1, t[1]), 'BRACKETS_IN_PROGRESS');
    UPDATE series SET forced_winner_id = t[9] WHERE id = cr1;
    PERFORM recompute_series(cr1);
    ASSERT (SELECT winner_id FROM series WHERE id = cr1) = t[9] AND (SELECT status FROM brackets WHERE id = b_cross) = 'completed', 'forced winner';
    SELECT stage INTO STRICT v_series.status FROM matches WHERE series_id = pr1 LIMIT 1;
    ASSERT v_series.status = 'promotion', 'promotion stage';

    -- ------------------------------------------------------------ finalize and roll over
    PERFORM finalize_season(s1, t[1], jsonb_build_array(
        jsonb_build_object('team_id', t[4], 'from_division_id', d1, 'to_division_id', d2, 'reason', 'relegated_playoff'),
        jsonb_build_object('team_id', t[6], 'from_division_id', d2, 'to_division_id', d1, 'reason', 'promoted_playoff'),
        jsonb_build_object('team_id', t[8], 'from_division_id', d2, 'to_division_id', d3, 'reason', 'relegated_playoff'),
        jsonb_build_object('team_id', t[9], 'from_division_id', d3, 'to_division_id', d2, 'reason', 'promoted_playoff')));
    ASSERT (SELECT phase FROM seasons WHERE id = s1) = 'offseason', 'offseason';
    ASSERT (SELECT champion_team_id FROM seasons WHERE id = s1) = t[1], 'champion recorded';
    PERFORM pg_temp.expect_hint(format($q$UPDATE matches SET status = 'void' WHERE series_id = %L$q$, fin), 'MATCH_LOCKED');

    s2 := start_next_season(s1, 'ZZ Season 2');
    ASSERT (SELECT active FROM seasons WHERE id = s1) = false AND (SELECT phase FROM seasons WHERE id = s1) = 'completed', 's1 completed';
    ASSERT (SELECT number FROM seasons WHERE id = s2) = 902 AND (SELECT active FROM seasons WHERE id = s2), 's2 active';
    ASSERT (SELECT division_id FROM season_teams WHERE season_id = s2 AND team_id = t[4]) = d2, 't4 relegated to d2';
    ASSERT (SELECT division_id FROM season_teams WHERE season_id = s2 AND team_id = t[6]) = d1, 't6 promoted to d1';
    ASSERT (SELECT group_id FROM season_teams WHERE season_id = s2 AND team_id = t[6]) IS NULL, 'promoted team has no group yet';
    ASSERT (SELECT group_id FROM season_teams WHERE season_id = s2 AND team_id = t[1]) = g_east, 'staying team keeps group';
    ASSERT (SELECT division_id FROM season_teams WHERE season_id = s1 AND team_id = t[4]) = d1, 'history kept: t4 was d1 in s1';
    SELECT count(*) INTO v_count FROM season_teams WHERE season_id = s2;
    ASSERT v_count = 12, 'every team carried over';
    ASSERT (SELECT team_id FROM rosters WHERE season_id = s2 AND player_id = p1 AND left_at IS NULL) = t[2], 'roster carried';
    ASSERT (SELECT bool_and(applied) FROM season_movements WHERE season_id = s1), 'movements applied';
    PERFORM pg_temp.expect_hint(format($q$SELECT start_next_season(%L, 'again')$q$, s1), 'SEASON_WRONG_PHASE');

    -- ------------------------------------------------------------ stats functions
    ASSERT (SELECT total_points FROM get_player_totals(p1, s1)) = 5, 'player totals';
    ASSERT (SELECT count(*) FROM get_player_totals(p1, s1, d2)) = 0, 'division filter excludes other divisions';
    ASSERT (SELECT player_name FROM get_leaderboard('points', s1, 5, 0, d1) LIMIT 1) = 'ZZ P1', 'leaderboard';

    -- ------------------------------------------------------------ lockdown
    ASSERT NOT has_table_privilege('anon', 'public.teams', 'SELECT'), 'anon cannot read base tables';
    ASSERT has_table_privilege('service_role', 'public.teams', 'SELECT'), 'service_role reads';

    -- ------------------------------------------------------------ website views
    ASSERT has_schema_privilege('anon', 'api', 'USAGE'), 'anon can use the api schema';
    ASSERT has_table_privilege('anon', 'api.standings', 'SELECT'), 'anon reads api views';
    ASSERT NOT has_table_privilege('anon', 'api.standings', 'INSERT'), 'api views are read-only';
    ASSERT NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'api' AND column_name IN ('owner_discord_id', 'discord_id', 'recorded_by', 'voided_by', 'settings', 'metadata')
    ), 'api views expose no discord ids or internals';
    ASSERT (SELECT count(*) FROM api.matches WHERE season_id = s1) = (SELECT count(*) FROM matches WHERE season_id = s1 AND status = 'final'),
        'api.matches hides voided games';
    SELECT count(*) INTO v_count FROM teams;
    SET LOCAL ROLE anon;
    ASSERT (SELECT count(*) FROM api.teams) = v_count, 'anon reads teams through the view';
    RESET ROLE;

    RAISE NOTICE 'ALL LEAGUE TESTS PASSED';
END;
$$;

ROLLBACK;
