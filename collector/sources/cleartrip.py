"""Cleartrip — OTA. Public search results page, no account."""

from __future__ import annotations

from .base import Cell, Source, SourceSpec, harvest_offers_from_dom, harvest_offers_from_json, register


@register
class Cleartrip(Source):
    spec = SourceSpec(
        name="cleartrip",
        domain="www.cleartrip.com",
        enabled=True,
        needs_browser=True,
        confidence="unverified",
        notes="Third reading. Kept disabled-by-default in the runner until the first two "
        "are boring — adding a flaky third source is a regression, not progress.",
    )

    def search_url(self, cell: Cell) -> str:
        dt = cell.dep_date.strftime("%d/%m/%Y")
        return (
            "https://www.cleartrip.com/flights/results"
            f"?adults=1&childs=0&infants=0&class=Economy"
            f"&depart_date={dt}&from={cell.origin}&to={cell.destination}&intl=n"
        )

    def looks_like_fare_endpoint(self, url: str) -> bool:
        u = url.lower()
        return any(p in u for p in ("/results", "/search", "/air", "/fare", "/api"))

    def extract_from_json(self, payload, cell: Cell) -> list[dict]:
        return harvest_offers_from_json(payload)

    def extract_from_dom(self, html: str, cell: Cell) -> list[dict]:
        return harvest_offers_from_dom(html)
