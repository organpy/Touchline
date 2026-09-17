"""
Database utility functions to prevent blocking the event loop.
Wraps slow Supabase queries in asyncio.to_thread() when appropriate.
"""

import asyncio
import logging
from typing import Any, Callable, Optional, TypeVar

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
    return await asyncio.to_thread(query.execute)


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
