"""Back-test APIx against the official statistics it hopes to augment.

Runs three comparisons, and reports each honestly rather than reporting whichever one
happens to look best:

1. **Movement vs MoSPI's published CPI air-transport figures**, monthly. Correlation of
   month-on-month log changes, not of levels. Correlating levels of two trending series
   is the classic way to manufacture an impressive number out of nothing — both series
   drift upward, so they correlate at 0.9-something regardless of whether they agree
   about anything. Changes are the honest comparison.

2. **Level vs DGCA average-fare data** for the same sectors, where published. This checks
   something different and equally important: that our fares are the right *size*. An
   index can track movement perfectly while sitting at half the true level, which would
   mean the collector is systematically reading the wrong number off the page.

3. **Internal agreement** among the four constructions — chained Jevons, direct
   fixed-base, GEKS and hedonic. Cheap, always available, and it catches the failure that
   external benchmarks cannot: an index that is internally inconsistent is broken
   regardless of what it correlates with.

Nothing here is a pass/fail gate. These are diagnostics with interpretations attached, and
the interpretation for a disappointing result is written out in full rather than left for
someone to explain away in a slide.

    python scripts/backtest.py --index data/index --out data/backtest_report.json
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]

MIN_MONTHS_FOR_CPI = 3
MIN_DAYS_FOR_INTERNAL = 5


def _log_changes(s: pd.Series) -> pd.Series:
    return np.log(s.astype(float)).diff().dropna()


def compare_to_cpi(monthly: pd.DataFrame, cpi: pd.DataFrame) -> dict:
    """Month-on-month log changes, correlated. Levels are deliberately not used."""
    if cpi is None or cpi.empty:
        return {
            "available": False,
            "reason": "No CPI benchmark present. Run scripts/fetch_cpi_benchmark.py.",
        }

    m = monthly.copy()
    m["month"] = pd.to_datetime(m["collection_date"]).dt.to_period("M").dt.to_timestamp()
    m = m.groupby("month", as_index=False)["index_value"].mean()

    c = cpi.copy()
    c["month"] = pd.to_datetime(c["month"]).dt.to_period("M").dt.to_timestamp()

    j = m.merge(c, on="month", how="inner").sort_values("month")
    if len(j) < MIN_MONTHS_FOR_CPI:
        return {
            "available": False,
            "overlapping_months": len(j),
            "reason": (
                f"Only {len(j)} overlapping month(s). A correlation computed on fewer than "
                f"{MIN_MONTHS_FOR_CPI} points is noise with a decimal point, and reporting "
                "one would be worse than reporting nothing. Keep collecting."
            ),
        }

    apix_ch = _log_changes(j["index_value"])
    cpi_ch = _log_changes(j["cpi_index"])
    n = min(len(apix_ch), len(cpi_ch))
    r = float(np.corrcoef(apix_ch[:n], cpi_ch[:n])[0, 1])
    same_sign = float((np.sign(apix_ch[:n]) == np.sign(cpi_ch[:n])).mean())

    if r > 0.6:
        reading = (
            "Strong agreement in direction and magnitude. Supporting evidence that daily "
            "collection reproduces the official signal — not proof of correctness, since "
            "the two measure related but distinct things from different samples."
        )
    elif r > 0.2:
        reading = (
            "Moderate agreement. Consistent with the two series capturing a common airfare "
            "signal while differing on intra-month movement, which is the expected result "
            "if daily collection is picking up variation a monthly spot price cannot see."
        )
    else:
        reading = (
            "Weak or no agreement, and this needs explaining rather than burying. Candidate "
            "explanations, in the order they should be checked: (a) the benchmark exported "
            "is the Transport division rather than the air sub-class, which is dominated by "
            "fuel and moves for unrelated reasons; (b) too few months; (c) the basket's 12 "
            "pairs are not representative of what the CPI samples; (d) a genuine finding "
            "that monthly spot pricing misses most airfare movement. Only (d) is a result. "
            "Rule out (a) to (c) first."
        )

    return {
        "available": True,
        "overlapping_months": int(len(j)),
        "n_changes": int(n),
        "pearson_r_of_log_changes": round(r, 4),
        "direction_agreement": round(same_sign, 4),
        "method": "month-on-month log changes, not levels",
        "why_changes_not_levels": (
            "Two trending series correlate highly in levels whether or not they agree about "
            "anything. Changes are the comparison that can actually fail."
        ),
        "interpretation": reading,
    }


def compare_to_dgca_levels(quotes: pd.DataFrame, dgca_fares: pd.DataFrame | None) -> dict:
    """Are our fares the right size, not just the right shape?"""
    if dgca_fares is None or dgca_fares.empty:
        return {
            "available": False,
            "reason": (
                "No DGCA average-fare file supplied. This check is about level, not "
                "movement: an index can track perfectly while sitting at half the true "
                "level, which would mean the collector reads the wrong number off the page."
            ),
        }

    ours = (
        quotes[quotes["is_available"]]
        .groupby(["origin", "destination"], as_index=False)["total_fare"]
        .median()
        .rename(columns={"total_fare": "apix_median_fare"})
    )
    j = ours.merge(dgca_fares, on=["origin", "destination"], how="inner")
    if j.empty:
        return {"available": False, "reason": "No overlapping routes."}

    j["ratio"] = j["apix_median_fare"] / j["dgca_average_fare"]
    return {
        "available": True,
        "routes_compared": int(len(j)),
        "median_ratio_apix_to_dgca": round(float(j["ratio"].median()), 4),
        "note": (
            "A ratio meaningfully below 1 is expected and correct: we sample the *cheapest* "
            "economy offer, while a DGCA average includes every fare bucket sold. A ratio "
            "above 1, or below roughly 0.4, would indicate a collection problem."
        ),
        "per_route": j.to_dict("records"),
    }


def internal_agreement(index_dir: Path) -> dict:
    """Do our own four constructions agree with each other?"""

    def read(name: str, col: str) -> pd.DataFrame | None:
        p = index_dir / name
        if not p.exists():
            return None
        d = pd.read_csv(p)[["collection_date", col]]
        d["collection_date"] = pd.to_datetime(d["collection_date"])
        return d

    frames = {
        "chained": read("index_uniform.csv", "index_value"),
        "geks": read("index_geks.csv", "geks_index"),
        "direct": read("index_direct.csv", "direct_index"),
        "hedonic": read("index_hedonic.csv", "hedonic_index"),
    }
    present = {k: v for k, v in frames.items() if v is not None and not v.empty}
    if len(present) < 2:
        return {"available": False, "reason": "Fewer than two series built."}

    merged = None
    for name, df in present.items():
        df = df.rename(columns={df.columns[1]: name})
        merged = df if merged is None else merged.merge(df, on="collection_date", how="inner")

    merged = merged.dropna()
    if len(merged) < MIN_DAYS_FOR_INTERNAL:
        return {
            "available": False,
            "overlapping_days": int(len(merged)),
            "reason": f"Fewer than {MIN_DAYS_FOR_INTERNAL} overlapping days.",
        }

    cols = [c for c in merged.columns if c != "collection_date"]
    changes = merged[cols].apply(lambda s: np.log(s).diff())
    corr = changes.corr().round(4)

    pairs = {}
    for i, a in enumerate(cols):
        for b in cols[i + 1 :]:
            pairs[f"{a}_vs_{b}"] = {
                "corr_of_log_changes": float(corr.loc[a, b]),
                "mean_abs_level_gap_pts": round(float((merged[a] - merged[b]).abs().mean()), 4),
                "final_level_gap_pts": round(float(merged[a].iloc[-1] - merged[b].iloc[-1]), 4),
            }

    weakest = min(pairs.items(), key=lambda kv: kv[1]["corr_of_log_changes"]) if pairs else None

    return {
        "available": True,
        "overlapping_days": int(len(merged)),
        "series_compared": cols,
        "pairs": pairs,
        "weakest_pair": weakest[0] if weakest else None,
        "interpretation": (
            "Constructions built on different principles from overlapping but non-identical "
            "samples should agree on direction and diverge modestly in level. A persistent, "
            "growing level gap between the chained series and GEKS is chain drift, and is "
            "the signal to promote GEKS to headline. Divergence between the hedonic and the "
            "matched model is a statement about basket composition, not an error."
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default="data/index")
    ap.add_argument("--raw", default="data/raw")
    ap.add_argument("--dgca-fares", help="Optional CSV: origin,destination,dgca_average_fare")
    ap.add_argument("--out", default="data/backtest_report.json")
    args = ap.parse_args()

    index_dir = REPO / args.index
    headline_p = index_dir / "index_uniform.csv"
    if not headline_p.exists():
        raise SystemExit(
            f"No index at {headline_p}. Build it first:\n"
            "  python -m index.build --source csv --out data/index"
        )

    headline = pd.read_csv(headline_p)
    cpi_p = index_dir / "cpi_benchmark.csv"
    cpi = pd.read_csv(cpi_p) if cpi_p.exists() else None

    raw_dir = REPO / args.raw
    quote_files = sorted(raw_dir.glob("*.csv"))
    quotes = (
        pd.concat([pd.read_csv(f) for f in quote_files], ignore_index=True)
        if quote_files
        else pd.DataFrame(columns=["origin", "destination", "total_fare", "is_available"])
    )
    if not quotes.empty:
        quotes["is_available"] = (
            quotes["is_available"].astype(str).str.lower().isin(["true", "1", "t", "yes"])
        )
        quotes["total_fare"] = pd.to_numeric(quotes["total_fare"], errors="coerce")

    dgca = pd.read_csv(args.dgca_fares) if args.dgca_fares else None

    report = {
        "generated_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "collection_days": int(headline["collection_date"].nunique()),
        "vs_cpi_air_transport": compare_to_cpi(headline, cpi),
        "vs_dgca_fare_levels": compare_to_dgca_levels(quotes, dgca),
        "internal_agreement": internal_agreement(index_dir),
        "standing_caveat": (
            "None of these comparisons validates the index on its own. Agreement with the "
            "CPI is supporting evidence; disagreement is a question, not a failure. The "
            "checks that would actually falsify this index — parallel manual collection, "
            "and transaction-price data from airline MIS — are named in docs/limitations.md."
        ),
    }

    out = REPO / args.out
    out.write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report, indent=2, default=str))
    print(f"\nwritten: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
