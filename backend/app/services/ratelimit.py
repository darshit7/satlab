"""A token bucket: ``rate`` tokens per second, holding at most ``burst``."""

import time


class TokenBucket:
    def __init__(self, rate: float, burst: int) -> None:
        self.rate = rate
        self.burst = burst
        self._tokens = float(burst)
        self._last = time.monotonic()

    def take(self) -> bool:
        """Spend one token if there is one; False means the caller is over the limit."""
        now = time.monotonic()
        self._tokens = min(self.burst, self._tokens + (now - self._last) * self.rate)
        self._last = now
        if self._tokens < 1:
            return False
        self._tokens -= 1
        return True
