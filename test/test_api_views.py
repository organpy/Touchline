"""Static checks on the website views in db/migrations/init.sql (the SQL test covers them on a live DB)."""

import re
from pathlib import Path

INIT_SQL = Path(__file__).resolve().parent.parent / "db" / "migrations" / "init.sql"
FORBIDDEN = ("owner_discord_id", "discord_id", "recorded_by", "voided_by", "metadata", "transactions",
             "trade_", "pending_signings", "bot_config", "draft_picks")
EXPECTED = {"divisions", "division_groups", "seasons", "teams", "season_teams", "players", "rosters", "matches",
            "player_stats", "standings", "brackets", "bracket_entries", "series", "season_movements"}


def api_section() -> str:
    sql = INIT_SQL.read_text(encoding="utf-8")
    start = sql.index("CREATE SCHEMA api;")
    return sql[start:sql.index("COMMIT;", start)]


def views() -> dict:
    section = api_section()
    return {name: body for name, body in re.findall(r"CREATE VIEW api\.(\w+) AS(.*?);", section, re.S)}


def test_every_website_view_exists():
    assert set(views()) == EXPECTED


def test_views_never_expose_discord_ids_or_private_tables():
    for name, body in views().items():
        for word in FORBIDDEN:
            assert word not in body, f"api.{name} mentions {word}"


def test_division_view_only_exposes_scoring_rules():
    body = views()["divisions"]
    keys = set(re.findall(r"settings ->> '(\w+)'", body))
    assert keys == {"scoring_mode", "points_per_win"}
    assert "settings," not in body and "settings\n" not in body


def test_voided_games_are_hidden():
    assert "status = 'final'" in views()["matches"]
    assert "status = 'final'" in views()["player_stats"]


def test_views_are_read_only_for_anon():
    section = api_section()
    assert "GRANT SELECT ON ALL TABLES IN SCHEMA api TO anon" in section
    assert not re.search(r"GRANT\s+(INSERT|UPDATE|DELETE|ALL)", section)


def test_reset_drops_the_api_schema():
    reset = (INIT_SQL.parent.parent / "reset_legacy.sql").read_text(encoding="utf-8")
    assert "DROP SCHEMA IF EXISTS api CASCADE;" in reset
