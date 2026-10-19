"""Rate limiting: cap how many questions can be asked, to control cost and abuse.

Three limits, checked together before a question is answered:
- per browser session: e.g. 10 questions per 10 minutes (one user, one tab);
- per client IP: e.g. 30 per 10 minutes, so opening new tabs doesn't reset
  the count (higher than the session limit, as an office may share one IP);
- global daily cap: e.g. 200 per day across all users, a cost safety net.

A sliding window counts the requests in the last `window_s` seconds, so there
is no burst allowed at the boundary of fixed time slots.

All state is in memory: it resets when the app restarts and is not shared
between several app processes. That's enough for one Streamlit server; with
several replicas, move the counters to a shared store (e.g. Redis) or enforce
the per-IP limit at the reverse proxy / ingress in front of the app.

The client IP comes from the WebSocket connection. Behind a reverse proxy
every request comes from the proxy's IP, so set
RATE_LIMIT_TRUST_FORWARDED_FOR=true to read X-Forwarded-For instead. Never
enable it without a proxy: clients can set that header to anything.
"""

import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import NamedTuple, Protocol

LOCAL_CLIENT = "local"  # Streamlit reports no IP for localhost


class Limit(Protocol):
    def retry_after(self, key: str) -> float | None: ...
    def record(self, key: str) -> None: ...


class RateLimitHit(NamedTuple):
    scope: str  # "session", "ip" or "daily"
    retry_after_s: float


class SlidingWindowLimiter:
    """At most `max_requests` per key in any `window_s`-second window. Thread-safe."""

    def __init__(
        self,
        max_requests: int,
        window_s: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_requests = max_requests
        self.window_s = window_s
        self.clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        hits = self._hits.get(key, deque())
        while hits and now - hits[0] >= self.window_s:
            hits.popleft()
        if hits:
            self._hits[key] = hits
        else:
            self._hits.pop(key, None)  # forget idle keys so memory stays bounded
        return hits

    def retry_after(self, key: str) -> float | None:
        """None if a request is allowed now; else seconds until one is."""
        with self._lock:
            now = self.clock()
            hits = self._prune(key, now)
            if len(hits) < self.max_requests:
                return None
            return hits[0] + self.window_s - now

    def record(self, key: str) -> None:
        with self._lock:
            now = self.clock()
            self._prune(key, now)
            self._hits.setdefault(key, deque()).append(now)


class DailyCap:
    """At most `max_requests` per UTC day across all keys. Thread-safe."""

    def __init__(
        self,
        max_requests: int,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.max_requests = max_requests
        self.clock = clock
        self._day: date | None = None
        self._count = 0
        self._lock = threading.Lock()

    def _roll_over(self, now: datetime) -> None:
        if now.date() != self._day:
            self._day, self._count = now.date(), 0

    def retry_after(self, key: str = "") -> float | None:
        with self._lock:
            now = self.clock()
            self._roll_over(now)
            if self._count < self.max_requests:
                return None
            midnight = datetime.combine(
                now.date() + timedelta(days=1), datetime.min.time(), now.tzinfo
            )
            return (midnight - now).total_seconds()

    def record(self, key: str = "") -> None:
        with self._lock:
            self._roll_over(self.clock())
            self._count += 1


def check_limits(checks: Sequence[tuple[str, Limit, str]]) -> RateLimitHit | None:
    """Check every (scope, limit, key); count the request only if all allow it,
    so a blocked request doesn't use up quota in the other limits."""
    for scope, limit, key in checks:
        wait = limit.retry_after(key)
        if wait is not None:
            return RateLimitHit(scope, wait)
    for _, limit, key in checks:
        limit.record(key)
    return None


def client_ip(
    ip_address: str | None, headers: Mapping[str, str], trust_forwarded_for: bool
) -> str:
    """The client's IP. X-Forwarded-For is used only if a trusted proxy sets it;
    its first entry is the original client."""
    if trust_forwarded_for:
        forwarded = headers.get("X-Forwarded-For", "")
        if first := forwarded.split(",")[0].strip():
            return first
    return ip_address or LOCAL_CLIENT


def format_wait(seconds: float) -> str:
    minutes = max(1, round(seconds / 60))
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60} min"


def limit_message(hit: RateLimitHit, max_requests: int, window_s: int) -> str:
    wait = format_wait(hit.retry_after_s)
    window = format_wait(window_s)
    if hit.scope == "daily":
        return (
            "The assistant has reached its daily question limit (a cost safety net). "
            f"Please try again in {wait}."
        )
    who = "this session" if hit.scope == "session" else "your network"
    return (
        f"You've reached the limit of {max_requests} questions per {window} "
        f"from {who}. Please try again in {wait}."
    )
