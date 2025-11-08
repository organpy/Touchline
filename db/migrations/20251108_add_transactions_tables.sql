-- 2025-11-08: Transactions feature tables
-- Creates supporting tables for draft picks, trade proposals, trade assets,
-- pending signing offers, and append-only transaction events log.

BEGIN;

-- ---------------------------------------------------------------------------
-- Draft picks (per season, supports future pick trading)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS draft_picks (
    id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    season_id uuid NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
    original_team_id uuid NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    current_owner_team_id uuid NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    round integer NOT NULL,
    overall integer NOT NULL,
    notes text,
    metadata jsonb,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_draft_picks_season_overall
    ON draft_picks(season_id, overall);
CREATE INDEX IF NOT EXISTS idx_draft_picks_owner
    ON draft_picks(current_owner_team_id);

-- ---------------------------------------------------------------------------
-- Pending signing offers (player confirmation workflow)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pending_signings (
    id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    guild_id bigint NOT NULL,
    season_id uuid NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
    team_id uuid NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    player_id uuid NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    created_by bigint NOT NULL,
    status text DEFAULT 'pending', -- 'pending', 'accepted', 'declined', 'expired'
    message_id bigint,
    channel_id bigint,
    expires_at timestamptz,
    metadata jsonb,
    created_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_pending_signings_player
    ON pending_signings(player_id);
CREATE INDEX IF NOT EXISTS idx_pending_signings_team
    ON pending_signings(team_id);

-- ---------------------------------------------------------------------------
-- Trade proposals + assets
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trade_proposals (
    id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    guild_id bigint NOT NULL,
    season_id uuid NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
    initiating_team_id uuid NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    counterparty_team_id uuid NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    status text DEFAULT 'pending', -- 'pending', 'accepted', 'declined', 'cancelled', 'executed'
    created_by bigint NOT NULL,
    reviewed_by bigint,
    expires_at timestamptz,
    metadata jsonb,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_trade_proposals_status
    ON trade_proposals(status);
CREATE INDEX IF NOT EXISTS idx_trade_proposals_teams
    ON trade_proposals(initiating_team_id, counterparty_team_id);

CREATE TABLE IF NOT EXISTS trade_assets (
    id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    proposal_id uuid NOT NULL REFERENCES trade_proposals(id) ON DELETE CASCADE,
    direction text NOT NULL, -- 'outbound' or 'inbound'
    asset_type text NOT NULL, -- 'player', 'pick', 'cash', 'other'
    player_id uuid REFERENCES players(id) ON DELETE SET NULL,
    draft_pick_id uuid REFERENCES draft_picks(id) ON DELETE SET NULL,
    cash_value numeric,
    notes text,
    metadata jsonb,
    created_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_trade_assets_proposal
    ON trade_assets(proposal_id);
CREATE INDEX IF NOT EXISTS idx_trade_assets_pick
    ON trade_assets(draft_pick_id);

-- ---------------------------------------------------------------------------
-- Transaction events (append-only audit trail)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS transaction_events (
    id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    transaction_id uuid REFERENCES transactions(id) ON DELETE SET NULL,
    event_type text NOT NULL,
    payload jsonb,
    created_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_transaction_events_tx
    ON transaction_events(transaction_id);

COMMIT;
