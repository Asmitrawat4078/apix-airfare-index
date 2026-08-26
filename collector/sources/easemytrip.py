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

import re
from datetime import datetime
from decimal import Decimal

from .base import Cell, Source, SourceSpec, harvest_offers_from_dom, harvest_offers_from_json, register

# --------------------------------------------------------------------------------------
# The payload, as observed on 2026-08-26
# --------------------------------------------------------------------------------------
#
# The response is ~2 MB with heavily abbreviated keys. Two fields carry everything the
# index needs, and both are positional strings rather than structured objects:
#
#   SD              "Non-Stop|6324|10|DEL-BOM||"
#                    stops-label | TOTAL FARE | seats left | route
#
#   segMatchingKey  "DELBOMThu-10Sep202604:0006:15SG 510"
#                    ORG DST dow-ddMonyyyy  dep   arr  carrier + flight number
#
# Parsing positional strings is uglier than reading named keys, and it is still the right
# call here: these two fields are what EaseMyTrip's own front-end uses to render the card a
# traveller clicks, so they are the least likely thing in the payload to be quietly
# restructured. The named per-fare-family breakdown sits several levels deeper under
# b[].dctFC and would give us base/tax/fee — worth having eventually, not worth blocking
# the collector's first real rows on.
#
# Fares parse to Decimal and never to float. Fractions of a rupee compound through a
# geometric mean in a direction nobody can predict.

SD_RE = re.compile(
    r"^(?P<stops_label>[^|]*)\|(?P<fare>\d+(?:\.\d+)?)\|(?P<seats>[^|]*)\|(?P<route>[^|]*)"
)

SEG_KEY_RE = re.compile(
    r"^(?P<org>[A-Z]{3})(?P<dst>[A-Z]{3})"
    r"(?P<dow>[A-Za-z]{3})-(?P<date>\d{1,2}[A-Za-z]{3}\d{4})"
    r"(?P<dep>\d{2}:\d{2})(?P<arr>\d{2}:\d{2})"
    r"\s*(?P<carrier>[A-Z0-9]{2})\s*(?P<flight>\d{1,4})$"
)

STOPS_FROM_LABEL = {"non-stop": 0, "nonstop": 0, "direct": 0}

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

    # ---- extraction -------------------------------------------------------------

    @staticmethod
    def _parse_seg_key(key: str) -> dict:
        """Unpack segMatchingKey into carrier, flight number and departure timestamp."""
        m = SEG_KEY_RE.match((key or "").strip())
        if not m:
            return {}
        out = {
            "carrier": m.group("carrier"),
            "flight_no": f"{m.group('carrier')}{m.group('flight')}",
            "origin": m.group("org"),
            "destination": m.group("dst"),
        }
        try:
            out["dep_ts"] = datetime.strptime(f"{m.group('date')} {m.group('dep')}", "%d%b%Y %H:%M")
        except ValueError:
            pass
        return out

    @staticmethod
    def _parse_sd(sd: str) -> dict:
        m = SD_RE.match((sd or "").strip())
        if not m:
            return {}
        label = m.group("stops_label").strip().lower()
        stops = STOPS_FROM_LABEL.get(label)
        if stops is None:
            digits = re.search(r"(\d+)", label)
            stops = int(digits.group(1)) if digits else None
        return {
            "total_fare": Decimal(m.group("fare")),
            "stops": stops,
            "seats_left": m.group("seats").strip() or None,
            "route": m.group("route").strip() or None,
        }

    def extract_from_json(self, payload, cell: Cell) -> list[dict]:
        """Read itineraries out of the AirAvail response.

        Falls back to the generic structural harvester if the shape has moved. That
        fallback is a safety net, not a plan: it logs nothing distinctive, so a silent
        switch to it would look like everything is fine while the data quietly degrades.
        `extraction` on every offer records which path produced it, so the health table
        can show the switch.
        """
        offers: list[dict] = []
        try:
            journeys = payload.get("j") or []
            convenience_fee = payload.get("CFee")
            for journey in journeys:
                for itin in journey.get("s") or []:
                    sd = self._parse_sd(itin.get("SD", ""))
                    if not sd.get("total_fare"):
                        continue
                    seg = self._parse_seg_key(itin.get("segMatchingKey", ""))

                    # Only keep itineraries actually on the cell we asked for. The payload
                    # can carry nearby-airport suggestions, and silently indexing BOM->DEL
                    # under a DEL->BOM cell would corrupt the stratum without ever erroring.
                    if seg.get("origin") and seg["origin"] != cell.origin:
                        continue
                    if seg.get("destination") and seg["destination"] != cell.destination:
                        continue

                    stops = sd.get("stops")
                    if stops is None:
                        first_bucket = (itin.get("b") or [{}])[0]
                        raw_stp = first_bucket.get("stp")
                        stops = int(raw_stp) if str(raw_stp).isdigit() else None

                    offers.append(
                        {
                            "total_fare": sd["total_fare"],
                            # base/taxes/fees are deliberately absent: this payload level gives
                            # only the all-in figure, and the data contract says store nulls
                            # rather than guess a split.
                            "carrier": seg.get("carrier"),
                            "flight_no": seg.get("flight_no"),
                            "dep_ts": seg.get("dep_ts"),
                            "stops": stops,
                            "raw": {
                                "extraction": "easemytrip_airavail",
                                "SD": itin.get("SD"),
                                "segMatchingKey": itin.get("segMatchingKey"),
                                "seats_left": sd.get("seats_left"),
                                "seat_availability": itin.get("SeatAv"),
                                "fare_type": itin.get("FareTypeUI"),
                                # Charged at payment rather than quoted in the fare, so it is
                                # recorded beside the quote and never folded into total_fare.
                                "convenience_fee_inr": convenience_fee,
                            },
                        }
                    )
        except (AttributeError, TypeError, ValueError):
            offers = []

        if offers:
            return offers
        return harvest_offers_from_json(payload)

    def extract_from_dom(self, html: str, cell: Cell) -> list[dict]:
        return harvest_offers_from_dom(html)
