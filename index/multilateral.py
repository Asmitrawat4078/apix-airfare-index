"""Chain drift, and the multilateral fix.

A problem we found by building it
---------------------------------
Chaining a daily index is the obvious thing to do and it has a well-documented failure
mode. Each day's Jevons relative carries sampling noise; chaining multiplies those
relatives together, so the *noise* accumulates as a random walk even when the underlying
price level does not move. Over a long enough run the chained series can wander away from
the truth entirely. This is the "chain drift" problem, and it is the central reason
Eurostat, the ILO and every statistics office working with scanner or web-scraped data
have moved to multilateral index methods for high-frequency data.

We did not read this in a paper and add it defensively. We built the chained index first,
compared it against the hedonic time-dummy series on a long fixture, and found the two
disagreeing far more than they should. The chained series was accumulating noise; the
hedonic, being a single regression over the whole window, was not. That divergence is
the diagnostic that sent us here.

Three series, published together
--------------------------------
1. **Chained Jevons** — the headline. Matches how a CPI is normally compiled, revises
   cleanly, and every day's movement is attributable to that day's observations.
2. **Direct fixed-base Jevons** — the same elementary formula, but every day compared
   straight back to the base day instead of to yesterday. Immune to chain drift and
   trivially cheap; its weakness is the mirror image, since the matched sample against a
   base day 60 days ago shrinks as the market turns over.
3. **GEKS-Jevons on a rolling window** — the multilateral answer. Compute the bilateral
   Jevons index between *every* pair of days in a window, then take the geometric mean of
   all the indirect routes from day 0 to day t. Using every path rather than one makes the
   result transitive: it does not matter which day you route through, so there is nothing
   for drift to accumulate in. Windows are joined with a mean splice.

The gap between the chained series and the GEKS series *is* the drift estimate. We publish
it as a number rather than asserting that our chaining is fine.
"""

from __future__ import annotations

import itertools
import logging

import numpy as np
import pandas as pd

log = logging.getLogger("apix.index.multilateral")

ITEM_KEY = ["source", "origin", "destination", "lead_time_days", "carrier"]
BASE_VALUE = 100.0


def _bilateral_jevons(items: pd.DataFrame, day_a: str, day_b: str) -> float | None:
    """Matched-sample Jevons index of day_b relative to day_a. None if nothing matched."""
    a = items[items["collection_date"] == day_a]
    b = items[items["collection_date"] == day_b]
    if a.empty or b.empty:
        return None
    m = b.merge(a, on=ITEM_KEY, suffixes=("_b", "_a"), how="inner")
    if m.empty:
        return None
    return float(np.exp(np.log(m["total_fare_b"] / m["total_fare_a"]).mean()))


def direct_index(items: pd.DataFrame, dates: list[str]) -> pd.DataFrame:
    """Every day compared straight back to the base day. No chaining, so no chain drift."""
    base = dates[0]
    rows = [{"collection_date": base, "direct_index": BASE_VALUE, "matched_vs_base": np.nan}]
    for d in dates[1:]:
        rel = _bilateral_jevons(items, base, d)
        matched = len(
            items[items["collection_date"] == d].merge(
                items[items["collection_date"] == base], on=ITEM_KEY, how="inner"
            )
        )
        rows.append({
            "collection_date": d,
            "direct_index": BASE_VALUE * rel if rel is not None else np.nan,
            "matched_vs_base": matched,
        })
    return pd.DataFrame(rows)


