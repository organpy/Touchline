"""Tests for the health check HTTP server lifecycle."""

from unittest.mock import Mock

import pytest

from utils.health_check import HealthCheckServer


@pytest.mark.asyncio
async def test_stop_can_be_called_twice():
    server = HealthCheckServer(Mock(), port=0, host="127.0.0.1")
    await server.start()

    await server.stop()
    await server.stop()

    assert server.site is None
    assert server.runner is None
