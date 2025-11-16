"""
Rate limiting utilities to prevent spam and abuse.
Uses token bucket algorithm for fair rate limiting.
"""

import logging
import time
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    Token bucket rate limiter.
    Allows N requests per time period on a per-key basis (e.g., per guild).
    """

    def __init__(self, max_tokens: int = 1, refill_rate: float = 1.0):
        """
        Initialize rate limiter.

        Args:
            max_tokens: Maximum tokens available (default 1 = 1 request)
            refill_rate: Tokens refilled per second (default 1.0 = 1 token/sec)
        """
        self.max_tokens = max_tokens
        self.refill_rate = refill_rate
        self._buckets: Dict[str, tuple[float, float]] = {}  # key -> (tokens, last_refill_time)

    def _get_bucket(self, key: str) -> tuple[float, float]:
        """Get or create bucket for key."""
        if key not in self._buckets:
            self._buckets[key] = (self.max_tokens, time.time())
        return self._buckets[key]

    def _refill(self, key: str) -> None:
        """Refill tokens based on time elapsed since last refill."""
        tokens, last_refill = self._get_bucket(key)
        now = time.time()
        elapsed = now - last_refill

        # Add tokens based on elapsed time
        new_tokens = min(self.max_tokens, tokens + elapsed * self.refill_rate)
        self._buckets[key] = (new_tokens, now)

    def allow(self, key: str, cost: int = 1) -> bool:
        """
        Check if request is allowed. Consumes tokens if allowed.

        Args:
            key: Rate limit key (e.g., guild_id)
            cost: Token cost of this request (default 1)

        Returns:
            True if request allowed, False if rate limited
        """
        self._refill(key)
        tokens, last_refill = self._buckets[key]

        if tokens >= cost:
            self._buckets[key] = (tokens - cost, last_refill)
            return True

        return False

    def reset(self, key: str) -> None:
        """Reset rate limit for a key."""
        self._buckets[key] = (self.max_tokens, time.time())

    def get_retry_after(self, key: str) -> float:
        """
        Get seconds to wait before next request is allowed.

        Args:
            key: Rate limit key

        Returns:
            Seconds to wait (0 if immediately available)
        """
        self._refill(key)
        tokens, _ = self._buckets[key]

        if tokens >= 1:
            return 0.0

        # Calculate time needed to get 1 token
        tokens_needed = 1 - tokens
        return tokens_needed / self.refill_rate


# Global rate limiters for different cogs/features
timer_requests_limiter = RateLimiter(
    max_tokens=1, refill_rate=1.0 / 5.0  # 1 request per 5 seconds per guild
)
