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
- **Readiness Check**: `http://localhost:8080/ready` - Readiness probe
- **Metrics**: `http://localhost:8080/metrics` - Performance metrics

Configure port via `HEALTH_CHECK_PORT` environment variable (default: 8080).

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

