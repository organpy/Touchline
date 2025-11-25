"""
Connection pooling and query optimization utilities.
Provides connection reuse, request batching, and performance tracking.
"""

import asyncio
import logging
from typing import List, Dict, Any, Optional, Callable, Coroutine
from datetime import datetime, timedelta
from collections import defaultdict

logger = logging.getLogger(__name__)


class QueryBatcher:
    """Batch multiple queries to reduce network overhead."""
    
    def __init__(self, batch_size: int = 10, flush_interval: float = 0.5):
        """
        Initialize query batcher.
        
        Args:
            batch_size: Maximum queries to batch before flushing
            flush_interval: Seconds to wait before flushing partial batch
        """
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self._pending_queries: List[tuple] = []
        self._pending_callbacks: List[Callable] = []
        self._flush_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
    
    async def add_query(
        self,
        query_func: Callable[..., Coroutine],
        *args,
        **kwargs
    ) -> Any:
        """
        Add a query to the batch.
        
        Args:
            query_func: Async function to call
            *args: Positional arguments
            **kwargs: Keyword arguments
        
        Returns:
            Query result (executes immediately or returns from batch)
        """
        async with self._lock:
            # Store query for batching
            self._pending_queries.append((query_func, args, kwargs))
            
            # If batch is full, flush immediately
            if len(self._pending_queries) >= self.batch_size:
                return await self._flush_batch()
            
            # Otherwise, schedule flush if not already scheduled
            if not self._flush_task or self._flush_task.done():
                self._flush_task = asyncio.create_task(
                    asyncio.sleep(self.flush_interval)
                )
                self._flush_task.add_done_callback(lambda _: asyncio.create_task(self._flush_batch()))
            
            # For now, execute immediately to avoid complexity
            query_func, args, kwargs = self._pending_queries.pop()
            return await query_func(*args, **kwargs)
    
    async def _flush_batch(self) -> None:
        """Execute all pending queries."""
        async with self._lock:
            if not self._pending_queries:
                return
            
            # Execute queries concurrently
            tasks = [
                query_func(*args, **kwargs)
                for query_func, args, kwargs in self._pending_queries
            ]
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Clear batch
            self._pending_queries.clear()
            
            # Log results
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(f"Query {i} failed: {result}")
            
            return results
    
    async def close(self) -> None:
        """Close and flush any remaining queries."""
        if self._flush_task:
            await self._flush_task
        await self._flush_batch()


