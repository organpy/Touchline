"""Unit tests for MetricsCollector."""

import pytest
import time
from utils.metrics import MetricsCollector, track_query_time, track_stat_calculation


@pytest.fixture(autouse=True)
def reset_metrics():
    """Reset metrics before each test."""
    MetricsCollector.reset()
    yield
    MetricsCollector.reset()


class TestMetricsCollectorSingleton:
    """Test MetricsCollector singleton behavior."""

    def test_singleton_instance(self):
        """Verify MetricsCollector is a singleton."""
        m1 = MetricsCollector()
        m2 = MetricsCollector()
        assert m1 is m2


class TestCacheMetrics:
    """Test cache hit/miss tracking."""

    def test_record_cache_hit(self):
        """Test recording cache hits."""
        MetricsCollector.record_cache_hit("seasons", 1.5)
        MetricsCollector.record_cache_hit("seasons", 2.0)
        
        assert MetricsCollector().cache_hits["seasons"] == 2

    def test_record_cache_miss(self):
        """Test recording cache misses."""
        MetricsCollector.record_cache_miss("seasons")
        MetricsCollector.record_cache_miss("seasons")
        MetricsCollector.record_cache_miss("teams")
        
        assert MetricsCollector().cache_misses["seasons"] == 2
        assert MetricsCollector().cache_misses["teams"] == 1

    def test_cache_hit_rate_all_hits(self):
        """Test cache hit rate when all accesses are hits."""
        MetricsCollector.record_cache_hit("seasons", 1.0)
        MetricsCollector.record_cache_hit("seasons", 1.5)
        MetricsCollector.record_cache_hit("seasons", 2.0)
        
        rate = MetricsCollector.get_cache_hit_rate("seasons")
        assert rate == 100.0

    def test_cache_hit_rate_all_misses(self):
        """Test cache hit rate when all accesses are misses."""
        MetricsCollector.record_cache_miss("seasons")
        MetricsCollector.record_cache_miss("seasons")
        
        rate = MetricsCollector.get_cache_hit_rate("seasons")
        assert rate == 0.0

    def test_cache_hit_rate_mixed(self):
        """Test cache hit rate with mixed hits and misses."""
        MetricsCollector.record_cache_hit("seasons", 1.0)
        MetricsCollector.record_cache_hit("seasons", 1.0)
        MetricsCollector.record_cache_miss("seasons")
        MetricsCollector.record_cache_miss("seasons")
        
        rate = MetricsCollector.get_cache_hit_rate("seasons")
        assert rate == 50.0

    def test_cache_hit_rate_no_accesses(self):
        """Test cache hit rate returns 0 when no accesses recorded."""
        rate = MetricsCollector.get_cache_hit_rate("nonexistent")
        assert rate == 0.0

    def test_avg_cache_latency(self):
        """Test average cache latency calculation."""
        MetricsCollector.record_cache_hit("seasons", 1.0)
        MetricsCollector.record_cache_hit("seasons", 2.0)
        MetricsCollector.record_cache_hit("seasons", 3.0)
        
        avg = MetricsCollector.get_avg_cache_latency("seasons")
        assert avg == 2.0

    def test_avg_cache_latency_no_accesses(self):
        """Test average cache latency returns 0 when no accesses."""
        avg = MetricsCollector.get_avg_cache_latency("nonexistent")
        assert avg == 0.0


class TestQueryMetrics:
    """Test query execution tracking."""

    def test_record_query_success(self):
        """Test recording successful query."""
        MetricsCollector.record_query("get_leaderboard", 150.5, error=False)
        
        times = MetricsCollector().query_times["get_leaderboard"]
        assert len(times) == 1
        assert times[0] == 150.5

    def test_record_query_error(self):
        """Test recording query error."""
        MetricsCollector.record_query("get_leaderboard", 50.0, error=True)
        
        assert MetricsCollector().query_errors["get_leaderboard"] == 1

    def test_avg_query_latency(self):
        """Test average query latency."""
        MetricsCollector.record_query("get_leaderboard", 100.0)
        MetricsCollector.record_query("get_leaderboard", 200.0)
        MetricsCollector.record_query("get_leaderboard", 300.0)
        
        avg = MetricsCollector.get_avg_query_latency("get_leaderboard")
        assert avg == 200.0

    def test_avg_query_latency_no_queries(self):
        """Test average query latency returns 0 when no queries."""
        avg = MetricsCollector.get_avg_query_latency("nonexistent")
        assert avg == 0.0


class TestStatCalculationMetrics:
    """Test stat calculation tracking."""

    def test_record_stat_calculation(self):
        """Test recording stat calculation."""
        MetricsCollector.record_stat_calculation("average_stats", 250.0)
        
        times = MetricsCollector().stat_calc_times["average_stats"]
        assert len(times) == 1
        assert times[0] == 250.0

    def test_multiple_stat_calculations(self):
        """Test tracking multiple different stats."""
        MetricsCollector.record_stat_calculation("average_stats", 100.0)
        MetricsCollector.record_stat_calculation("total_stats", 200.0)
        
        assert len(MetricsCollector().stat_calc_times["average_stats"]) == 1
        assert len(MetricsCollector().stat_calc_times["total_stats"]) == 1


class TestEvictionTracking:
    """Test eviction tracking."""

    def test_record_ttl_eviction(self):
        """Test recording TTL-based evictions."""
        MetricsCollector.record_ttl_eviction("seasons")
        MetricsCollector.record_ttl_eviction("seasons")
        MetricsCollector.record_ttl_eviction("teams")
        
        assert MetricsCollector().ttl_evictions["seasons"] == 2
        assert MetricsCollector().ttl_evictions["teams"] == 1

    def test_record_capacity_eviction(self):
        """Test recording capacity-based evictions."""
        MetricsCollector.record_capacity_eviction("standings")
        
        assert MetricsCollector().capacity_evictions["standings"] == 1


