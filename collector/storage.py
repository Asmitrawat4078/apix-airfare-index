"""Writing the day down, twice.

Every run writes to Postgres *and* to a CSV committed into the repo. That is deliberate
redundancy, not indecision. The free-tier database is the weakest link in the system: if
it is paused, migrated, or lost, thirty days of a series that cannot be re-collected go
with it — you cannot go back and scrape what a fare was last Tuesday. The CSV in git costs
nothing, is diffable, and means the entire index is reproducible from a clone with no
database at all. It is also, incidentally, the cleanest possible audit trail for a judge:
`git log data/raw/` is the collection history.

If the database write fails, the CSV write still happens and the run is marked degraded
rather than failed. Losing the observation is the only unrecoverable outcome here.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .schema import Quote, UnavailableReason

log = logging.getLogger("apix.storage")

CSV_COLUMNS = [
    "run_id",
    "basket_version",
    "collection_ts_utc",
    "collection_date",
    "source",
    "url",
    "origin",
    "destination",
    "lead_time_days",
    "dep_date",
    "carrier",
    "flight_no",
    "dep_ts",
    "arr_ts",
    "stops",
    "fare_class",
    "base_fare",
    "taxes",
    "fees",
    "total_fare",
    "currency",
    "is_available",
    "is_cheapest_in_cell",
    "unavailable_reason",
]


@dataclass
class RunStats:
    run_id: str
    collection_date: str
    started_at_utc: datetime
    cells_expected: int
    cells_attempted: int = 0
    cells_available: int = 0
    quotes_written: int = 0
    per_source: dict[str, dict[str, int]] = field(default_factory=dict)
    db_ok: bool = False
    csv_path: str | None = None

    @property
    def availability_rate(self) -> float:
        return self.cells_available / self.cells_expected if self.cells_expected else 0.0

    def note(self, source: str, key: str, n: int = 1) -> None:
        self.per_source.setdefault(
            source,
            {
                "cells_attempted": 0,
                "cells_available": 0,
                "quotes_written": 0,
                "blocked_count": 0,
                "timeout_count": 0,
                "parse_error_count": 0,
                "sold_out_count": 0,
            },
        )
        self.per_source[source][key] = self.per_source[source].get(key, 0) + n


REASON_TO_STAT = {
    UnavailableReason.BLOCKED: "blocked_count",
    UnavailableReason.ROBOTS_DISALLOWED: "blocked_count",
    UnavailableReason.RATE_LIMITED: "blocked_count",
    UnavailableReason.TIMEOUT: "timeout_count",
    UnavailableReason.PARSE_ERROR: "parse_error_count",
    UnavailableReason.SOLD_OUT: "sold_out_count",
    UnavailableReason.NO_SERVICE: "sold_out_count",
}


def write_csv(quotes: list[Quote], collection_date: str, out_dir: Path) -> Path:
    """One CSV per collection day, appended if the day is re-run. Never rewritten."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{collection_date}.csv"
    exists = path.exists()

    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        for q in quotes:
            row = q.to_row()
            row["collection_date"] = collection_date
            writer.writerow({k: row.get(k) for k in CSV_COLUMNS})

    log.info("csv wrote %d quotes to %s", len(quotes), path)
    return path


def _conn():
    import psycopg

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL is not set")
    return psycopg.connect(dsn, connect_timeout=20)


def write_postgres(quotes: list[Quote], stats: RunStats, robots_decisions: list) -> bool:
    """Append quotes, the run record, per-source health and the robots audit trail.

    Returns False rather than raising: a database outage must not cost us the observation,
    and the CSV has already been written by the time this is called.
    """
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                insert into apix.collection_runs
                  (run_id, basket_version, started_at_utc, collection_date, scheduled_ist,
                   git_sha, runner, cells_expected, cells_attempted, cells_available,
                   quotes_written, availability_rate, status, finished_at_utc)
                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                on conflict (run_id) do update set
                  finished_at_utc = excluded.finished_at_utc,
                  cells_attempted = excluded.cells_attempted,
                  cells_available = excluded.cells_available,
                  quotes_written  = excluded.quotes_written,
                  availability_rate = excluded.availability_rate,
                  status = excluded.status
                """,
                (
                    stats.run_id,
                    1,
                    stats.started_at_utc,
                    stats.collection_date,
                    os.environ.get("APIX_SCHEDULED_IST"),
                    os.environ.get("GITHUB_SHA"),
                    os.environ.get("GITHUB_RUN_ID", "local"),
                    stats.cells_expected,
                    stats.cells_attempted,
                    stats.cells_available,
                    stats.quotes_written,
                    round(stats.availability_rate, 4),
                    "finished",
                    datetime.now(UTC),
                ),
            )

            rows = []
            for q in quotes:
                r = q.to_row()
                rows.append(
                    (
                        stats.run_id,
                        q.basket_version,
                        q.collection_ts_utc,
                        stats.collection_date,
                        q.source,
                        q.url,
                        q.origin,
                        q.destination,
                        q.lead_time_days,
                        q.dep_date,
                        q.carrier,
                        q.flight_no,
                        q.dep_ts,
                        q.arr_ts,
                        q.stops,
                        q.fare_class,
                        r["base_fare"],
                        r["taxes"],
                        r["fees"],
                        r["total_fare"],
                        q.currency,
                        q.is_available,
                        q.is_cheapest_in_cell,
                        q.unavailable_reason,
                        json.dumps(q.raw_payload, default=str),
                    )
                )
            if rows:
                cur.executemany(
                    """
                    insert into apix.raw_quotes
                      (run_id, basket_version, collection_ts_utc, collection_date, source, url,
                       origin, destination, lead_time_days, dep_date, carrier, flight_no,
                       dep_ts, arr_ts, stops, fare_class, base_fare, taxes, fees, total_fare,
                       currency, is_available, is_cheapest_in_cell, unavailable_reason, raw_payload)
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    rows,
                )

            for source, s in stats.per_source.items():
                cur.execute(
                    """
                    insert into apix.source_health
                      (run_id, source, cells_attempted, cells_available, quotes_written,
                       blocked_count, timeout_count, parse_error_count, sold_out_count)
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    on conflict (run_id, source) do nothing
                    """,
                    (
                        stats.run_id,
                        source,
                        s["cells_attempted"],
                        s["cells_available"],
                        s["quotes_written"],
                        s["blocked_count"],
                        s["timeout_count"],
                        s["parse_error_count"],
                        s["sold_out_count"],
                    ),
                )

            for d in robots_decisions:
                cur.execute(
                    """
                    insert into apix.robots_log
                      (run_id, checked_at_utc, domain, url, user_agent, allowed, reason, crawl_delay_s)
                    values (%s, to_timestamp(%s), %s,%s,%s,%s,%s,%s)
                    """,
                    (
                        stats.run_id,
                        d.checked_at,
                        d.domain,
                        d.url,
                        d.user_agent,
                        d.allowed,
                        d.reason,
                        d.crawl_delay,
                    ),
                )
        log.info("postgres wrote %d quotes for run %s", len(quotes), stats.run_id)
        return True
    except Exception as exc:  # noqa: BLE001
        log.error(
            "postgres write FAILED (%s) — the CSV in data/raw is now the only copy of "
            "today's observations. The run is degraded, not lost.",
            exc,
        )
        return False


def new_run_id() -> str:
    return str(uuid.uuid4())
