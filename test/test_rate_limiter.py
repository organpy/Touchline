"""
Tests for rate limiting utilities.
"""

import pytest
import time

from utils.rate_limiter import RateLimiter, timer_requests_limiter


class TestRateLimiter:
    """Test rate limiter functionality."""

    def test_allow_under_limit(self):
        """Test that requests are allowed when under limit."""
        limiter = RateLimiter(max_tokens=5, refill_rate=1.0)
        
        # All 5 requests should be allowed
        for i in range(5):
            assert limiter.allow(f"key{i}") is True

    def test_deny_over_limit(self):
        """Test that requests are denied when over limit."""
        limiter = RateLimiter(max_tokens=1, refill_rate=0.0)  # No refill
        
        # First request allowed
        assert limiter.allow("key1") is True
        
        # Second request denied (no refill rate)
        assert limiter.allow("key1") is False

    def test_token_refill_over_time(self):
        """Test that tokens refill at correct rate."""
        limiter = RateLimiter(max_tokens=1, refill_rate=1.0)  # 1 token per second
        
        # First request uses token
        assert limiter.allow("key1") is True
        
        # Immediately deny second request
        assert limiter.allow("key1") is False
        
        # Wait for token to refill
        time.sleep(1.1)
        
        # Should be allowed again
        assert limiter.allow("key1") is True

    def test_per_key_rate_limiting(self):
        """Test that rate limits are per-key."""
        limiter = RateLimiter(max_tokens=1, refill_rate=0.0)
        
        # key1 uses its token
        assert limiter.allow("key1") is True
        assert limiter.allow("key1") is False
        
        # key2 has its own token
        assert limiter.allow("key2") is True
        assert limiter.allow("key2") is False

    def test_get_retry_after(self):
        """Test retry_after calculation."""
        limiter = RateLimiter(max_tokens=1, refill_rate=1.0)
        
        # Use the token
        limiter.allow("key1")
        
        # Should need ~1 second for retry
        retry_after = limiter.get_retry_after("key1")
        assert 0.9 < retry_after < 1.1

    def test_get_retry_after_zero_when_available(self):
        """Test that retry_after is 0 when request is available."""
        limiter = RateLimiter(max_tokens=5, refill_rate=1.0)
        
        # No requests made, should be immediately available
        retry_after = limiter.get_retry_after("key1")
        assert retry_after == 0.0

    def test_reset_bucket(self):
        """Test resetting rate limit for a key."""
        limiter = RateLimiter(max_tokens=1, refill_rate=0.0)
        
        # Use token, now should be denied
        limiter.allow("key1")
        assert limiter.allow("key1") is False
        
        # Reset
        limiter.reset("key1")
        
        # Should be allowed again
        assert limiter.allow("key1") is True

    def test_global_timer_limiter(self):
        """Test that global timer_requests_limiter is configured."""
        assert timer_requests_limiter is not None
        assert isinstance(timer_requests_limiter, RateLimiter)
        # Should allow ~1 per 5 seconds (refill_rate = 1/5)
        assert timer_requests_limiter.max_tokens == 1

    def test_cost_parameter(self):
        """Test that cost parameter works correctly."""
        limiter = RateLimiter(max_tokens=10, refill_rate=0.0)
        
        # Use 5 tokens
        assert limiter.allow("key1", cost=5) is True
        
        # 5 tokens remain
        assert limiter.allow("key1", cost=5) is True
        
        # 0 tokens remain, nothing is allowed
        assert limiter.allow("key1", cost=2) is False
        assert limiter.allow("key1", cost=1) is False

    def test_burst_capacity(self):
        """Test burst capacity handling."""
        limiter = RateLimiter(max_tokens=5, refill_rate=1.0)
        
        # Can burst 5 requests immediately
        for i in range(5):
            assert limiter.allow("key1") is True
        
        # 6th denied
        assert limiter.allow("key1") is False


class TestRateLimiterIntegration:
    """Integration tests for rate limiting patterns."""

    def test_simulated_timer_requests(self):
        """Simulate timer request pattern from bot."""
        limiter = RateLimiter(max_tokens=1, refill_rate=1.0 / 5.0)  # 1 per 5 sec
        guild_id = 12345
        
        # First request allowed
        assert limiter.allow(f"timer_start_{guild_id}") is True
        
        # Immediate retry denied
        assert limiter.allow(f"timer_start_{guild_id}") is False
        
        # Different guild not affected
        assert limiter.allow(f"timer_start_{67890}") is True

    def test_multiple_guilds_independent(self):
        """Test that multiple guilds have independent rate limits."""
        limiter = RateLimiter(max_tokens=1, refill_rate=0.0)
        
        guild_keys = [f"guild_{i}" for i in range(10)]
        
        # Each guild gets 1 token
        for key in guild_keys:
            assert limiter.allow(key) is True
        
        # None get a second
        for key in guild_keys:
            assert limiter.allow(key) is False
