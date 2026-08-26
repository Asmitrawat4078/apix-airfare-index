"""EaseMyTrip — the verified source.

Probe result, 2026-08-25, from a GitHub Actions runner in Azure US:

    verdict          json_endpoint_found
    robots.txt       HTTP 200, path allowed, no crawl-delay
    page             HTTP 200, no bot wall
    fare endpoint    POST flightservice-node.easemytrip.com/AirAvail_Lights/AirBus_New
    payload          2.0 MB JSON, 307 fare-shaped keys, 144 flight-shaped keys
    fares observed   1535 ... 6218 INR on DEL-BOM at T+15

A deliberate decision about *how* we read that endpoint
------------------------------------------------------
The probe captured the request body, so we could POST to the fare endpoint directly and
skip the browser entirely. It would be faster and much cheaper.

We do not, and the reason is in our own scraping policy. That request carries a `TKN`
token and a per-session trace UUID issued by EaseMyTrip's own page. Synthesising those to
call the endpoint out of band would be reverse-engineering an internal API and working
around the flow the site intends — precisely the class of thing the policy rules out,
regardless of whether it happens to work.

So collection navigates to the public search results page, lets EaseMyTrip's own booking
widget call its own endpoint, and reads the response. Slower, entirely within policy, and
in practice more robust: there is no token expiry to chase and no request signature to keep
reverse-engineering when it changes.
"""

from __future__ import annotations

from .base import Cell, Source, SourceSpec, harvest_offers_from_dom, harvest_offers_from_json, register

CITY_NAMES = {
    "DEL": "Delhi",
    "BOM": "Mumbai",
    "BLR": "Bengaluru",
    "HYD": "Hyderabad",
    "CCU": "Kolkata",
    "MAA": "Chennai",
    "AMD": "Ahmedabad",
    "PNQ": "Pune",
    "GAU": "Guwahati",
    "GOI": "Goa",
    "COK": "Kochi",
    "JAI": "Jaipur",
}


@register
class EaseMyTrip(Source):
    spec = SourceSpec(
        name="easemytrip",
        domain="flight.easemytrip.com",
        enabled=True,
        needs_browser=True,
        confidence="verified",
        notes="Verified working from an Azure US runner on 2026-08-25: robots.txt allows "
        "the path, no bot wall, and the booking widget's own fare endpoint returns a 2 MB "
        "JSON payload we read by interception. Also a genuinely independent reading from "
        "metasearch, rather than the same underlying feed seen twice.",
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

    # The endpoint the probe found. Narrow on purpose: a broad matcher pulls in currency
    # converters and ad payloads, and every one of those is a chance for the structural
    # harvester to find a plausible-looking number that is not a fare.
    FARE_ENDPOINTS = ("/airavail_lights/", "/airavail", "/airbus_new")

    def looks_like_fare_endpoint(self, url: str) -> bool:
        u = url.lower()
        return any(p in u for p in self.FARE_ENDPOINTS)

    def extract_from_json(self, payload, cell: Cell) -> list[dict]:
        return harvest_offers_from_json(payload)

    def extract_from_dom(self, html: str, cell: Cell) -> list[dict]:
        return harvest_offers_from_dom(html)
