"""The daily collection run.

One pass over the basket, one browser context per cell, two extraction strategies, and a
row written for every cell whether or not it produced a price. That last part is the one
people skip: if a blocked cell writes nothing, the index cannot tell "we didn't look" from
"there was nothing to see", and the availability rate — our headline data-quality number —
becomes a lie by omission.

Structure of a run:

    for each cell in the basket (120 of them)
        for each enabled source
            check robots, rate-limit, open the search URL in a browser
            intercept JSON responses -> structural extraction     (preferred)
            if that yields nothing, read the rendered DOM          (fallback)
            if that yields nothing, decide whether it is sold out or we failed
            write quotes, or write one unavailable row with a reason

Everything is written twice: CSV into the repo, rows into Postgres. See storage.py for why.

    python -m collector.run --limit 5 --sources ixigo --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import load_basket
from .fetch import CONTACT_EMAIL, USER_AGENT
from .ratelimit import DomainRateLimiter
from .robots import RobotsGate
from .schema import Quote, UnavailableReason
from .sources import get_enabled
from .sources.base import Cell, Source
from .storage import REASON_TO_STAT, RunStats, new_run_id, write_csv, write_postgres

IST = ZoneInfo("Asia/Kolkata")
REPO = Path(__file__).resolve().parents[1]
RAW_DIR = REPO / "data" / "raw"

log = logging.getLogger("apix.run")

# Text that means "we looked and there is genuinely nothing to buy", as opposed to
# "something went wrong". Getting this distinction right is the difference between an
# honest availability rate and a decorative one.
SOLD_OUT_MARKERS = (
    "no flights found",
    "no flights available",
    "sold out",
    "no results",
    "we couldn't find any flights",
    "no direct flights",
    "0 flights",
    "try changing your search",
    "no matching flights",
)


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)-18s %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


async def collect_cell(
    browser,
    source: Source,
    cell: Cell,
    gate: RobotsGate,
    limiter: DomainRateLimiter,
    run_id: str,
    settle_s: float,
    stats: RunStats,
) -> list[Quote]:
    """Collect one (source, cell). Always returns at least one row."""
    url = source.search_url(cell)
    stats.note(source.spec.name, "cells_attempted")

    decision = gate.check(url)
    if not decision.allowed:
        log.warning("cell=%s source=%s robots disallowed — skipping", cell.cell_id, source.spec.name)
        return [
            source.unavailable(cell, url, UnavailableReason.ROBOTS_DISALLOWED, decision.reason, run_id)
        ]
    limiter.honour_crawl_delay(source.spec.domain, decision.crawl_delay)
    await limiter.acquire(source.spec.domain)

    collected_at = datetime.now(UTC)
    json_offers: list[dict] = []
    context = await browser.new_context(
        user_agent=USER_AGENT.format(repo="apix", email=CONTACT_EMAIL),
        locale="en-IN",
        timezone_id="Asia/Kolkata",
        viewport={"width": 1440, "height": 900},
        extra_http_headers={"From": CONTACT_EMAIL},
    )
    page = await context.new_page()

    async def on_response(response):
        try:
            if "json" not in (response.headers or {}).get("content-type", "").lower():
                return
            if not source.looks_like_fare_endpoint(response.url):
                return
            body = await response.body()
            if not body or len(body) < 200:
                return
            payload = json.loads(body.decode("utf-8", errors="ignore"))
            found = source.extract_from_json(payload, cell)
            if found:
                for f in found:
                    f.setdefault("raw", {})["endpoint"] = response.url.split("?")[0]
                json_offers.extend(found)
        except Exception:  # noqa: BLE001 — one odd response must not kill the cell
            return

    page.on("response", lambda r: asyncio.create_task(on_response(r)))

    html = ""
    status = None
    try:
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        status = resp.status if resp else None
        await page.wait_for_timeout(int(settle_s * 1000))
        html = await page.content()
    except Exception as exc:  # noqa: BLE001
        log.warning("cell=%s source=%s navigation failed: %s", cell.cell_id, source.spec.name, exc)
        await context.close()
        reason = UnavailableReason.TIMEOUT if "imeout" in str(exc) else UnavailableReason.PARSE_ERROR
        stats.note(source.spec.name, REASON_TO_STAT[reason])
        return [source.unavailable(cell, url, reason, str(exc)[:400], run_id)]
    finally:
        if not page.is_closed():
            await context.close()

    lowered = html[:20_000].lower()

    if any(
        m in lowered
        for m in ("captcha", "unusual traffic", "access denied", "checking your browser", "px-captcha")
    ):
        log.warning(
            "cell=%s source=%s bot wall — recording blocked, not working around it",
            cell.cell_id,
            source.spec.name,
        )
        stats.note(source.spec.name, "blocked_count")
        return [
            source.unavailable(
                cell, url, UnavailableReason.BLOCKED, f"challenge page at HTTP {status}", run_id
            )
        ]

    offers = json_offers or source.extract_from_dom(html, cell)
    strategy = "json" if json_offers else ("dom" if offers else "none")

    if not offers:
        if any(m in lowered for m in SOLD_OUT_MARKERS):
            log.info("cell=%s source=%s reports no flights — sold_out", cell.cell_id, source.spec.name)
            stats.note(source.spec.name, "sold_out_count")
            return [
                source.unavailable(
                    cell, url, UnavailableReason.SOLD_OUT, "page reported no available flights", run_id
                )
            ]
        log.warning(
            "cell=%s source=%s page loaded (HTTP %s) but nothing parsed — parse_error",
            cell.cell_id,
            source.spec.name,
            status,
        )
        stats.note(source.spec.name, "parse_error_count")
        return [
            source.unavailable(
                cell,
                url,
                UnavailableReason.PARSE_ERROR,
                f"HTTP {status}, {len(html)} bytes, no offers matched",
                run_id,
            )
        ]

    quotes = source.to_quotes(offers, cell, url, collected_at, run_id)
    if not quotes:
        stats.note(source.spec.name, "parse_error_count")
        return [
            source.unavailable(
                cell,
                url,
                UnavailableReason.PARSE_ERROR,
                "offers matched but none produced a valid fare",
                run_id,
            )
        ]

    stats.note(source.spec.name, "cells_available")
    stats.note(source.spec.name, "quotes_written", len(quotes))
    log.info(
        "cell=%s source=%s strategy=%s quotes=%d cheapest=%s",
        cell.cell_id,
        source.spec.name,
        strategy,
        len(quotes),
        min(q.total_fare for q in quotes),
    )
    return quotes


async def run(args: argparse.Namespace) -> int:
    basket = load_basket()
    now_ist = datetime.now(IST)
    collection_day = date.fromisoformat(args.date) if args.date else now_ist.date()

    only = [s.strip() for s in args.sources.split(",")] if args.sources else None
    sources = get_enabled(only)
    if not sources:
        log.error("no sources enabled")
        return 2

    cells = basket.cells_for(collection_day)
    if args.limit:
        cells = cells[: args.limit]

    run_id = new_run_id()
    stats = RunStats(
        run_id=run_id,
        collection_date=collection_day.isoformat(),
        started_at_utc=datetime.now(UTC),
        cells_expected=len(cells),
    )

    log.info("=" * 78)
    log.info("APIx collection run %s", run_id)
    log.info(
        "basket v%d | collection day %s IST | %d cells | sources: %s",
        basket.version,
        collection_day,
        len(cells),
        ", ".join(f"{s.spec.name}({s.spec.confidence})" for s in sources),
    )
    for s in sources:
        if s.spec.confidence != "verified":
            log.warning(
                "source %s is marked %s — it has not been confirmed working from "
                "this machine. Run scripts/probe_sources.py here.",
                s.spec.name,
                s.spec.confidence,
            )
    log.info("=" * 78)

    if args.jitter and basket.randomisation_window_minutes:
        delay = random.uniform(0, basket.randomisation_window_minutes * 60)
        log.info("randomising start by %.0fs within the published collection window", delay)
        await asyncio.sleep(delay)

    gate = RobotsGate(USER_AGENT.format(repo="apix", email=CONTACT_EMAIL))
    limiter = DomainRateLimiter(min_interval=args.min_interval)
    all_quotes: list[Quote] = []
    available_cells: set[str] = set()

    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(args=["--disable-blink-features=AutomationControlled"])
        try:
            for i, cell in enumerate(cells, 1):
                for source in sources:
                    quotes = await collect_cell(
                        browser, source, cell, gate, limiter, run_id, args.settle, stats
                    )
                    all_quotes.extend(quotes)
                    if any(q.is_available for q in quotes):
                        available_cells.add(cell.cell_id)
                if i % 10 == 0:
                    log.info(
                        "progress %d/%d cells, %d quotes, %d cells with a price",
                        i,
                        len(cells),
                        len(all_quotes),
                        len(available_cells),
                    )
        finally:
            await browser.close()

    stats.cells_attempted = len(cells)
    stats.cells_available = len(available_cells)
    stats.quotes_written = sum(1 for q in all_quotes if q.is_available)

    log.info("-" * 78)
    log.info(
        "run finished: %d/%d cells priced (availability %.1f%%), %d priced quotes",
        stats.cells_available,
        stats.cells_expected,
        stats.availability_rate * 100,
        stats.quotes_written,
    )
    for src, s in sorted(stats.per_source.items()):
        log.info(
            "  %-12s attempted=%-4d priced=%-4d blocked=%-3d timeout=%-3d parse_err=%-3d sold_out=%-3d",
            src,
            s["cells_attempted"],
            s["cells_available"],
            s["blocked_count"],
            s["timeout_count"],
            s["parse_error_count"],
            s["sold_out_count"],
        )

    if args.dry_run:
        log.info("dry run — nothing written")
        print(
            json.dumps(
                {"availability_rate": stats.availability_rate, "per_source": stats.per_source}, indent=2
            )
        )
        return 0

    csv_path = write_csv(all_quotes, stats.collection_date, RAW_DIR)
    stats.csv_path = str(csv_path)
    stats.db_ok = (
        write_postgres(all_quotes, stats, gate.decisions) if os.environ.get("DATABASE_URL") else False
    )
    if not os.environ.get("DATABASE_URL"):
        log.warning("DATABASE_URL not set — CSV only. The series is still complete in git.")

    health = REPO / "data" / "collection_health.json"
    history = json.loads(health.read_text()) if health.exists() else []
    history = [h for h in history if h.get("collection_date") != stats.collection_date]
    history.append(
        {
            "collection_date": stats.collection_date,
            "run_id": run_id,
            "started_at_utc": stats.started_at_utc.isoformat(),
            "cells_expected": stats.cells_expected,
            "cells_available": stats.cells_available,
            "availability_rate": round(stats.availability_rate, 4),
            "quotes_written": stats.quotes_written,
            "db_ok": stats.db_ok,
            "per_source": stats.per_source,
            "robots_checks": len(gate.decisions),
            "robots_disallowed": sum(1 for d in gate.decisions if not d.allowed),
        }
    )
    health.write_text(json.dumps(sorted(history, key=lambda h: h["collection_date"]), indent=2))

    # A run that priced almost nothing is a failure even though no exception was raised.
    # Exiting non-zero makes GitHub Actions show it red, which is the only way anyone
    # finds out before the series has a week-long hole in it.
    if stats.availability_rate < args.min_availability:
        log.error(
            "availability %.1f%% is below the %.0f%% floor — failing the run loudly",
            stats.availability_rate * 100,
            args.min_availability * 100,
        )
        return 1
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="APIx daily fare collection")
    ap.add_argument("--date", help="collection date (IST), defaults to today")
    ap.add_argument("--sources", help="comma-separated source names")
    ap.add_argument("--limit", type=int, help="only the first N cells (smoke testing)")
    ap.add_argument("--settle", type=float, default=12.0, help="seconds to let XHRs land")
    ap.add_argument(
        "--min-interval", type=float, default=4.0, help="seconds between requests per domain"
    )
    ap.add_argument(
        "--min-availability",
        type=float,
        default=0.0,
        help="fail the run below this availability rate (0-1)",
    )
    ap.add_argument("--jitter", action="store_true", help="randomise start within the window")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    _configure_logging(args.verbose)
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
