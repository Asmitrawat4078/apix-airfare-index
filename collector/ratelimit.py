"""Per-domain politeness. Slow on purpose.

The whole daily job needs 120 cells. Spread over a run window that is minutes long,
that is a request every few seconds — far below any threshold a booking site would
notice, let alone care about. There is no version of this project where going faster
helps: the index samples once a day, so throughput buys us nothing and costs us the
legal and ethical argument.

`crawl_delay` from robots.txt always wins if it is longer than our own floor.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import defaultdict

log = logging.getLogger("apix.ratelimit")


class DomainRateLimiter:
    def __init__(self, min_interval: float = 4.0, jitter: float = 2.0) -> None:
        self.min_interval = min_interval
        self.jitter = jitter
        self._last: dict[str, float] = defaultdict(float)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._overrides: dict[str, float] = {}

    def honour_crawl_delay(self, domain: str, crawl_delay: float | None) -> None:
        """Adopt a site's stated crawl-delay when it asks for more space than we give."""
        if crawl_delay and crawl_delay > self.min_interval:
            if self._overrides.get(domain) != crawl_delay:
                log.info("ratelimit domain=%s adopting robots crawl-delay=%.1fs", domain, crawl_delay)
            self._overrides[domain] = crawl_delay

    async def acquire(self, domain: str) -> None:
        async with self._locks[domain]:
            interval = self._overrides.get(domain, self.min_interval)
            wait = interval + random.uniform(0, self.jitter) - (time.monotonic() - self._last[domain])
            if wait > 0:
                log.debug("ratelimit domain=%s sleeping %.2fs", domain, wait)
                await asyncio.sleep(wait)
            self._last[domain] = time.monotonic()
