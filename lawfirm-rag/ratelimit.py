from __future__ import annotations

import asyncio
import time
from collections import deque

from fastapi import HTTPException

from config import settings


class RateLimiter:
    """
    In-process token-bucket rate limiter keyed by user ID.

    Keeps one deque of arrival timestamps per user behind an ``asyncio.Lock``
    so concurrent requests are serialised safely without any external storage.
    Timestamps older than the sliding window are evicted on every check.
    """

    def __init__(
        self,
        max_requests: int | None = None,
        window_seconds: int | None = None,
    ) -> None:
        """
        Initialise the limiter with values from settings unless overridden.
        """
        self.max_requests = max_requests if max_requests is not None else settings.rate_limit_rpm
        self.window_seconds = (
            window_seconds if window_seconds is not None else settings.rate_limit_window_seconds
        )
        self._lock = asyncio.Lock()
        self._timestamps: dict[str, deque[float]] = {}

    async def check(self, user_id: str) -> None:
        """
        Record a request for ``user_id`` and raise 429 if the limit is exceeded.

        Uses a monotonic clock so the window is immune to wall-clock changes.
        """
        now = time.monotonic()
        async with self._lock:
            timestamps = self._timestamps.setdefault(user_id, deque())

            while timestamps and now - timestamps[0] > self.window_seconds:
                timestamps.popleft()

            if len(timestamps) >= self.max_requests:
                retry_after = int(max(1, self.window_seconds - (now - timestamps[0])))
                raise HTTPException(
                    status_code=429,
                    detail="TOO MANY REQUESTS: rate limit exceeded. Try again shortly.",
                    headers={"Retry-After": str(retry_after)},
                )

            timestamps.append(now)


rate_limiter = RateLimiter()