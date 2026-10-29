"""Rendering runs off the event loop and never more than a few at a time."""

import asyncio
import threading
import time

import pytest

from utils.render.limit import MAX_CONCURRENT_RENDERS, render_in_thread


@pytest.mark.asyncio
async def test_renders_run_a_few_at_a_time():
    running = 0
    peak = 0
    lock = threading.Lock()

    def slow_render(n):
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.05)
        with lock:
            running -= 1
        return n

    results = await asyncio.gather(*(render_in_thread(slow_render, n) for n in range(6)))

    assert results == list(range(6))
    assert peak == MAX_CONCURRENT_RENDERS


@pytest.mark.asyncio
async def test_arguments_and_keywords_reach_the_renderer():
    assert await render_in_thread(lambda a, b=0: a + b, 1, b=2) == 3


@pytest.mark.asyncio
async def test_a_failed_render_frees_its_slot():
    def boom():
        raise RuntimeError("bad image")

    for _ in range(MAX_CONCURRENT_RENDERS + 1):
        with pytest.raises(RuntimeError):
            await render_in_thread(boom)

    assert await render_in_thread(lambda: "still works") == "still works"
