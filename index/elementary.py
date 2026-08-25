"""Elementary aggregation: Jevons price relatives within a stratum.

A stratum is one (origin, destination, lead_time) cell. Within it we may see several
offers on a given day — different carriers, seen through different sources. The
elementary index compares *the same item* across two consecutive collection days and
takes the geometric mean of those matched relatives.

Why geometric (Jevons) rather than arithmetic (Dutot or Carli):

  - Dutot (ratio of mean prices) lets an expensive item dominate. On DEL-BOM at T+1 a
    ₹22,000 last-minute Air India fare and a ₹6,000 IndiGo fare are equally *informative*
    about price change, but Dutot weights the first nearly four times as heavily.
  - Carli (mean of relatives) fails the time-reversal test and has a known upward bias.
  - Jevons is invariant to the base period, symmetric in time, and — the reason Eurostat
    recommends it specifically for web-scraped data — it damps the downward bias that
    creeps in when observations go missing non-randomly, which for airfares they very much do.

What counts as "the same item":

    (source, origin, destination, lead_time_days, carrier)

Not the flight number. A carrier's cheapest economy seat on a route at a given lead time
is the product a traveller actually shops for; which specific departure that turns out to
be is the carrier's inventory decision, not a change in the thing being priced. Pinning to
flight number would shrink the matched sample to near nothing — flights get retimed and
renumbered constantly — and a matched model that matches nothing is just noise.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

log = logging.getLogger("apix.index.elementary")

ITEM_KEY = ["source", "origin", "destination", "lead_time_days", "carrier"]
STRATUM_KEY = ["origin", "destination", "lead_time_days"]


@dataclass(frozen=True, slots=True)
class StratumRelative:
    """One stratum's price movement between two consecutive collection days."""

    origin: str
    destination: str
    lead_time_days: int
    collection_date: str
    relative: float | None      # None when nothing matched — imputation decides what happens next
    matched_items: int
    items_this_period: int
    items_prev_period: int

    @property
    def stratum_id(self) -> str:
        return f"{self.origin}->{self.destination}/T+{self.lead_time_days}"


def collapse_to_items(quotes: pd.DataFrame) -> pd.DataFrame:
    """Reduce raw quotes to one price per item per collection day: the cheapest offer.

    The offer definition ("cheapest economy, 1 adult, one-way, non-stop where available,
    no baggage add-on, non-refundable") is enforced upstream by the collector. Here we
    only apply the *cheapest* part, within the item key, and only to available quotes.
    """
    required = set(ITEM_KEY) | {"collection_date", "total_fare", "is_available"}
    missing = required - set(quotes.columns)
    if missing:
        raise ValueError(f"quotes frame is missing columns: {sorted(missing)}")

    avail = quotes[quotes["is_available"] & quotes["total_fare"].notna()].copy()
    avail["total_fare"] = avail["total_fare"].astype(float)

    if (avail["total_fare"] <= 0).any():
        bad = avail[avail["total_fare"] <= 0]
        raise ValueError(
            f"{len(bad)} available quotes have non-positive fares; a log-based index "
            "cannot consume these and they must be caught by the data contract, not here"
        )

    items = (
        avail.sort_values("total_fare")
        .groupby(ITEM_KEY + ["collection_date"], as_index=False)
        .first()[ITEM_KEY + ["collection_date", "total_fare"]]
    )
    return items


EMPTY_RELATIVES = pd.DataFrame(
    columns=STRATUM_KEY + ["collection_date", "relative", "matched_items",
                           "items_this_period", "items_prev_period"]
)


def jevons_relatives(
    items: pd.DataFrame,
    collection_dates: list[str] | None = None,
    strata: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Matched-sample Jevons relative per stratum, for every consecutive day pair.

    Returns one row per (stratum, collection_date) from the second collection day onward.
    `relative` is None where the matched sample was empty — deliberately not 1.0, because
    "we saw no change" and "we saw nothing" are different facts and only one of them is true.

    `collection_dates` and `strata` should be passed from the *raw* quote table rather than
    inferred from `items`. A day on which every source was blocked contributes no priced
    items at all, and if the calendar were inferred from priced items that day would simply
    vanish from the series — no gap, no flag, nothing to explain. The series must show the
    hole. Same argument for strata: a cell that has been sold out for a week still belongs
    to the basket and still owes the reader an availability number.
    """
    items = items.copy()
    if not items.empty:
        items["collection_date"] = items["collection_date"].astype(str)

    if collection_dates is not None:
        dates = sorted({str(d) for d in collection_dates})
    elif not items.empty:
        dates = sorted(items["collection_date"].unique())
    else:
        dates = []

    if len(dates) < 2:
        return EMPTY_RELATIVES.copy()

    if items.empty:
        items = pd.DataFrame(columns=ITEM_KEY + ["collection_date", "total_fare"])

    rows: list[StratumRelative] = []
    for prev_d, this_d in zip(dates, dates[1:]):
        prev = items[items["collection_date"] == prev_d]
        cur = items[items["collection_date"] == this_d]

        merged = cur.merge(
            prev, on=ITEM_KEY, suffixes=("_t", "_p"), how="inner"
        )
        merged["log_rel"] = np.log(merged["total_fare_t"] / merged["total_fare_p"])

        if strata is not None:
            day_strata = {(r.origin, r.destination, int(r.lead_time_days)) for r in strata.itertuples()}
        else:
            day_strata = set(map(tuple, cur[STRATUM_KEY].drop_duplicates().to_numpy())) | set(
                map(tuple, prev[STRATUM_KEY].drop_duplicates().to_numpy())
            )

        for o, d, lt in sorted(day_strata):
            m = merged[
                (merged["origin"] == o) & (merged["destination"] == d) & (merged["lead_time_days"] == lt)
            ]
            n_cur = int(((cur["origin"] == o) & (cur["destination"] == d) & (cur["lead_time_days"] == lt)).sum())
            n_prev = int(((prev["origin"] == o) & (prev["destination"] == d) & (prev["lead_time_days"] == lt)).sum())

            if len(m) == 0:
                rel = None
            else:
                # Geometric mean == exp(mean of logs). Computed in logs for numerical
                # stability: a product of 300 relatives underflows long before the log sum does.
                rel = float(np.exp(m["log_rel"].mean()))

            rows.append(
                StratumRelative(o, d, int(lt), this_d, rel, len(m), n_cur, n_prev)
            )

    if not rows:
        return EMPTY_RELATIVES.copy()

    out = pd.DataFrame([
        {
            "origin": r.origin,
            "destination": r.destination,
            "lead_time_days": r.lead_time_days,
            "collection_date": r.collection_date,
            "relative": r.relative,
            "matched_items": r.matched_items,
            "items_this_period": r.items_this_period,
            "items_prev_period": r.items_prev_period,
        }
        for r in rows
    ])

    unmatched = out["relative"].isna().sum()
    if unmatched:
        log.info(
            "elementary: %d of %d stratum-days had no matched item pair and go to imputation",
            unmatched, len(out),
        )
    return out
