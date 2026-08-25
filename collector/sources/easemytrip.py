"""EaseMyTrip — OTA. Public search results page, no account."""

from __future__ import annotations

from .base import Cell, Source, SourceSpec, register, harvest_offers_from_json, harvest_offers_from_dom

CITY_NAMES = {
    "DEL": "Delhi", "BOM": "Mumbai", "BLR": "Bengaluru", "HYD": "Hyderabad",
    "CCU": "Kolkata", "MAA": "Chennai", "AMD": "Ahmedabad", "PNQ": "Pune",
    "GAU": "Guwahati", "GOI": "Goa", "COK": "Kochi", "JAI": "Jaipur",
}


@register
class EaseMyTrip(Source):
    spec = SourceSpec(
        name="easemytrip",
        domain="flight.easemytrip.com",
        enabled=True,
        needs_browser=True,
        confidence="unverified",
        notes="Direct OTA. Independent of the ixigo aggregation, so a genuine second "
              "reading rather than the same underlying feed seen twice.",
    )

    def search_url(self, cell: Cell) -> str:
        o, d = cell.origin, cell.destination
        on, dn = CITY_NAMES.get(o, o), CITY_NAMES.get(d, d)
        dt = cell.dep_date.strftime("%d/%m/%Y")
        return (
            "https://flight.easemytrip.com/FlightList/Index"
            f"?srch={o}-{on}-India|{d}-{dn}-India|{dt}"
            "&px=1-0-0&cbn=0&ar=undefined&isow=true&isdm=true"
            "&lang=en-us&IsDoubleSeat=false&CCODE=IN&curr=INR"
        )

    def looks_like_fare_endpoint(self, url: str) -> bool:
        u = url.lower()
        return any(p in u for p in ("flight", "search", "fare", "list", "api"))

    def extract_from_json(self, payload, cell: Cell) -> list[dict]:
        return harvest_offers_from_json(payload)

    def extract_from_dom(self, html: str, cell: Cell) -> list[dict]:
        return harvest_offers_from_dom(html)
