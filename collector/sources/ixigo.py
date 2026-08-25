"""ixigo — flight metasearch.

URL format is ixigo's own public search deep link: the one the site's search form
produces. No account, no API key, no private endpoint.

Confidence is `unverified` until scripts/probe_sources.py has been run from the machine
that will do the collecting and the report says otherwise. Marking it verified before
that would be exactly the kind of number-that-looks-finished this project refuses to print.
"""

from __future__ import annotations

from .base import Cell, Source, SourceSpec, register, harvest_offers_from_json, harvest_offers_from_dom


@register
class Ixigo(Source):
    spec = SourceSpec(
        name="ixigo",
        domain="www.ixigo.com",
        enabled=True,
        needs_browser=True,
        confidence="unverified",
        notes="Metasearch. Aggregates several OTAs, so its 'cheapest' is close to the "
              "market minimum a consumer would actually find.",
    )

    def search_url(self, cell: Cell) -> str:
        d = cell.dep_date.strftime("%d%m%Y")
        return (
            f"https://www.ixigo.com/search/result/flight"
            f"?from={cell.origin}&to={cell.destination}&date={d}"
            f"&adults=1&children=0&infants=0&class=e&source=Search%20Form"
        )

    def looks_like_fare_endpoint(self, url: str) -> bool:
        u = url.lower()
        return any(p in u for p in ("/search", "/flight", "/fare", "/result", "/api"))

    def extract_from_json(self, payload, cell: Cell) -> list[dict]:
        return harvest_offers_from_json(payload)

    def extract_from_dom(self, html: str, cell: Cell) -> list[dict]:
        return harvest_offers_from_dom(html)
