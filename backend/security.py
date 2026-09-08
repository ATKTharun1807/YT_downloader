"""
security.py — Rate limiting and request security helpers for FastAPI.

Simple in-memory sliding-window rate limiter keyed by IP address.
No external dependencies required.
"""

import time
import threading
from collections import defaultdict
from functools import wraps
from typing import Callable, Any
from fastapi import Request
from fastapi.responses import JSONResponse


class RateLimiter:
    """
    Sliding-window in-memory rate limiter for FastAPI endpoints.

    Usage:
        limiter = RateLimiter(max_calls=10, window_seconds=60)

        @router.post("/analyze")
        @limiter.limit
        async def analyze(req: Request, ...): ...
    """

    def __init__(self, max_calls: int, window_seconds: float):
        self.max_calls = max_calls
        self.window = window_seconds
        self._lock = threading.Lock()
        # ip -> list of timestamps
        self._calls: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, ip: str) -> tuple[bool, int]:
        """
        Check if `ip` is within rate limit.
        Returns (allowed, retry_after_seconds).
        """
        now = time.monotonic()
        cutoff = now - self.window

        with self._lock:
            # Purge old entries
            self._calls[ip] = [t for t in self._calls[ip] if t > cutoff]
            count = len(self._calls[ip])

            if count >= self.max_calls:
                oldest = self._calls[ip][0]
                retry_after = int(self.window - (now - oldest)) + 1
                return False, retry_after

            self._calls[ip].append(now)
            return True, 0

    def limit(self, f: Callable) -> Callable:
        """Decorator to apply rate limiting to a FastAPI route function."""
        @wraps(f)
        async def decorated(*args: Any, **kwargs: Any):
            # Find the Request object in kwargs or args
            request = kwargs.get("request")
            if not request:
                for arg in args:
                    if isinstance(arg, Request):
                        request = arg
                        break

            ip = _get_client_ip(request) if request else "unknown"
            allowed, retry_after = self.is_allowed(ip)
            if not allowed:
                return JSONResponse(
                    status_code=429,
                    content={
                        "success": False,
                        "error": f"Too many requests. Please wait {retry_after} seconds.",
                        "retry_after": retry_after,
                    },
                    headers={"Retry-After": str(retry_after)},
                )
            return await f(*args, **kwargs)
        return decorated


# ---------------------------------------------------------------------------
# Pre-configured limiters
# ---------------------------------------------------------------------------

# 10 analyze requests per minute per IP
analyze_limiter = RateLimiter(max_calls=10, window_seconds=60)

# 5 download requests per minute per IP
download_limiter = RateLimiter(max_calls=5, window_seconds=60)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_client_ip(request: Request) -> str:
    """Extract real client IP, respecting X-Forwarded-For if present."""
    if not request:
        return "unknown"
    xff = request.headers.get("x-forwarded-for") or request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"
