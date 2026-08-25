"""The one HTTP client the collector is allowed to use.

Everything the scraping policy promises is enforced here rather than left to each
source to remember: identified User-Agent, robots gate, per-domain rate limit,
exponential backoff, and — importantly — bot-wall detection that gives up instead
of getting clever.

We do not rotate user agents, use residential proxies, or solve challenges. If a site
does not want us, the correct output is `is_available=False, reason=blocked`, which
flows through imputation and lands in the published availability rate. Missingness we
declare is a methodology; missingness we evade is a liability.
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from .ratelimit import DomainRateLimiter
from .robots import RobotsGate
from .schema import UnavailableReason

log = logging.getLogger("apix.fetch")

CONTACT_EMAIL = "asmitrawat4078@gmail.com"
USER_AGENT = (
    "APIx-PriceIndexBot/1.0 (+https://github.com/{repo}; {email}) "
    "research crawler for an official-statistics airfare price index (SIH26056)"
)

# Signatures of a challenge page. If any of these appear we stop, we do not attempt
# to satisfy the challenge, and we record the cell as blocked.
BOT_WALL_MARKERS = (
    "captcha",
    "recaptcha",
    "hcaptcha",
    "are you a human",
    "unusual traffic",
    "access denied",
    "request unsuccessful",
    "incapsula",
    "cf-challenge",
    "checking your browser",
    "px-captcha",
    "perimeterx",
    "datadome",
)

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


class Blocked(Exception):
    """Raised when a source is walled. Carries the reason that goes into the row."""

    def __init__(self, reason: UnavailableReason, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


@dataclass(slots=True)
class FetchResult:
    url: str
    status: int
    text: str
    headers: dict
    attempts: int
    elapsed_s: float


class PoliteClient:
    def __init__(
        self,
        repo: str = "apix",
        max_attempts: int = 3,
        base_backoff: float = 4.0,
        timeout: float = 30.0,
        min_interval: float = 4.0,
    ) -> None:
        self.user_agent = USER_AGENT.format(repo=repo, email=CONTACT_EMAIL)
        self.robots = RobotsGate(self.user_agent)
        self.limiter = DomainRateLimiter(min_interval=min_interval)
        self.max_attempts = max_attempts
        self.base_backoff = base_backoff
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> PoliteClient:
        self._client = httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            headers={
                "User-Agent": self.user_agent,
                "Accept-Language": "en-IN,en;q=0.9",
                "From": CONTACT_EMAIL,
            },
        )
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client:
            await self._client.aclose()

    @staticmethod
    def _looks_like_bot_wall(status: int, text: str) -> str | None:
        if status in (403, 401, 407):
            return f"HTTP {status}"
        head = text[:4000].lower()
        for marker in BOT_WALL_MARKERS:
            if marker in head:
                return f"challenge marker {marker!r} in response body"
        return None

    async def get(
        self, url: str, *, headers: dict | None = None, json_body: dict | None = None
    ) -> FetchResult:
        """Fetch one URL under the full policy. Raises Blocked rather than working around a wall."""
        decision = self.robots.check(url)
        if not decision.allowed:
            raise Blocked(UnavailableReason.ROBOTS_DISALLOWED, decision.reason)

        domain = urlparse(url).netloc
        self.limiter.honour_crawl_delay(domain, decision.crawl_delay)

        assert self._client is not None, "use PoliteClient as an async context manager"
        loop = asyncio.get_running_loop()
        started = loop.time()
        last_error = "no attempt made"

        for attempt in range(1, self.max_attempts + 1):
            await self.limiter.acquire(domain)
            try:
                if json_body is not None:
                    resp = await self._client.post(url, headers=headers, json=json_body)
                else:
                    resp = await self._client.get(url, headers=headers)
            except httpx.TimeoutException as exc:
                last_error = f"timeout: {exc}"
                log.warning("fetch url=%s attempt=%d %s", url, attempt, last_error)
                if attempt == self.max_attempts:
                    raise Blocked(UnavailableReason.TIMEOUT, last_error) from exc
                await self._backoff(attempt)
                continue
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                log.warning("fetch url=%s attempt=%d %s", url, attempt, last_error)
                if attempt == self.max_attempts:
                    raise Blocked(UnavailableReason.PARSE_ERROR, last_error) from exc
                await self._backoff(attempt)
                continue

            wall = self._looks_like_bot_wall(resp.status_code, resp.text)
            if wall:
                # Deliberate: one polite retry after a long pause, then we accept the answer.
                log.warning("fetch url=%s attempt=%d bot wall detected (%s)", url, attempt, wall)
                if attempt == self.max_attempts:
                    raise Blocked(UnavailableReason.BLOCKED, wall)
                await self._backoff(attempt, multiplier=3)
                continue

            if resp.status_code == 429:
                log.warning("fetch url=%s attempt=%d rate limited by origin", url, attempt)
                if attempt == self.max_attempts:
                    raise Blocked(UnavailableReason.RATE_LIMITED, "HTTP 429 after retries")
                await self._backoff(attempt, multiplier=4)
                continue

            if resp.status_code in RETRYABLE_STATUS:
                last_error = f"HTTP {resp.status_code}"
                if attempt == self.max_attempts:
                    raise Blocked(UnavailableReason.PARSE_ERROR, last_error)
                await self._backoff(attempt)
                continue

            if resp.status_code >= 400:
                raise Blocked(UnavailableReason.PARSE_ERROR, f"HTTP {resp.status_code}")

            return FetchResult(
                url=url,
                status=resp.status_code,
                text=resp.text,
                headers=dict(resp.headers),
                attempts=attempt,
                elapsed_s=loop.time() - started,
            )

        raise Blocked(UnavailableReason.PARSE_ERROR, last_error)

    async def _backoff(self, attempt: int, multiplier: float = 1.0) -> None:
        delay = self.base_backoff * multiplier * (2 ** (attempt - 1)) * random.uniform(0.8, 1.3)
        log.info("backoff attempt=%d sleeping %.1fs", attempt, delay)
        await asyncio.sleep(delay)
