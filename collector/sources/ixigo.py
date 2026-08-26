"""ixigo — flight metasearch.

URL format is ixigo's own public search deep link: the one the site's search form
produces. No account, no API key, no private endpoint.

Probe result, 2026-08-25, from a GitHub Actions runner in Azure US:

    verdict      json_seen_no_fares
    robots.txt   HTTP 200, path allowed
    page         HTTP 200, title "Flight Results" — so the search itself worked
    endpoints    only /action/content, a 346-byte navigation config

The page rendered and ixigo accepted the request; we simply did not catch the fare payload
in the fifteen-second settle window. Being a metasearch, ixigo polls suppliers and streams
results in progressively, so fares can arrive well after DOMContentLoaded — often over a
long-poll or a stream rather than a single fat XHR. The `/api/flights/itinerary` and
`/flight-booking/flight-fare` paths that appeared in that nav config are the obvious things
to watch on a longer probe.

Left `unverified` and second in priority. It stays a valuable target because it aggregates
several OTAs, so its cheapest is close to the market minimum a consumer would actually
find — a genuinely different reading from a single direct OTA rather than the same feed
seen twice.
"""

from __future__ import annotations

from .base import Cell, Source, SourceSpec, harvest_offers_from_dom, harvest_offers_from_json, register


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
        return any(
            p in u
            for p in (
                "/api/flights/itinerary",
                "/flight-fare",
                "/search/result",
                "/api/v",
                "/flights/search",
            )
        )

    def extract_from_json(self, payload, cell: Cell) -> list[dict]:
        return harvest_offers_from_json(payload)

    def extract_from_dom(self, html: str, cell: Cell) -> list[dict]:
        return harvest_offers_from_dom(html)
