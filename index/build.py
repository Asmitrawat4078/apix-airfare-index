"""Build every published series, from raw quotes to versioned index values.

Reads from Postgres when DATABASE_URL is set, and otherwise from the CSVs committed in
data/raw/. The CSV path is not a fallback for emergencies — it is the reproducibility
story. Anyone who clones this repository can run

    python -m index.build --source csv --out data/index

and regenerate every published number from the raw observations, with no credentials and
no infrastructure. If the index cannot be reproduced from the repo alone, the index cannot
be audited, and an index that cannot be audited has no business near a CPI.

Writes are always new vintages. Nothing is ever updated in place; recomputing yesterday
because a late scrape arrived produces a second row with a later computed_at, and the
difference between them is a revision that can be queried.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from .aggregate import aggregate, build_weights, chain_strata, resample
from .elementary import collapse_to_items, jevons_relatives
from .hedonic import compare_to_headline
from .hedonic import fit as fit_hedonic
from .imputation import impute_relatives
from .imputation import summarise as summarise_imputation
from .multilateral import direct_index, drift_diagnostic, geks_jevons
from .scenarios import HEADLINE_SCENARIO, SCENARIOS, by_name

log = logging.getLogger("apix.index.build")

REPO = Path(__file__).resolve().parents[1]
RAW_DIR = REPO / "data" / "raw"
WEIGHTS_CSV = REPO / "data" / "route_weights.csv"

QUOTE_COLUMNS = [
    "collection_date",
    "source",
    "origin",
    "destination",
    "lead_time_days",
    "carrier",
    "flight_no",
    "dep_date",
    "dep_ts",
    "total_fare",
    "is_available",
    "unavailable_reason",
]


def git_sha() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:  # noqa: BLE001
        return os.environ.get("GITHUB_SHA")


def load_quotes_csv(raw_dir: Path = RAW_DIR) -> pd.DataFrame:
    files = sorted(raw_dir.glob("*.csv"))
    if not files:
        raise SystemExit(
            f"no collected data in {raw_dir}. The index has nothing to measure yet — "
            "this is the correct outcome before the collector has run, not an error to work around."
        )
    frames = []
    for f in files:
        df = pd.read_csv(f)
        if "collection_date" not in df.columns:
            df["collection_date"] = f.stem
        frames.append(df)
    out = pd.concat(frames, ignore_index=True)
    log.info(
        "loaded %d rows from %d daily CSVs (%s .. %s)",
        len(out),
        len(files),
        files[0].stem,
        files[-1].stem,
    )
    return out


def load_quotes_postgres() -> pd.DataFrame:
    import psycopg

    dsn = os.environ["DATABASE_URL"]
    with psycopg.connect(dsn, connect_timeout=20) as conn:
        return pd.read_sql(
            f"select {', '.join(QUOTE_COLUMNS)} from apix.raw_quotes order by collection_date",
            conn,
        )


SIMULATION_PREFIX = "SIMULATED_"


def reject_simulated(df: pd.DataFrame) -> pd.DataFrame:
    """Refuse to build a published series from fabricated rows.

    scripts/simulate.py exists so the pipeline can be exercised over a long horizon before
    enough real days exist. This is the guard that keeps its output out of anything
    anyone might mistake for a measurement. It raises rather than filtering, because
    silently dropping rows would let a half-simulated series through.
    """
    if (
        "is_simulated" in df.columns
        and df["is_simulated"].astype(str).str.lower().isin(["true", "1", "t", "yes"]).any()
    ):
        raise SystemExit(
            "REFUSED: simulated rows reached the index builder. Simulated data may be used "
            "only via --allow-simulation, which watermarks every output."
        )
    bad = df["source"].astype(str).str.startswith(SIMULATION_PREFIX)
    if bad.any():
        raise SystemExit(
            f"REFUSED: {int(bad.sum())} rows have a SIMULATED_ source. A published index is "
            "built from observations or it is not published."
        )
    return df


def normalise(quotes: pd.DataFrame) -> pd.DataFrame:
    df = quotes.copy()
    df["collection_date"] = df["collection_date"].astype(str)
    df["is_available"] = df["is_available"].astype(str).str.lower().isin(["true", "1", "t", "yes"])
    df["total_fare"] = pd.to_numeric(df["total_fare"], errors="coerce")
    df["lead_time_days"] = pd.to_numeric(df["lead_time_days"], errors="coerce").astype("Int64")
    for c in ("origin", "destination"):
        df[c] = df[c].astype(str).str.upper().str.strip()
    df["carrier"] = df["carrier"].fillna("UNKNOWN").astype(str)
    if "dep_ts" not in df.columns:
        df["dep_ts"] = pd.NaT
    return df.dropna(subset=["lead_time_days"])


def cell_availability(quotes: pd.DataFrame, cells_in_basket: int) -> pd.DataFrame:
    """Share of basket cells that returned a real price, per collection day.

    Published beside every index value. CLAUDE.md forbids reporting one without the other,
    because an index built from 40 of 120 cells and one built from 118 are not the same
    claim, and the reader cannot tell them apart from the number alone.
    """
    rows = []
    for day, chunk in quotes.groupby("collection_date", sort=True):
        priced = chunk[chunk["is_available"] & chunk["total_fare"].notna()]
        cells = priced.groupby(["origin", "destination", "lead_time_days"]).ngroups
        blocked = (
            chunk[
                chunk["unavailable_reason"].isin(
                    ["blocked", "robots_disallowed", "rate_limited", "timeout", "parse_error"]
                )
            ]
            .groupby(["origin", "destination", "lead_time_days"])
            .ngroups
        )
        sold_out = (
            chunk[chunk["unavailable_reason"].isin(["sold_out", "no_service"])]
            .groupby(["origin", "destination", "lead_time_days"])
            .ngroups
        )
        rows.append(
            {
                "collection_date": str(day),
                "cells_priced": cells,
                "cells_in_basket": cells_in_basket,
                "availability_rate": cells / cells_in_basket if cells_in_basket else 0.0,
                "cells_blocked": blocked,
                "cells_sold_out": sold_out,
            }
        )
    return pd.DataFrame(rows)


def build(quotes: pd.DataFrame, route_weights: pd.DataFrame, allow_simulation: bool = False) -> dict:
    if not allow_simulation:
        reject_simulated(quotes)
    else:
        log.warning("=" * 78)
        log.warning("SIMULATION MODE — every output of this run is a fixture, not a measurement.")
        log.warning("=" * 78)
    quotes = normalise(quotes)
    dates = sorted(quotes["collection_date"].unique())
    if len(dates) < 2:
        raise SystemExit(
            f"only {len(dates)} collection day(s) of data. A price index measures change "
            "between periods; with one observation there is no change to measure. This is "
            "the honest answer on day one, not a bug."
        )

    strata = quotes[["origin", "destination", "lead_time_days"]].drop_duplicates()
    items = collapse_to_items(quotes)
    relatives = jevons_relatives(items, collection_dates=dates, strata=strata)
    imputed = impute_relatives(relatives)
    chained = chain_strata(imputed, base_date=dates[0])

    n_cells = len(route_weights) * 5
    availability = cell_availability(quotes, n_cells)

    series: dict[str, pd.DataFrame] = {}
    all_points = []
    for scenario in SCENARIOS:
        weights = build_weights(route_weights, scenario)
        pts = aggregate(chained, weights, scenario.name, availability)
        df = pd.DataFrame([p.as_row() for p in pts])
        series[scenario.name] = df
        all_points.extend(p.as_row() for p in pts)

    headline = series[HEADLINE_SCENARIO]

    direct = direct_index(items, dates)
    geks = geks_jevons(items, dates, window=min(25, max(3, len(dates))))
    drift = drift_diagnostic(headline, geks, direct)
    log.info("chain-drift diagnostic: %s", drift.get("interpretation", drift))

    hedonic = fit_hedonic(
        quotes,
        route_weights=route_weights,
        lead_time_weights=by_name(HEADLINE_SCENARIO).weights,
    )
    hedonic_df = hedonic.series if hedonic else pd.DataFrame()
    comparison = (
        compare_to_headline(hedonic_df, headline)
        if hedonic is not None and not headline.empty
        else {"note": "hedonic model not identified yet — too few observations"}
    )

    band = (
        pd.DataFrame(all_points)
        .pivot_table(index="collection_date", columns="scenario", values="index_value")
        .assign(
            band_low=lambda d: d.min(axis=1),
            band_high=lambda d: d.max(axis=1),
        )
    )
    band["band_width_pts"] = (band["band_high"] - band["band_low"]).round(4)

    return {
        "dates": dates,
        "quotes": quotes,
        "relatives": imputed,
        "chained": chained,
        "availability": availability,
        "series": series,
        "headline": headline,
        "band": band.reset_index(),
        "direct": direct,
        "geks": geks,
        "drift": drift,
        "hedonic": hedonic,
        "hedonic_series": hedonic_df,
        "hedonic_vs_headline": comparison,
        "imputation": [
            dataclasses.asdict(s) | {"observed_share": round(s.observed_share, 4)}
            for s in summarise_imputation(imputed)
        ],
        "weekly": resample(pd.DataFrame(all_points), "W"),
        "monthly": resample(pd.DataFrame(all_points), "MS"),
    }


def write_outputs(result: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    computed_at = datetime.now(UTC).isoformat(timespec="seconds")
    sha = git_sha()

    for name, df in result["series"].items():
        df.assign(computed_at_utc=computed_at, code_git_sha=sha).to_csv(
            out_dir / f"index_{name}.csv", index=False
        )
    result["band"].to_csv(out_dir / "index_band.csv", index=False)
    result["availability"].to_csv(out_dir / "availability.csv", index=False)
    result["chained"].to_csv(out_dir / "stratum_index.csv", index=False)
    result["direct"].to_csv(out_dir / "index_direct.csv", index=False)
    result["geks"].to_csv(out_dir / "index_geks.csv", index=False)
    result["weekly"].to_csv(out_dir / "index_weekly.csv", index=False)
    result["monthly"].to_csv(out_dir / "index_monthly.csv", index=False)
    if not result["hedonic_series"].empty:
        result["hedonic_series"].assign(computed_at_utc=computed_at).to_csv(
            out_dir / "index_hedonic.csv", index=False
        )

    h = result["hedonic"]
    meta = {
        "computed_at_utc": computed_at,
        "code_git_sha": sha,
        "basket_version": 1,
        "collection_days": len(result["dates"]),
        "first_day": result["dates"][0],
        "last_day": result["dates"][-1],
        "headline_scenario": HEADLINE_SCENARIO,
        "latest": (result["headline"].iloc[-1].to_dict() if not result["headline"].empty else None),
        "band_latest": (result["band"].iloc[-1].to_dict() if not result["band"].empty else None),
        "hedonic": (
            {
                "formula": h.formula,
                "r_squared": round(h.r_squared, 4),
                "n_observations": h.n_observations,
                "diagnostics": h.diagnostics,
            }
            if h
            else None
        ),
        "hedonic_vs_headline": result["hedonic_vs_headline"],
        "chain_drift": result["drift"],
        "imputation_by_day": result["imputation"],
    }
    (out_dir / "index_meta.json").write_text(json.dumps(meta, indent=2, default=str))
    log.info("wrote index outputs to %s", out_dir)


def write_postgres_vintage(result: dict) -> None:
    """Append a new vintage of every published point. Never an update."""
    import psycopg

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        log.warning("DATABASE_URL not set — index values written to CSV only")
        return

    computed_at = datetime.now(UTC)
    sha = git_sha()
    rows = []
    for name, df in result["series"].items():
        for r in df.itertuples():
            rows.append(
                (
                    r.collection_date,
                    name,
                    "jevons_headline",
                    computed_at,
                    1,
                    sha,
                    r.index_value,
                    r.availability_rate,
                    r.observed_weight_share,
                    r.strata_contributing,
                    r.strata_in_basket,
                    None,
                    None,
                )
            )
    hed = result["hedonic_series"]
    if not hed.empty:
        for r in hed.itertuples():
            rows.append(
                (
                    r.collection_date,
                    "n/a",
                    "hedonic",
                    computed_at,
                    1,
                    sha,
                    r.hedonic_index,
                    None,
                    None,
                    None,
                    None,
                    r.ci_low,
                    r.ci_high,
                )
            )

    with psycopg.connect(dsn, connect_timeout=20) as conn, conn.cursor() as cur:
        cur.executemany(
            """
            insert into apix.index_values
              (collection_date, scenario, series, computed_at_utc, basket_version, code_git_sha,
               index_value, availability_rate, observed_weight_share, strata_contributing,
               strata_in_basket, ci_low, ci_high)
            values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict do nothing
            """,
            rows,
        )
    log.info("wrote %d index rows as vintage %s", len(rows), computed_at.isoformat())


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the APIx index")
    ap.add_argument("--source", choices=["csv", "postgres"], default="csv")
    ap.add_argument("--out", default="data/index")
    ap.add_argument("--no-db-write", action="store_true")
    ap.add_argument(
        "--allow-simulation",
        action="store_true",
        help="build from data/_simulation/ — outputs are watermarked and must "
        "never be published or shown without the watermark",
    )
    ap.add_argument("--raw-dir", default=None)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)-22s %(message)s")

    if args.allow_simulation:
        raw_dir = Path(args.raw_dir) if args.raw_dir else REPO / "data" / "_simulation"
        quotes = load_quotes_csv(raw_dir)
    elif args.source == "postgres":
        quotes = load_quotes_postgres()
    else:
        quotes = load_quotes_csv(Path(args.raw_dir) if args.raw_dir else RAW_DIR)

    route_weights = pd.read_csv(WEIGHTS_CSV)
    result = build(quotes, route_weights, allow_simulation=args.allow_simulation)
    out_dir = REPO / args.out
    write_outputs(result, out_dir)
    if args.allow_simulation:
        (out_dir / "_THIS_IS_SIMULATED_DATA.txt").write_text(
            "Every file in this directory was built from fabricated fares by "
            "scripts/simulate.py.\nThese numbers are a test fixture. They are not "
            "measurements and must never be presented as an index.\n"
        )
    if not args.no_db_write and not args.allow_simulation:
        write_postgres_vintage(result)

    head = result["headline"]
    band = result["band"]
    print(f"\n=== APIx headline (scenario: {HEADLINE_SCENARIO}) ===")
    print(head.tail(10).to_string(index=False))
    print("\n=== sensitivity band across lead-time scenarios ===")
    print(band.tail(10).to_string(index=False))
    print("\n=== hedonic vs headline ===")
    print(json.dumps(result["hedonic_vs_headline"], indent=2))
    print("\n=== chain-drift diagnostic (chained vs GEKS vs direct) ===")
    print(json.dumps(result["drift"], indent=2))


if __name__ == "__main__":
    main()
