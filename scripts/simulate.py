"""SIMULATION HARNESS — NOT DATA. Read this before running it.

This script generates fabricated fares so the index pipeline can be exercised over a long
horizon before enough real days have been collected. It exists for exactly one reason:
chaining, revisions and the hedonic fit only reveal their bugs across dozens of periods,
and waiting thirty days to discover that the chaining is wrong is not an engineering plan.

Every safeguard below is deliberate, because a simulated fare that leaks into a published
series is the single worst thing that could happen to this project:

  1. It refuses to run unless APIX_ALLOW_SIMULATION=1 is set explicitly.
  2. It writes to data/_simulation/, never to data/raw/. The two are never read together.
  3. Every row's `source` is prefixed SIMULATED_ and carries is_simulated=true.
  4. data/_simulation/ is gitignored, so simulated rows cannot be committed.
  5. index.build refuses to consume any row whose source starts with SIMULATED_.

The generating process is a plain econometric toy — a route level, a lead-time curve that
steepens near departure, day-of-week effects, a random walk, and occasional sell-outs. It
is not a forecast, a model of Indian aviation, or evidence of anything. It is a fixture.
"""

from __future__ import annotations

import argparse
import csv
import os
import random
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collector.config import load_basket  # noqa: E402
from collector.storage import CSV_COLUMNS  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
SIM_DIR = REPO / "data" / "_simulation"

CARRIERS = ["6E", "AI", "IX", "QP", "SG"]
CARRIER_LEVEL = {"6E": 1.00, "AI": 1.22, "IX": 0.92, "QP": 0.97, "SG": 0.95}
LEAD_CURVE = {1: 1.95, 7: 1.35, 15: 1.12, 30: 1.00, 45: 0.96}
SELL_OUT_P = {1: 0.22, 7: 0.08, 15: 0.04, 30: 0.02, 45: 0.02}
BLOCK_P = 0.03


def guard() -> None:
    if os.environ.get("APIX_ALLOW_SIMULATION") != "1":
        raise SystemExit(
            "REFUSED. This script fabricates fares. Set APIX_ALLOW_SIMULATION=1 if you "
            "genuinely intend to generate a test fixture, and never point it at data/raw."
        )


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate a SIMULATED fixture for pipeline testing")
    ap.add_argument("--days", type=int, default=40)
    ap.add_argument("--end", default=date.today().isoformat())
    ap.add_argument("--seed", type=int, default=20260825)
    ap.add_argument("--sources", default="SIMULATED_a,SIMULATED_b")
    args = ap.parse_args()
    guard()

    rng = random.Random(args.seed)
    basket = load_basket()
    end = date.fromisoformat(args.end)
    days = [end - timedelta(days=i) for i in range(args.days - 1, -1, -1)]
    sources = args.sources.split(",")

    if SIM_DIR.exists():
        for f in SIM_DIR.glob("*.csv"):
            f.unlink()
    SIM_DIR.mkdir(parents=True, exist_ok=True)

    # A persistent per-route level that random-walks, so the index has real signal to track.
    level = {(o, d): rng.uniform(3800, 9200) for o, d, _ in basket.directed_routes}
    written = 0

    for day in days:
        for key in level:
            level[key] *= (1 + rng.gauss(0, 0.018))
        rows = []
        for origin, destination, _w in basket.directed_routes:
            for lt in basket.lead_times:
                dep = day + timedelta(days=lt)
                cell_sold_out = rng.random() < SELL_OUT_P[lt]
                for src in sources:
                    if rng.random() < BLOCK_P:
                        rows.append(_row(day, dep, src, origin, destination, lt, None, None,
                                         False, "blocked"))
                        continue
                    if cell_sold_out:
                        rows.append(_row(day, dep, src, origin, destination, lt, None, None,
                                         False, "sold_out"))
                        continue
                    dow_factor = 1.10 if dep.weekday() in (4, 6) else 1.0
                    for carrier in rng.sample(CARRIERS, rng.randint(2, 4)):
                        fare = (level[(origin, destination)] * LEAD_CURVE[lt]
                                * CARRIER_LEVEL[carrier] * dow_factor
                                * (1 + rng.gauss(0, 0.05)))
                        rows.append(_row(day, dep, src, origin, destination, lt, carrier,
                                         round(max(fare, 1300), 2), True, None))
        path = SIM_DIR / f"{day.isoformat()}.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS + ["is_simulated"])
            w.writeheader()
            w.writerows(rows)
        written += len(rows)

    print(f"WROTE {written} SIMULATED rows across {len(days)} days to {SIM_DIR}")
    print("These are NOT observations. They must never appear in data/raw/ or in a published series.")


def _row(day, dep, source, o, d, lt, carrier, fare, available, reason):
    return {
        "run_id": "00000000-0000-0000-0000-00000000sim", "basket_version": 1,
        "collection_ts_utc": datetime.combine(day, datetime.min.time(), timezone.utc).isoformat(),
        "collection_date": day.isoformat(), "source": source,
        "url": "simulated://not-a-real-observation", "origin": o, "destination": d,
        "lead_time_days": lt, "dep_date": dep.isoformat(), "carrier": carrier,
        "flight_no": f"{carrier}{1000 + lt}" if carrier else None,
        "dep_ts": f"{dep.isoformat()}T08:00:00+05:30", "arr_ts": None, "stops": 0,
        "fare_class": "economy", "base_fare": None, "taxes": None, "fees": None,
        "total_fare": fare, "currency": "INR", "is_available": available,
        "is_cheapest_in_cell": False, "unavailable_reason": reason, "is_simulated": True,
    }


if __name__ == "__main__":
    main()
