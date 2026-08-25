"""The source interface.

A source knows three things: how to build the public search URL for a cell, how to pull
fares out of whatever comes back, and how confident we are that it currently works. It
knows nothing about the index, the database, or the basket. That separation is what lets
a source be swapped out in November — when MakeMyTrip re-platforms and the extractor
stops matching — without touching a line of statistical code.

Two extraction strategies, tried in order:

  1. JSON interception. The booking widget calls an internal endpoint that returns the
     fare list as structured data. This is by far the better path: stable field names,
     no layout coupling, no ambiguity about which number on the page is the price.
     `scripts/probe_sources.py` finds these endpoints automatically.

  2. Rendered-DOM extraction. Fall back to reading the page a human would read. Brittle
     by nature, so it is a fallback and it is loud about being one.

If both fail the cell is unavailable with reason `parse_error` — which is a *different*
thing from `sold_out`, and the difference shows up in the health page rather than being
quietly averaged into the index.
"""

from __future__ import annotations

import abc
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Iterable

from ..schema import Quote, UnavailableReason

log = logging.getLogger("apix.source")

# Carrier detection. Codes first (unambiguous), then names, then the airline's own
# flight-number prefixes. Anything unmatched stays None rather than being guessed —
# an unknown carrier is a usable observation for the hedonic model only if it is
# honestly labelled unknown.
CARRIER_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("6E", re.compile(r"\b(6E|indigo)\b", re.I)),
    ("AI", re.compile(r"\b(AI\d|air\s*india(?!\s*express))\b", re.I)),
    ("IX", re.compile(r"\b(IX\d|air\s*india\s*express)\b", re.I)),
    ("QP", re.compile(r"\b(QP\d|akasa)\b", re.I)),
    ("SG", re.compile(r"\b(SG\d|spice\s*jet|spicejet)\b", re.I)),
    ("UK", re.compile(r"\b(UK\d|vistara)\b", re.I)),
]

FLIGHT_NO_RE = re.compile(r"\b(6E|AI|IX|QP|SG|UK)[\s-]?(\d{2,4})\b", re.I)
# Indian price formatting: ₹4,999 / Rs. 4999 / INR 4,999
PRICE_RE = re.compile(r"(?:₹|rs\.?|inr)\s*([\d,]{3,10})", re.I)


KNOWN_CODES = {"6E", "AI", "IX", "QP", "SG", "UK"}
BARE_CODE_RE = re.compile(r"\b(6E|AI|IX|QP|SG|UK)\b")


def detect_carrier(text: str) -> str | None:
    """Identify the marketing carrier.

    Order matters and is load-bearing. "Air India Express" contains "Air India", so IX
    must be tested before AI or every Air India Express fare is silently filed under the
    wrong airline — which would corrupt the carrier fixed effect in the hedonic model and
    break the matched-model item key for two carriers at once.
    """
    t = text or ""
    # Names and code+number forms first: these are unambiguous.
    for code, pattern in CARRIER_PATTERNS:
        if pattern.search(t):
            return code
    # Then a bare two-letter code, e.g. an API field that just says "SG".
    m = BARE_CODE_RE.search(t.upper())
    return m.group(1) if m else None


def detect_flight_no(text: str) -> str | None:
    m = FLIGHT_NO_RE.search(text or "")
    return f"{m.group(1).upper()}{m.group(2)}" if m else None


def parse_inr(raw: Any) -> Decimal | None:
    """Parse an Indian fare into Decimal. Money is never a float in this codebase."""
    if raw is None:
        return None
    if isinstance(raw, Decimal):
        return raw
    if isinstance(raw, (int, float)):
        try:
            return Decimal(str(raw))
        except InvalidOperation:
            return None
    s = str(raw).strip()
    m = PRICE_RE.search(s)
    digits = (m.group(1) if m else s).replace(",", "").replace("₹", "").strip()
    if not re.fullmatch(r"\d+(\.\d+)?", digits):
        return None
    try:
        return Decimal(digits)
    except InvalidOperation:
        return None


@dataclass(frozen=True, slots=True)
class Cell:
    """One basket cell on one collection day."""

    origin: str
    destination: str
    lead_time_days: int
    dep_date: date

    @property
    def cell_id(self) -> str:
        return f"{self.origin}->{self.destination}/T+{self.lead_time_days}"


@dataclass
class SourceSpec:
    name: str
    domain: str
    enabled: bool
    needs_browser: bool
    confidence: str  # 'verified' | 'unverified' | 'retired'
    notes: str = ""


