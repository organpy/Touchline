"""
Performance metrics collection for caching, queries, and stats calculations.
Tracks cache hit/miss rates, query latencies, stat calculation times.
"""

import asyncio
import inspect
import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class MetricsCollector:
    """
    Singleton metrics collector for tracking performance across the bot.
    Provides real-time and historical metrics for optimization.
    """

    _instance: Optional["MetricsCollector"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        
        self._initialized = True
        
        # Cache metrics
        self.cache_hits: Dict[str, int] = defaultdict(int)  # {cache_name: count}
        self.cache_misses: Dict[str, int] = defaultdict(int)  # {cache_name: count}
        self.cache_hit_times: Dict[str, List[float]] = defaultdict(list)  # {cache_name: [times_ms]}
        
        # Query metrics
        self.query_times: Dict[str, List[float]] = defaultdict(list)  # {query_name: [times_ms]}
        self.query_errors: Dict[str, int] = defaultdict(int)  # {query_name: count}
        self.query_counts: Dict[str, int] = defaultdict(int)  # {query_name: total ever run}
        
        # Stat calculation metrics
        self.stat_calc_times: Dict[str, List[float]] = defaultdict(list)  # {stat_name: [times_ms]}
        
        # TTL and eviction tracking
        self.ttl_evictions: Dict[str, int] = defaultdict(int)  # {cache_name: count}
        self.capacity_evictions: Dict[str, int] = defaultdict(int)  # {cache_name: count}
        
        # Startup timestamp for uptime calculation
        self.started_at = datetime.now(timezone.utc)

    @classmethod
    def record_cache_hit(cls, cache_name: str, latency_ms: float) -> None:
        """
        Record a cache hit.
        
        Args:
            cache_name: Cache identifier (e.g., 'seasons', 'teams')
            latency_ms: Time taken to retrieve from cache in milliseconds
        """
        metrics = cls()
        metrics.cache_hits[cache_name] += 1
        metrics.cache_hit_times[cache_name].append(latency_ms)
        
        # Keep only last 1000 times per cache to avoid unbounded memory
        if len(metrics.cache_hit_times[cache_name]) > 1000:
            metrics.cache_hit_times[cache_name] = metrics.cache_hit_times[cache_name][-1000:]

    @classmethod
    def record_cache_miss(cls, cache_name: str) -> None:
        """
        Record a cache miss.
        
        Args:
            cache_name: Cache identifier
        """
        metrics = cls()
        metrics.cache_misses[cache_name] += 1

    @classmethod
    def record_query(cls, query_name: str, latency_ms: float, error: bool = False) -> None:
        """
        Record a query execution.
        
        Args:
            query_name: Query identifier (e.g., 'get_leaderboard', 'get_player_averages')
            latency_ms: Time taken in milliseconds
            error: Whether the query failed
        """
        metrics = cls()
        metrics.query_counts[query_name] += 1
        metrics.query_times[query_name].append(latency_ms)
        
        if error:
            metrics.query_errors[query_name] += 1
        
        # Keep only last 1000 times per query
        if len(metrics.query_times[query_name]) > 1000:
            metrics.query_times[query_name] = metrics.query_times[query_name][-1000:]

    @classmethod
    def record_stat_calculation(cls, stat_name: str, latency_ms: float) -> None:
        """
        Record a stat calculation.
        
        Args:
            stat_name: Stat identifier (e.g., 'average_stats', 'total_stats')
            latency_ms: Time taken in milliseconds
        """
        metrics = cls()
        metrics.stat_calc_times[stat_name].append(latency_ms)
        
        # Keep only last 500 times per stat
        if len(metrics.stat_calc_times[stat_name]) > 500:
            metrics.stat_calc_times[stat_name] = metrics.stat_calc_times[stat_name][-500:]

    @classmethod
    def record_ttl_eviction(cls, cache_name: str) -> None:
        """Record a TTL-based cache eviction."""
        metrics = cls()
        metrics.ttl_evictions[cache_name] += 1

    @classmethod
    def record_capacity_eviction(cls, cache_name: str) -> None:
        """Record a capacity-based cache eviction."""
        metrics = cls()
        metrics.capacity_evictions[cache_name] += 1

    @classmethod
    def get_cache_hit_rate(cls, cache_name: str) -> float:
        """
        Get cache hit rate for a cache.
        
        Returns:
            Hit rate as percentage (0-100), or 0 if no accesses recorded
        """
        metrics = cls()
        hits = metrics.cache_hits.get(cache_name, 0)
        misses = metrics.cache_misses.get(cache_name, 0)
        total = hits + misses
        
        if total == 0:
            return 0.0
        
        return (hits / total) * 100.0

    @classmethod
    def get_avg_cache_latency(cls, cache_name: str) -> float:
        """
        Get average cache access latency in milliseconds.
        
        Returns:
            Average latency in ms, or 0 if no accesses recorded
        """
        metrics = cls()
        times = metrics.cache_hit_times.get(cache_name, [])
        
        if not times:
            return 0.0
        
        return sum(times) / len(times)

    @classmethod
    def get_avg_query_latency(cls, query_name: str) -> float:
        """
        Get average query latency in milliseconds.
        
        Returns:
            Average latency in ms, or 0 if no queries recorded
        """
        metrics = cls()
        times = metrics.query_times.get(query_name, [])
        
        if not times:
            return 0.0
        
        return sum(times) / len(times)

    @classmethod
    def get_uptime_seconds(cls) -> float:
        """Get bot uptime in seconds since metrics were initialized."""
        metrics = cls()
        delta = datetime.now(timezone.utc) - metrics.started_at
        return delta.total_seconds()

    @classmethod
    def get_summary(cls) -> Dict[str, Any]:
        """
        Get comprehensive metrics summary.
        
        Returns:
            Dictionary with all metrics, averages, and rates
        """
        metrics = cls()
        
        summary = {
            "uptime_seconds": metrics.get_uptime_seconds(),
            "cache_stats": {},
            "query_stats": {},
            "total_queries": sum(metrics.query_counts.values()),
            "stat_calc_stats": {},
            "eviction_stats": {
                "ttl_evictions": dict(metrics.ttl_evictions),
                "capacity_evictions": dict(metrics.capacity_evictions)
            }
        }
        
        # Cache stats
        for cache_name in set(list(metrics.cache_hits.keys()) + list(metrics.cache_misses.keys())):
            summary["cache_stats"][cache_name] = {
                "hits": metrics.cache_hits.get(cache_name, 0),
                "misses": metrics.cache_misses.get(cache_name, 0),
                "hit_rate_percent": metrics.get_cache_hit_rate(cache_name),
                "avg_latency_ms": metrics.get_avg_cache_latency(cache_name)
            }
        
        # Query stats
        for query_name in metrics.query_times.keys():
            times = metrics.query_times[query_name]
            summary["query_stats"][query_name] = {
                "count": metrics.query_counts[query_name],
                "avg_latency_ms": metrics.get_avg_query_latency(query_name),
                "min_latency_ms": min(times) if times else 0,
                "max_latency_ms": max(times) if times else 0,
                "errors": metrics.query_errors.get(query_name, 0)
            }
        
        # Stat calc stats
        for stat_name in metrics.stat_calc_times.keys():
            times = metrics.stat_calc_times[stat_name]
            summary["stat_calc_stats"][stat_name] = {
                "count": len(times),
                "avg_latency_ms": sum(times) / len(times) if times else 0,
                "min_latency_ms": min(times) if times else 0,
                "max_latency_ms": max(times) if times else 0
            }
        
        return summary

    @classmethod
    def reset(cls) -> None:
        """Reset all metrics to zero."""
        metrics = cls()
        metrics.cache_hits.clear()
        metrics.cache_misses.clear()
        metrics.cache_hit_times.clear()
        metrics.query_times.clear()
        metrics.query_errors.clear()
        metrics.query_counts.clear()
        metrics.stat_calc_times.clear()
        metrics.ttl_evictions.clear()
        metrics.capacity_evictions.clear()
        metrics.started_at = datetime.now(timezone.utc)
        logger.info("✅ Metrics reset")


def track_query_time(query_name: str) -> Callable:
    """
    Decorator to track async query execution time.
    
    Usage:
        @track_query_time("get_leaderboard")
        async def my_query_function(supabase, season_id):
            ...
    
    Args:
        query_name: Identifier for this query
    
    Returns:
        Decorated async function
    """
    def decorator(func: Callable) -> Callable:
        if inspect.iscoroutinefunction(func):
            @wraps(func)
            async def async_wrapper(*args, **kwargs) -> Any:
                start = time.time()
                try:
                    result = await func(*args, **kwargs)
                    latency_ms = (time.time() - start) * 1000
                    MetricsCollector.record_query(query_name, latency_ms, error=False)
                    return result
                except Exception as e:
                    latency_ms = (time.time() - start) * 1000
                    MetricsCollector.record_query(query_name, latency_ms, error=True)
                    logger.error(f"Query {query_name} failed after {latency_ms:.2f}ms: {e}", exc_info=True)
                    raise
            return async_wrapper
        else:
            @wraps(func)
            def sync_wrapper(*args, **kwargs) -> Any:
                start = time.time()
                try:
                    result = func(*args, **kwargs)
                    latency_ms = (time.time() - start) * 1000
                    MetricsCollector.record_query(query_name, latency_ms, error=False)
                    return result
                except Exception as e:
                    latency_ms = (time.time() - start) * 1000
                    MetricsCollector.record_query(query_name, latency_ms, error=True)
                    logger.error(f"Query {query_name} failed after {latency_ms:.2f}ms: {e}", exc_info=True)
                    raise
            return sync_wrapper
    
    return decorator


def track_stat_calculation(stat_name: str) -> Callable:
    """
    Decorator to track stat calculation time.
    
    Usage:
        @track_stat_calculation("average_stats")
        async def calc_averages(players):
            ...
    
    Args:
        stat_name: Identifier for this calculation
    
    Returns:
        Decorated async function
    """
    def decorator(func: Callable) -> Callable:
        if inspect.iscoroutinefunction(func):
            @wraps(func)
            async def async_wrapper(*args, **kwargs) -> Any:
                start = time.time()
                result = await func(*args, **kwargs)
                latency_ms = (time.time() - start) * 1000
                MetricsCollector.record_stat_calculation(stat_name, latency_ms)
                logger.debug(f"📊 Stat calc '{stat_name}' took {latency_ms:.2f}ms")
                return result
            return async_wrapper
        else:
            @wraps(func)
            def sync_wrapper(*args, **kwargs) -> Any:
                start = time.time()
                result = func(*args, **kwargs)
                latency_ms = (time.time() - start) * 1000
                MetricsCollector.record_stat_calculation(stat_name, latency_ms)
                logger.debug(f"📊 Stat calc '{stat_name}' took {latency_ms:.2f}ms")
                return result
            return sync_wrapper
    
    return decorator
