"""Chaining and higher-level aggregation.

Two stages, in this order, and the order matters:

  1. Chain each stratum's daily Jevons relatives into a stratum index level,
     I_s(t) = I_s(t-1) * J_s(t), with I_s(0) = 100 on the first collection day.

  2. Aggregate stratum levels into published series with a weighted arithmetic mean,
     I(t) = sum_s w_s * I_s(t), where w_s = w_route(s) * w_leadtime(s).

Stage 2 is arithmetic on purpose, and it is not an inconsistency with the Jevons in
stage 1. This is standard CPI construction: the geometric mean is the right tool for
combining *unweighted, homogeneous* items inside an elementary aggregate, where you have
no expenditure information and want base-invariance. Above the elementary level you do
have weights — real ones, from DGCA — and a weighted arithmetic mean of index levels is
the Laspeyres-type aggregation that keeps the published number interpretable as
"the cost of the fixed basket, relative to day one". Eurostat's HICP works exactly this
way, and so does MoSPI's CPI.

A note on chaining and drift: chained daily indices can drift when prices bounce
(the "chain drift" problem, well known from scanner data). We report a direct
fixed-base index alongside the chained one as a diagnostic. If the two diverge
materially, that divergence is a finding to publish, not a bug to hide.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .imputation import TIER_OBSERVED
from .scenarios import LeadTimeScenario

log = logging.getLogger("apix.index.aggregate")

BASE_VALUE = 100.0
STRATUM_KEY = ["origin", "destination", "lead_time_days"]


def chain_strata(imputed: pd.DataFrame, base_date: str) -> pd.DataFrame:
    """Turn per-day relatives into per-stratum index levels, based at 100 on `base_date`."""
    if imputed.empty:
        raise ValueError("cannot chain an empty relatives frame")

    strata = imputed[STRATUM_KEY].drop_duplicates()
    dates = sorted(imputed["collection_date"].astype(str).unique())

    rows = []
    for _, s in strata.iterrows():
        o, d, lt = s["origin"], s["destination"], int(s["lead_time_days"])
        level = BASE_VALUE
        rows.append(
            {"origin": o, "destination": d, "lead_time_days": lt, "collection_date": base_date,
             "stratum_index": level, "imputation_tier": TIER_OBSERVED, "matched_items": np.nan}
        )
        sub = imputed[
            (imputed["origin"] == o) & (imputed["destination"] == d) & (imputed["lead_time_days"] == lt)
        ].set_index("collection_date")
        for day in dates:
            if day not in sub.index:
                continue
            r = sub.loc[day]
            rel = r["relative_used"]
            if pd.isna(rel):
                # No donor was available. The stratum's level is genuinely unknown today;
                # we hold the level but mark it, and the caller drops it from the weighted
                # mean rather than pretending it contributed.
                rows.append(
                    {"origin": o, "destination": d, "lead_time_days": lt, "collection_date": day,
                     "stratum_index": np.nan, "imputation_tier": r["imputation_tier"],
                     "matched_items": r["matched_items"]}
                )
                continue
            level = level * float(rel)
            rows.append(
                {"origin": o, "destination": d, "lead_time_days": lt, "collection_date": day,
                 "stratum_index": level, "imputation_tier": r["imputation_tier"],
                 "matched_items": r["matched_items"]}
            )
    return pd.DataFrame(rows)


@dataclass(frozen=True, slots=True)
class IndexPoint:
    collection_date: str
    scenario: str
    index_value: float
    availability_rate: float
    observed_weight_share: float
    strata_contributing: int
    strata_in_basket: int

    def as_row(self) -> dict:
        return {
            "collection_date": self.collection_date,
            "scenario": self.scenario,
            "index_value": round(self.index_value, 4),
            "availability_rate": round(self.availability_rate, 4),
            "observed_weight_share": round(self.observed_weight_share, 4),
            "strata_contributing": self.strata_contributing,
            "strata_in_basket": self.strata_in_basket,
        }


def build_weights(route_weights: pd.DataFrame, scenario: LeadTimeScenario) -> pd.DataFrame:
    """w_s = w_route * w_leadtime, over the full 120-cell basket. Sums to 1 by construction."""
    rows = []
    for r in route_weights.itertuples():
        for lt, wlt in scenario.weights.items():
            rows.append(
                {"origin": r.origin, "destination": r.destination, "lead_time_days": int(lt),
                 "weight": float(r.weight) * float(wlt)}
            )
    w = pd.DataFrame(rows)
    total = w["weight"].sum()
    if abs(total - 1.0) > 1e-8:
        raise ValueError(f"basket weights sum to {total}, not 1 — route weights are not normalised")
    return w


def aggregate(
    chained: pd.DataFrame,
    weights: pd.DataFrame,
    scenario_name: str,
    availability: pd.DataFrame | None = None,
) -> list[IndexPoint]:
    """Weighted arithmetic mean of stratum levels, renormalised over what was computable.

    Renormalisation matters. If 8 of 120 strata have no level today, dividing by the full
    basket weight would silently drag the index toward zero. We divide by the weight that
    actually contributed and publish that share, so a reader can see how much of the basket
    the number rests on.
    """
    merged = chained.merge(weights, on=STRATUM_KEY, how="left", validate="many_to_one")
    if merged["weight"].isna().any():
        orphan = merged[merged["weight"].isna()][STRATUM_KEY].drop_duplicates()
        raise ValueError(f"strata present in data but not in the frozen basket:\n{orphan}")

    n_basket = len(weights)
    points: list[IndexPoint] = []

    for day, chunk in merged.groupby("collection_date", sort=True):
        usable = chunk[chunk["stratum_index"].notna()]
        contributing_weight = float(usable["weight"].sum())
        if contributing_weight <= 0:
            log.warning("aggregate date=%s no stratum contributed — no index value published", day)
            continue

        value = float((usable["stratum_index"] * usable["weight"]).sum() / contributing_weight)

        observed_weight = float(usable[usable["imputation_tier"] == TIER_OBSERVED]["weight"].sum())

        if availability is not None and not availability.empty:
            a = availability[availability["collection_date"].astype(str) == str(day)]
            avail_rate = float(a["availability_rate"].iloc[0]) if len(a) else np.nan
        else:
            avail_rate = contributing_weight  # weight-based fallback when no cell census supplied

        points.append(
            IndexPoint(
                collection_date=str(day),
                scenario=scenario_name,
                index_value=value,
                availability_rate=avail_rate,
                observed_weight_share=observed_weight / contributing_weight,
                strata_contributing=len(usable),
                strata_in_basket=n_basket,
            )
        )
    return points


def resample(points: pd.DataFrame, freq: str) -> pd.DataFrame:
    """Weekly ('W') or monthly ('MS') aggregates. Geometric mean of daily levels within
    the period — the period average of a log-scale quantity, which is what a monthly
    figure derived from daily observations should be."""
    df = points.copy()
    df["collection_date"] = pd.to_datetime(df["collection_date"])
    out = (
        df.set_index("collection_date")
        .groupby("scenario")
        .resample(freq)
        .agg(
            index_value=("index_value", lambda s: float(np.exp(np.log(s).mean())) if len(s) else np.nan),
            availability_rate=("availability_rate", "mean"),
            observed_weight_share=("observed_weight_share", "mean"),
            days_in_period=("index_value", "size"),
        )
        .reset_index()
    )
    return out