class Source(abc.ABC):
    """Subclass this, implement `search_url` and at least one extractor."""

    spec: SourceSpec

    @abc.abstractmethod
    def search_url(self, cell: Cell) -> str:
        """The public search results URL a traveller would land on for this cell."""

    def looks_like_fare_endpoint(self, url: str) -> bool:
        """Which intercepted JSON responses to keep. Override for a known endpoint path."""
        return False

    def extract_from_json(self, payload: Any, cell: Cell) -> list[dict]:
        """Pull raw offers out of an intercepted JSON payload. Return [] if not applicable."""
        return []

    def extract_from_dom(self, html: str, cell: Cell) -> list[dict]:
        """Fallback: read the rendered page. Return [] if not applicable."""
        return []

    # --- shared plumbing, not usually overridden ---

    def to_quotes(
        self,
        offers: Iterable[dict],
        cell: Cell,
        url: str,
        collected_at: datetime,
        run_id: str,
    ) -> list[Quote]:
        """Turn extracted offers into validated Quotes and mark the cheapest.

        `is_cheapest_in_cell` marks the single lowest fare across the whole cell. The
        elementary index does its own per-carrier cheapest selection; this flag exists so
        the dashboard and any future consumer can reproduce the headline offer definition
        straight from the raw table.
        """
        quotes: list[Quote] = []
        for o in offers:
            fare = parse_inr(o.get("total_fare"))
            if fare is None or fare <= 0:
                continue
            quotes.append(
                Quote(
                    collection_ts_utc=collected_at,
                    source=self.spec.name,
                    url=url,
                    origin=cell.origin,
                    destination=cell.destination,
                    lead_time_days=cell.lead_time_days,
                    dep_date=cell.dep_date.isoformat(),
                    is_available=True,
                    run_id=run_id,
                    carrier=o.get("carrier"),
                    flight_no=o.get("flight_no"),
                    dep_ts=o.get("dep_ts"),
                    arr_ts=o.get("arr_ts"),
                    stops=o.get("stops"),
                    base_fare=parse_inr(o.get("base_fare")),
                    taxes=parse_inr(o.get("taxes")),
                    fees=parse_inr(o.get("fees")),
                    total_fare=fare,
                    raw_payload=o.get("raw", {}),
                )
            )

        if quotes:
            cheapest = min(quotes, key=lambda q: q.total_fare)
            cheapest.is_cheapest_in_cell = True
        return quotes

    def unavailable(
        self, cell: Cell, url: str, reason: UnavailableReason, detail: str, run_id: str
    ) -> Quote:
        return Quote(
            collection_ts_utc=datetime.now(timezone.utc),
            source=self.spec.name,
            url=url,
            origin=cell.origin,
            destination=cell.destination,
            lead_time_days=cell.lead_time_days,
            dep_date=cell.dep_date.isoformat(),
            is_available=False,
            unavailable_reason=str(reason),
            run_id=run_id,
            raw_payload={"detail": detail[:1000]},
        )


REGISTRY: dict[str, type[Source]] = {}


def register(cls: type[Source]) -> type[Source]:
    REGISTRY[cls.spec.name] = cls
    return cls


def get_enabled(only: list[str] | None = None) -> list[Source]:
    """Instantiate the sources we intend to run today.

    `confidence` is not decoration. A source marked `unverified` has never been confirmed
    to work from the machine that will run it, and the runner logs that prominently so
    nobody mistakes 'we haven't checked' for 'it's broken' or, worse, the reverse.
    """
    out = []
    for name, cls in REGISTRY.items():
        if only and name not in only:
            continue
        s = cls()
        if not s.spec.enabled and not only:
            continue
        out.append(s)
    return out


# --------------------------------------------------------------------------------------
# Generic structural extraction
# --------------------------------------------------------------------------------------
#
# Every OTA returns a different JSON shape and all of them change it without warning.
# Hand-writing a path like payload["data"]["searchResult"]["tripInfos"]["ONWARD"][0]
# gives you a scraper that works beautifully until the Tuesday it doesn't, and then
# fails silently by returning an empty list — which, if you are not careful, looks
# exactly like "no flights available" and quietly biases the index.
#
# So instead of a path, we describe the *shape* of a flight offer: an object that carries
# something identifying an airline or flight, and something that is plausibly an INR fare.
# Walk the whole payload, keep every object matching that shape, take the deepest matches
# so we get individual offers rather than the container holding them. This survives
# renames, re-nesting and re-platforming, and when it does break it breaks loudly because
# the offer count drops to zero while the page still returns 200.

