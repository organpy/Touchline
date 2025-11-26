-- Transaction blocker persistent table
CREATE TABLE IF NOT EXISTS transaction_blocker_entries (
    id uuid PRIMARY KEY DEFAULT uuid_generate_v4(),
    guild_id bigint NOT NULL,
    team_id uuid NOT NULL,
    blocked boolean NOT NULL DEFAULT TRUE,
    reason text,
    blocked_by bigint,
    blocked_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_transaction_blocker_guild_team
    ON transaction_blocker_entries(guild_id, team_id);

CREATE INDEX IF NOT EXISTS idx_transaction_blocker_guild_blocked
    ON transaction_blocker_entries(guild_id, blocked)
    WHERE blocked = TRUE;
