"""A simple speed limiter for streamed file bodies (Phase T3): ``Throttle(rate).wait(n)`` after sending ``n`` bytes."""

from __future__ import annotations

import time
from typing import Callable

#: Largest piece moved between two waits when a limit is on, so Stop and the limit react within about a quarter second.
MAX_CHUNK = 1 << 20
#: Seconds of unused time that may be spent later as a short burst.
CREDIT_S = 1.0
MIN_CHUNK = 4096


class Throttle:
    """Keeps the average speed at or below ``bytes_per_second`` (0 or less = unlimited, ``wait`` returns at once)."""

    def __init__(
        self,
        bytes_per_second: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.rate = float(bytes_per_second)
        self._clock = clock
        self._sleep = sleep
        self._next = clock()

    @property
    def limited(self) -> bool:
        return self.rate > 0

    def chunk_size(self) -> int:
        """How many bytes to move per step: small when the limit is low, so it is applied smoothly."""
        if not self.limited:
            return MAX_CHUNK
        return int(max(MIN_CHUNK, min(MAX_CHUNK, self.rate / 4)))

    def wait(self, nbytes: int) -> None:
        if not self.limited or nbytes <= 0:
            return
        now = self._clock()
        # `_next` is when the data sent so far should be done. Time the caller spent on other things counts as
        # credit (at most CREDIT_S), so a slow disk does not get an extra pause on top of the limit.
        self._next = max(self._next, now - CREDIT_S) + nbytes / self.rate
        delay = self._next - now
        if delay > 0:
            self._sleep(delay)
