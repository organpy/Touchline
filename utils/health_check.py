"""
Health check HTTP endpoint for monitoring and orchestration.
Runs a simple HTTP server on a separate thread to respond to health checks.
"""
import asyncio
import logging
import math
from aiohttp import web
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class HealthCheckServer:
    """Simple HTTP server for health checks"""
    
    def __init__(self, bot, port: int = 8080, host: str = "127.0.0.1"):
        self.bot = bot
        self.port = port
        self.host = host
        self.app = web.Application()
        self.runner = None
        self.site = None
        self.start_time = datetime.now(timezone.utc)
        
        # Setup routes
        self.app.router.add_get('/health', self.health_check)
        self.app.router.add_get('/ready', self.readiness_check)
        self.app.router.add_get('/metrics', self.metrics)

    def _latency_ms(self):
        """Gateway latency in ms, or None before the first heartbeat (NaN/inf would not be valid JSON)."""
        latency = self.bot.latency
        return round(latency * 1000, 2) if math.isfinite(latency) else None
    
    async def health_check(self, request):
        """Liveness probe - is the bot process alive?"""
        return web.json_response({
            'status': 'healthy',
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'uptime_seconds': (datetime.now(timezone.utc) - self.start_time).total_seconds()
        })
    
    async def readiness_check(self, request):
        """Readiness probe - is the bot ready to serve traffic?"""
        # is_ready() stays True through gateway outages, so also require an open websocket.
        gateway_connected = self.bot.ws is not None and self.bot.ws.open
        ready = (
            self.bot.is_ready() and
            gateway_connected and
            self.bot.user is not None and
            len(self.bot.guilds) > 0
        )

        status_code = 200 if ready else 503

        return web.json_response({
            'status': 'ready' if ready else 'not_ready',
            'gateway_connected': gateway_connected,
            'bot_user': str(self.bot.user) if self.bot.user else None,
            'guilds_count': len(self.bot.guilds),
            'latency_ms': self._latency_ms(),
            'timestamp': datetime.now(timezone.utc).isoformat()
        }, status=status_code)
    
    async def metrics(self, request):
        """Basic metrics endpoint"""
        from utils.metrics import MetricsCollector
        
        summary = MetricsCollector.get_summary()
        
        return web.json_response({
            'uptime_seconds': (datetime.now(timezone.utc) - self.start_time).total_seconds(),
            'bot_latency_ms': self._latency_ms(),
            'guilds_count': len(self.bot.guilds),
            'users_count': len(self.bot.users),
            'commands_synced': len(self.bot.tree.get_commands()),
            'cache_stats': summary.get('cache_stats', {}),
            'query_stats': summary.get('query_stats', {}),
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
    
    async def start(self):
        """Start the health check server"""
        try:
            self.runner = web.AppRunner(self.app)
            await self.runner.setup()
            self.site = web.TCPSite(self.runner, self.host, self.port)
            await self.site.start()
            logger.info(f"✅ Health check server started on {self.host}:{self.port}")
        except Exception as e:
            logger.error(f"❌ Failed to start health check server: {e}", exc_info=True)
    
    async def stop(self):
        """Stop the health check server"""
        if self.site:
            await self.site.stop()
            self.site = None
        if self.runner:
            await self.runner.cleanup()
            self.runner = None
        logger.info("Health check server stopped")
