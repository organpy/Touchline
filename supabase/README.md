# Supabase Multi-League Bot Scaffold

Fast-track setup for a Postgres-backed Discord bot supporting three sports leagues (NBL, MBL, NCBA).

## 📋 Overview

This directory contains:
- **Postgres schema** (`migrations/001_init.sql`) — Tables for multi-league standings, teams, players, matches, and stats.
- **Python adapter** (`adapter.py`) — Thin DB abstraction for bot commands (psycopg2-based).
- **Migration scripts** (`migrations/scripts/`) — Extract from Google Sheets and load into DB.
- **Design docs** (`DESIGN.md`, `PLAN_SUMMARY.md`) — Complete architecture and migration strategy.

## 🚀 Quick Start (5 minutes)

### 1. Setup Supabase (or Postgres)

Create a project at [Supabase](https://supabase.com) or use any Postgres instance.
Get your connection string:
```
postgresql://user:password@host:5432/dbname
```

### 2. Set Environment Variable

Create a `.env` file in project root:
```bash
DATABASE_URL=postgresql://user:password@host:5432/dbname
```

Or export directly (PowerShell):
```powershell
$env:DATABASE_URL = "postgresql://..."
```

### 3. Deploy Schema

Apply the initial migration using psql or Supabase SQL editor:

**Option A: psql (command line)**
```powershell
# Install psql if needed, then run:
psql $env:DATABASE_URL -f .\supabase\migrations\001_init.sql
```

**Option B: Supabase Console**
Copy-paste contents of `migrations/001_init.sql` into Supabase SQL editor and execute.

### 4. Install Python Dependencies

```powershell
pip install psycopg2-binary
```

### 5. Test the Adapter

```powershell
python -m supabase.adapter
```

Expected output (if DB running):
```
Testing adapter...
Active season: None (expected if no data loaded yet)
NBL Leaderboard (first 5): []
✅ Adapter smoke test passed (if DB is running)
```

## 📚 Documentation

| File | Purpose |
|------|---------|
| **DESIGN.md** | Complete database schema, tables, materialized views, example queries |
| **PLAN_SUMMARY.md** | Architecture overview, design rationale, roadmap, risks |
| **PHASE_SUMMARY.md** | Planning phase closure, delivered artifacts |
| **INDEX.md** | File index and navigation |
| **migrations/MIGRATION_PLAN.md** | Data extraction and loading strategy (sheets → DB) |

## 💡 Key Concepts

### Multi-League Model
- **Single database** for NBL, MBL, NCBA (distinguished by `divisions.league_type`).
- **Flexible rosters** — Players can be loaned/transferred mid-season (`rosters.left_at` field).
- **Pre-computed stats** — `powerup_percentage` calculated on insert for fast queries.
- **Auto-computed standings** — Materialized views refresh after each match.

### Standings Logic
**NBL/NCBA (point-based):**
- Team score = Sum of player points (manually entered per player per match).
- Sorting: Point differential DESC, Points for DESC.

**MBL (goal-based):**
- Team score = Sum of player goals.
- Competition points: 3 per win, 1 per tie, 0 per loss.
- Sorting: Competition points DESC, Goal differential DESC.

## 🔧 Adapter Methods

See `adapter.py` for full docstrings. Key methods:

```python
# Leaderboard queries
get_leaderboard(league='NBL', limit=25)           # Get standings
get_team_info(league='NBL', team_code='pandas')   # Get team + roster

# Player stats
get_player_totals(league='NBL', stat='goals', limit=50)     # Top goal scorers
get_player_averages(league='NBL', stat='goals', limit=50)   # Avg per match

# Match recording (stub, to be implemented)
record_match(payload)                              # Record match + player stats

# Utility
get_active_season()                                # Get current season
get_divisions()                                    # League → division ID mapping
```

## 📊 Data Loading

To load data from Google Sheets:

1. **Extract** — Fetch sheets as CSV:
   ```powershell
   python .\supabase\migrations\scripts\extract_sheets.py
   ```

2. **Load** — Import into DB:
   ```powershell
   python .\supabase\migrations\scripts\load_db.py
   ```

3. **Validate** — Verify data quality:
   ```powershell
   python .\supabase\migrations\scripts\validate.py
   ```

See `migrations/MIGRATION_PLAN.md` for detailed extraction strategy.

## 🎯 Next Steps

1. **Finalize sheet structure** — Inspect actual CSV exports from sheets.
2. **Load sample data** — Test extraction and validation scripts.
3. **Scaffold bot commands** — Implement Discord commands in `bot/` package.
4. **Wire to adapter** — Connect commands to DB reads/writes.
5. **Deploy to production** — Load real data and switch bot to DB.

## ⚙️ Configuration

### Environment Variables

```bash
DATABASE_URL=postgresql://...           # Postgres connection
DISCORD_TOKEN=your_bot_token            # Discord bot token
APPLICATION_ID=your_app_id              # Discord app ID
BOT_OWNER_ID=discord_user_id            # Owner for admin commands
LEAGUE_GUILD_MAPPING={"guild1":"NBL"}   # Optional: map guilds to leagues
```

### Database Connection

Adapter uses `psycopg2.connect()` with `DATABASE_URL`. For connection pooling (advanced), consider:
- `psycopg2.pool.SimpleConnectionPool`
- `asyncpg` (async alternative)

## ⚠️ Important Notes

- **`inspiration/` folder** is reference only; do NOT modify it.
- **Schema deployment** is idempotent (uses `IF NOT EXISTS`).
- **Materialized views** refresh after writes for consistency. Tune refresh frequency as needed.
- **Pre-computed stats** (powerup%) avoid expensive calculations on read; trade-off: slightly slower inserts.

## 🐛 Troubleshooting

**"DATABASE_URL not set"**
```powershell
# Ensure environment variable is set:
$env:DATABASE_URL = "postgresql://..."
```

**"Could not connect to database"**
- Confirm Supabase project is running and connection string is correct.
- Check firewall/security group rules allow your IP.
- Test with: `psql $env:DATABASE_URL -c "SELECT 1"`

**"Permission denied" errors**
- Ensure Postgres user has CREATE TABLE, CREATE VIEW permissions.
- Supabase projects default to owner role; should work fine.

## 📝 License

Same as parent project (see `LICENSE`).
