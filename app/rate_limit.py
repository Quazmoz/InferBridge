"""Simple per-IP sliding-window rate limiter for the FastAPI server.

Configurable via ``OV_LLM_RATE_LIMIT`` (requests per minute, 0 = disabled).
Uses a bounded-lifetime in-memory dict of timestamps per IP.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections import defaultdict, deque

from starlette.responses import JSONResponse

logger = logging.getLogger("ov-llm.ratelimit")


class RateLimitMiddleware:
    """Sliding-window ASGI rate limiter keyed by client IP address.

    Only applies to ``/v1/`` API routes. Static pages, health checks, and browser
    CORS preflight requests are exempt. A direct ASGI middleware avoids the response
    buffering and context propagation overhead of ``BaseHTTPMiddleware``, which is
    important for InferBridge's streaming endpoints.
    """

    def __init__(self, app, requests_per_minute: int = 60) -> None:
        self.app = app
        self.rpm = max(int(requests_per_minute), 0)
        self.window = 60.0
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._last_cleanup = time.monotonic()
        self._lock = threading.Lock()

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http" or self.rpm <= 0:
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path") or "")
        method = str(scope.get("method") or "GET").upper()
        if not path.startswith("/v1/") or method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        client_ip = str(client[0]) if client else "unknown"
        retry_after = self._record_request(client_ip, time.monotonic())
        if retry_after is not None:
            logger.warning("Rate limit exceeded for %s (%d rpm)", client_ip, self.rpm)
            response = JSONResponse(
                {"detail": f"Rate limit exceeded. Try again in {retry_after}s."},
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)

    def _record_request(self, client_ip: str, now: float) -> int | None:
        """Record an allowed request or return its retry delay when blocked."""

        with self._lock:
            if now - self._last_cleanup > self.window:
                self._cleanup_locked(now)
                self._last_cleanup = now

            window = self._hits[client_ip]
            cutoff = now - self.window
            while window and window[0] < cutoff:
                window.popleft()

            if len(window) >= self.rpm:
                return max(1, math.ceil(window[0] + self.window - now))

            window.append(now)
            return None

    def _cleanup_locked(self, now: float) -> None:
        """Remove clients with no recent activity while ``self._lock`` is held."""

        cutoff = now - self.window
        stale = [ip for ip, hits in self._hits.items() if not hits or hits[-1] < cutoff]
        for ip in stale:
            del self._hits[ip]
