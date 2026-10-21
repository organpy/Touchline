-- 20251110_remove_player_position.sql
-- Remove position column from players table
-- Date: 2025-11-10

BEGIN;

-- Drop the position column from players table if it exists
ALTER TABLE IF EXISTS players
  DROP COLUMN IF EXISTS position CASCADE;

COMMIT;