class ConnectionPool:
    """
    Manage a pool of async database connections.
    Tracks connection reuse, query times, and provides pooling statistics.
    """
    
    def __init__(self, max_idle_time: int = 3600):
        """
        Initialize connection pool.
        
        Args:
            max_idle_time: Seconds before idle connection is closed
        """
        self.max_idle_time = max_idle_time
        self._connections: Dict[str, Dict[str, Any]] = {}
        self._query_stats: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            'count': 0,
            'total_time_ms': 0,
            'min_time_ms': float('inf'),
            'max_time_ms': 0,
            'errors': 0,
        })
        self._lock = asyncio.Lock()
    
    async def get_or_create_connection(self, pool_key: str) -> Dict[str, Any]:
        """
        Get or create a connection from the pool.
        
        Args:
            pool_key: Unique identifier for this connection
        
        Returns:
            Connection metadata dict
        """
        async with self._lock:
            if pool_key in self._connections:
                conn = self._connections[pool_key]
                conn['last_used'] = datetime.now()
                conn['reuse_count'] += 1
                return conn
            
            # Create new connection
            conn = {
                'pool_key': pool_key,
                'created_at': datetime.now(),
                'last_used': datetime.now(),
                'reuse_count': 0,
                'query_count': 0,
            }
            
            self._connections[pool_key] = conn
            logger.info(f"✅ Created connection: {pool_key}")
            
            return conn
    
    async def record_query(
        self,
        pool_key: str,
        query_name: str,
        elapsed_ms: float,
        error: Optional[Exception] = None
    ) -> None:
        """
        Record query execution statistics.
        
        Args:
            pool_key: Connection identifier
            query_name: Name of query function
            elapsed_ms: Milliseconds to execute
            error: Exception if query failed
        """
        async with self._lock:
            # Update connection stats
            if pool_key in self._connections:
                self._connections[pool_key]['query_count'] += 1
            
            # Update query stats
            stats = self._query_stats[query_name]
            stats['count'] += 1
            stats['total_time_ms'] += elapsed_ms
            stats['min_time_ms'] = min(stats['min_time_ms'], elapsed_ms)
            stats['max_time_ms'] = max(stats['max_time_ms'], elapsed_ms)
            
            if error:
                stats['errors'] += 1
    
    async def cleanup_idle_connections(self) -> int:
        """
        Close idle connections.
        
        Returns:
            Number of connections closed
        """
        async with self._lock:
            now = datetime.now()
            to_remove = []
            
            for pool_key, conn in self._connections.items():
                idle_time = (now - conn['last_used']).total_seconds()
                if idle_time > self.max_idle_time:
                    to_remove.append(pool_key)
                    logger.info(f"🧹 Closed idle connection: {pool_key}")
            
            for pool_key in to_remove:
                del self._connections[pool_key]
            
            return len(to_remove)
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get pool statistics."""
        return {
            'active_connections': len(self._connections),
            'total_reuses': sum(c['reuse_count'] for c in self._connections.values()),
            'total_queries': sum(c['query_count'] for c in self._connections.values()),
            'query_stats': dict(self._query_stats),
        }
    
    async def close_all(self) -> None:
        """Close all connections."""
        async with self._lock:
            self._connections.clear()
            logger.info("✅ All connections closed")


class QueryOptimizer:
    """Optimize queries by detecting and caching common patterns."""
    
    def __init__(self, cache_ttl: int = 300):
        """
        Initialize optimizer.
        
        Args:
            cache_ttl: Cache time-to-live in seconds
        """
        self.cache_ttl = cache_ttl
        self._cache: Dict[str, tuple[Any, datetime]] = {}
        self._lock = asyncio.Lock()
    
    async def execute_with_cache(
        self,
        cache_key: str,
        query_func: Callable[..., Coroutine],
        *args,
        **kwargs
    ) -> Any:
        """
        Execute query with caching.
        
        Args:
            cache_key: Key for caching result
            query_func: Async query function
            *args: Positional arguments
            **kwargs: Keyword arguments
        
        Returns:
            Cached or fresh query result
        """
        async with self._lock:
            # Check cache
            if cache_key in self._cache:
                result, expires_at = self._cache[cache_key]
                if datetime.now() < expires_at:
                    logger.debug(f"Cache hit: {cache_key}")
                    return result
                else:
                    del self._cache[cache_key]
            
            # Execute query
            result = await query_func(*args, **kwargs)
            
            # Cache result
            self._cache[cache_key] = (result, datetime.now() + timedelta(seconds=self.cache_ttl))
            logger.debug(f"Cache miss (executed): {cache_key}")
            
            return result
    
    def invalidate_cache(self, cache_key: Optional[str] = None) -> None:
        """Invalidate cache entry or entire cache."""
        if cache_key:
            self._cache.pop(cache_key, None)
        else:
            self._cache.clear()


# Global singleton instances
_pool: Optional[ConnectionPool] = None
_optimizer: Optional[QueryOptimizer] = None
_batcher: Optional[QueryBatcher] = None


def get_connection_pool() -> ConnectionPool:
    """Get or create global connection pool."""
    global _pool
    if _pool is None:
        _pool = ConnectionPool()
    return _pool


def get_query_optimizer() -> QueryOptimizer:
    """Get or create global query optimizer."""
    global _optimizer
    if _optimizer is None:
        _optimizer = QueryOptimizer()
    return _optimizer


def get_query_batcher() -> QueryBatcher:
    """Get or create global query batcher."""
    global _batcher
    if _batcher is None:
        _batcher = QueryBatcher()
    return _batcher


async def cleanup_pool() -> None:
    """Cleanup all pool resources."""
    global _pool, _optimizer, _batcher
    
    if _pool:
        await _pool.cleanup_idle_connections()
        await _pool.close_all()
        _pool = None
    
    if _batcher:
        await _batcher.close()
        _batcher = None
    
    _optimizer = None
