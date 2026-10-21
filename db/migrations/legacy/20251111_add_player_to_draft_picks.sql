-- 20251111_add_player_to_draft_picks.sql
-- Add selected_player_id column to draft_picks table to track which player was picked
-- Date: 2025-11-11

BEGIN;

-- Add selected_player_id column to draft_picks
ALTER TABLE IF EXISTS draft_picks
  ADD COLUMN IF NOT EXISTS selected_player_id uuid REFERENCES players(id) ON DELETE SET NULL;

-- Add index for quick lookups
CREATE INDEX IF NOT EXISTS idx_draft_picks_selected_player
  ON draft_picks(selected_player_id);

COMMIT;
