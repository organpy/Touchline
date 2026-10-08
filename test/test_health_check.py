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


def _connected_bot():
    bot = Mock()
    bot.is_ready.return_value = True
    bot.ws.open = True
    bot.guilds = [Mock()]
    bot.latency = 0.05
    return bot


@pytest.mark.asyncio
async def test_ready_when_gateway_is_connected():
    response = await HealthCheckServer(_connected_bot()).readiness_check(None)

    assert response.status == 200


@pytest.mark.asyncio
async def test_not_ready_while_gateway_is_disconnected():
    import json

    bot = _connected_bot()
    bot.ws.open = False

    response = await HealthCheckServer(bot).readiness_check(None)

    assert response.status == 503
    assert json.loads(response.body)['gateway_connected'] is False


@pytest.mark.asyncio
async def test_unknown_latency_is_reported_as_null():
    import json

    bot = _connected_bot()
    bot.latency = float('inf')

    response = await HealthCheckServer(bot).readiness_check(None)

    assert json.loads(response.body)['latency_ms'] is None
