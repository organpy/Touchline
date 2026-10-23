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

COMMIT;
