"""Goibibo — OTA. Public search results page, no account.

Off by default. Goibibo and MakeMyTrip share bot protection that is known to be
aggressive; the correct response to that is to leave them off rather than to work
around it. Enable only if the probe reports a clean pass.
"""

from __future__ import annotations

from .base import Cell, Source, SourceSpec, harvest_offers_from_dom, harvest_offers_from_json, register


@register
class Goibibo(Source):
    spec = SourceSpec(
        name="goibibo",
        domain="www.goibibo.com",
        enabled=False,
        needs_browser=True,
        confidence="unverified",
        notes="Disabled pending a clean probe. If it walls us, that is the site's answer "
        "and we record it as blocked rather than evading it.",
    )

    def search_url(self, cell: Cell) -> str:
        d = cell.dep_date.strftime("%Y%m%d")
        return f"https://www.goibibo.com/flights/air-{cell.origin}-{cell.destination}-{d}--1-0-0-E-D/"

    def looks_like_fare_endpoint(self, url: str) -> bool:
        return any(p in url.lower() for p in ("/api", "search", "fare", "flight"))

    def extract_from_json(self, payload, cell: Cell) -> list[dict]:
        return harvest_offers_from_json(payload)

    def extract_from_dom(self, html: str, cell: Cell) -> list[dict]:
        return harvest_offers_from_dom(html)
