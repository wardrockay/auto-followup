"""
Rate Limiting.

Provides request rate limiting for API endpoints.
"""

import time
from collections import defaultdict
from dataclasses import dataclass, field
from functools import wraps
from threading import Lock
from typing import Any, Callable, Dict, Optional, Tuple

from flask import request, Response
import json


@dataclass
class RateLimitConfig:
    """Rate limit configuration."""
    requests_per_minute: int = 60
    requests_per_hour: int = 1000
    burst_size: int = 10


@dataclass
class TokenBucket:
    """Token bucket for rate limiting."""
    capacity: float
    tokens: float
    last_update: float
    refill_rate: float  # tokens per second
    
    def consume(self, tokens: int = 1) -> bool:
        """Try to consume tokens. Returns True if successful."""
        now = time.time()
        elapsed = now - self.last_update
        
        # Refill tokens
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_update = now
        
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False
    
    @property
    def retry_after(self) -> int:
        """Seconds until a token is available."""
        if self.tokens >= 1:
            return 0
        return int((1 - self.tokens) / self.refill_rate) + 1


class RateLimiter:
    """
    Rate limiter using token bucket algorithm.
    
    Thread-safe implementation for Flask applications.
    """
    
    def __init__(self, config: Optional[RateLimitConfig] = None) -> None:
        self._config = config or RateLimitConfig()
        self._buckets: Dict[str, TokenBucket] = {}
        self._lock = Lock()
    
    def _get_client_id(self) -> str:
        """Get unique client identifier."""
        # Use X-Forwarded-For for Cloud Run (behind load balancer)
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.remote_addr or "unknown"
    
    def _get_or_create_bucket(self, client_id: str) -> TokenBucket:
        """Get or create a token bucket for a client."""
        with self._lock:
            if client_id not in self._buckets:
                self._buckets[client_id] = TokenBucket(
                    capacity=float(self._config.burst_size),
                    tokens=float(self._config.burst_size),
                    last_update=time.time(),
                    refill_rate=self._config.requests_per_minute / 60.0,
                )
            return self._buckets[client_id]
    
    def is_allowed(self) -> Tuple[bool, int]:
        """
        Check if request is allowed.
        
        Returns:
            Tuple of (is_allowed, retry_after_seconds)
        """
        client_id = self._get_client_id()
        bucket = self._get_or_create_bucket(client_id)
        
        if bucket.consume():
            return True, 0
        return False, bucket.retry_after
    
    def cleanup_old_buckets(self, max_age_seconds: int = 3600) -> int:
        """Remove buckets that haven't been used recently."""
        now = time.time()
        removed = 0
        
        with self._lock:
            expired = [
                client_id
                for client_id, bucket in self._buckets.items()
                if now - bucket.last_update > max_age_seconds
            ]
            for client_id in expired:
                del self._buckets[client_id]
                removed += 1
        
        return removed


# Global rate limiter instance
_rate_limiter: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    """Get global rate limiter instance."""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter()
    return _rate_limiter


def rate_limit(func: Callable) -> Callable:
    """
    Decorator to apply rate limiting to an endpoint.
    
    Usage:
        @api_bp.route("/my-endpoint", methods=["POST"])
        @rate_limit
        def my_endpoint():
            ...
    """
    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        limiter = get_rate_limiter()
        allowed, retry_after = limiter.is_allowed()
        
        if not allowed:
            response_data = {
                "success": False,
                "error": "Rate limit exceeded",
                "error_type": "rate_limit_exceeded",
                "retry_after": retry_after,
            }
            response = Response(
                json.dumps(response_data),
                status=429,
                mimetype="application/json",
            )
            response.headers["Retry-After"] = str(retry_after)
            return response
        
        return func(*args, **kwargs)
    
    return wrapper
