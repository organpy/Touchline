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


class TestQueryMetrics:
    """Every database call is counted so changes to query volume can be measured."""

    def setup_method(self):
        from utils.metrics import MetricsCollector
        MetricsCollector.reset()

    @staticmethod
    def _query(method="GET", path="https://x.supabase.co/rest/v1/matches", result=None, error=None):
        from unittest.mock import Mock
        query = Mock()
        query.request = Mock(http_method=method, path=path)
        if error:
            query.execute.side_effect = error
        else:
            query.execute.return_value = result
        return query

    @pytest.mark.asyncio
    async def test_queries_are_counted_by_method_and_table(self):
        from db.db_utils import execute_async
        from utils.metrics import MetricsCollector

        await execute_async(self._query(result="a"))
        await execute_async(self._query(result="b"))
        await execute_async(self._query("POST", "https://x.supabase.co/rest/v1/rpc/record_match", result="c"))

        summary = MetricsCollector.get_summary()
        assert summary["total_queries"] == 3
        assert summary["query_stats"]["GET matches"]["count"] == 2
        assert summary["query_stats"]["POST rpc/record_match"]["count"] == 1

    @pytest.mark.asyncio
    async def test_failed_queries_are_counted_and_still_raise(self):
        from db.db_utils import execute_async
        from utils.metrics import MetricsCollector

        with pytest.raises(ConnectionError):
            await execute_async(self._query(error=ConnectionError("down")))

        stats = MetricsCollector.get_summary()["query_stats"]["GET matches"]
        assert stats["count"] == 1 and stats["errors"] == 1

    @pytest.mark.asyncio
    async def test_the_count_keeps_growing_past_the_timing_window(self):
        from db.db_utils import execute_async
        from utils.metrics import MetricsCollector

        for _ in range(1005):
            await execute_async(self._query(result="x"))

        assert MetricsCollector.get_summary()["query_stats"]["GET matches"]["count"] == 1005

    @pytest.mark.asyncio
    async def test_unnameable_queries_are_still_counted(self):
        from unittest.mock import Mock
        from db.db_utils import execute_async
        from utils.metrics import MetricsCollector

        query = Mock(spec=["execute"])
        query.execute.return_value = "x"
        await execute_async(query)

        assert MetricsCollector.get_summary()["query_stats"]["unknown"]["count"] == 1
