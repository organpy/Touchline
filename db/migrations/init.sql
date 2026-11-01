-- Touchline baseline schema
-- Generic divisions (any number of tiers), league-wide seasons, promotion/relegation brackets,
-- a D1 championship bracket, transactions and trades, and the stats functions.
--
-- Apply to an empty database:  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/migrations/init.sql
-- Upgrading from the old NBL/MBL/NCBA schema: back up, run db/reset_legacy.sql, then this file.
--
-- Errors raised for rule violations carry a stable code in their HINT (e.g. MATCH_NOT_REGULAR_PHASE)
-- that the bot maps to a user-facing message.

BEGIN;

-- ============================================================================
-- Core league structure
-- ============================================================================

-- Divisions: one row per tier (D1 = tier 1 = top). Rules live in settings (validated by the bot):
--   scoring_mode, points_per_win, games_per_matchup, picks_enabled,
--   championship {enabled, teams, seeding, best_of_by_round}   (tier 1 only)
--   boundary_below {direct_swaps, playoff_swaps, mode, upper_playoff_teams, lower_playoff_teams, best_of}
CREATE TABLE divisions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code text NOT NULL CHECK (code ~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,15}$'),
    name text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 64),
    tier integer NOT NULL CHECK (tier >= 1),
    color text CHECK (color IS NULL OR color ~ '^#[0-9A-Fa-f]{6}$'),
    emoji text,
    logo_url text,
    settings jsonb NOT NULL DEFAULT '{}'::jsonb,
    archived_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    -- Deferred so tiers can be swapped/reordered inside one transaction.
    CONSTRAINT ex_divisions_tier EXCLUDE USING btree (tier WITH =)
        WHERE (archived_at IS NULL) DEFERRABLE INITIALLY DEFERRED
);
CREATE UNIQUE INDEX ux_divisions_code ON divisions (lower(code));

-- Optional groups (conferences) inside a division, e.g. East / West.
CREATE TABLE division_groups (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    division_id uuid NOT NULL REFERENCES divisions(id) ON DELETE CASCADE,
    name text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 32),
    sort_order integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ux_division_groups_name ON division_groups (division_id, lower(name));

-- Teams belong to a division per season (season_teams), not permanently.
CREATE TABLE teams (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code text NOT NULL CHECK (code ~ '^[a-z0-9][a-z0-9-]{0,31}$'),
    name text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 64),
    emoji text,
    color text CHECK (color IS NULL OR color ~ '^#[0-9A-Fa-f]{6}$'),
    logo_url text,
    owner_discord_id bigint,
    metadata jsonb,
    archived_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ux_teams_code ON teams (code);
-- One team per owner.
CREATE UNIQUE INDEX ux_teams_owner ON teams (owner_discord_id)
    WHERE owner_discord_id IS NOT NULL AND archived_at IS NULL;

CREATE TABLE players (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    display_name text NOT NULL,
    discord_id bigint UNIQUE,
    metadata jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- League-wide seasons. Exactly one season is active at a time.
CREATE TABLE seasons (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    number integer NOT NULL UNIQUE CHECK (number >= 1),
    name text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 64),
    phase text NOT NULL DEFAULT 'setup'
        CHECK (phase IN ('setup', 'regular', 'postseason', 'offseason', 'completed')),
    active boolean NOT NULL DEFAULT false,
    champion_team_id uuid REFERENCES teams(id) ON DELETE SET NULL,
    started_at timestamptz,
    regular_ended_at timestamptz,
    ended_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ux_seasons_name ON seasons (lower(name));
CREATE UNIQUE INDEX ux_seasons_one_active ON seasons ((true)) WHERE active;

-- Which division (and group) each team plays in for a season. History survives promotion/relegation.
CREATE TABLE season_teams (
    season_id uuid NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
    team_id uuid NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    division_id uuid NOT NULL REFERENCES divisions(id) ON DELETE RESTRICT,
    group_id uuid REFERENCES division_groups(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (season_id, team_id)
);
CREATE INDEX idx_season_teams_division ON season_teams (season_id, division_id);

-- Roster membership per season. A player can be on one team at a time.
CREATE TABLE rosters (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    season_id uuid NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
    team_id uuid NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    player_id uuid NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    joined_at timestamptz NOT NULL DEFAULT now(),
    left_at timestamptz,
    role text,
    metadata jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (season_id, team_id, player_id, joined_at)
);
CREATE UNIQUE INDEX ux_rosters_one_active_team ON rosters (season_id, player_id) WHERE left_at IS NULL;
CREATE INDEX idx_rosters_season_team ON rosters (season_id, team_id);
CREATE INDEX idx_rosters_player ON rosters (player_id);

-- ============================================================================
-- Postseason: brackets and series
-- ============================================================================

-- kind / division:
--   championship  division_id = the top division
--   promotion     division_id = the lower division whose teams try to go up
--   relegation    division_id = the upper division whose teams try to stay up (losers advance)
--   promo_rel     division_id = upper, lower_division_id = lower (cross-tier head-to-head)
CREATE TABLE brackets (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    season_id uuid NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
    kind text NOT NULL CHECK (kind IN ('championship', 'promotion', 'relegation', 'promo_rel')),
    division_id uuid NOT NULL REFERENCES divisions(id) ON DELETE RESTRICT,
    lower_division_id uuid REFERENCES divisions(id) ON DELETE RESTRICT,
    spots integer NOT NULL DEFAULT 1 CHECK (spots >= 1),
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'completed')),
    config jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    CHECK ((kind = 'promo_rel') = (lower_division_id IS NOT NULL))
);
CREATE UNIQUE INDEX ux_brackets_season_kind_division ON brackets (season_id, kind, division_id);

CREATE TABLE bracket_entries (
    bracket_id uuid NOT NULL REFERENCES brackets(id) ON DELETE CASCADE,
    team_id uuid NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    seed integer NOT NULL CHECK (seed >= 1),
    source_division_id uuid NOT NULL REFERENCES divisions(id) ON DELETE RESTRICT,
    PRIMARY KEY (bracket_id, team_id),
    UNIQUE (bracket_id, seed) DEFERRABLE INITIALLY IMMEDIATE
);

-- One matchup. Wins are recomputed from its matches; the winner (and/or loser) feeds a later series.
CREATE TABLE series (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    bracket_id uuid NOT NULL REFERENCES brackets(id) ON DELETE CASCADE,
    round integer NOT NULL CHECK (round >= 1),
    position integer NOT NULL CHECK (position >= 0),
    team1_id uuid REFERENCES teams(id) ON DELETE CASCADE,
    team2_id uuid REFERENCES teams(id) ON DELETE CASCADE,
    team1_seed integer,
    team2_seed integer,
    best_of integer NOT NULL DEFAULT 1 CHECK (best_of >= 1 AND best_of % 2 = 1),
    team1_wins integer NOT NULL DEFAULT 0,
    team2_wins integer NOT NULL DEFAULT 0,
    winner_id uuid REFERENCES teams(id) ON DELETE SET NULL,
    loser_id uuid REFERENCES teams(id) ON DELETE SET NULL,
    forced_winner_id uuid REFERENCES teams(id) ON DELETE SET NULL,
    status text NOT NULL DEFAULT 'waiting' CHECK (status IN ('waiting', 'ready', 'completed')),
    winner_to uuid REFERENCES series(id) ON DELETE SET NULL,
    winner_slot smallint CHECK (winner_slot IN (1, 2)),
    loser_to uuid REFERENCES series(id) ON DELETE SET NULL,
    loser_slot smallint CHECK (loser_slot IN (1, 2)),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (bracket_id, round, position),
    CHECK (team1_id IS NULL OR team2_id IS NULL OR team1_id <> team2_id),
    CHECK ((winner_to IS NULL) = (winner_slot IS NULL)),
    CHECK ((loser_to IS NULL) = (loser_slot IS NULL))
);
CREATE INDEX idx_series_bracket ON series (bracket_id);

