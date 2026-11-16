"""Brute-force protection for sign-in (in-process; the API runs as one process in Docker).

A key (email, or client address) is blocked once it reaches its limit of failed attempts inside the window,
until the oldest counted failure leaves the window. While blocked, even a correct password is refused, so
guessing cannot continue. A successful sign-in clears the email's failures.
"""
from __future__ import annotations

import threading
import time
from collections import deque

from app.core.errors import OrbitError

WINDOW_S = 15 * 60
LIMITS = {"email": 5, "ip": 30}


class TooManyAttempts(OrbitError):
    status_code = 429
    code = "too_many_attempts"


class LoginThrottle:
    def __init__(self) -> None:
        self._fails: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> deque[float]:
        q = self._fails.setdefault(key, deque())
        while q and now - q[0] > WINDOW_S:
            q.popleft()
        return q

    def check(self, email: str, ip: str) -> None:
        now = time.time()
        with self._lock:
            for kind, key in (("email", f"e:{email}"), ("ip", f"i:{ip}")):
                q = self._recent(key, now)
                if len(q) >= LIMITS[kind]:
                    wait = int(WINDOW_S - (now - q[0])) + 1
                    raise TooManyAttempts(f"Too many failed sign-in attempts. Try again in {max(1, wait // 60)} minute(s).",
                                          details={"retry_after_s": wait})

    def failed(self, email: str, ip: str) -> None:
        now = time.time()
        with self._lock:
            for key in (f"e:{email}", f"i:{ip}"):
                self._recent(key, now).append(now)

    def succeeded(self, email: str) -> None:
        with self._lock:
            self._fails.pop(f"e:{email}", None)

    def reset(self) -> None:
        with self._lock:
            self._fails.clear()


login_throttle = LoginThrottle()
