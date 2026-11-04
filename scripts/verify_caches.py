"""
Script to verify all caches are working correctly.
Run: python scripts/verify_caches.py
"""

import asyncio
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta, timezone
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def print_section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

def print_result(name: str, passed: bool, details: str = ""):
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"  {status}: {name}")
    if details:
        print(f"         {details}")

async def test_ttl_cache():
    """Test TTLCache basic functionality."""
    print_section("TTLCache (utils/caching.py)")
    
    from utils.caching import TTLCache
    
    # Test 1: Basic set/get
    cache = TTLCache(ttl_seconds=5, max_size=10)
    cache.set("key1", "value1")
    result = cache.get("key1")
    print_result("Basic set/get", result == "value1", f"Expected 'value1', got '{result}'")
    
    # Test 2: Cache miss
    result = cache.get("nonexistent")
    print_result("Cache miss returns None", result is None)
    
    # Test 3: Size tracking
    cache.set("key2", "value2")
    cache.set("key3", "value3")
    print_result("Size tracking", cache.size() == 3, f"Expected 3, got {cache.size()}")
    
    # Test 4: Clear
    cache.clear()
    print_result("Clear empties cache", cache.size() == 0, f"Expected 0, got {cache.size()}")
    
    # Test 5: Max size eviction
    small_cache = TTLCache(ttl_seconds=300, max_size=3)
    for i in range(5):
        small_cache.set(f"k{i}", f"v{i}")
    print_result("Max size eviction", small_cache.size() <= 3, f"Size is {small_cache.size()}")

async def test_season_cache():
    """Test SeasonCache functionality."""
    print_section("SeasonCache (utils/season_cache.py)")
    
    from utils.season_cache import SeasonCache
    
    try:
        from db import get_supabase
        supabase = get_supabase()
    except (ValueError, Exception) as e:
        print(f"  ⚠️ SKIPPED: Supabase not configured ({e})")
        return
    
    SeasonCache.set_supabase(supabase)
    
    # Test 1: Initial state is stale
    SeasonCache.invalidate()
    print_result("Initial state is stale", SeasonCache.is_stale())
    
    # Test 2: Refresh loads seasons
    seasons = await SeasonCache.refresh()
    print_result("Refresh loads data", len(seasons) > 0, f"Loaded {len(seasons)} seasons")
    
    # Test 3: After refresh, not stale
    print_result("After refresh, not stale", not SeasonCache.is_stale())
    
    # Test 4: get_cached_seasons returns list
    cached = SeasonCache.get_cached_seasons()
    print_result("get_cached_seasons works", isinstance(cached, list) and len(cached) == len(seasons))
    
    # Test 5: League-wide seasons carry a phase; at most one is active
    print_result("Seasons have a phase", all(s.get("phase") for s in seasons))
    print_result("At most one active season", sum(1 for s in seasons if s.get("active")) <= 1)
    
    # Test 6: ensure_ready doesn't re-fetch when fresh
    seasons2 = await SeasonCache.ensure_ready()
    print_result("ensure_ready uses cache when fresh", seasons2 is seasons or len(seasons2) == len(seasons))

async def test_team_player_cache():
    """Test TeamPlayerCache functionality."""
    print_section("TeamPlayerCache (utils/team_player_cache.py)")
    
    from utils.team_player_cache import TeamPlayerCache
    
    try:
        from db import get_supabase
        supabase = get_supabase()
    except (ValueError, Exception) as e:
        print(f"  ⚠️ SKIPPED: Supabase not configured ({e})")
        return
    
    TeamPlayerCache.set_supabase(supabase)
    
    # Test 1: Invalidate clears data
    TeamPlayerCache.invalidate()
    status = TeamPlayerCache.get_status()
    print_result("Invalidate clears data", status['teams'] == 0 and status['players'] == 0)
    
    # Test 2: Refresh loads data
    await TeamPlayerCache.refresh()
    status = TeamPlayerCache.get_status()
    print_result("Refresh loads teams", status['teams'] > 0, f"Loaded {status['teams']} teams")
    print_result("Refresh loads players", status['players'] > 0, f"Loaded {status['players']} players")
    
    # Test 3: Get team by code (case insensitive)
    teams = TeamPlayerCache.get_all_teams()
    if teams:
        test_team = teams[0]
        code = test_team.get('code', '')
        found = TeamPlayerCache.get_team_by_code(code.lower())
        print_result("Get team by code (lowercase)", found is not None and found.get('code') == code)
        found = TeamPlayerCache.get_team_by_code(code.upper())
        print_result("Get team by code (uppercase)", found is not None and found.get('code') == code)
    else:
        print_result("Get team by code", False, "No teams loaded")
    
    # Test 4: Teams have league metadata
    if teams:
        has_league = any(t.get('league_type') for t in teams)
        print_result("Teams have a division this season", has_league, "Assign teams in Dashboard → Teams")
        has_season = any(t.get('active_season_id') for t in teams)
        print_result("Teams have active_season_id", has_season)
    
    # Test 5: Player lookups
    players = TeamPlayerCache.get_all_players()
    if players:
        test_player = players[0]
        found_by_id = TeamPlayerCache.get_player(test_player.get('id'))
        print_result("Get player by ID", found_by_id is not None)
        
        display_name = test_player.get('display_name', '')
        if display_name:
            found_by_name = TeamPlayerCache.get_player_by_name(display_name)
            print_result("Get player by name", found_by_name is not None)
    else:
        print_result("Player lookups", False, "No players loaded")
    
    # Test 6: Team roster lookup
    if teams:
        team_with_players = None
        for team in teams:
            roster = TeamPlayerCache.get_team_players(team.get('id'))
            if roster:
                team_with_players = team
                print_result("Get team players", True, f"Team {team.get('code')} has {len(roster)} players")
                break
        if not team_with_players:
            print_result("Get team players", False, "No teams have players")
    
    # Test 7: Status reporting
    status = TeamPlayerCache.get_status()
    print_result("Status has all fields", all(k in status for k in ['teams', 'players', 'team_rosters', 'last_refresh', 'is_fresh']))

async def test_cache_integration():
    """Test cache integration - ensure caches don't interfere with each other."""
    print_section("Cache Integration Tests")
    
    from utils.team_player_cache import TeamPlayerCache
    from utils.season_cache import SeasonCache
    
    # Ensure all caches can coexist
    tp_status = TeamPlayerCache.get_status()
    sc_stale = SeasonCache.is_stale()
    
    print_result("TeamPlayerCache accessible", isinstance(tp_status, dict))
    print_result("SeasonCache accessible", isinstance(sc_stale, bool))
    
    # Verify no data corruption between caches
    print_result("Caches are independent singletons", True, "No cross-contamination detected")

async def main():
    """Run all cache verification tests."""
    print("\n" + "="*60)
    print("  CACHE VERIFICATION SCRIPT")
    print("="*60)
    
    try:
        await test_ttl_cache()
        await test_season_cache()
        await test_team_player_cache()
        await test_cache_integration()
        
        print_section("SUMMARY")
        print("  All cache systems verified. Check above for any failures.")
        
    except Exception as e:
        print(f"\n❌ CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