-- ============================================================================
-- Games and stats
-- ============================================================================

CREATE TABLE matches (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    season_id uuid NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
    division_id uuid REFERENCES divisions(id) ON DELETE CASCADE,  -- NULL only for cross-tier games
    stage text NOT NULL DEFAULT 'regular'
        CHECK (stage IN ('regular', 'championship', 'promotion', 'relegation', 'promo_rel')),
    series_id uuid REFERENCES series(id) ON DELETE CASCADE,
    team1_id uuid NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    team2_id uuid NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    team1_score integer,
    team2_score integer,
    final_turn integer,
    recorded_by bigint,
    status text NOT NULL DEFAULT 'final' CHECK (status IN ('final', 'void')),
    played_at timestamptz NOT NULL DEFAULT now(),
    voided_at timestamptz,
    voided_by bigint,
    metadata jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (team1_id <> team2_id),
    CHECK (status = 'void' OR (team1_score IS NOT NULL AND team2_score IS NOT NULL)),
    CHECK (team1_score IS NULL OR team2_score IS NULL OR team1_score <> team2_score),
    CHECK ((stage = 'regular') = (series_id IS NULL)),
    CHECK (stage <> 'regular' OR division_id IS NOT NULL)
);
CREATE INDEX idx_matches_season_division ON matches (season_id, division_id);
CREATE INDEX idx_matches_series ON matches (series_id) WHERE series_id IS NOT NULL;
CREATE INDEX idx_matches_played_at ON matches (played_at);

