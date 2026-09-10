-- 2026-10-27: Atomic trade execution
-- Executes a trade proposal in a single database transaction:
--   * locks the proposal row so concurrent accepts/declines serialize
--   * re-validates every asset against current ownership at execution time
--   * moves rosters and draft picks
--   * records one 'trade' transaction (+ audit event) per moved asset
--   * marks the proposal executed
-- Any failure raises and rolls the whole trade back. The HINT of each raised
-- error is a stable code the bot maps to a user-facing message.

BEGIN;

CREATE OR REPLACE FUNCTION execute_trade_proposal(
    p_proposal_id uuid,
    p_executed_by bigint
) RETURNS integer
LANGUAGE plpgsql
AS $$
DECLARE
    v_proposal trade_proposals%ROWTYPE;
    v_asset trade_assets%ROWTYPE;
    v_from_team uuid;
    v_to_team uuid;
    v_now timestamptz := now();
    v_rows integer;
    v_moved integer := 0;
    v_tx_id uuid;
    v_force boolean;
BEGIN
    SELECT * INTO v_proposal
    FROM trade_proposals
    WHERE id = p_proposal_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'trade proposal % not found', p_proposal_id
            USING HINT = 'TRADE_NOT_FOUND';
    END IF;

    IF v_proposal.status IS DISTINCT FROM 'pending' THEN
        RAISE EXCEPTION 'trade proposal % is already %', p_proposal_id, v_proposal.status
            USING HINT = 'TRADE_NOT_PENDING';
    END IF;

    IF v_proposal.expires_at IS NOT NULL AND v_proposal.expires_at < v_now THEN
        RAISE EXCEPTION 'trade proposal % expired at %', p_proposal_id, v_proposal.expires_at
            USING HINT = 'TRADE_EXPIRED';
    END IF;

    v_force := COALESCE((v_proposal.metadata ->> 'force_trade')::boolean, false);

    FOR v_asset IN
        SELECT * FROM trade_assets
        WHERE proposal_id = p_proposal_id
        ORDER BY created_at, id
    LOOP
        IF v_asset.direction = 'outbound' THEN
            v_from_team := v_proposal.initiating_team_id;
            v_to_team := v_proposal.counterparty_team_id;
        ELSIF v_asset.direction = 'inbound' THEN
            v_from_team := v_proposal.counterparty_team_id;
            v_to_team := v_proposal.initiating_team_id;
        ELSE
            RAISE EXCEPTION 'trade asset % has invalid direction %', v_asset.id, v_asset.direction
                USING HINT = 'TRADE_INVALID_ASSET';
        END IF;

        IF v_asset.asset_type = 'player' THEN
            IF v_asset.player_id IS NULL THEN
                RAISE EXCEPTION 'trade asset % is a player without player_id', v_asset.id
                    USING HINT = 'TRADE_INVALID_ASSET';
            END IF;

            UPDATE rosters
            SET left_at = v_now
            WHERE season_id = v_proposal.season_id
              AND player_id = v_asset.player_id
              AND team_id = v_from_team
              AND left_at IS NULL;
            GET DIAGNOSTICS v_rows = ROW_COUNT;

            IF v_rows = 0 THEN
                RAISE EXCEPTION 'player % is no longer on team %', v_asset.player_id, v_from_team
                    USING HINT = 'TRADE_PLAYER_UNAVAILABLE';
            END IF;

            INSERT INTO rosters (season_id, team_id, player_id, joined_at)
            VALUES (v_proposal.season_id, v_to_team, v_asset.player_id, v_now);

        ELSIF v_asset.asset_type = 'pick' THEN
            IF v_asset.draft_pick_id IS NULL THEN
                RAISE EXCEPTION 'trade asset % is a pick without draft_pick_id', v_asset.id
                    USING HINT = 'TRADE_INVALID_ASSET';
            END IF;

            UPDATE draft_picks
            SET current_owner_team_id = v_to_team,
                updated_at = v_now
            WHERE id = v_asset.draft_pick_id
              AND current_owner_team_id = v_from_team;
            GET DIAGNOSTICS v_rows = ROW_COUNT;

            IF v_rows = 0 THEN
                RAISE EXCEPTION 'draft pick % is no longer owned by team %', v_asset.draft_pick_id, v_from_team
                    USING HINT = 'TRADE_PICK_UNAVAILABLE';
            END IF;

        ELSE
            -- cash / other assets carry no ownership to move
            CONTINUE;
        END IF;

        INSERT INTO transactions (season_id, type, player_id, from_team_id, to_team_id, executed_by, details)
        VALUES (
            v_proposal.season_id,
            'trade',
            v_asset.player_id,
            v_from_team,
            v_to_team,
            p_executed_by,
            jsonb_build_object(
                'trade_proposal_id', p_proposal_id,
                'trade_asset_id', v_asset.id,
                'asset_type', v_asset.asset_type,
                'draft_pick_id', v_asset.draft_pick_id,
                'force_trade', v_force,
                'executed_at', v_now
            )
        )
        RETURNING id INTO v_tx_id;

        INSERT INTO transaction_events (transaction_id, event_type, payload)
        VALUES (
            v_tx_id,
            CASE WHEN v_force THEN 'admin_force_trade' ELSE 'trade' END,
            jsonb_build_object('trade_proposal_id', p_proposal_id, 'trade_asset_id', v_asset.id)
        );

        v_moved := v_moved + 1;
    END LOOP;

    IF v_moved = 0 THEN
        RAISE EXCEPTION 'trade proposal % has no players or picks to move', p_proposal_id
            USING HINT = 'TRADE_HAS_NO_ASSETS';
    END IF;

    UPDATE trade_proposals
    SET status = 'executed',
        reviewed_by = p_executed_by,
        updated_at = v_now
    WHERE id = p_proposal_id;

    RETURN v_moved;
END;
$$;

COMMIT;
