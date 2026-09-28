-- 2026-10-27: Lock the database down to the bot's service role
-- The bot is the only client of this database. Supabase exposes the public schema through
-- its REST API to the `anon` and `authenticated` roles, whose key is designed to be public,
-- so without this anyone holding the project URL + anon key could read and rewrite rosters,
-- results, and trades directly.
--
-- After this migration:
--   * every table in public has RLS enabled with no policies -> anon/authenticated see nothing
--   * anon/authenticated lose all table, sequence, view, and function privileges
--   * service_role (bypasses RLS) keeps full access and can execute every function
--
-- REQUIRED: the bot's SUPABASE_KEY must be the service_role / secret key before applying this.

BEGIN;

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

-- Objects created later inherit the same rules.
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC, anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO service_role;

COMMIT;
