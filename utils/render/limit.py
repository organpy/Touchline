"""Run image rendering off the event loop, a few at a time."""

import asyncio
from typing import Any, Callable, TypeVar

T = TypeVar("T")

# Rendering is CPU-bound Pillow work. A few at once is plenty for one server; more only queue up behind each other
# and starve the threads that database calls need.
MAX_CONCURRENT_RENDERS = 2

_slots = asyncio.Semaphore(MAX_CONCURRENT_RENDERS)


async def render_in_thread(func: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """``asyncio.to_thread`` that waits for a free render slot first."""
    async with _slots:
        return await asyncio.to_thread(func, *args, **kwargs)
