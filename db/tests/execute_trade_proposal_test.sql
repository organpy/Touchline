-- Test script for execute_trade_proposal().
-- Run against a database that has all migrations applied (e.g. a Supabase branch):
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/tests/execute_trade_proposal_test.sql
-- Everything runs inside one transaction and is rolled back; no data is kept.
-- A failed assertion aborts with an error; success prints "ALL TRADE TESTS PASSED".

BEGIN;

DO $$
DECLARE
    t_past timestamptz := now() - interval '1 day';
    v_division uuid;
    v_season uuid;
    v_team_a uuid;
    v_team_b uuid;
    v_player_1 uuid;
    v_player_2 uuid;
    v_pick_1 uuid;
    v_pick_2 uuid;
    v_proposal uuid;
    v_moved integer;
    v_hint text;
    v_count integer;
BEGIN
    -- ---------------------------------------------------------------- fixtures
    INSERT INTO divisions (league_type, name) VALUES ('ZZTEST', 'Trade Test Division') RETURNING id INTO v_division;
    INSERT INTO seasons (division_id, name, active) VALUES (v_division, 'ZZ Test Season', false) RETURNING id INTO v_season;
    INSERT INTO teams (division_id, code, name, owner_discord_id) VALUES (v_division, 'zztest_a', 'Test A', 1001) RETURNING id INTO v_team_a;
    INSERT INTO teams (division_id, code, name, owner_discord_id) VALUES (v_division, 'zztest_b', 'Test B', 1002) RETURNING id INTO v_team_b;
    INSERT INTO players (display_name) VALUES ('ZZ Player One') RETURNING id INTO v_player_1;
    INSERT INTO players (display_name) VALUES ('ZZ Player Two') RETURNING id INTO v_player_2;
    INSERT INTO rosters (season_id, team_id, player_id, joined_at) VALUES (v_season, v_team_a, v_player_1, t_past);
    INSERT INTO rosters (season_id, team_id, player_id, joined_at) VALUES (v_season, v_team_b, v_player_2, t_past);
    INSERT INTO draft_picks (season_id, original_team_id, current_owner_team_id, round, overall)
        VALUES (v_season, v_team_b, v_team_b, 1, 9001) RETURNING id INTO v_pick_1;
    INSERT INTO draft_picks (season_id, original_team_id, current_owner_team_id, round, overall)
        VALUES (v_season, v_team_a, v_team_a, 1, 9002) RETURNING id INTO v_pick_2;

    -- ------------------------------------------- 1. happy path: player out, pick in
    INSERT INTO trade_proposals (guild_id, season_id, initiating_team_id, counterparty_team_id, created_by)
        VALUES (1, v_season, v_team_a, v_team_b, 1001) RETURNING id INTO v_proposal;
    INSERT INTO trade_assets (proposal_id, direction, asset_type, player_id)
        VALUES (v_proposal, 'outbound', 'player', v_player_1);
    INSERT INTO trade_assets (proposal_id, direction, asset_type, draft_pick_id)
        VALUES (v_proposal, 'inbound', 'pick', v_pick_1);

    v_moved := execute_trade_proposal(v_proposal, 1002);
    ASSERT v_moved = 2, format('expected 2 moved assets, got %s', v_moved);

    SELECT count(*) INTO v_count FROM rosters
        WHERE season_id = v_season AND player_id = v_player_1 AND team_id = v_team_b AND left_at IS NULL;
    ASSERT v_count = 1, 'player 1 should be active on team B';
    SELECT count(*) INTO v_count FROM rosters
        WHERE season_id = v_season AND player_id = v_player_1 AND team_id = v_team_a AND left_at IS NULL;
    ASSERT v_count = 0, 'player 1 should have left team A';
    ASSERT (SELECT current_owner_team_id FROM draft_picks WHERE id = v_pick_1) = v_team_a, 'pick 1 should belong to team A';
    ASSERT (SELECT status FROM trade_proposals WHERE id = v_proposal) = 'executed', 'proposal should be executed';
    ASSERT (SELECT reviewed_by FROM trade_proposals WHERE id = v_proposal) = 1002, 'reviewed_by should be the executor';

    SELECT count(*) INTO v_count FROM transactions
        WHERE type = 'trade' AND details ->> 'trade_proposal_id' = v_proposal::text AND executed_by = 1002;
    ASSERT v_count = 2, format('expected 2 trade transactions, got %s', v_count);
    SELECT count(*) INTO v_count FROM transactions
        WHERE type = 'trade' AND player_id = v_player_1 AND from_team_id = v_team_a AND to_team_id = v_team_b;
    ASSERT v_count = 1, 'player transaction should record A -> B';
    SELECT count(*) INTO v_count FROM transactions
        WHERE type = 'trade' AND details ->> 'draft_pick_id' = v_pick_1::text AND from_team_id = v_team_b AND to_team_id = v_team_a;
    ASSERT v_count = 1, 'pick transaction should record B -> A';
    SELECT count(*) INTO v_count FROM transaction_events e JOIN transactions t ON t.id = e.transaction_id
        WHERE t.details ->> 'trade_proposal_id' = v_proposal::text AND e.event_type = 'trade';
    ASSERT v_count = 2, 'each moved asset should have an audit event';

    -- ------------------------------------------- 2. executing twice is refused
    v_hint := NULL;
    BEGIN
        PERFORM execute_trade_proposal(v_proposal, 1002);
    EXCEPTION WHEN others THEN
        GET STACKED DIAGNOSTICS v_hint = PG_EXCEPTION_HINT;
    END;
    ASSERT v_hint = 'TRADE_NOT_PENDING', format('double execute should fail with TRADE_NOT_PENDING, got %s', v_hint);
    SELECT count(*) INTO v_count FROM rosters
        WHERE season_id = v_season AND player_id = v_player_1 AND left_at IS NULL;
    ASSERT v_count = 1, 'double execute must not create extra roster rows';

    -- ------------------- 3. stale asset rolls back the whole trade (pick listed first)
    INSERT INTO trade_proposals (guild_id, season_id, initiating_team_id, counterparty_team_id, created_by)
        VALUES (1, v_season, v_team_a, v_team_b, 1001) RETURNING id INTO v_proposal;
    INSERT INTO trade_assets (proposal_id, direction, asset_type, draft_pick_id, created_at)
        VALUES (v_proposal, 'outbound', 'pick', v_pick_2, now() - interval '2 seconds');
    INSERT INTO trade_assets (proposal_id, direction, asset_type, player_id, created_at)
        VALUES (v_proposal, 'outbound', 'player', v_player_1, now() - interval '1 second');  -- already moved to B

    v_hint := NULL;
    BEGIN
        PERFORM execute_trade_proposal(v_proposal, 1002);
    EXCEPTION WHEN others THEN
        GET STACKED DIAGNOSTICS v_hint = PG_EXCEPTION_HINT;
    END;
    ASSERT v_hint = 'TRADE_PLAYER_UNAVAILABLE', format('stale player should fail with TRADE_PLAYER_UNAVAILABLE, got %s', v_hint);
    ASSERT (SELECT current_owner_team_id FROM draft_picks WHERE id = v_pick_2) = v_team_a, 'pick 2 move must be rolled back';
    ASSERT (SELECT status FROM trade_proposals WHERE id = v_proposal) = 'pending', 'failed proposal must stay pending';

    -- ------------------------------------------- 4. pick no longer owned
    INSERT INTO trade_proposals (guild_id, season_id, initiating_team_id, counterparty_team_id, created_by)
        VALUES (1, v_season, v_team_a, v_team_b, 1001) RETURNING id INTO v_proposal;
    INSERT INTO trade_assets (proposal_id, direction, asset_type, draft_pick_id)
        VALUES (v_proposal, 'inbound', 'pick', v_pick_1);  -- now owned by A, not B

    v_hint := NULL;
    BEGIN
        PERFORM execute_trade_proposal(v_proposal, 1002);
    EXCEPTION WHEN others THEN
        GET STACKED DIAGNOSTICS v_hint = PG_EXCEPTION_HINT;
    END;
    ASSERT v_hint = 'TRADE_PICK_UNAVAILABLE', format('stale pick should fail with TRADE_PICK_UNAVAILABLE, got %s', v_hint);

    -- ------------------------------------------- 5. proposal without assets
    INSERT INTO trade_proposals (guild_id, season_id, initiating_team_id, counterparty_team_id, created_by)
        VALUES (1, v_season, v_team_a, v_team_b, 1001) RETURNING id INTO v_proposal;

    v_hint := NULL;
    BEGIN
        PERFORM execute_trade_proposal(v_proposal, 1002);
    EXCEPTION WHEN others THEN
        GET STACKED DIAGNOSTICS v_hint = PG_EXCEPTION_HINT;
    END;
    ASSERT v_hint = 'TRADE_HAS_NO_ASSETS', format('empty proposal should fail with TRADE_HAS_NO_ASSETS, got %s', v_hint);

    -- ------------------------------------------- 6. unknown proposal
    v_hint := NULL;
    BEGIN
        PERFORM execute_trade_proposal(uuid_generate_v4(), 1002);
    EXCEPTION WHEN others THEN
        GET STACKED DIAGNOSTICS v_hint = PG_EXCEPTION_HINT;
    END;
    ASSERT v_hint = 'TRADE_NOT_FOUND', format('unknown proposal should fail with TRADE_NOT_FOUND, got %s', v_hint);

    -- ------------------------------------------- 7. expired proposal
    INSERT INTO trade_proposals (guild_id, season_id, initiating_team_id, counterparty_team_id, created_by, expires_at)
        VALUES (1, v_season, v_team_b, v_team_a, 1002, now() - interval '1 minute') RETURNING id INTO v_proposal;
    INSERT INTO trade_assets (proposal_id, direction, asset_type, player_id)
        VALUES (v_proposal, 'outbound', 'player', v_player_2);

    v_hint := NULL;
    BEGIN
        PERFORM execute_trade_proposal(v_proposal, 1001);
    EXCEPTION WHEN others THEN
        GET STACKED DIAGNOSTICS v_hint = PG_EXCEPTION_HINT;
    END;
    ASSERT v_hint = 'TRADE_EXPIRED', format('expired proposal should fail with TRADE_EXPIRED, got %s', v_hint);

    RAISE NOTICE 'ALL TRADE TESTS PASSED';
END;
$$;

ROLLBACK;