# Fare keys are tiered on purpose. A payload very often carries the base fare, the taxes,
# and the all-in total side by side. `total_fare` in our schema means "what a traveller
# would pay, all-in" — so picking the smallest number in sight would systematically
# understate every fare by the tax component, which on Indian domestic tickets is not a
# rounding error. Tier 1 keys unambiguously mean the total and win outright; tier 2 are
# generic and used only when no tier-1 key is present; base-fare keys are excluded outright.
PRICE_KEYS_TOTAL = (
    "totalfare", "totalprice", "totalamount", "totalfareamount", "grandtotal",
    "displayprice", "netamount", "publishedfare", "payableamount", "finalprice", "tf",
)
PRICE_KEYS_GENERIC = ("adultfare", "fare", "price", "amount")
PRICE_KEYS_EXCLUDED = ("basefare", "baseprice", "bf", "taxes", "tax", "taf", "surcharge",
                       "convenience", "discount", "markup", "commission")
PRICE_KEYS = PRICE_KEYS_TOTAL + PRICE_KEYS_GENERIC

AIRLINE_KEYS = (
    "airline", "carrier", "airlinecode", "airlinename", "flightnumber", "flightno",
    "flightcode", "marketingairline", "operatingairline", "fltno", "airlineid",
)
TIME_KEYS = ("departuretime", "deptime", "departure", "dt", "std", "arrivaltime", "arrtime")
STOPS_KEYS = ("stops", "stopcount", "numberofstops", "sc", "nostops")

MIN_PLAUSIBLE_INR = 1000
MAX_PLAUSIBLE_INR = 200_000


def _norm_key(k: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(k).lower())


def _best_number(obj: dict, keys: tuple[str, ...] | None = None) -> Decimal | None:
    """The all-in fare on this object, if it carries one.

    Tier-1 (unambiguously total) keys win. Only if none is present do we fall back to
    generic price keys. Base-fare and tax components are excluded so we can never mistake
    a component for the thing a traveller actually pays. Within a tier we take the
    smallest, which implements the "cheapest offer" half of the offer definition when a
    payload lists several fare families on one itinerary.
    """
    total: list[Decimal] = []
    generic: list[Decimal] = []

    for k, v in obj.items():
        nk = _norm_key(k)
        if any(nk == ex or nk.endswith(ex) for ex in PRICE_KEYS_EXCLUDED):
            continue
        val = parse_inr(v if not isinstance(v, dict) else (v.get("amount") or v.get("value")))
        if val is None or not (MIN_PLAUSIBLE_INR <= val <= MAX_PLAUSIBLE_INR):
            continue
        if any(nk == pk or nk.endswith(pk) for pk in PRICE_KEYS_TOTAL):
            total.append(val)
        elif any(nk == pk or nk.endswith(pk) for pk in PRICE_KEYS_GENERIC):
            generic.append(val)

    if total:
        return min(total)
    return min(generic) if generic else None


def _first_string(obj: dict, keys: tuple[str, ...]) -> str | None:
    for k, v in obj.items():
        nk = _norm_key(k)
        if any(nk == ak or nk.endswith(ak) for ak in keys):
            if isinstance(v, str) and v.strip():
                return v.strip()
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return str(v)
            if isinstance(v, dict):
                for vv in v.values():
                    if isinstance(vv, str) and vv.strip():
                        return vv.strip()
    return None


def _airline_hint(obj: dict) -> str | None:
    """Find an airline identifier on this object.

    Two passes, because OTAs abbreviate their JSON keys aggressively — MakeMyTrip's
    segment objects nest the airline under `sI[].fD.aI.code`, and no reasonable list of
    key names catches `aI`. So after checking known key names we simply look at the
    object's own string values for something that resolves to a carrier we track. A field
    whose value is literally "6E" or "IndiGo" is an airline field regardless of what it
    is called.
    """
    named = _first_string(obj, AIRLINE_KEYS)
    if named:
        return named
    for v in obj.values():
        if isinstance(v, str) and 1 < len(v) <= 40 and detect_carrier(v):
            return v.strip()
    return None


def _first_int(obj: dict, keys: tuple[str, ...]) -> int | None:
    for k, v in obj.items():
        nk = _norm_key(k)
        if any(nk == sk or nk.endswith(sk) for sk in keys):
            if isinstance(v, bool):
                continue
            if isinstance(v, int):
                return v
            if isinstance(v, str) and v.strip().isdigit():
                return int(v.strip())
            if isinstance(v, list):
                return max(len(v) - 1, 0)
    return None