class TestMetricsGetSummary:
    """Test comprehensive metrics summary."""

    def test_summary_includes_all_sections(self):
        """Test that summary includes all metric sections."""
        MetricsCollector.record_cache_hit("seasons", 1.0)
        MetricsCollector.record_query("get_leaderboard", 100.0)
        MetricsCollector.record_stat_calculation("average_stats", 250.0)
        
        summary = MetricsCollector.get_summary()
        
        assert "uptime_seconds" in summary
        assert "cache_stats" in summary
        assert "query_stats" in summary
        assert "stat_calc_stats" in summary
        assert "eviction_stats" in summary

    def test_summary_cache_stats_detail(self):
        """Test cache stats in summary are detailed."""
        MetricsCollector.record_cache_hit("seasons", 1.0)
        MetricsCollector.record_cache_hit("seasons", 3.0)
        MetricsCollector.record_cache_miss("seasons")
        
        summary = MetricsCollector.get_summary()
        cache_stats = summary["cache_stats"]["seasons"]
        
        assert cache_stats["hits"] == 2
        assert cache_stats["misses"] == 1
        assert cache_stats["hit_rate_percent"] == pytest.approx(66.67, abs=0.1)
        assert cache_stats["avg_latency_ms"] == 2.0

    def test_summary_query_stats_detail(self):
        """Test query stats in summary include min/max."""
        MetricsCollector.record_query("get_leaderboard", 50.0)
        MetricsCollector.record_query("get_leaderboard", 150.0)
        MetricsCollector.record_query("get_leaderboard", 100.0, error=True)
        
        summary = MetricsCollector.get_summary()
        query_stats = summary["query_stats"]["get_leaderboard"]
        
        assert query_stats["count"] == 3
        assert query_stats["avg_latency_ms"] == 100.0
        assert query_stats["min_latency_ms"] == 50.0
        assert query_stats["max_latency_ms"] == 150.0
        assert query_stats["errors"] == 1


class TestMetricsReset:
    """Test metrics reset."""

    def test_reset_clears_all_metrics(self):
        """Test that reset clears all tracked metrics."""
        MetricsCollector.record_cache_hit("seasons", 1.0)
        MetricsCollector.record_query("get_leaderboard", 100.0)
        
        MetricsCollector.reset()
        
        assert len(MetricsCollector().cache_hits) == 0
        assert len(MetricsCollector().cache_misses) == 0
        assert len(MetricsCollector().query_times) == 0


class TestTrackQueryTimeDecorator:
    """Test track_query_time decorator."""

    @pytest.mark.asyncio
    async def test_track_query_time_async_success(self):
        """Test decorator on successful async query."""
        @track_query_time("test_query")
        async def test_query():
            await asyncio.sleep(0.001)  # 1ms
            return {"result": "success"}
        
        result = await test_query()
        assert result["result"] == "success"
        assert MetricsCollector().query_times["test_query"]
        assert MetricsCollector().query_errors["test_query"] == 0

    @pytest.mark.asyncio
    async def test_track_query_time_async_error(self):
        """Test decorator on failing async query."""
        @track_query_time("test_query_fail")
        async def test_query_fail():
            raise ValueError("Test error")

        with pytest.raises(ValueError):
            await test_query_fail()

        # Check that error was recorded even though function failed
        assert MetricsCollector().query_errors["test_query_fail"] == 1
        assert len(MetricsCollector().query_times["test_query_fail"]) == 1
class TestTrackStatCalculationDecorator:
    """Test track_stat_calculation decorator."""

    @pytest.mark.asyncio
    async def test_track_stat_calculation_async(self):
        """Test decorator on async stat calculation."""
        @track_stat_calculation("test_stat_calc")
        async def test_calc():
            await asyncio.sleep(0.001)  # 1ms
            return {"result": 42}
        
        result = await test_calc()
        assert result["result"] == 42
        assert len(MetricsCollector().stat_calc_times["test_stat_calc"]) == 1


class TestUptimeTracking:
    """Test uptime calculation."""

    def test_uptime_seconds_increases(self):
        """Test that uptime increases over time."""
        uptime1 = MetricsCollector.get_uptime_seconds()
        time.sleep(0.01)  # 10ms
        uptime2 = MetricsCollector.get_uptime_seconds()
        
        assert uptime2 > uptime1


class TestMemoryManagement:
    """Test that memory usage doesn't grow unbounded."""

    def test_cache_hit_times_truncated(self):
        """Test that cache hit times are truncated to last 1000."""
        for i in range(1100):
            MetricsCollector.record_cache_hit("seasons", float(i))
        
        times = MetricsCollector().cache_hit_times["seasons"]
        assert len(times) <= 1000

    def test_query_times_truncated(self):
        """Test that query times are truncated to last 1000."""
        for i in range(1100):
            MetricsCollector.record_query("test_query", float(i))
        
        times = MetricsCollector().query_times["test_query"]
        assert len(times) <= 1000

    def test_stat_calc_times_truncated(self):
        """Test that stat calc times are truncated to last 500."""
        for i in range(600):
            MetricsCollector.record_stat_calculation("test_stat", float(i))
        
        times = MetricsCollector().stat_calc_times["test_stat"]
        assert len(times) <= 500


# Import asyncio for async tests
import asyncio