def geks_jevons(items: pd.DataFrame, dates: list[str], window: int = 25) -> pd.DataFrame:
    """GEKS-Jevons with a rolling window and a mean splice.

    For a window of days D, the GEKS index of day t against base day 0 is

        I(0,t) = prod over k in D of [ P(k,t) / P(k,0) ] ^ (1/|D|)

    where P(k,t) is the bilateral Jevons index of day t relative to day k. Averaging over
    every possible link day k makes the result transitive — the answer no longer depends
    on which path through time you happened to take, which is precisely what chaining gets
    wrong. Missing bilateral pairs (no matched items between two particular days) are
    dropped from that geometric mean rather than treated as 1.0.

    Windows are joined by a mean splice: when the window rolls forward, the new window's
    movement is applied to the existing level using the geometric mean of the overlapping
    days' ratios, which spreads the join across the overlap instead of hinging it on one day.
    """
    if len(dates) < 2:
        return pd.DataFrame(columns=["collection_date", "geks_index"])

    window = max(3, min(window, len(dates)))
    items = items.copy()
    items["collection_date"] = items["collection_date"].astype(str)

    def geks_for(window_days: list[str]) -> dict[str, float]:
        n = len(window_days)
        # Cache every bilateral pair once: O(n^2) merges, and n is the window, not the series.
        pair: dict[tuple[str, str], float] = {}
        for a, b in itertools.permutations(window_days, 2):
            v = _bilateral_jevons(items, a, b)
            if v is not None and v > 0:
                pair[(a, b)] = v
        for d in window_days:
            pair[(d, d)] = 1.0

        base = window_days[0]
        out: dict[str, float] = {}
        for t in window_days:
            logs = []
            for k in window_days:
                p_kt, p_k0 = pair.get((k, t)), pair.get((k, base))
                if p_kt is None or p_k0 is None:
                    continue
                logs.append(np.log(p_kt / p_k0))
            out[t] = float(np.exp(np.mean(logs))) if logs else np.nan
        return out

    levels: dict[str, float] = {}
    first_window = dates[:window]
    for d, v in geks_for(first_window).items():
        levels[d] = BASE_VALUE * v

    # Roll forward one day at a time, mean-splicing each new window onto the existing level.
    for end in range(window + 1, len(dates) + 1):
        w = dates[end - window:end]
        g = geks_for(w)
        overlap = [d for d in w[:-1] if d in levels and not np.isnan(g.get(d, np.nan))]
        new_day = w[-1]
        if not overlap or np.isnan(g.get(new_day, np.nan)):
            levels[new_day] = np.nan
            continue
        ratios = [levels[d] / (BASE_VALUE * g[d]) for d in overlap if g[d] > 0]
        scale = float(np.exp(np.mean(np.log(ratios)))) if ratios else 1.0
        levels[new_day] = BASE_VALUE * g[new_day] * scale

    return pd.DataFrame(
        [{"collection_date": d, "geks_index": levels.get(d, np.nan)} for d in dates]
    )


def drift_diagnostic(chained: pd.DataFrame, geks: pd.DataFrame, direct: pd.DataFrame) -> dict:
    """How far has the chained headline drifted from the drift-free constructions?

    This number belongs in the published methodology. If it is small, the chained headline
    is safe and we have evidence rather than an assurance. If it grows with the length of
    the series — the signature of accumulated noise rather than a real divergence — that is
    the moment to make GEKS the headline, and we will be able to see it coming.
    """
    m = chained.merge(geks, on="collection_date", how="inner").merge(
        direct, on="collection_date", how="inner")
    m = m.dropna(subset=["index_value", "geks_index"])
    if len(m) < 3:
        return {"n": len(m), "note": "too few overlapping days to assess drift"}

    gap = m["index_value"] - m["geks_index"]
    days = np.arange(len(m))
    slope = float(np.polyfit(days, gap, 1)[0]) if len(m) > 2 else np.nan

    return {
        "n": int(len(m)),
        "final_chained": round(float(m["index_value"].iloc[-1]), 4),
        "final_geks": round(float(m["geks_index"].iloc[-1]), 4),
        "final_direct": (round(float(m["direct_index"].iloc[-1]), 4)
                         if not np.isnan(m["direct_index"].iloc[-1]) else None),
        "mean_abs_gap_pts": round(float(gap.abs().mean()), 4),
        "final_gap_pts": round(float(gap.iloc[-1]), 4),
        "gap_trend_pts_per_day": round(slope, 5),
        "interpretation": (
            "gap growing steadily with series length — consistent with chain drift; "
            "consider promoting GEKS to headline"
            if abs(slope) > 0.05 else
            "no systematic trend in the gap — chaining is behaving"
        ),
    }
