# NBLBot

NBLBot is a Discord bot for managing National Bobble League divisions (NBL, MBL, NCBA). It includes commands for stats, transactions, and admin operations.

## 🚀 Quick Start

### Development Setup

1. **Clone and setup environment**
   ```bash
   git clone https://github.com/organpy/NBLBot.git
   cd NBLBot
   python -m venv .venv
   source .venv/bin/activate  # Linux/Mac
   .\.venv\Scripts\Activate.ps1  # Windows
   ```

2. **Install dependencies** (tested on Python 3.12)
   ```bash
   pip install -r requirements.txt       # running the bot
   pip install -r requirements-dev.txt   # running the bot and the tests
   ```

3. **Configure environment**
   ```bash
   cp .env.example .env
   # Edit .env with your Discord token and Supabase service_role key
   ```

4. **Run the bot**
   ```bash
   python bot.py
   ```

5. **Verify health check**
   ```bash
   curl http://localhost:8080/health
   ```

### Production Deployment

`deploy/nblbot.service` runs the bot under systemd from `/opt/nblbot` as an unprivileged `nblbot` user, restarts it on failure, and stops it cleanly on `systemctl stop`.

```bash
sudo useradd --system --home-dir /opt/nblbot --shell /usr/sbin/nologin nblbot
sudo git clone https://github.com/organpy/NBLBot.git /opt/nblbot
cd /opt/nblbot
sudo python3.12 -m venv .venv
sudo .venv/bin/pip install -r requirements.txt
sudo cp .env.example .env && sudo nano .env   # fill in the real values
sudo chown -R nblbot:nblbot /opt/nblbot && sudo chmod 600 .env
sudo cp deploy/nblbot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nblbot
journalctl -u nblbot -f                        # follow the logs
```

To update:

```bash
sudo -u nblbot git -C /opt/nblbot pull
sudo -u nblbot /opt/nblbot/.venv/bin/pip install -r /opt/nblbot/requirements.txt   # if it changed
sudo systemctl restart nblbot
```

## 🗄️ Database

The schema lives in `db/migrations/` and is applied by hand with `psql` (`DATABASE_URL` is the Supabase Postgres connection string). On a new database, run `init.sql` first, then every dated migration in filename order:

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/migrations/init.sql
for f in db/migrations/2*.sql; do psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$f"; done
```

On an existing database, apply only the migrations it does not have yet, oldest first. Nothing records which migrations have run, so note each one in your deploy log.

### Upgrading to the 2026-10-27 release

Follow this order, or the bot (or the live database) breaks partway through:

1. Apply `20261027_atomic_trade_execution.sql`. It only adds a function, so the running bot is unaffected.
2. Set `SUPABASE_KEY` to the **service_role** (or `sb_secret_...`) key, then deploy and restart the bot. The new bot refuses to start with the public anon key.
3. Apply `20261027_lock_down_public_access.sql`. It revokes all anon access, so any client still using the anon key stops working.

To check the trade function, run `db/tests/execute_trade_proposal_test.sql` against a Supabase branch with all migrations applied. It runs in one transaction, rolls back, and prints `ALL TRADE TESTS PASSED`.

## 📚 Documentation

### In-Discord Help
Use `/help` inside Discord to list available commands and `/help <command>` to view detailed usage for a specific command.

## 🧪 Testing

Run all tests (needs `requirements-dev.txt`):
```bash
pytest test/ -v
```

Run specific test suites:
```bash
pytest test/test_integration.py -v        # Integration tests
pytest test/test_admin_force_commands.py -v  # Admin command tests
```

## 🔧 Development

### Test Data Seeding

Two helper scripts keep the Supabase database populated while testing:

- **Teams & emojis**: syncs every team in `teams.json` into `divisions`/`teams`.
  ```bash
  python scripts/seed_teams.py --apply
  python scripts/seed_teams.py --apply --prune-missing   # optional cleanup
  ```

- **Sample standings**: creates a handful of final matches per active season so `/standings` has data to render.
  ```bash
  python scripts/seed_sample_standings.py --apply --reset
  ```

Omit `--apply` on either script to preview the actions without writing to the database.

### Health Monitoring

The bot includes HTTP endpoints for monitoring:

- **Health Check**: `http://localhost:8080/health` - Liveness probe
- **Readiness Check**: `http://localhost:8080/ready` - Readiness probe (503 while the Discord gateway is disconnected)
- **Metrics**: `http://localhost:8080/metrics` - Performance metrics

Configure the port with `HEALTH_CHECK_PORT` (default: 8080) and the bind address with `HEALTH_CHECK_HOST` (default: `127.0.0.1`; use `0.0.0.0` when probed from outside the host or container).

### Slash Command Sync

On startup the bot syncs slash commands to Discord only when their definitions changed since the last successful sync, tracked in `data/.command_sync_hash`. Delete that file to force a sync on the next boot.

### Rate Limiting

Commands are protected by rate limiting (5 commands per 60 seconds per user). Admins bypass rate limits.

To apply rate limiting to a command:
```python
from utils.rate_limit_decorator import rate_limit, admin_bypass_rate_limit

@app_commands.command()
@admin_bypass_rate_limit
@rate_limit(max_requests=5, window_seconds=60)
async def my_command(interaction: discord.Interaction):
    # Command logic here
    pass
```

## 🏗️ Project Structure

```
nbl-bot/
├── bot.py                    # Main bot entry point
├── .env.example             # Environment variable template
├── requirements.txt         # Runtime dependencies (pinned)
├── requirements-dev.txt     # Runtime + test dependencies
├── cogs/                    # Command modules (auto-discovered)
│   ├── stats/              # Stats commands
│   ├── transactions/       # Transaction commands
│   ├── admin/              # Admin commands
│   └── ...
├── db/                      # Database utilities
│   ├── migrations/         # SQL schema migrations
│   ├── queries.py          # Database query functions
│   └── supabase_client.py  # Supabase singleton
├── utils/                   # Utility modules
│   ├── health_check.py     # Health check server
│   ├── rate_limiter.py     # Rate limiting
│   └── ...
└── test/                    # Test suite
    ├── test_integration.py # Integration tests
    └── ...
```

## 🚀 Production Features

- ✅ **Health Monitoring**: HTTP endpoints for liveness, readiness, and metrics
- ✅ **Rate Limiting**: Prevent command abuse with configurable limits
- ✅ **Auto-Restart**: Systemd service with automatic restart on failure
- ✅ **Error Handling**: Comprehensive error logging and user-friendly messages
- ✅ **Cache Management**: In-memory caching for fast lookups
- ✅ **Audit Logging**: All admin actions logged to database
- ✅ **Test Coverage**: 330+ unit tests, integration test suite

## 📊 Commands

31+ commands across categories:
- **[STATS]** - Leaderboards, averages, totals, comparisons, standings
- **[TRANSACTIONS]** - Signing, releasing, trading, roster management
- **[ADMIN]** - Force operations, configuration, audit reports

See `/help` in Discord for complete command list.

## 📄 License

MIT License — see [LICENSE](LICENSE) for full text.

## 🤝 Contributing

Contributions welcome! Please ensure all tests pass before submitting PRs:
```bash
pytest test/ -v
```

