"""The daily collection run.

One pass over the basket, one page per cell, two extraction strategies, and a row written
for every cell whether or not it produced a price. That last part is the one people skip:
if a blocked cell writes nothing, the index cannot tell "we didn't look" from "there was
nothing to see", and the availability rate — our headline data-quality number — becomes a
lie by omission.

Designed around one fact: **you cannot go back and scrape what a fare was last Tuesday.**
Everything below follows from that.

  * **Every cell is checkpointed to disk the moment it completes.** A run that dies at cell
    ninety keeps ninety cells. The alternative — accumulate in memory, write at the end —
    means a crash at 95% costs the entire day, permanently.
  * **`--resume` skips cells already recorded today**, so re-running after a failure
    continues rather than starting over and re-hitting sites we have already asked.
  * **The browser is recycled every N cells.** A single Chromium driving 120 page loads
    over the better part of an hour accumulates memory until the runner kills it, which is
    exactly the "it works for a while then dies" failure.
  * **Every cell has a hard ceiling.** One page that hangs cannot consume the run's budget.
  * **In-flight response handlers are awaited before the page closes.** Reading a response
    body is itself async; closing the page out from under a handler throws "Target closed"
    and silently drops the fare payload we came for.

    python -m collector.run --limit 5 --sources easemytrip --dry-run -v
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import csv
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

BOT_WALL_MARKERS = (
    "captcha",
    "unusual traffic",
    "access denied",
    "checking your browser",
    "px-captcha",
)

CONTEXT_OPTIONS = {
    "locale": "en-IN",
    "timezone_id": "Asia/Kolkata",
    "viewport": {"width": 1440, "height": 900},
}


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)-18s %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


async def _new_context(browser):
    return await browser.new_context(
        user_agent=USER_AGENT.format(repo="apix", email=CONTACT_EMAIL),
        extra_http_headers={"From": CONTACT_EMAIL},
        **CONTEXT_OPTIONS,
    )


def already_collected(collection_date: str, raw_dir: Path = RAW_DIR) -> set[tuple[str, str, str, int]]:
    """(source, origin, destination, lead_time) pairs already written for this day.

    Read from the checkpoint CSV rather than from memory, because the whole point is to
    survive the process that held that memory. Re-running a failed collection then costs
    only the cells that are actually missing — which is both faster and politer, since we
    do not ask a site for something we already have.
    """
    path = raw_dir / f"{collection_date}.csv"
    if not path.exists():
        return set()
    done: set[tuple[str, str, str, int]] = set()
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                done.add((row["source"], row["origin"], row["destination"], int(row["lead_time_days"])))
            except (KeyError, ValueError, TypeError):
                continue
    return done


async def collect_cell(
    context,
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
    pending: set[asyncio.Task] = set()
    page = await context.new_page()

    async def on_response(response) -> None:
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
        except Exception:  # noqa: BLE001 — one odd response must never kill the cell
            return

    def _dispatch(response) -> None:
        # Tracked, not fire-and-forget. Reading a response body is itself async, so a
        # handler can still be mid-read when the page closes — and a page closed out from
        # under it throws "Target closed" and silently drops the payload we came for.
        task = asyncio.create_task(on_response(response))
        pending.add(task)
        task.add_done_callback(pending.discard)

    page.on("response", _dispatch)

    html = ""
    status = None
    failure: tuple[UnavailableReason, str] | None = None

    try:
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        status = resp.status if resp else None
        await page.wait_for_timeout(int(settle_s * 1000))
        html = await page.content()
    except Exception as exc:  # noqa: BLE001
        log.warning("cell=%s source=%s navigation failed: %s", cell.cell_id, source.spec.name, exc)
        failure = (
            UnavailableReason.TIMEOUT if "imeout" in str(exc) else UnavailableReason.PARSE_ERROR,
            str(exc)[:400],
        )
    finally:
        # Let in-flight handlers finish before the page goes away. Five seconds is generous
        # for a body read that has already been delivered to the browser.
        if pending:
            with contextlib.suppress(Exception):
                await asyncio.wait(set(pending), timeout=5)
        for task in list(pending):
            task.cancel()
        with contextlib.suppress(Exception):
            await page.close()

    if failure is not None:
        reason, detail = failure
        stats.note(source.spec.name, REASON_TO_STAT[reason])
        return [source.unavailable(cell, url, reason, detail, run_id)]

    lowered = html[:20_000].lower()

    if any(m in lowered for m in BOT_WALL_MARKERS):
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
            "cell=%s source=%s page loaded (HTTP %s, %d bytes) but nothing parsed — parse_error",
            cell.cell_id,
            source.spec.name,
            status,
            len(html),
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
    collection_date = collection_day.isoformat()

    only = [s.strip() for s in args.sources.split(",")] if args.sources else None
    sources = get_enabled(only)
    if not sources:
        log.error("no sources enabled")
        return 2

    cells = basket.cells_for(collection_day)
    if args.limit:
        cells = cells[: args.limit]

    done = already_collected(collection_date) if args.resume and not args.dry_run else set()

    run_id = new_run_id()
    stats = RunStats(
        run_id=run_id,
        collection_date=collection_date,
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
    if done:
        log.info("resuming: %d (source, cell) pairs already recorded today will be skipped", len(done))
    for s in sources:
        if s.spec.confidence != "verified":
            log.warning(
                "source %s is marked %s — it has not been confirmed working from this "
                "machine. Run scripts/probe_sources.py here.",
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
    skipped = 0

    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        launch = {"args": ["--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"]}
        browser = await pw.chromium.launch(**launch)
        context = await _new_context(browser)
        since_restart = 0

        try:
            for i, cell in enumerate(cells, 1):
                # Recycle the browser periodically. A single Chromium driving a hundred-plus
                # page loads over the better part of an hour grows until the runner kills it,
                # which is precisely the "collects for a while then dies" symptom.
                if since_restart >= args.restart_every:
                    log.info("recycling browser after %d cells to bound memory", since_restart)
                    with contextlib.suppress(Exception):
                        await context.close()
                    with contextlib.suppress(Exception):
                        await browser.close()
                    browser = await pw.chromium.launch(**launch)
                    context = await _new_context(browser)
                    since_restart = 0

                for source in sources:
                    key = (source.spec.name, cell.origin, cell.destination, cell.lead_time_days)
                    if key in done:
                        skipped += 1
                        continue

                    try:
                        quotes = await asyncio.wait_for(
                            collect_cell(
                                context, source, cell, gate, limiter, run_id, args.settle, stats
                            ),
                            timeout=args.cell_timeout,
                        )
                    except TimeoutError:
                        log.error(
                            "cell=%s source=%s exceeded the %.0fs ceiling — recording timeout",
                            cell.cell_id,
                            source.spec.name,
                            args.cell_timeout,
                        )
                        stats.note(source.spec.name, "timeout_count")
                        quotes = [
                            source.unavailable(
                                cell,
                                source.search_url(cell),
                                UnavailableReason.TIMEOUT,
                                f"cell exceeded {args.cell_timeout}s ceiling",
                                run_id,
                            )
                        ]
                    except Exception as exc:  # noqa: BLE001
                        log.error(
                            "cell=%s source=%s unexpected failure: %s",
                            cell.cell_id,
                            source.spec.name,
                            exc,
                        )
                        stats.note(source.spec.name, "parse_error_count")
                        quotes = [
                            source.unavailable(
                                cell,
                                source.search_url(cell),
                                UnavailableReason.PARSE_ERROR,
                                f"{type(exc).__name__}: {exc}"[:400],
                                run_id,
                            )
                        ]

                    all_quotes.extend(quotes)
                    if any(q.is_available for q in quotes):
                        available_cells.add(cell.cell_id)

                    # Checkpoint immediately. A run that dies at cell ninety keeps ninety
                    # cells; accumulating in memory and writing at the end would cost the
                    # whole day, and a day of fares cannot be re-collected.
                    if not args.dry_run:
                        write_csv(quotes, collection_date, RAW_DIR)

                since_restart += 1
                if i % 10 == 0:
                    log.info(
                        "progress %d/%d cells | %d quotes | %d cells priced | %d skipped (resume)",
                        i,
                        len(cells),
                        len(all_quotes),
                        len(available_cells),
                        skipped,
                    )
        finally:
            with contextlib.suppress(Exception):
                await context.close()
            with contextlib.suppress(Exception):
                await browser.close()

    stats.cells_attempted = len(cells)
    stats.cells_available = len(available_cells)
    stats.quotes_written = sum(1 for q in all_quotes if q.is_available)

    log.info("-" * 78)
    log.info(
        "run finished: %d/%d cells priced (availability %.1f%%), %d priced quotes, %d skipped",
        stats.cells_available,
        stats.cells_expected,
        stats.availability_rate * 100,
        stats.quotes_written,
        skipped,
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
                {"availability_rate": stats.availability_rate, "per_source": stats.per_source},
                indent=2,
            )
        )
        return 0

    stats.csv_path = str(RAW_DIR / f"{collection_date}.csv")
    stats.db_ok = (
        write_postgres(all_quotes, stats, gate.decisions) if os.environ.get("DATABASE_URL") else False
    )
    if not os.environ.get("DATABASE_URL"):
        log.warning("DATABASE_URL not set — CSV only. The series is still complete in git.")

    health = REPO / "data" / "collection_health.json"
    history = json.loads(health.read_text()) if health.exists() else []
    history = [h for h in history if h.get("collection_date") != collection_date]
    history.append(
        {
            "collection_date": collection_date,
            "run_id": run_id,
            "started_at_utc": stats.started_at_utc.isoformat(),
            "cells_expected": stats.cells_expected,
            "cells_available": stats.cells_available,
            "availability_rate": round(stats.availability_rate, 4),
            "quotes_written": stats.quotes_written,
            "cells_skipped_on_resume": skipped,
            "db_ok": stats.db_ok,
            "per_source": stats.per_source,
            "robots_checks": len(gate.decisions),
            "robots_disallowed": sum(1 for d in gate.decisions if not d.allowed),
        }
    )
    health.write_text(json.dumps(sorted(history, key=lambda h: h["collection_date"]), indent=2))

    # A run that priced almost nothing is a failure even though no exception was raised.
    # Exiting non-zero makes GitHub Actions show it red, which is the only way anyone finds
    # out before the series has a week-long hole in it.
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
        "--cell-timeout",
        type=float,
        default=120.0,
        help="hard ceiling per cell; one hung page must not consume the run",
    )
    ap.add_argument(
        "--restart-every",
        type=int,
        default=20,
        help="recycle the browser after this many cells, to bound memory growth",
    )
    ap.add_argument(
        "--resume",
        action="store_true",
        help="skip cells already recorded for this collection date",
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
