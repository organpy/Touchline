"""
Rate limiting utilities to prevent spam and abuse.
Uses token bucket algorithm for fair rate limiting.
"""

import logging
import time
from dataclasses import dataclass
from typing import Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class _Bucket:
    tokens: float
    last_refill: float
    last_access: float


class RateLimiter:
    """
    Token bucket rate limiter.
    Allows N requests per time period on a per-key basis (e.g., per guild).
    """

    def __init__(
        self,
        max_tokens: int = 1,
        refill_rate: float = 1.0,
        *,
        idle_ttl_seconds: float = 900.0,
    ):
        """
        Initialize rate limiter.

        Args:
            max_tokens: Maximum tokens available (default 1 = 1 request)
            refill_rate: Tokens refilled per second (default 1.0 = 1 token/sec)
        """
        self.max_tokens = max_tokens
        self.refill_rate = refill_rate
        self.idle_ttl_seconds = max(idle_ttl_seconds, 0.0)
        self._buckets: Dict[str, _Bucket] = {}
        self._last_prune_check = 0.0
        self._prune_interval = max(30.0, min(self.idle_ttl_seconds or 30.0, 300.0))

    def _get_bucket(self, key: str, *, now: Optional[float] = None) -> _Bucket:
        """Get or create bucket for key."""
        now = now or time.time()
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket(tokens=float(self.max_tokens), last_refill=now, last_access=now)
            self._buckets[key] = bucket
        return bucket

    def _refill(self, key: str, *, now: Optional[float] = None) -> _Bucket:
        """Refill tokens based on time elapsed since last refill."""
        now = now or time.time()
        bucket = self._get_bucket(key, now=now)
        elapsed = max(0.0, now - bucket.last_refill)

        if elapsed > 0 and bucket.tokens < self.max_tokens:
            bucket.tokens = min(self.max_tokens, bucket.tokens + elapsed * self.refill_rate)
            bucket.last_refill = now

        bucket.last_access = now
        self._maybe_prune(now)
        return bucket

    def _maybe_prune(self, now: float) -> None:
        if not self.idle_ttl_seconds:
            return

        if now - self._last_prune_check < self._prune_interval:
            return

        self._last_prune_check = now
        to_delete: list[tuple[str, float]] = []
        for key, bucket in self._buckets.items():
            idle_time = now - bucket.last_access
            if idle_time >= self.idle_ttl_seconds and bucket.tokens >= (self.max_tokens - 0.01):
                to_delete.append((key, idle_time))

        for key, idle_time in to_delete:
            logger.debug("RateLimiter pruning bucket %s after %.1fs idle", key, idle_time)
            self._buckets.pop(key, None)

    def allow(self, key: str, cost: int = 1) -> bool:
        """
        Check if request is allowed. Consumes tokens if allowed.

        Args:
            key: Rate limit key (e.g., guild_id)
            cost: Token cost of this request (default 1)

        Returns:
            True if request allowed, False if rate limited
        """
        bucket = self._refill(key)

        if bucket.tokens >= cost:
            bucket.tokens -= cost
            return True

        return False

    def reset(self, key: str) -> None:
        """Reset rate limit for a key."""
        now = time.time()
        self._buckets[key] = _Bucket(tokens=float(self.max_tokens), last_refill=now, last_access=now)

    def get_retry_after(self, key: str, cost: int = 1) -> float:
        """
        Get seconds to wait before a request of ``cost`` tokens is allowed.

        Args:
            key: Rate limit key
            cost: Token cost of the request (default 1)

        Returns:
            Seconds to wait (0 if immediately available)
        """
        bucket = self._refill(key)

        if bucket.tokens >= cost:
            return 0.0

        # Calculate time needed to earn the missing tokens
        tokens_needed = cost - bucket.tokens
        return tokens_needed / self.refill_rate