CREATE TABLE player_stats (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    match_id uuid NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    player_id uuid NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    team_id uuid REFERENCES teams(id) ON DELETE SET NULL,  -- filled from rosters when not given
    goals integer DEFAULT 0,
    points integer DEFAULT 0,
    own_goals integer DEFAULT 0,
    assists integer DEFAULT 0,
    saves integer DEFAULT 0,
    blocks integer DEFAULT 0,
    powerup_plays integer DEFAULT 0,
    powerups_used integer DEFAULT 0,
    powerup_percentage numeric(5,2) DEFAULT 0,
    metadata jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_player_stats_match ON player_stats (match_id);
CREATE INDEX idx_player_stats_player ON player_stats (player_id);
CREATE INDEX idx_player_stats_team ON player_stats (team_id);

-- Ranked standings, written by the bot after every result so the bot and the website agree.
-- zone: champion_playoff | promoted | promotion_playoff | relegation_playoff | relegated
CREATE TABLE standings (
    season_id uuid NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
    team_id uuid NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    division_id uuid NOT NULL REFERENCES divisions(id) ON DELETE CASCADE,
    group_id uuid REFERENCES division_groups(id) ON DELETE SET NULL,
    rank integer NOT NULL,
    group_rank integer,
    games_played integer NOT NULL DEFAULT 0,
    wins integer NOT NULL DEFAULT 0,
    losses integer NOT NULL DEFAULT 0,
    points_for integer NOT NULL DEFAULT 0,
    points_against integer NOT NULL DEFAULT 0,
    point_diff integer NOT NULL DEFAULT 0,
    competition_points integer NOT NULL DEFAULT 0,
    zone text CHECK (zone IN ('champion_playoff', 'promoted', 'promotion_playoff', 'relegation_playoff', 'relegated')),
    final boolean NOT NULL DEFAULT false,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (season_id, team_id)
);
CREATE INDEX idx_standings_division ON standings (season_id, division_id, rank);

-- Promotions and relegations decided at the end of a season, applied when the next one starts.
CREATE TABLE season_movements (
    season_id uuid NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
    team_id uuid NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    from_division_id uuid NOT NULL REFERENCES divisions(id) ON DELETE RESTRICT,
    to_division_id uuid NOT NULL REFERENCES divisions(id) ON DELETE RESTRICT,
    reason text NOT NULL CHECK (reason IN ('promoted', 'relegated', 'promoted_playoff', 'relegated_playoff')),
    applied boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (season_id, team_id),
    CHECK (from_division_id <> to_division_id)
);

-- ============================================================================
-- Transactions, picks and trades
-- ============================================================================

CREATE TABLE transactions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    season_id uuid REFERENCES seasons(id) ON DELETE SET NULL,
    type text NOT NULL,  -- 'sign', 'release', 'loan', 'loan_return', 'transfer', 'trade'
    player_id uuid REFERENCES players(id) ON DELETE SET NULL,
    from_team_id uuid REFERENCES teams(id) ON DELETE SET NULL,
    to_team_id uuid REFERENCES teams(id) ON DELETE SET NULL,
    executed_by bigint,
    details jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_transactions_player ON transactions (player_id);
CREATE INDEX idx_transactions_season ON transactions (season_id);

CREATE TABLE transaction_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    transaction_id uuid REFERENCES transactions(id) ON DELETE SET NULL,
    event_type text NOT NULL,
    payload jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_transaction_events_tx ON transaction_events (transaction_id);

-- Draft picks belong to a division's draft in a season (only divisions with picks_enabled).
CREATE TABLE draft_picks (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    season_id uuid NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
    division_id uuid NOT NULL REFERENCES divisions(id) ON DELETE CASCADE,
    original_team_id uuid NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    current_owner_team_id uuid NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    round integer NOT NULL,
    overall integer NOT NULL,
    selected_player_id uuid REFERENCES players(id) ON DELETE SET NULL,
    notes text,
    metadata jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX ux_draft_picks_season_division_overall ON draft_picks (season_id, division_id, overall);
CREATE INDEX idx_draft_picks_owner ON draft_picks (current_owner_team_id);
CREATE INDEX idx_draft_picks_selected_player ON draft_picks (selected_player_id);

CREATE TABLE pending_signings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    guild_id bigint NOT NULL,
    season_id uuid NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
    team_id uuid NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    player_id uuid NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    created_by bigint NOT NULL,
    status text DEFAULT 'pending',  -- 'pending', 'accepted', 'declined', 'expired'
    message_id bigint,
    channel_id bigint,
    expires_at timestamptz,
    metadata jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_pending_signings_player ON pending_signings (player_id);
CREATE INDEX idx_pending_signings_team ON pending_signings (team_id);
CREATE INDEX idx_pending_signings_season ON pending_signings (season_id);

CREATE TABLE trade_proposals (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    guild_id bigint NOT NULL,
    season_id uuid NOT NULL REFERENCES seasons(id) ON DELETE CASCADE,
    initiating_team_id uuid NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    counterparty_team_id uuid NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    status text DEFAULT 'pending',  -- 'pending', 'accepted', 'declined', 'cancelled', 'executed'
    created_by bigint NOT NULL,
    reviewed_by bigint,
    expires_at timestamptz,
    metadata jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_trade_proposals_status ON trade_proposals (status);
CREATE INDEX idx_trade_proposals_teams ON trade_proposals (initiating_team_id, counterparty_team_id);

CREATE TABLE trade_assets (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    proposal_id uuid NOT NULL REFERENCES trade_proposals(id) ON DELETE CASCADE,
    direction text NOT NULL,   -- 'outbound' or 'inbound'
    asset_type text NOT NULL,  -- 'player', 'pick', 'cash', 'other'
    player_id uuid REFERENCES players(id) ON DELETE SET NULL,
    draft_pick_id uuid REFERENCES draft_picks(id) ON DELETE SET NULL,
    cash_value numeric,
    notes text,
    metadata jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_trade_assets_proposal ON trade_assets (proposal_id);
CREATE INDEX idx_trade_assets_pick ON trade_assets (draft_pick_id);

CREATE TABLE transaction_blocker_entries (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    guild_id bigint NOT NULL,
    team_id uuid NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    blocked boolean NOT NULL DEFAULT true,
    reason text,
    blocked_by bigint,
    blocked_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now()
);
CREATE UNIQUE INDEX ux_transaction_blocker_guild_team ON transaction_blocker_entries (guild_id, team_id);
CREATE INDEX idx_transaction_blocker_guild_blocked ON transaction_blocker_entries (guild_id, blocked) WHERE blocked;

-- Per-guild bot settings. Kept by db/reset_legacy.sql, so it may already exist.
CREATE TABLE IF NOT EXISTS bot_config (
    guild_id bigint PRIMARY KEY,
    config jsonb DEFAULT '{}'::jsonb,
    updated_at timestamptz DEFAULT now()
);

-- ============================================================================
-- Integrity triggers
-- ============================================================================

-- Seasons only move forward: setup -> regular -> postseason -> offseason -> completed.
CREATE FUNCTION guard_season_phase() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.phase IS DISTINCT FROM OLD.phase AND NOT (
        (OLD.phase = 'setup' AND NEW.phase = 'regular') OR
        (OLD.phase = 'regular' AND NEW.phase = 'postseason') OR
        (OLD.phase = 'postseason' AND NEW.phase = 'offseason') OR
        (OLD.phase = 'offseason' AND NEW.phase = 'completed')
    ) THEN
        RAISE EXCEPTION 'season cannot move from % to %', OLD.phase, NEW.phase
            USING HINT = 'SEASON_BAD_TRANSITION';
    END IF;
    IF NEW.phase = 'regular' AND OLD.phase = 'setup' THEN
        NEW.started_at := COALESCE(NEW.started_at, now());
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_seasons_phase BEFORE UPDATE ON seasons
    FOR EACH ROW EXECUTE FUNCTION guard_season_phase();

-- Division assignments can change while a season is being set up, or during the regular season
-- for teams that have not played yet. Groups must belong to the assigned division.
CREATE FUNCTION guard_season_teams() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    v_season_id uuid := CASE WHEN TG_OP = 'DELETE' THEN OLD.season_id ELSE NEW.season_id END;
    v_phase text;
    v_team_id uuid := CASE WHEN TG_OP = 'DELETE' THEN OLD.team_id ELSE NEW.team_id END;
BEGIN
    SELECT phase INTO v_phase FROM seasons WHERE id = v_season_id;
    IF v_phase IS NULL THEN
        -- the season itself is being deleted (cascade)
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END IF;

    IF TG_OP <> 'INSERT' AND (TG_OP = 'DELETE' OR NEW.division_id IS DISTINCT FROM OLD.division_id) THEN
        IF v_phase NOT IN ('setup', 'regular') THEN
            RAISE EXCEPTION 'division assignments are locked once the regular season has ended'
                USING HINT = 'SEASON_TEAMS_LOCKED';
        END IF;
        IF EXISTS (
            SELECT 1 FROM matches m
            WHERE m.season_id = v_season_id AND v_team_id IN (m.team1_id, m.team2_id)
        ) THEN
            RAISE EXCEPTION 'team % already has games this season', v_team_id
                USING HINT = 'SEASON_TEAM_HAS_MATCHES';
        END IF;
    END IF;

    IF TG_OP = 'INSERT' AND v_phase NOT IN ('setup', 'regular') THEN
        RAISE EXCEPTION 'teams can only join a season during setup or the regular season'
            USING HINT = 'SEASON_TEAMS_LOCKED';
    END IF;

    IF TG_OP <> 'DELETE' THEN
        IF EXISTS (SELECT 1 FROM divisions WHERE id = NEW.division_id AND archived_at IS NOT NULL) THEN
            RAISE EXCEPTION 'division % is archived', NEW.division_id USING HINT = 'DIVISION_ARCHIVED';
        END IF;
        IF NEW.group_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM division_groups g WHERE g.id = NEW.group_id AND g.division_id = NEW.division_id
        ) THEN
            RAISE EXCEPTION 'group % is not part of division %', NEW.group_id, NEW.division_id
                USING HINT = 'GROUP_DIVISION_MISMATCH';
        END IF;
        RETURN NEW;
    END IF;
    RETURN OLD;
END;
$$;
CREATE TRIGGER trg_season_teams_guard BEFORE INSERT OR UPDATE OR DELETE ON season_teams
    FOR EACH ROW EXECUTE FUNCTION guard_season_teams();

-- A roster row needs its team to be in that season.
CREATE FUNCTION ensure_roster_team_in_season() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM season_teams st WHERE st.season_id = NEW.season_id AND st.team_id = NEW.team_id
    ) THEN
        RAISE EXCEPTION 'team % is not in season %', NEW.team_id, NEW.season_id
            USING HINT = 'ROSTER_TEAM_NOT_IN_SEASON';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_rosters_team_in_season BEFORE INSERT ON rosters
    FOR EACH ROW EXECUTE FUNCTION ensure_roster_team_in_season();

-- Transactions move rosters (one active team per player per season).
CREATE FUNCTION apply_transaction_to_roster() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.type IN ('sign', 'transfer', 'loan') AND NEW.to_team_id IS NOT NULL THEN
        UPDATE rosters
            SET left_at = COALESCE(NEW.created_at, now())
            WHERE player_id = NEW.player_id
              AND season_id = NEW.season_id
              AND left_at IS NULL;
        INSERT INTO rosters (season_id, team_id, player_id, joined_at)
            VALUES (NEW.season_id, NEW.to_team_id, NEW.player_id, COALESCE(NEW.created_at, now()));
    ELSIF NEW.type IN ('release', 'loan_return') THEN
        UPDATE rosters
            SET left_at = COALESCE(NEW.created_at, now())
            WHERE player_id = NEW.player_id
              AND season_id = NEW.season_id
              AND team_id = NEW.from_team_id
              AND left_at IS NULL;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_transactions_apply_roster AFTER INSERT ON transactions
    FOR EACH ROW EXECUTE FUNCTION apply_transaction_to_roster();

-- Matches: regular games need both teams in the same division this season and the season in its
-- regular phase. Series games need the postseason, a ready series and that series' two teams.
-- After insert, teams, season and series are fixed; only scores, status and metadata may change,
-- and only while the game's phase is still open.
CREATE FUNCTION validate_match() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    v_phase text;
    v_series series%ROWTYPE;
    v_bracket brackets%ROWTYPE;
    v_div1 uuid;
    v_div2 uuid;
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
    ELSE
        SELECT * INTO v_series FROM series WHERE id = NEW.series_id;
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
CREATE TRIGGER trg_matches_validate BEFORE INSERT OR UPDATE ON matches
    FOR EACH ROW EXECUTE FUNCTION validate_match();

-- player_stats: team from the roster when the reporter did not say, and power-up percentage.
CREATE FUNCTION prepare_player_stats() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    v_match matches%ROWTYPE;
BEGIN
    IF NEW.team_id IS NULL THEN
        SELECT * INTO v_match FROM matches WHERE id = NEW.match_id;
        SELECT r.team_id INTO NEW.team_id
        FROM rosters r
        WHERE r.season_id = v_match.season_id
          AND r.player_id = NEW.player_id
          AND r.team_id IN (v_match.team1_id, v_match.team2_id)
        ORDER BY (r.left_at IS NULL) DESC, r.joined_at DESC
        LIMIT 1;
    END IF;
    IF NEW.powerups_used IS NOT NULL AND NEW.powerups_used > 0 THEN
        NEW.powerup_percentage := round((NEW.powerup_plays::numeric / NEW.powerups_used::numeric) * 100, 2);
    ELSE
        NEW.powerup_percentage := 0;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_player_stats_prepare BEFORE INSERT OR UPDATE ON player_stats
    FOR EACH ROW EXECUTE FUNCTION prepare_player_stats();

-- ============================================================================
-- Series progression
-- ============================================================================

-- Put a team into a later series' slot. Refuses to change a slot whose series already has results.
CREATE FUNCTION feed_series_slot(p_target uuid, p_slot smallint, p_team uuid, p_seed integer)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    v_current uuid;
BEGIN
    SELECT CASE WHEN p_slot = 1 THEN team1_id ELSE team2_id END INTO v_current
    FROM series WHERE id = p_target FOR UPDATE;
    IF v_current IS NOT DISTINCT FROM p_team THEN
        RETURN;
    END IF;
    IF EXISTS (SELECT 1 FROM matches WHERE series_id = p_target AND status = 'final')
       OR EXISTS (SELECT 1 FROM series WHERE id = p_target AND forced_winner_id IS NOT NULL) THEN
        RAISE EXCEPTION 'series % already has results; void them first', p_target
            USING HINT = 'SERIES_DOWNSTREAM_PLAYED';
    END IF;
    IF p_slot = 1 THEN
        UPDATE series SET team1_id = p_team, team1_seed = p_seed, updated_at = now() WHERE id = p_target;
    ELSE
        UPDATE series SET team2_id = p_team, team2_seed = p_seed, updated_at = now() WHERE id = p_target;
    END IF;
    PERFORM recompute_series(p_target);
END;
$$;

-- Recount a series from its final games (or an admin-forced winner), then feed the next series
-- and mark the bracket completed when every series in it is.
CREATE FUNCTION recompute_series(p_series_id uuid) RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    s series%ROWTYPE;
    v_t1_wins integer;
    v_t2_wins integer;
    v_needed integer;
    v_winner uuid;
    v_loser uuid;
    v_status text;
    v_open integer;
BEGIN
    SELECT * INTO s FROM series WHERE id = p_series_id FOR UPDATE;
    IF NOT FOUND THEN
        RETURN;
    END IF;

    SELECT
        count(*) FILTER (WHERE (m.team1_id = s.team1_id AND m.team1_score > m.team2_score)
                            OR (m.team2_id = s.team1_id AND m.team2_score > m.team1_score)),
        count(*) FILTER (WHERE (m.team1_id = s.team2_id AND m.team1_score > m.team2_score)
                            OR (m.team2_id = s.team2_id AND m.team2_score > m.team1_score))
    INTO v_t1_wins, v_t2_wins
    FROM matches m
    WHERE m.series_id = p_series_id AND m.status = 'final';

    v_needed := s.best_of / 2 + 1;
    IF s.forced_winner_id IS NOT NULL AND s.forced_winner_id IN (s.team1_id, s.team2_id) THEN
        v_winner := s.forced_winner_id;
        v_loser := CASE WHEN v_winner = s.team1_id THEN s.team2_id ELSE s.team1_id END;
    ELSIF s.team1_id IS NOT NULL AND s.team2_id IS NOT NULL AND v_t1_wins >= v_needed THEN
        v_winner := s.team1_id;
        v_loser := s.team2_id;
    ELSIF s.team1_id IS NOT NULL AND s.team2_id IS NOT NULL AND v_t2_wins >= v_needed THEN
        v_winner := s.team2_id;
        v_loser := s.team1_id;
    END IF;

    v_status := CASE
        WHEN v_winner IS NOT NULL THEN 'completed'
        WHEN s.team1_id IS NOT NULL AND s.team2_id IS NOT NULL THEN 'ready'
        ELSE 'waiting'
    END;

    UPDATE series
    SET team1_wins = v_t1_wins, team2_wins = v_t2_wins,
        winner_id = v_winner, loser_id = v_loser,
        status = v_status, updated_at = now()
    WHERE id = p_series_id;

    IF s.winner_to IS NOT NULL THEN
        PERFORM feed_series_slot(s.winner_to, s.winner_slot, v_winner,
            CASE WHEN v_winner = s.team1_id THEN s.team1_seed WHEN v_winner = s.team2_id THEN s.team2_seed END);
    END IF;
    IF s.loser_to IS NOT NULL THEN
        PERFORM feed_series_slot(s.loser_to, s.loser_slot, v_loser,
            CASE WHEN v_loser = s.team1_id THEN s.team1_seed WHEN v_loser = s.team2_id THEN s.team2_seed END);
    END IF;

    SELECT count(*) INTO v_open FROM series WHERE bracket_id = s.bracket_id AND status <> 'completed';
    UPDATE brackets
    SET status = CASE WHEN v_open = 0 THEN 'completed' ELSE 'active' END,
        completed_at = CASE WHEN v_open = 0 THEN COALESCE(completed_at, now()) ELSE NULL END
    WHERE id = s.bracket_id;
END;
$$;

CREATE FUNCTION after_match_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.series_id IS NOT NULL THEN
        PERFORM recompute_series(NEW.series_id);
    END IF;
    RETURN NULL;
END;
$$;
CREATE TRIGGER trg_matches_after AFTER INSERT OR UPDATE ON matches
    FOR EACH ROW EXECUTE FUNCTION after_match_change();

-- ============================================================================
-- Season lifecycle (atomic multi-row changes, called by the bot)
-- ============================================================================

-- Set division tiers from an ordered list of ids (first = tier 1).
CREATE FUNCTION reorder_divisions(p_division_ids uuid[]) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
    UPDATE divisions d
    SET tier = x.ord, updated_at = now()
    FROM unnest(p_division_ids) WITH ORDINALITY AS x(id, ord)
    WHERE d.id = x.id;
END;
$$;

-- End the regular season: lock standings, create every bracket from the bot's plan, enter postseason.
-- p_plan = {"brackets": [{"kind", "division_id", "lower_division_id", "spots", "config",
--            "entries": [{"team_id", "seed", "source_division_id"}],
--            "series": [{"key", "round", "position", "team1_id", "team2_id", "team1_seed",
--                        "team2_seed", "best_of", "winner_to", "winner_slot", "loser_to", "loser_slot"}]}]}
-- Series keys are unique across the plan; winner_to/loser_to reference them.
CREATE FUNCTION create_postseason(p_season_id uuid, p_plan jsonb) RETURNS integer LANGUAGE plpgsql AS $$
DECLARE
    v_phase text;
    b jsonb;
    s jsonb;
    v_bracket_id uuid;
    v_series_id uuid;
    v_keys jsonb := '{}'::jsonb;
    v_count integer := 0;
BEGIN
    SELECT phase INTO v_phase FROM seasons WHERE id = p_season_id FOR UPDATE;
    IF v_phase IS DISTINCT FROM 'regular' THEN
        RAISE EXCEPTION 'season % is not in the regular season', p_season_id USING HINT = 'SEASON_WRONG_PHASE';
    END IF;

    UPDATE standings SET final = true, updated_at = now() WHERE season_id = p_season_id;

    FOR b IN SELECT value FROM jsonb_array_elements(COALESCE(p_plan -> 'brackets', '[]'::jsonb)) LOOP
        INSERT INTO brackets (season_id, kind, division_id, lower_division_id, spots, config)
        VALUES (
            p_season_id,
            b ->> 'kind',
            (b ->> 'division_id')::uuid,
            NULLIF(b ->> 'lower_division_id', '')::uuid,
            COALESCE((b ->> 'spots')::integer, 1),
            COALESCE(b -> 'config', '{}'::jsonb)
        )
        RETURNING id INTO v_bracket_id;

        INSERT INTO bracket_entries (bracket_id, team_id, seed, source_division_id)
        SELECT v_bracket_id, (e ->> 'team_id')::uuid, (e ->> 'seed')::integer, (e ->> 'source_division_id')::uuid
        FROM jsonb_array_elements(COALESCE(b -> 'entries', '[]'::jsonb)) AS e;

        FOR s IN SELECT value FROM jsonb_array_elements(COALESCE(b -> 'series', '[]'::jsonb)) LOOP
            INSERT INTO series (bracket_id, round, position, team1_id, team2_id, team1_seed, team2_seed, best_of)
            VALUES (
                v_bracket_id,
                (s ->> 'round')::integer,
                (s ->> 'position')::integer,
                NULLIF(s ->> 'team1_id', '')::uuid,
                NULLIF(s ->> 'team2_id', '')::uuid,
                (s ->> 'team1_seed')::integer,
                (s ->> 'team2_seed')::integer,
                COALESCE((s ->> 'best_of')::integer, 1)
            )
            RETURNING id INTO v_series_id;
            IF v_keys ? (s ->> 'key') THEN
                RAISE EXCEPTION 'duplicate series key %', s ->> 'key' USING HINT = 'PLAN_INVALID';
            END IF;
            v_keys := v_keys || jsonb_build_object(s ->> 'key', v_series_id);
        END LOOP;
        v_count := v_count + 1;
    END LOOP;

    -- Second pass: link series now that every key has an id.
    FOR b IN SELECT value FROM jsonb_array_elements(COALESCE(p_plan -> 'brackets', '[]'::jsonb)) LOOP
        FOR s IN SELECT value FROM jsonb_array_elements(COALESCE(b -> 'series', '[]'::jsonb)) LOOP
            IF (s ->> 'winner_to') IS NOT NULL OR (s ->> 'loser_to') IS NOT NULL THEN
                UPDATE series
                SET winner_to = (v_keys ->> (s ->> 'winner_to'))::uuid,
                    winner_slot = (s ->> 'winner_slot')::smallint,
                    loser_to = (v_keys ->> (s ->> 'loser_to'))::uuid,
                    loser_slot = (s ->> 'loser_slot')::smallint
                WHERE id = (v_keys ->> (s ->> 'key'))::uuid;
            END IF;
        END LOOP;
    END LOOP;

    UPDATE seasons SET phase = 'postseason', regular_ended_at = now() WHERE id = p_season_id;

    PERFORM recompute_series(sr.id)
    FROM series sr JOIN brackets br ON br.id = sr.bracket_id
    WHERE br.season_id = p_season_id;

    RETURN v_count;
END;
$$;

-- Swap two teams' seeds in a bracket before any of its games are played (manual tiebreak).
CREATE FUNCTION swap_bracket_seeds(p_bracket_id uuid, p_team_a uuid, p_team_b uuid)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    v_seed_a integer;
    v_seed_b integer;
BEGIN
    PERFORM 1 FROM brackets WHERE id = p_bracket_id FOR UPDATE;
    IF EXISTS (
        SELECT 1 FROM matches m JOIN series s ON s.id = m.series_id
        WHERE s.bracket_id = p_bracket_id AND m.status = 'final'
    ) OR EXISTS (SELECT 1 FROM series WHERE bracket_id = p_bracket_id AND forced_winner_id IS NOT NULL) THEN
        RAISE EXCEPTION 'bracket % already has results', p_bracket_id USING HINT = 'BRACKET_STARTED';
    END IF;
    SELECT seed INTO v_seed_a FROM bracket_entries WHERE bracket_id = p_bracket_id AND team_id = p_team_a;
    SELECT seed INTO v_seed_b FROM bracket_entries WHERE bracket_id = p_bracket_id AND team_id = p_team_b;
    IF v_seed_a IS NULL OR v_seed_b IS NULL THEN
        RAISE EXCEPTION 'both teams must be in bracket %', p_bracket_id USING HINT = 'BRACKET_TEAM_MISSING';
    END IF;

    SET CONSTRAINTS bracket_entries_bracket_id_seed_key DEFERRED;
    UPDATE bracket_entries SET seed = CASE WHEN team_id = p_team_a THEN v_seed_b ELSE v_seed_a END
    WHERE bracket_id = p_bracket_id AND team_id IN (p_team_a, p_team_b);

    UPDATE series
    SET team1_id = CASE WHEN team1_id = p_team_a THEN p_team_b WHEN team1_id = p_team_b THEN p_team_a ELSE team1_id END,
        team2_id = CASE WHEN team2_id = p_team_a THEN p_team_b WHEN team2_id = p_team_b THEN p_team_a ELSE team2_id END,
        updated_at = now()
    WHERE bracket_id = p_bracket_id AND (team1_id IN (p_team_a, p_team_b) OR team2_id IN (p_team_a, p_team_b));
END;
$$;

-- Close the postseason: record the champion and the promotions/relegations. Every bracket must be done.
-- p_movements = [{"team_id", "from_division_id", "to_division_id", "reason"}]
CREATE FUNCTION finalize_season(p_season_id uuid, p_champion_team_id uuid, p_movements jsonb)
RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    v_phase text;
BEGIN
    SELECT phase INTO v_phase FROM seasons WHERE id = p_season_id FOR UPDATE;
    IF v_phase IS DISTINCT FROM 'postseason' THEN
        RAISE EXCEPTION 'season % is not in the postseason', p_season_id USING HINT = 'SEASON_WRONG_PHASE';
    END IF;
    IF EXISTS (SELECT 1 FROM brackets WHERE season_id = p_season_id AND status <> 'completed') THEN
        RAISE EXCEPTION 'season % still has brackets in progress', p_season_id USING HINT = 'BRACKETS_IN_PROGRESS';
    END IF;

    DELETE FROM season_movements WHERE season_id = p_season_id;
    INSERT INTO season_movements (season_id, team_id, from_division_id, to_division_id, reason)
    SELECT p_season_id, (m ->> 'team_id')::uuid, (m ->> 'from_division_id')::uuid,
           (m ->> 'to_division_id')::uuid, m ->> 'reason'
    FROM jsonb_array_elements(COALESCE(p_movements, '[]'::jsonb)) AS m;

    UPDATE seasons SET phase = 'offseason', champion_team_id = p_champion_team_id WHERE id = p_season_id;
END;
$$;

-- Start the next season: complete the old one, carry teams over with movements applied (groups are
-- kept when a team stays in its division) and carry every active roster over.
CREATE FUNCTION start_next_season(p_prev_season_id uuid, p_name text) RETURNS uuid LANGUAGE plpgsql AS $$
DECLARE
    v_prev seasons%ROWTYPE;
    v_new uuid;
BEGIN
    SELECT * INTO v_prev FROM seasons WHERE id = p_prev_season_id FOR UPDATE;
    IF v_prev.phase IS DISTINCT FROM 'offseason' THEN
        RAISE EXCEPTION 'season % is not in the offseason', p_prev_season_id USING HINT = 'SEASON_WRONG_PHASE';
    END IF;

    UPDATE seasons SET phase = 'completed', active = false, ended_at = now() WHERE id = p_prev_season_id;

    INSERT INTO seasons (number, name, phase, active)
    VALUES (v_prev.number + 1, p_name, 'setup', true)
    RETURNING id INTO v_new;

    INSERT INTO season_teams (season_id, team_id, division_id, group_id)
    SELECT v_new,
           st.team_id,
           COALESCE(mv.to_division_id, st.division_id),
           CASE WHEN mv.to_division_id IS NULL THEN st.group_id END
    FROM season_teams st
    JOIN teams t ON t.id = st.team_id AND t.archived_at IS NULL
    LEFT JOIN season_movements mv ON mv.season_id = p_prev_season_id AND mv.team_id = st.team_id
    JOIN divisions d ON d.id = COALESCE(mv.to_division_id, st.division_id) AND d.archived_at IS NULL
    WHERE st.season_id = p_prev_season_id;

    INSERT INTO rosters (season_id, team_id, player_id, joined_at, role)
    SELECT v_new, r.team_id, r.player_id, now(), r.role
    FROM rosters r
    JOIN season_teams st ON st.season_id = v_new AND st.team_id = r.team_id
    WHERE r.season_id = p_prev_season_id AND r.left_at IS NULL;

    UPDATE season_movements SET applied = true WHERE season_id = p_prev_season_id;
    RETURN v_new;
END;
$$;

-- ============================================================================
-- Views
-- ============================================================================

-- Regular-season record of every team in every season (the bot ranks these into `standings`).
CREATE VIEW team_records AS
SELECT
    st.season_id,
    st.team_id,
    st.division_id,
    st.group_id,
    count(m.id)::integer AS games_played,
    count(m.id) FILTER (WHERE (m.team1_id = st.team_id AND m.team1_score > m.team2_score)
                           OR (m.team2_id = st.team_id AND m.team2_score > m.team1_score))::integer AS wins,
    count(m.id) FILTER (WHERE (m.team1_id = st.team_id AND m.team1_score < m.team2_score)
                           OR (m.team2_id = st.team_id AND m.team2_score < m.team1_score))::integer AS losses,
    COALESCE(sum(CASE WHEN m.team1_id = st.team_id THEN m.team1_score ELSE m.team2_score END), 0)::integer AS points_for,
    COALESCE(sum(CASE WHEN m.team1_id = st.team_id THEN m.team2_score ELSE m.team1_score END), 0)::integer AS points_against
FROM season_teams st
LEFT JOIN matches m
    ON m.season_id = st.season_id
   AND m.stage = 'regular'
   AND m.status = 'final'
   AND st.team_id IN (m.team1_id, m.team2_id)
GROUP BY st.season_id, st.team_id, st.division_id, st.group_id;

-- ============================================================================
-- Stats functions (season-wide; pass p_division_id to limit to one division's teams)
-- ============================================================================

-- Final (non-void) stat lines with the team each player played for and that team's division.
CREATE VIEW stat_lines AS
SELECT
    ps.*,
    m.season_id,
    m.stage,
    m.played_at,
    m.team1_id,
    m.team2_id,
    st.division_id AS team_division_id
FROM player_stats ps
JOIN matches m ON m.id = ps.match_id AND m.status = 'final'
LEFT JOIN season_teams st ON st.season_id = m.season_id AND st.team_id = ps.team_id;

CREATE FUNCTION player_season_team_name(p_player_id uuid, p_season_id uuid) RETURNS text
LANGUAGE sql STABLE AS $$
    SELECT t.name
    FROM rosters r JOIN teams t ON t.id = r.team_id
    WHERE r.player_id = p_player_id AND r.season_id = p_season_id
    ORDER BY (r.left_at IS NULL) DESC, r.joined_at DESC
    LIMIT 1;
$$;

CREATE FUNCTION get_player_averages(p_player_id uuid, p_season_id uuid, p_division_id uuid DEFAULT NULL)
RETURNS TABLE(player_id uuid, display_name text, team_name text, games_played integer,
              ppg numeric, apg numeric, gpg numeric, spg numeric, ogpg numeric, bpg numeric, pp_efficiency numeric)
LANGUAGE sql STABLE AS $$
WITH agg AS (
    SELECT
        count(sl.id)::integer AS gp,
        COALESCE(sum(sl.points), 0) AS pts, COALESCE(sum(sl.assists), 0) AS ast,
        COALESCE(sum(sl.goals), 0) AS gls, COALESCE(sum(sl.saves), 0) AS svs,
        COALESCE(sum(sl.own_goals), 0) AS ogs, COALESCE(sum(sl.blocks), 0) AS blk,
        COALESCE(sum(sl.powerup_plays), 0) AS ppp, COALESCE(sum(sl.powerups_used), 0) AS ppu
    FROM stat_lines sl
    WHERE sl.player_id = p_player_id AND sl.season_id = p_season_id
      AND (p_division_id IS NULL OR sl.team_division_id = p_division_id)
)
SELECT p.id, p.display_name, COALESCE(player_season_team_name(p.id, p_season_id), 'Free Agent'), agg.gp,
       round(agg.pts::numeric / NULLIF(agg.gp, 0), 2), round(agg.ast::numeric / NULLIF(agg.gp, 0), 2),
       round(agg.gls::numeric / NULLIF(agg.gp, 0), 2), round(agg.svs::numeric / NULLIF(agg.gp, 0), 2),
       round(agg.ogs::numeric / NULLIF(agg.gp, 0), 2), round(agg.blk::numeric / NULLIF(agg.gp, 0), 2),
       CASE WHEN agg.ppu > 0 THEN round(agg.ppp::numeric / agg.ppu * 100, 2) ELSE 0 END
FROM players p CROSS JOIN agg
WHERE p.id = p_player_id AND agg.gp > 0;
$$;

CREATE FUNCTION get_player_totals(p_player_id uuid, p_season_id uuid, p_division_id uuid DEFAULT NULL)
RETURNS TABLE(player_id uuid, display_name text, team_name text, games_played integer,
              total_points integer, total_assists integer, total_goals integer, total_saves integer,
              total_own_goals integer, total_blocks integer, total_pp_plays integer, total_pp_used integer)
LANGUAGE sql STABLE AS $$
WITH agg AS (
    SELECT
        count(sl.id)::integer AS gp,
        COALESCE(sum(sl.points), 0)::integer AS pts, COALESCE(sum(sl.assists), 0)::integer AS ast,
        COALESCE(sum(sl.goals), 0)::integer AS gls, COALESCE(sum(sl.saves), 0)::integer AS svs,
        COALESCE(sum(sl.own_goals), 0)::integer AS ogs, COALESCE(sum(sl.blocks), 0)::integer AS blk,
        COALESCE(sum(sl.powerup_plays), 0)::integer AS ppp, COALESCE(sum(sl.powerups_used), 0)::integer AS ppu
    FROM stat_lines sl
    WHERE sl.player_id = p_player_id AND sl.season_id = p_season_id
      AND (p_division_id IS NULL OR sl.team_division_id = p_division_id)
)
SELECT p.id, p.display_name, COALESCE(player_season_team_name(p.id, p_season_id), 'Free Agent'), agg.gp,
       agg.pts, agg.ast, agg.gls, agg.svs, agg.ogs, agg.blk, agg.ppp, agg.ppu
FROM players p CROSS JOIN agg
WHERE p.id = p_player_id AND agg.gp > 0;
$$;

CREATE FUNCTION get_team_totals(p_team_id uuid, p_season_id uuid)
RETURNS TABLE(team_id uuid, team_name text, player_count integer, games_played integer,
              total_points integer, total_assists integer, total_goals integer, total_saves integer,
              total_own_goals integer, total_blocks integer, total_pp_plays integer, total_pp_used integer)
LANGUAGE sql STABLE AS $$
SELECT t.id, t.name,
       count(DISTINCT sl.player_id)::integer, count(DISTINCT sl.match_id)::integer,
       COALESCE(sum(sl.points), 0)::integer, COALESCE(sum(sl.assists), 0)::integer,
       COALESCE(sum(sl.goals), 0)::integer, COALESCE(sum(sl.saves), 0)::integer,
       COALESCE(sum(sl.own_goals), 0)::integer, COALESCE(sum(sl.blocks), 0)::integer,
       COALESCE(sum(sl.powerup_plays), 0)::integer, COALESCE(sum(sl.powerups_used), 0)::integer
FROM teams t
LEFT JOIN stat_lines sl ON sl.team_id = t.id AND sl.season_id = p_season_id
WHERE t.id = p_team_id
GROUP BY t.id, t.name;
$$;

CREATE FUNCTION get_team_averages(p_team_id uuid, p_season_id uuid)
RETURNS TABLE(team_id uuid, team_name text, games_played integer,
              ppg numeric, apg numeric, gpg numeric, spg numeric, ogpg numeric, bpg numeric, pp_efficiency numeric)
LANGUAGE sql STABLE AS $$
WITH agg AS (
    SELECT count(DISTINCT sl.match_id)::integer AS gp,
           COALESCE(sum(sl.points), 0)::numeric AS pts, COALESCE(sum(sl.assists), 0)::numeric AS ast,
           COALESCE(sum(sl.goals), 0)::numeric AS gls, COALESCE(sum(sl.saves), 0)::numeric AS svs,
           COALESCE(sum(sl.own_goals), 0)::numeric AS ogs, COALESCE(sum(sl.blocks), 0)::numeric AS blk,
           COALESCE(sum(sl.powerup_plays), 0)::numeric AS ppp, COALESCE(sum(sl.powerups_used), 0)::numeric AS ppu
    FROM stat_lines sl
    WHERE sl.team_id = p_team_id AND sl.season_id = p_season_id
)
SELECT t.id, t.name, agg.gp,
       CASE WHEN agg.gp > 0 THEN round(agg.pts / agg.gp, 2) ELSE 0 END,
       CASE WHEN agg.gp > 0 THEN round(agg.ast / agg.gp, 2) ELSE 0 END,
       CASE WHEN agg.gp > 0 THEN round(agg.gls / agg.gp, 2) ELSE 0 END,
       CASE WHEN agg.gp > 0 THEN round(agg.svs / agg.gp, 2) ELSE 0 END,
       CASE WHEN agg.gp > 0 THEN round(agg.ogs / agg.gp, 2) ELSE 0 END,
       CASE WHEN agg.gp > 0 THEN round(agg.blk / agg.gp, 2) ELSE 0 END,
       CASE WHEN agg.ppu > 0 THEN round(agg.ppp / agg.ppu * 100, 2) ELSE 0 END
FROM teams t CROSS JOIN agg
WHERE t.id = p_team_id;
$$;

CREATE FUNCTION get_leaderboard(p_metric text, p_season_id uuid, p_limit integer DEFAULT 10,
                                p_offset integer DEFAULT 0, p_division_id uuid DEFAULT NULL)
RETURNS TABLE(rank integer, player_name text, team_name text, value numeric, games_played integer)
LANGUAGE sql STABLE AS $$
WITH totals AS (
    SELECT sl.player_id,
           sum(sl.points) AS points, sum(sl.assists) AS assists, sum(sl.goals) AS goals,
           sum(sl.saves) AS saves, sum(sl.blocks) AS blocks, sum(sl.own_goals) AS own_goals,
           sum(sl.powerup_plays) AS pp_plays, count(DISTINCT sl.match_id) AS games
    FROM stat_lines sl
    WHERE sl.season_id = p_season_id
      AND (p_division_id IS NULL OR sl.team_division_id = p_division_id)
    GROUP BY sl.player_id
),
valued AS (
    SELECT t.player_id, t.games,
           (CASE p_metric
                WHEN 'points' THEN t.points WHEN 'assists' THEN t.assists WHEN 'goals' THEN t.goals
                WHEN 'saves' THEN t.saves WHEN 'blocks' THEN t.blocks WHEN 'own_goals' THEN t.own_goals
                WHEN 'pp_plays' THEN t.pp_plays ELSE 0 END)::numeric AS value
    FROM totals t
)
SELECT (row_number() OVER (ORDER BY v.value DESC, p.display_name))::integer,
       p.display_name, COALESCE(player_season_team_name(p.id, p_season_id), 'Free Agent'),
       v.value, v.games::integer
FROM valued v JOIN players p ON p.id = v.player_id
ORDER BY 1
LIMIT p_limit OFFSET p_offset;
$$;

CREATE FUNCTION get_player_history(p_player_id uuid, p_season_id uuid, p_limit integer DEFAULT 50,
                                   p_offset integer DEFAULT 0)
RETURNS TABLE(match_id uuid, match_date timestamptz, opponent_name text, points integer, assists integer,
              goals integer, saves integer, own_goals integer, blocks integer, pp_plays integer, pp_used integer)
LANGUAGE sql STABLE AS $$
SELECT sl.match_id, sl.played_at,
       COALESCE(CASE WHEN sl.team_id = sl.team1_id THEN t2.name WHEN sl.team_id = sl.team2_id THEN t1.name END, 'Unknown'),
       COALESCE(sl.points, 0)::integer, COALESCE(sl.assists, 0)::integer, COALESCE(sl.goals, 0)::integer,
       COALESCE(sl.saves, 0)::integer, COALESCE(sl.own_goals, 0)::integer, COALESCE(sl.blocks, 0)::integer,
       COALESCE(sl.powerup_plays, 0)::integer, COALESCE(sl.powerups_used, 0)::integer
FROM stat_lines sl
JOIN teams t1 ON t1.id = sl.team1_id
JOIN teams t2 ON t2.id = sl.team2_id
WHERE sl.player_id = p_player_id AND sl.season_id = p_season_id
ORDER BY sl.played_at DESC NULLS LAST
LIMIT p_limit OFFSET p_offset;
$$;

CREATE FUNCTION compare_players(p_player_a_id uuid, p_player_b_id uuid, p_season_id uuid)
RETURNS TABLE(metric text, player_a_value numeric, player_b_value numeric, diff numeric)
LANGUAGE sql STABLE AS $$
WITH s AS (
    SELECT sl.player_id,
           COALESCE(sum(sl.points), 0)::numeric AS points, COALESCE(sum(sl.assists), 0)::numeric AS assists,
           COALESCE(sum(sl.goals), 0)::numeric AS goals, COALESCE(sum(sl.saves), 0)::numeric AS saves,
           COALESCE(sum(sl.blocks), 0)::numeric AS blocks,
           CASE WHEN sum(sl.powerups_used) > 0
                THEN sum(sl.powerup_plays)::numeric / sum(sl.powerups_used)::numeric * 100 ELSE 0 END AS pp_eff
    FROM stat_lines sl
    WHERE sl.season_id = p_season_id AND sl.player_id IN (p_player_a_id, p_player_b_id)
    GROUP BY sl.player_id
),
a AS (SELECT * FROM s WHERE player_id = p_player_a_id),
b AS (SELECT * FROM s WHERE player_id = p_player_b_id),
pairs AS (
    SELECT 1 AS ord, 'Points'::text AS metric, (SELECT points FROM a) AS av, (SELECT points FROM b) AS bv
    UNION ALL SELECT 2, 'Assists', (SELECT assists FROM a), (SELECT assists FROM b)
    UNION ALL SELECT 3, 'Goals', (SELECT goals FROM a), (SELECT goals FROM b)
    UNION ALL SELECT 4, 'Saves', (SELECT saves FROM a), (SELECT saves FROM b)
    UNION ALL SELECT 5, 'Blocks', (SELECT blocks FROM a), (SELECT blocks FROM b)
    UNION ALL SELECT 6, 'PP Efficiency %', (SELECT pp_eff FROM a), (SELECT pp_eff FROM b)
)
SELECT metric, COALESCE(av, 0), COALESCE(bv, 0), COALESCE(av, 0) - COALESCE(bv, 0)
FROM pairs ORDER BY ord;
$$;

CREATE FUNCTION get_season_summary(p_season_id uuid, p_division_id uuid DEFAULT NULL)
RETURNS TABLE(total_matches integer, total_teams integer, total_players integer, avg_points_per_match numeric,
              top_scorer_name text, top_scorer_points integer, top_team_name text, top_team_points integer)
LANGUAGE sql STABLE AS $$
WITH lines AS (
    SELECT * FROM stat_lines
    WHERE season_id = p_season_id AND (p_division_id IS NULL OR team_division_id = p_division_id)
),
scorer AS (
    SELECT p.display_name, sum(l.points)::integer AS pts
    FROM lines l JOIN players p ON p.id = l.player_id
    GROUP BY p.id, p.display_name ORDER BY pts DESC NULLS LAST, p.display_name LIMIT 1
),
team_pts AS (
    SELECT t.name, sum(l.points)::integer AS pts
    FROM lines l JOIN teams t ON t.id = l.team_id
    GROUP BY t.id, t.name ORDER BY pts DESC NULLS LAST, t.name LIMIT 1
)
SELECT
    (SELECT count(*) FROM matches m WHERE m.season_id = p_season_id AND m.status = 'final'
        AND (p_division_id IS NULL OR m.division_id = p_division_id))::integer,
    (SELECT count(*) FROM season_teams st WHERE st.season_id = p_season_id
        AND (p_division_id IS NULL OR st.division_id = p_division_id))::integer,
    (SELECT count(DISTINCT player_id) FROM lines)::integer,
    (SELECT round(avg(points)::numeric, 2) FROM lines),
    COALESCE((SELECT display_name FROM scorer), 'N/A'),
    COALESCE((SELECT pts FROM scorer), 0),
    COALESCE((SELECT name FROM team_pts), 'N/A'),
    COALESCE((SELECT pts FROM team_pts), 0);
$$;

-- ============================================================================
-- Atomic trade execution
-- ============================================================================
-- Locks the proposal, re-validates every asset against current ownership, moves rosters and picks,
-- logs one 'trade' transaction (+ audit event) per moved asset and marks the proposal executed.
-- Any failure rolls the whole trade back; the HINT is a stable code the bot maps to a message.
CREATE FUNCTION execute_trade_proposal(p_proposal_id uuid, p_executed_by bigint)
RETURNS integer LANGUAGE plpgsql AS $$
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
    SELECT * INTO v_proposal FROM trade_proposals WHERE id = p_proposal_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'trade proposal % not found', p_proposal_id USING HINT = 'TRADE_NOT_FOUND';
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
        SELECT * FROM trade_assets WHERE proposal_id = p_proposal_id ORDER BY created_at, id
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
            UPDATE rosters SET left_at = v_now
            WHERE season_id = v_proposal.season_id AND player_id = v_asset.player_id
              AND team_id = v_from_team AND left_at IS NULL;
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
            UPDATE draft_picks SET current_owner_team_id = v_to_team, updated_at = v_now
            WHERE id = v_asset.draft_pick_id AND current_owner_team_id = v_from_team;
            GET DIAGNOSTICS v_rows = ROW_COUNT;
            IF v_rows = 0 THEN
                RAISE EXCEPTION 'draft pick % is no longer owned by team %', v_asset.draft_pick_id, v_from_team
                    USING HINT = 'TRADE_PICK_UNAVAILABLE';
            END IF;
        ELSE
            CONTINUE;  -- cash / other assets carry no ownership to move
        END IF;

        INSERT INTO transactions (season_id, type, player_id, from_team_id, to_team_id, executed_by, details)
        VALUES (
            v_proposal.season_id, 'trade', v_asset.player_id, v_from_team, v_to_team, p_executed_by,
            jsonb_build_object(
                'trade_proposal_id', p_proposal_id, 'trade_asset_id', v_asset.id,
                'asset_type', v_asset.asset_type, 'draft_pick_id', v_asset.draft_pick_id,
                'force_trade', v_force, 'executed_at', v_now
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
    SET status = 'executed', reviewed_by = p_executed_by, updated_at = v_now
    WHERE id = p_proposal_id;
    RETURN v_moved;
END;
$$;

-- ============================================================================
-- Access: only the bot's service role reaches the public schema
-- ============================================================================
-- Supabase exposes `public` through its REST API to anon/authenticated, whose key is public.
-- Every table gets RLS with no policies, and anon/authenticated lose all privileges.
-- The bot's SUPABASE_KEY must be the service_role / secret key.

DO $$
DECLARE
    r record;
BEGIN
    FOR r IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', r.tablename);
    END LOOP;
END;
$$;

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated;
REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC, anon, authenticated;

GRANT ALL ON ALL TABLES IN SCHEMA public TO service_role;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO service_role;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO service_role;

ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC, anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO service_role;

COMMIT;
