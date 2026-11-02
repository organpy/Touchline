"""
Database utility functions to prevent blocking the event loop.
Wraps slow Supabase queries in asyncio.to_thread() when appropriate.
"""

import asyncio
import logging
import time
from typing import Any, Callable, Optional, TypeVar

from utils.metrics import MetricsCollector

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Threshold in milliseconds above which we consider a query "slow"
SLOW_QUERY_THRESHOLD_MS = 100


async def execute_async(query: Any) -> Any:
    """
    Run a Supabase query builder's blocking .execute() in a worker thread.

    The Supabase client is synchronous; calling .execute() directly inside a coroutine
    freezes the whole bot (gateway heartbeats, every other command) until the HTTP
    request returns. Exceptions propagate unchanged so callers keep their error handling.

    Example:
        response = await execute_async(
            supabase
            .table('players')
            .select('*')
        )
    """
    name = _query_name(query)
    started = time.perf_counter()
    try:
        response = await asyncio.to_thread(query.execute)
    except Exception:
        MetricsCollector.record_query(name, (time.perf_counter() - started) * 1000, error=True)
        raise
    MetricsCollector.record_query(name, (time.perf_counter() - started) * 1000)
    return response


# Supabase returns at most this many rows per request, silently dropping the rest.
PAGE_SIZE = 1000


async def fetch_all(build_query: Callable[[], Any], page_size: int = PAGE_SIZE) -> list:
    """Every row of a query, fetched page by page.

    ``build_query`` returns a fresh query each time (a query can only be used once) and should order by a unique
    column such as ``id`` so pages never overlap or skip rows.
    """
    rows: list = []
    start = 0
    while True:
        response = await execute_async(build_query().range(start, start + page_size - 1))
        batch = response.data or []
        rows.extend(batch)
        if len(batch) < page_size:
            return rows
        start += page_size


def _query_name(query: Any) -> str:
    """Metric key for a Supabase query, such as ``GET matches`` or ``POST rpc/record_match``."""
    try:
        request = query.request
        return f"{request.http_method} {str(request.path).split('/rest/v1/', 1)[-1]}"
    except Exception:
        return "unknown"


async def run_query_in_thread(
    func: Callable[..., T], *args, **kwargs
) -> Optional[T]:
    """
    Execute a potentially slow synchronous function in a thread to avoid blocking
    the Discord event loop.

    This is useful for Supabase queries that might take >100ms.

    Args:
        func: Synchronous callable (e.g., supabase.table().select().execute())
        *args: Positional arguments for func
        **kwargs: Keyword arguments for func

    Returns:
        Result of func(*args, **kwargs), or None if exception occurs

    Example:
        # Instead of:
        result = supabase.table('players').select('*').execute()

        # Use:
        result = await run_query_in_thread(
            lambda: supabase.table('players').select('*').execute()
        )
    """
    try:
        # Run the function in the default executor (ThreadPoolExecutor)
        return await asyncio.to_thread(func, *args, **kwargs)
    except Exception as e:
        logger.error(f"Query failed in thread: {e}", exc_info=True)
        return None
