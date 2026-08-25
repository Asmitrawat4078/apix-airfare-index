"""robots.txt enforcement, with an audit trail.

Checking robots.txt is table stakes. *Logging every decision* is the part that
matters for a statistics agency: when the panel asks whether this is legal, the
answer is a table of every URL we considered, what robots.txt said, and what we
did about it. A claim you can query is worth more than a claim in a slide.

Fail-closed by design: if robots.txt cannot be fetched we do not scrape the domain.
Treating an unreachable robots.txt as permission is how well-intentioned scrapers
end up in incident reports.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

log = logging.getLogger("apix.robots")

CACHE_TTL_SECONDS = 3600


@dataclass(slots=True)
class RobotsDecision:
    url: str
    domain: str
    user_agent: str
    allowed: bool
    reason: str
    crawl_delay: float | None
    checked_at: float

    def as_log_row(self) -> dict:
        return {
            "url": self.url,
            "domain": self.domain,
            "user_agent": self.user_agent,
            "allowed": self.allowed,
            "reason": self.reason,
            "crawl_delay": self.crawl_delay,
            "checked_at": self.checked_at,
        }


class RobotsGate:
    """One instance per collection run. Caches per domain, records every decision."""

    def __init__(self, user_agent: str, timeout: float = 15.0) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self._parsers: dict[str, tuple[RobotFileParser | None, str, float]] = {}
        self.decisions: list[RobotsDecision] = []

    def _load(self, domain: str) -> tuple[RobotFileParser | None, str]:
        cached = self._parsers.get(domain)
        if cached and (time.time() - cached[2]) < CACHE_TTL_SECONDS:
            return cached[0], cached[1]

        robots_url = f"https://{domain}/robots.txt"
        try:
            resp = httpx.get(
                robots_url,
                headers={"User-Agent": self.user_agent},
                timeout=self.timeout,
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            note = f"robots.txt unreachable ({type(exc).__name__}) — failing closed"
            log.warning("robots domain=%s %s", domain, note)
            self._parsers[domain] = (None, note, time.time())
            return None, note

        if resp.status_code == 404:
            # No robots.txt is a positive statement under RFC 9309: unrestricted.
            parser = RobotFileParser()
            parser.parse([])
            note = "no robots.txt published (HTTP 404) — unrestricted per RFC 9309"
            self._parsers[domain] = (parser, note, time.time())
            return parser, note

        if resp.status_code >= 400:
            note = f"robots.txt returned HTTP {resp.status_code} — failing closed"
            log.warning("robots domain=%s %s", domain, note)
            self._parsers[domain] = (None, note, time.time())
            return None, note

        parser = RobotFileParser()
        parser.parse(resp.text.splitlines())
        note = f"robots.txt fetched (HTTP {resp.status_code}, {len(resp.text)} bytes)"
        self._parsers[domain] = (parser, note, time.time())
        return parser, note

    def check(self, url: str) -> RobotsDecision:
        domain = urlparse(url).netloc
        parser, note = self._load(domain)

        if parser is None:
            decision = RobotsDecision(url, domain, self.user_agent, False, note, None, time.time())
        else:
            allowed = parser.can_fetch(self.user_agent, url)
            delay = parser.crawl_delay(self.user_agent)
            decision = RobotsDecision(
                url,
                domain,
                self.user_agent,
                allowed,
                note + ("; path allowed" if allowed else "; path DISALLOWED"),
                float(delay) if delay else None,
                time.time(),
            )

        self.decisions.append(decision)
        log.info(
            "robots url=%s allowed=%s reason=%s crawl_delay=%s",
            url,
            decision.allowed,
            decision.reason,
            decision.crawl_delay,
        )
        return decision