def _subtree_signals(node: Any, depth: int, cache: dict) -> tuple[Decimal | None, str | None]:
    """(cheapest plausible fare, airline hint) found anywhere at or below this node."""
    key = id(node)
    if key in cache:
        return cache[key]
    if depth > 16:
        return (None, None)

    fare: Decimal | None = None
    airline: str | None = None

    if isinstance(node, dict):
        fare = _best_number(node, PRICE_KEYS)
        airline = _airline_hint(node)
        children = list(node.values())
    elif isinstance(node, list):
        children = node[:200]
    else:
        cache[key] = (None, None)
        return (None, None)

    for child in children:
        cf, ca = _subtree_signals(child, depth + 1, cache)
        if cf is not None and (fare is None or cf < fare):
            fare = cf
        if ca and not airline:
            airline = ca

    cache[key] = (fare, airline)
    return (fare, airline)


def harvest_offers_from_json(payload: Any, max_depth: int = 16) -> list[dict]:
    """Find flight offers anywhere in an arbitrary JSON payload, by shape rather than path.

    An "offer" is the *smallest* object whose subtree contains both a plausible INR fare
    and an airline identifier. Smallest matters: OTAs routinely split those two facts into
    sibling branches — the segment tree carries the airline, a parallel price tree carries
    the fare — so matching only on objects that hold both directly finds nothing on some of
    the biggest sites. Walking subtrees and then keeping the shallowest node whose children
    do not *individually* qualify gives one record per itinerary instead of one per page.
    """
    cache: dict = {}
    offers: list[dict] = []
    seen: set[int] = set()

    def qualifies(node: Any, depth: int) -> bool:
        f, a = _subtree_signals(node, depth, cache)
        return f is not None and a is not None

    def visit(node: Any, depth: int, ancestry: str) -> None:
        if depth > max_depth or len(offers) > 400:
            return

        if isinstance(node, list):
            for item in node[:200]:
                visit(item, depth + 1, ancestry)
            return
        if not isinstance(node, dict):
            return

        if qualifies(node, depth):
            # Descend only while a *child* is itself a complete offer. When no child is,
            # this node is the offer boundary.
            child_offers = [v for v in node.values() if qualifies(v, depth + 1)
                            or (isinstance(v, list) and any(qualifies(i, depth + 2) for i in v[:200]))]
            if not child_offers:
                ident = id(node)
                if ident not in seen:
                    seen.add(ident)
                    fare, airline_hint = _subtree_signals(node, depth, cache)
                    blob = f"{airline_hint or ''} {ancestry}"
                    flat = str(node)[:1500]
                    offers.append({
                        "total_fare": fare,
                        "carrier": detect_carrier(blob) or detect_carrier(flat),
                        "flight_no": detect_flight_no(blob) or detect_flight_no(flat),
                        "stops": _first_int(node, STOPS_KEYS),
                        "dep_raw": _first_string(node, TIME_KEYS),
                        "raw": {k: v for k, v in list(node.items())[:25]
                                if not isinstance(v, (dict, list))},
                    })
                return

        for k, v in node.items():
            visit(v, depth + 1, f"{ancestry}.{k}"[-200:])

    visit(payload, 0, "$")
    return offers


def harvest_offers_from_dom(html: str) -> list[dict]:
    """Last-resort extraction from rendered HTML.

    Deliberately conservative. It looks for blocks of text that contain BOTH a recognisable
    flight number and a rupee-formatted price, and pairs them within the same block. If a
    page's markup does not give us that co-location we return nothing and record a
    parse_error, rather than grabbing the largest number on the page and hoping.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    offers: list[dict] = []
    seen: set[tuple] = set()

    for node in soup.find_all(True):
        if len(node.find_all(True)) > 40:      # too coarse: a whole results container
            continue
        text = " ".join(node.get_text(" ", strip=True).split())
        if len(text) > 400 or len(text) < 10:
            continue
        flight_no = detect_flight_no(text)
        if not flight_no:
            continue
        prices = [parse_inr(m.group(0)) for m in PRICE_RE.finditer(text)]
        prices = [p for p in prices if p and MIN_PLAUSIBLE_INR <= p <= MAX_PLAUSIBLE_INR]
        if not prices:
            continue
        key = (flight_no, min(prices))
        if key in seen:
            continue
        seen.add(key)
        offers.append({
            "total_fare": min(prices),
            "carrier": detect_carrier(text) or detect_carrier(flight_no),
            "flight_no": flight_no,
            "raw": {"text": text[:300], "extraction": "dom_fallback"},
        })

    return offers
