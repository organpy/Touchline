<div align="center">

<img src="touchlinebanner.svg" alt="Touchline banner" width="75%">

**League management for Discord: divisions, promotion and relegation, playoffs and stats.**

![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![discord.py](https://img.shields.io/badge/discord.py-2.7-5865F2?logo=discord&logoColor=white)
![Supabase](https://img.shields.io/badge/Supabase-3FCF8E?logo=supabase&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-4169E1?logo=postgresql&logoColor=white)
![Pillow](https://img.shields.io/badge/Pillow-13-3776AB)

[Features](#features) · [Quick start](#quick-start) · [Running a season](#running-a-season) · [Database](#database) · [Website data](#website-data) · [Deployment](#production-deployment) · [Development](#development) · [Troubleshooting](#troubleshooting)

</div>

---

Touchline started as the NBL's Discord bot and now runs any league with any number of tiers. Admins create divisions and teams from Discord. One league-wide season moves through a regular season, a D1 championship playoff, promotion and relegation (direct or through playoffs), and then rolls over into the next season with every team in its new division. A `/dashboard` panel runs everything, a league board in a channel keeps itself up to date, and read-only views let a website show the same data.

## Features

| Area | What it does |
|---|---|
| **Divisions and groups** | Any number of divisions (D1 at the top, then D2, D3 and so on), each with a name, code, colour and emoji. Divisions can be split into optional groups (conferences). Each division has its own rules: ranking by points scored or by points per win, the games limit between two teams, and whether draft picks are used. |
| **Seasons** | One season for the whole league: setup → regular season → postseason → offseason → next season. Each season records which division every team played in, so standings from past seasons keep their old divisions. |
| **Promotion and relegation** | Set between each pair of divisions: a number of direct swaps, plus playoff swaps decided by separate promotion and relegation brackets or by head-to-head series between the two divisions. Standings show the zones as they change. |
| **D1 playoffs** | A seeded championship bracket of 2 to 16 teams, with byes for the top seeds, best-of per round, and seeding overall or by group. Before the first game, admins can swap seeds to settle a tie by hand. |
| **Dashboard** | `/dashboard` for admins: divisions, teams (owners, logos), season steps, playoffs, matches, transactions and server settings, all inside one message. |
| **League board** | A channel showing an overview, a standings image per division coloured by zone, and the brackets. It edits itself about 10 seconds after every result. Playoff results, champions and promotions are announced too. |
| **Score reporting** | `/report-score`, `/report-stats` and `/report-stats-bulk`. In the regular season a game counts in the two teams' division, up to the division's games limit. In the postseason it goes to the open playoff series between the two teams. |
| **Stats and cards** | Standings, leaderboards, averages and totals, player and team comparisons, player cards, match tables and season summaries, per division or for the whole league. |
| **Transactions and trades** | Signing offers, releases, trades with approval, ownership transfers, admin force moves, a transaction blocker and an audit log. A player plays for one team per season, and an owner owns one team. |
| **Draft picks** | Per division, turned on in its rules. Picks are traded within their division. |
| **Website data** | Read-only views in a Supabase `api` schema, readable with the public (publishable) key. Discord ids and private tables stay hidden. |
| **Monitoring** | Health, readiness and metrics endpoints, and error alerts to a Discord webhook. |

## Quick start

Requires **Python 3.12** and a **Supabase** project (or any Postgres reachable through PostgREST).

```bash
git clone https://github.com/organpy/repo.git touchline && cd touchline
python -m venv .venv && source .venv/bin/activate      # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env                                   # then fill in the values below
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/migrations/init.sql
python bot.py
```

Invite the bot with the `bot` and `applications.commands` scopes, then run `/dashboard` in your server. The **Settings** section is the place to start.

### Configuration

Settings come from environment variables or a `.env` file in the project folder.

| Variable | Default | Purpose |
|---|---|---|
| `DISCORD_TOKEN` | | Bot token from the Discord developer portal |
| `APPLICATION_ID` | | The bot's application id |
| `BOT_OWNER_ID` | | Your Discord user id (owner-only commands) |
| `SUPABASE_URL` | | `https://<project>.supabase.co` |
| `SUPABASE_KEY` | | The **service_role** or `sb_secret_...` key. The bot refuses to start with the public anon key |
| `DATABASE_URL` | | Postgres connection string, used only by `psql` and the scripts |
| `ERROR_WEBHOOK_URL` | | Discord webhook that receives every error log |
| `HEALTH_CHECK_HOST` / `HEALTH_CHECK_PORT` | `127.0.0.1` / `8080` | Where the health server listens. Use `0.0.0.0` when probed from outside the host |

Keep `.env` private. It is git-ignored.

## Running a season

<details>
<summary><b>First setup</b></summary>

<br>

Open `/dashboard` → **Settings**:

- **Admin role**: who can use the dashboard and admin commands. Without one, only server administrators can.
- **Channels**: transactions, admin commands (leave unset to allow everywhere), trading, announcements and the **league board**.
- **League name**: shown in embed footers and on the board, for example *National Bobble League*.
- **Signing offers**: how long offers last and whether the league must approve signings.

Changes save straight away.

</details>

<details>
<summary><b>Divisions and groups</b></summary>

<br>

1. **Dashboard → Divisions → Add division** with a name, a code (`D1`) and an optional colour and emoji. New divisions go to the bottom tier. Use **Move up** and **Move down** to reorder.
2. **Rules**: ranking (`points_scored` or `win_points` with points per win), games per matchup and draft picks.
3. **Groups**: add groups such as *East* and *West*, then put teams in them from the team page.

Ties in the standings are broken by point difference, then points for, then head-to-head, wins and finally name.

</details>

<details>
<summary><b>Teams, owners and logos</b></summary>

<br>

**Dashboard → Teams → Add team** with a name and a short code. On the team page:

- pick its **division** (and group) for the season
- **Set owner** (one team per owner)
- upload a **logo**. It is squared to 512×512, stored in the `team-logos` Supabase Storage bucket and used on standings images and cards. Create the bucket once, as public, in Supabase → Storage.

Large leagues get a division-first team picker, so selects never cut off at 25 teams.

</details>

<details>
<summary><b>Promotion and relegation</b></summary>

<br>

Movement is set on the upper division of each pair: **Dashboard → Divisions → D1 → Pro/rel below**.

- **Direct swaps**: the bottom N of the upper division go down and the top N of the lower division go up.
- **Playoff swaps**: P more places decided by playoffs, in one of two modes:
  - **separate**: the upper division plays a relegation bracket (the losers advance, and whoever is left is relegated) and the lower division plays a promotion bracket. Sizes are written like `4 / 4` (upper / lower). A relegation bracket must have P×2, P×4, ... teams, so no team gets a bye.
  - **cross**: P head-to-head series, the best upper playoff team against the worst lower one. The winner takes the upper place.

The division page warns you when the zones overlap for the number of teams the division has.

</details>

<details>
<summary><b>D1 playoffs</b></summary>

<br>

**Dashboard → Divisions → D1 → Championship**: turn it on, choose the number of teams (2-16), best-of per round (for example `1, 3, 5`) and seeding (overall or by group). When the bracket isn't a full power of two, the top seeds get byes.

</details>

<details>
<summary><b>Reporting games</b></summary>

<br>

Owners report with `/report-score` (scores only), `/report-stats` (one game with player stats) or `/report-stats-bulk`. The preview shows where the game will count:

- **Regular season**: both teams must be in the same division. The division's games limit applies.
- **Postseason**: the game goes to the open series between the two teams, and the series moves on when someone has enough wins.

Standings and the league board update automatically. A game entered by mistake can be voided in **Dashboard → Matches**.

</details>

<details>
<summary><b>Ending the regular season and the postseason</b></summary>

<br>

**Dashboard → Season** shows the next step, and a preview before anything changes:

1. **Start regular season**: checks every division has enough teams and that the zones fit.
2. **End regular season**: locks the standings and creates every bracket (championship, promotion, relegation, cross).
3. **Dashboard → Playoffs**: follow each bracket. You can swap seeds before the first game, or decide a series by forfeit.
4. **Finish postseason**: when every bracket is done, review the champion and every promotion and relegation, then confirm.

Need to recalculate after fixing data? **Season → Recalculate standings** does it.

</details>

<details>
<summary><b>Starting the next season</b></summary>

<br>

In the offseason, **Season → Start next season** asks for a name (suggested from the last one). Teams move to their new divisions, active rosters carry over, and the old season keeps its standings, brackets and divisions for `/standings`, `/history` and the website.

</details>

<details>
<summary><b>Transactions, trades and draft picks</b></summary>

<br>

**Dashboard → Transactions** opens the existing panels: players, rosters, draft picks, trades, offers, overrides, rules, the blocker and the audit log. Owners use `/sign`, `/release`, `/trade`, `/transfer-ownership`, `/roster` and `/picks`. Admins also have `/admin-force-sign`, `/admin-force-release` and `/admin-force-pick`.

Draft picks only exist in divisions with **Draft picks** turned on in their rules.

</details>

## Database

The schema is a single file, `db/migrations/init.sql`. Apply it by hand with `psql` (`DATABASE_URL` is the Supabase Postgres connection string):

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/migrations/init.sql
```

It turns on row level security and removes every privilege from the public `anon` and `authenticated` roles on base tables. Only the bot's service role reads and writes them.

<details>
<summary><b>Upgrading from the old NBL schema</b></summary>

<br>

The new schema doesn't keep the NBL/MBL/NCBA data. **Back up first:**

```bash
pg_dump "$DATABASE_URL" --format=custom --file=nbl-backup-$(date +%Y%m%d).dump
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/reset_legacy.sql     # drops the old league tables
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/migrations/init.sql
```

`bot_config` (server settings such as the admin role and channels) is kept. The old dated migrations are in `db/migrations/legacy/` for reference only. After upgrading, delete `data/.command_sync_hash` so the renamed commands sync.

</details>

The SQL tests run inside one transaction and roll back. Run them on a Supabase branch or a local Postgres (run `db/tests/local_roles.sql` and `init.sql` first on plain Postgres):

```bash
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/tests/league_schema_test.sql        # ALL LEAGUE TESTS PASSED
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/tests/execute_trade_proposal_test.sql # ALL TRADE TESTS PASSED
```

## Website data

`init.sql` creates read-only views in an `api` schema: `divisions`, `division_groups`, `seasons`, `teams`, `season_teams`, `players`, `rosters`, `matches`, `player_stats`, `standings`, `brackets`, `bracket_entries`, `series` and `season_movements`. They leave out Discord ids, trades, signings, server settings and voided games.

To turn them on, add `api` under **Supabase → Settings → API → Exposed schemas**. Then any site can read them with the publishable key:

```bash
curl "$SUPABASE_URL/rest/v1/standings?select=*,teams(name,logo_url)&order=rank" \
  -H "apikey: $SUPABASE_PUBLISHABLE_KEY" \
  -H "Accept-Profile: api"
```

With supabase-js: `supabase.schema('api').from('standings').select('*')`. Standings are written by the bot after every result, so the website and Discord always agree. The base tables stay locked: the same request without `Accept-Profile: api` is refused.

## Production deployment

`deploy/touchline.service` runs the bot under systemd from `/opt/touchline` as an unprivileged `touchline` user, restarts it on failure and stops it cleanly.

```bash
sudo useradd --system --home-dir /opt/touchline --shell /usr/sbin/nologin touchline
sudo git clone https://github.com/organpy/repo.git /opt/touchline
cd /opt/touchline
sudo python3.12 -m venv .venv
sudo .venv/bin/pip install -r requirements.txt
sudo cp .env.example .env && sudo nano .env
sudo chown -R touchline:touchline /opt/touchline && sudo chmod 600 .env
sudo cp deploy/touchline.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now touchline
journalctl -u touchline -f
```

To update: `sudo -u touchline git -C /opt/touchline pull`, reinstall requirements if they changed, then `sudo systemctl restart touchline`. Moving from the old `nblbot` unit? Stop and disable it before you enable `touchline`.

The health server answers on `/health` (liveness), `/ready` (503 while disconnected from Discord) and `/metrics`. With `ERROR_WEBHOOK_URL` set, every error log is posted to that channel, at most 10 per minute and never with pings.

## Development

```bash
pip install -r requirements-dev.txt
pytest test/ -q
```

`scripts/seed_demo.py` fills a test database with divisions, teams and a season in setup (it previews without `--apply`):

```bash
python scripts/seed_demo.py --apply --divisions 4 --teams 8
```

Slash commands sync on startup only when their definitions changed, tracked in `data/.command_sync_hash`. Delete that file to force a sync. Domain rules (standings, brackets, movements, the season lifecycle and reporting) live in `services/` with no Discord code, and most tests target them directly.

## Project layout

```
bot.py                 entry point: caches, cog discovery, command sync, health server
cogs/
  dashboard/           /dashboard pages (divisions, teams, season, playoffs, matches, settings)
  board/               auto-updating league board and announcements
  score/               /report-score, /report-stats, /report-stats-bulk
  stats/               standings, leaderboards, cards, history
  transactions/        signings, releases, trades, picks
  admin/               force commands and transaction panels
  help/                /help and /admin-help
services/              league rules: standings, brackets, movements, lifecycle, reporting
db/
  migrations/init.sql  the schema, RPCs and website views
  reset_legacy.sql     removes the old NBL schema
  tests/               SQL tests
  league_repository.py divisions, seasons, standings, brackets
utils/                 caches, image generators, logo storage, config
scripts/               demo data, deployment checks, command docs
deploy/                systemd unit
test/                  pytest suite
```

## Troubleshooting

**Commands don't show up in Discord.** Delete `data/.command_sync_hash` and restart; the bot then syncs on startup. Discord can take a minute to show new commands, and the bot must have been invited with `applications.commands`.

**The bot exits saying SUPABASE_KEY is the anon key.** It needs the service_role (or `sb_secret_...`) key from Supabase → Settings → API. The publishable key is only for websites reading the `api` views.

**The league board doesn't update.** Check the board channel in **Dashboard → Settings**, and that the bot can send messages, embed links and attach files there. **Refresh board** redraws it immediately. If you delete the board messages, the bot posts them again on its next refresh.

**"Games can only be reported during the regular season or the playoffs."** The season is in setup or the offseason. Start the regular season from **Dashboard → Season**.

**"These teams don't have an open playoff series right now."** In the postseason, a game counts only when the two teams meet in a series that is ready, so both of its teams must be decided. Check **Dashboard → Playoffs**.

**"A later playoff series already has results. Void those games first."** A result that fed a later series can't be voided while that series has games. Void the later games in **Dashboard → Matches** first, newest first.

**Team logos don't appear.** Create a public `team-logos` bucket in Supabase Storage, then upload the logo again from the team page.

## License

MIT. See [LICENSE](LICENSE).
