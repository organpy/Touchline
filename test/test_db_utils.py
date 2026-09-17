"""Tests for non-blocking Supabase query helpers."""

import asyncio
import threading
from unittest.mock import Mock

import pytest

from db.db_utils import execute_async


@pytest.mark.asyncio
async def test_execute_async_runs_off_the_event_loop_thread():
    loop_thread = threading.get_ident()
    seen = {}

    def execute():
        seen['thread'] = threading.get_ident()
        return Mock(data=[{'id': 1}])

    query = Mock(execute=execute)

    response = await execute_async(query)

    assert response.data == [{'id': 1}]
    assert seen['thread'] != loop_thread


@pytest.mark.asyncio
async def test_execute_async_propagates_errors():
    query = Mock()
    query.execute.side_effect = RuntimeError("db down")

    with pytest.raises(RuntimeError, match="db down"):
        await execute_async(query)


@pytest.mark.asyncio
async def test_event_loop_keeps_running_during_slow_query():
    release = threading.Event()
    query = Mock()
    query.execute.side_effect = lambda: release.wait(2) or Mock(data=[])

    task = asyncio.ensure_future(execute_async(query))
    await asyncio.sleep(0.05)
    assert not task.done()  # the loop got control back while the query is in flight
    release.set()
    await task
