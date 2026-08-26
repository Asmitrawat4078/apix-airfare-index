"""Find the internal JSON fare endpoints, automatically, from the machine that will do the collecting.

The project brief says: open devtools, find the JSON endpoint the booking widget calls,
and use that instead of parsing HTML. This script does that step programmatically. It
drives a real browser to a real search results page and records every response that
looks like fare data — an XHR/fetch returning JSON that contains price-shaped keys and
plausible INR magnitudes.

Why it exists as a separate, committed script rather than a thing someone did once by
hand: booking sites re-platform. When a source breaks in November, re-running this tells
you what it moved to, instead of starting the archaeology from scratch.

It is also the honest way to answer "will this even work from a CI runner?", which is a
different question from "does it work on my laptop in India" — the runner is in a foreign
datacentre and some sites treat that very differently. Run it where collection will run.

    python scripts/probe_sources.py --out data/source_probe_report.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector.fetch import CONTACT_EMAIL, USER_AGENT  # noqa: E402
from collector.robots import RobotsGate  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
log = logging.getLogger("apix.probe")

UA = USER_AGENT.format(repo="apix", email=CONTACT_EMAIL)

# Keys that indicate a fare payload rather than analytics, config or content JSON.
FARE_KEY_PATTERN = re.compile(
    r"\b(fare|price|amount|totalFare|totalAmount|adultFare|baseFare|publishedFare|"
    r"grandTotal|displayPrice|tf|totalPrice|netAmount)\b",
    re.IGNORECASE,
)
FLIGHT_KEY_PATTERN = re.compile(
    r"\b(flight|segment|airline|carrier|flightNumber|depTime|departureTime|"
    r"arrivalTime|origin|destination|itinerar)\w*\b",
    re.IGNORECASE,
)
# Indian domestic economy one-way fares live roughly here. Used only to sanity-check
# that a numeric field is plausibly a fare, never to filter collected data.
PLAUSIBLE_FARE_RANGE = (1200, 90000)


@dataclass
class EndpointHit:
    url: str
    method: str
    status: int
    content_type: str
    body_bytes: int
    fare_key_hits: int
    flight_key_hits: int
    plausible_fare_values: list[float] = field(default_factory=list)
    sample_keys: list[str] = field(default_factory=list)
    request_post_data: str | None = None

    @property
    def score(self) -> float:
        """Rough confidence that this is *the* fare endpoint."""
        s = 0.0
        s += min(self.fare_key_hits, 20) * 1.0
        s += min(self.flight_key_hits, 20) * 1.5
        s += len(self.plausible_fare_values) * 2.0
        if self.body_bytes > 5000:
            s += 3
        return s


def json_skeleton(node, depth: int = 0, max_depth: int = 9):
    """A type map of a payload: same shape, no bulk.

    OTA fare responses run to megabytes of abbreviated keys. To write an extractor you need
    the *shape* — which key holds the airline, which holds the all-in fare, how deep the
    itinerary array sits — and none of the volume. So arrays collapse to their first element
    and scalars are replaced by their type plus one example value.

    Everything here is a public fare quote, so there is nothing sensitive to redact; the
    reason to summarise is that a 2 MB dump is unreadable, not that it is private.
    """
    if depth > max_depth:
        return "<max depth>"
    if isinstance(node, dict):
        return {k: json_skeleton(v, depth + 1, max_depth) for k, v in list(node.items())[:60]}
    if isinstance(node, list):
        if not node:
            return []
        return [json_skeleton(node[0], depth + 1, max_depth), f"<+{len(node) - 1} more>"]
    if isinstance(node, str):
        return f"str: {node[:40]!r}"
    if isinstance(node, bool):
        return f"bool: {node}"
    if node is None:
        return "null"
    return f"{type(node).__name__}: {node}"


@dataclass
class SourceProbe:
    name: str
    domain: str
    search_url: str
    robots_allowed: bool | None = None
    robots_reason: str = ""
    page_status: int | None = None
    page_title: str = ""
    bot_wall: bool = False
    error: str | None = None
    endpoints: list[EndpointHit] = field(default_factory=list)
    verdict: str = "not_probed"
    payload_schema: dict | None = None


def _walk_numbers(obj, out: list[float], depth: int = 0) -> None:
    if depth > 8 or len(out) > 60:
        return
    if isinstance(obj, dict):
        for v in obj.values():
            _walk_numbers(v, out, depth + 1)
    elif isinstance(obj, list):
        for v in obj[:40]:
            _walk_numbers(v, out, depth + 1)
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        if PLAUSIBLE_FARE_RANGE[0] <= obj <= PLAUSIBLE_FARE_RANGE[1]:
            out.append(float(obj))


def _top_keys(obj, out: set[str], depth: int = 0) -> None:
    if depth > 4 or len(out) > 40:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(str(k))
            _walk = _top_keys
            _walk(v, out, depth + 1)
    elif isinstance(obj, list) and obj:
        _top_keys(obj[0], out, depth + 1)


def build_targets(dep: date) -> list[SourceProbe]:
    """Candidate sources. Each is a *public search results page* — the same URL a
    traveller would land on. No accounts, no internal APIs guessed at, no deep links
    into anything that isn't linked from the site's own search form."""
    d_ddmmyyyy = dep.strftime("%d%m%Y")
    d_dd_mm_yyyy = dep.strftime("%d/%m/%Y")

    return [
        SourceProbe(
            "ixigo",
            "www.ixigo.com",
            f"https://www.ixigo.com/search/result/flight?from=DEL&to=BOM&date={dep.strftime('%d%m%Y')}"
            f"&adults=1&children=0&infants=0&class=e&source=Search%20Form",
        ),
        SourceProbe(
            "easemytrip",
            "flight.easemytrip.com",
            f"https://flight.easemytrip.com/FlightList/Index?srch=DEL-Delhi-India|BOM-Mumbai-India|{d_dd_mm_yyyy}"
            f"&px=1-0-0&cbn=0&ar=undefined&isow=true&isdm=true&lang=en-us&IsDoubleSeat=false&CCODE=IN&curr=INR",
        ),
        SourceProbe(
            "cleartrip",
            "www.cleartrip.com",
            f"https://www.cleartrip.com/flights/results?adults=1&childs=0&infants=0&class=Economy"
            f"&depart_date={dep.strftime('%d/%m/%Y')}&from=DEL&to=BOM&intl=n&sd=&sft=",
        ),
        SourceProbe(
            "yatra",
            "www.yatra.com",
            f"https://www.yatra.com/air-search-ui/dom2/trigger?type=O&viewName=normal&flight_depart_date={dep.strftime('%d/%m/%Y')}"
            f"&class=Economy&adults=1&childs=0&infants=0&origin=DEL&destination=BOM&flexi=0&ADT=1&CHD=0&INF=0",
        ),
        SourceProbe(
            "goibibo",
            "www.goibibo.com",
            f"https://www.goibibo.com/flights/air-DEL-BOM-{d_ddmmyyyy}--1-0-0-E-D/",
        ),
        SourceProbe(
            "makemytrip",
            "www.makemytrip.com",
            f"https://www.makemytrip.com/flight/search?itinerary=DEL-BOM-{dep.strftime('%d/%m/%Y')}"
            f"&tripType=O&paxType=A-1_C-0_I-0&intl=false&cabinClass=E",
        ),
        SourceProbe(
            "spicejet",
            "book.spicejet.com",
            "https://book.spicejet.com/",
        ),
        SourceProbe(
            "akasaair",
            "www.akasaair.com",
            "https://www.akasaair.com/",
        ),
    ]


async def probe_one(
    browser, probe: SourceProbe, gate: RobotsGate, settle_seconds: float, dump_schema: bool = False
) -> SourceProbe:
    decision = gate.check(probe.search_url)
    probe.robots_allowed = decision.allowed
    probe.robots_reason = decision.reason

    if not decision.allowed:
        probe.verdict = "robots_disallowed"
        log.warning("probe source=%s SKIPPED by robots.txt: %s", probe.name, decision.reason)
        return probe

    context = await browser.new_context(
        user_agent=UA,
        locale="en-IN",
        timezone_id="Asia/Kolkata",
        viewport={"width": 1440, "height": 900},
        extra_http_headers={"From": CONTACT_EMAIL},
    )
    page = await context.new_page()
    hits: dict[str, EndpointHit] = {}
    bodies: dict[str, object] = {}

    async def on_response(response):
        try:
            ctype = (response.headers or {}).get("content-type", "")
            if "json" not in ctype.lower():
                return
            body = await response.body()
            if not body or len(body) < 200:
                return
            text = body.decode("utf-8", errors="ignore")
            fare_hits = len(FARE_KEY_PATTERN.findall(text[:200_000]))
            flight_hits = len(FLIGHT_KEY_PATTERN.findall(text[:200_000]))
            if fare_hits < 2 or flight_hits < 2:
                return
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            values: list[float] = []
            keys: set[str] = set()
            if parsed is not None:
                _walk_numbers(parsed, values)
                _top_keys(parsed, keys)
            req = response.request
            hit = EndpointHit(
                url=response.url.split("?")[0],
                method=req.method,
                status=response.status,
                content_type=ctype.split(";")[0],
                body_bytes=len(body),
                fare_key_hits=fare_hits,
                flight_key_hits=flight_hits,
                plausible_fare_values=sorted(set(values))[:15],
                sample_keys=sorted(keys)[:25],
                request_post_data=(req.post_data or "")[:600] or None,
            )
            prev = hits.get(hit.url)
            if prev is None or hit.score > prev.score:
                hits[hit.url] = hit
                if parsed is not None:
                    bodies[hit.url] = parsed
        except Exception:  # noqa: BLE001 — a probe must never die on one odd response
            return

    page.on("response", lambda r: asyncio.create_task(on_response(r)))

    try:
        resp = await page.goto(probe.search_url, wait_until="domcontentloaded", timeout=60_000)
        probe.page_status = resp.status if resp else None
        await page.wait_for_timeout(int(settle_seconds * 1000))
        probe.page_title = (await page.title())[:120]
        content = (await page.content()).lower()
        probe.bot_wall = any(
            m in content[:8000]
            for m in ("captcha", "unusual traffic", "access denied", "checking your browser")
        )
    except Exception as exc:  # noqa: BLE001
        probe.error = f"{type(exc).__name__}: {exc}"[:300]
    finally:
        await context.close()

    probe.endpoints = sorted(hits.values(), key=lambda h: h.score, reverse=True)[:6]
    if dump_schema and probe.endpoints:
        top = probe.endpoints[0].url
        if top in bodies:
            probe.payload_schema = {
                "endpoint": top,
                "method": probe.endpoints[0].method,
                "skeleton": json_skeleton(bodies[top]),
            }

    if probe.bot_wall:
        probe.verdict = "bot_wall"
    elif probe.error:
        probe.verdict = "error"
    elif probe.endpoints and probe.endpoints[0].plausible_fare_values:
        probe.verdict = "json_endpoint_found"
    elif probe.endpoints:
        probe.verdict = "json_seen_no_fares"
    else:
        probe.verdict = "no_json_fare_endpoint"

    log.info(
        "probe source=%-12s verdict=%-22s status=%s endpoints=%d top=%s",
        probe.name,
        probe.verdict,
        probe.page_status,
        len(probe.endpoints),
        probe.endpoints[0].url if probe.endpoints else "-",
    )
    return probe


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/source_probe_report.json")
    ap.add_argument("--lead-days", type=int, default=15)
    ap.add_argument("--settle", type=float, default=12.0, help="seconds to let XHRs land")
    ap.add_argument("--only", help="comma-separated source names")
    ap.add_argument(
        "--dump-schema",
        action="store_true",
        help="write a type skeleton of the top endpoint's payload, so an "
        "extractor can be written against the real shape",
    )
    args = ap.parse_args()

    from playwright.async_api import async_playwright

    dep = date.today() + timedelta(days=args.lead_days)
    targets = build_targets(dep)
    if args.only:
        wanted = {s.strip() for s in args.only.split(",")}
        targets = [t for t in targets if t.name in wanted]

    gate = RobotsGate(UA)
    results: list[SourceProbe] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--disable-blink-features=AutomationControlled"])
        for t in targets:
            results.append(await probe_one(browser, t, gate, args.settle, args.dump_schema))
            await asyncio.sleep(6)  # politeness between domains
        await browser.close()

    report = {
        "probed_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "departure_date_probed": dep.isoformat(),
        "user_agent": UA,
        "runner": {"note": "run this where collection runs — geography changes the answer"},
        "sources": [asdict(r) for r in results],
        "robots_decisions": [d.as_log_row() for d in gate.decisions],
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str))

    # One file per source whose payload we captured. Separate from the report because the
    # report is a summary a human reads, and this is the reference an extractor is written
    # against — different audiences, different lifetimes.
    for r in results:
        if not r.payload_schema:
            continue
        schema_path = out.parent / f"probe_payload_schema_{r.name}.json"
        schema_path.write_text(json.dumps(r.payload_schema, indent=2, default=str))
        print(f"wrote payload skeleton: {schema_path}")

    print("\n=== SOURCE PROBE SUMMARY ===")
    for r in results:
        print(f"{r.name:14s} {r.verdict:24s} robots={r.robots_allowed} status={r.page_status}")
        for e in r.endpoints[:2]:
            print(f"    score={e.score:6.1f} {e.method} {e.url}")
            if e.plausible_fare_values:
                print(f"        fare-like values: {e.plausible_fare_values[:8]}")
    print(f"\nwritten: {out}")


if __name__ == "__main__":
    asyncio.run(main())
