"""Cleartrip — the India-only source.

Why this file exists twice over
------------------------------
Probed from a GitHub Actions runner in Azure US on 2026-08-25, Cleartrip's *robots.txt
itself* returned HTTP 403. Not the search page — the rulebook. We could not read their
crawl rules, so under the fail-closed rule in docs/scraping-policy.md we left the domain
alone entirely.

From a residential Indian IP the same site answers normally. That was established by a
working prototype the project owner built independently, which collected real DEL-BOM,
BOM-BLR and DEL-BLR fares from this exact URL pattern.

So Cleartrip is not blocked. It is *geo-conditional*, and the honest response is to collect
it from a runner where we can actually read and obey its rules. `enabled` stays False so
the cloud collector never touches it; the self-hosted Indian runner passes
`--sources cleartrip` explicitly. The robots gate then does its normal job on every request
and will refuse the domain if Cleartrip disallows our paths — which is the correct outcome
even though it would cost us the source.

What this deliberately does NOT inherit from the prototype
----------------------------------------------------------
The prototype was right about the site and wrong about three things, and all three would
have been found by a panel reading the code:

1. It fell back to recording the carrier as ``"IndiGo"`` whenever it could not identify the
   airline. That is a guess written down as a fact, and it is worse than useless here: the
   matched model compares *the same carrier's* price day over day, so a mislabelled fare
   corrupts two carriers at once and quietly biases the carrier fixed effect in the hedonic
   model. **We record `carrier=None` instead.** Downstream that becomes an explicit
   `UNKNOWN` item which matches honestly against tomorrow's `UNKNOWN`, and it is visible.
2. It sent a spoofed Chrome User-Agent. We identify ourselves; see collector/fetch.py.
3. It never read robots.txt. We read it before every request and log the decision.

The prototype's real contribution — the URL pattern, and the proof that an Indian IP is
what matters — is preserved here.
"""

from __future__ import annotations

import re

from .base import (
    Cell,
    Source,
    SourceSpec,
    harvest_offers_from_dom,
    harvest_offers_from_json,
    register,
)

# Carrier names as Cleartrip renders them. Order matters: "Air India Express" contains
# "Air India", so the longer name has to be tested first or every Express fare is filed
# under the wrong airline.
CARRIER_NAMES: list[tuple[str, str]] = [
    ("IX", "air india express"),
    ("AI", "air india"),
    ("6E", "indigo"),
    ("QP", "akasa"),
    ("SG", "spicejet"),
    ("UK", "vistara"),
]

PRICE_RE = re.compile(r"₹\s*([\d,]{3,10})")
MIN_PLAUSIBLE_INR = 1200
MAX_PLAUSIBLE_INR = 90_000


def carrier_from_text(text: str) -> str | None:
    """Map a rendered airline name to its IATA code, or None. Never a default."""
    low = (text or "").lower()
    for code, name in CARRIER_NAMES:
        if name in low:
            return code
    return None


@register
class Cleartrip(Source):
    spec = SourceSpec(
        name="cleartrip",
        domain="www.cleartrip.com",
        enabled=False,  # cloud runner must never touch this; the Indian runner opts in
        needs_browser=True,
        confidence="india_only",
        notes=(
            "Serves an Indian IP normally and returns HTTP 403 on robots.txt to a US "
            "datacentre. Collected only from the self-hosted Indian runner, which passes "
            "--sources cleartrip explicitly. The robots gate still applies per request."
        ),
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
        return any(p in u for p in ("/flights/results", "/air/search", "/fare", "/api/flights"))

    def extract_from_json(self, payload, cell: Cell) -> list[dict]:
        """Cleartrip's internal endpoint has not been characterised yet — the US probe never
        got past robots. Until a probe from India identifies it, fall back to the generic
        structural harvester, which finds offers by shape rather than by a guessed path."""
        return harvest_offers_from_json(payload)

    def extract_from_dom(self, html: str, cell: Cell) -> list[dict]:
        """Read the rendered results page, requiring airline and price to be co-located.

        The prototype scanned a flat `innerText.split('\\n')` and attributed each price to
        the most recently seen airline name. That works until the page interleaves anything
        between the two — a badge, a baggage note, an ad — and then it silently assigns the
        wrong carrier. Here we require both facts inside the *same* small DOM element, which
        is what a result row actually is, and emit `carrier=None` when only the price is
        present rather than inventing one.
        """
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        offers: list[dict] = []
        seen: set[tuple] = set()

        for node in soup.find_all(True):
            # Too many descendants means this is a container holding many results, not one
            # row, and any airline/price pairing inside it would be coincidental.
            if len(node.find_all(True)) > 25:
                continue
            text = " ".join(node.get_text(" ", strip=True).split())
            if not (12 <= len(text) <= 320):
                continue

            prices = []
            for m in PRICE_RE.finditer(text):
                try:
                    v = int(m.group(1).replace(",", ""))
                except ValueError:
                    continue
                if MIN_PLAUSIBLE_INR <= v <= MAX_PLAUSIBLE_INR:
                    prices.append(v)
            if not prices:
                continue

            carrier = carrier_from_text(text)
            fare = min(prices)
            key = (carrier, fare)
            if key in seen:
                continue
            seen.add(key)

            offers.append(
                {
                    "total_fare": fare,
                    "carrier": carrier,  # None when unidentifiable. Never a default.
                    "flight_no": None,
                    "raw": {
                        "extraction": "cleartrip_dom",
                        "text": text[:240],
                        "carrier_identified": carrier is not None,
                    },
                }
            )

        # Prefer rows where we actually know the airline. Unknown-carrier rows are kept —
        # they are real observations of a real fare — but they sort last so the cheapest
        # *identified* offer wins the matched-model slot where one exists.
        offers.sort(key=lambda o: (o["carrier"] is None, o["total_fare"]))
        return offers or harvest_offers_from_dom(html)
