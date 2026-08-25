"""What to do about the cells that didn't return a price.

The rule that must never be used here is carry-forward. If a stratum goes missing and
you repeat yesterday's price, you have asserted zero price change on no evidence. Do that
across a sold-out weekend and the series flattens exactly when the market is moving most —
the index understates inflation precisely in the periods it most needs to capture it.

Instead: a missing stratum is assumed to have moved the way its nearest observed
neighbours moved. That is the standard official-statistics answer (Eurostat calls it
imputation by the movement of the parent aggregate), and it is honest because it makes a
stated assumption rather than a silent one.

The donor hierarchy, most specific first:

  1. same route, other lead times, same day   — captures a route-specific shock
  2. same lead time, other routes, same day   — captures a lead-time-wide shock
  3. all observed strata that day             — the last resort
  4. nothing observed at all that day         — refuse; return None and let the caller
                                                publish a gap. An index value computed
                                                from no observations is not a number.

Every imputed cell is flagged with the tier used, and the share of imputed weight is
published next to the index value. A gap that is labelled is fine. A gap that is filled
quietly is fraud.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

log = logging.getLogger("apix.index.imputation")

TIER_OBSERVED = "observed"
TIER_ROUTE = "imputed_route_movement"
TIER_LEADTIME = "imputed_leadtime_movement"
TIER_ALL = "imputed_all_items_movement"
TIER_NONE = "no_donor"


@dataclass(frozen=True, slots=True)
class ImputationSummary:
    collection_date: str
    observed: int
    imputed_route: int
    imputed_leadtime: int
    imputed_all: int
    no_donor: int

    @property
    def total(self) -> int:
        return self.observed + self.imputed_route + self.imputed_leadtime + self.imputed_all + self.no_donor

    @property
    def observed_share(self) -> float:
        return self.observed / self.total if self.total else 0.0


def _geomean(values: pd.Series) -> float | None:
    v = values.dropna()
    v = v[v > 0]
    if v.empty:
        return None
    return float(np.exp(np.log(v).mean()))


def impute_relatives(relatives: pd.DataFrame) -> pd.DataFrame:
    """Fill missing stratum relatives by donor movement. Adds `relative_used` and `imputation_tier`."""
    if relatives.empty:
        return relatives.assign(relative_used=[], imputation_tier=[])

    out = relatives.copy()
    out["relative_used"] = out["relative"]
    out["imputation_tier"] = np.where(out["relative"].notna(), TIER_OBSERVED, TIER_NONE)

    for day, chunk in out.groupby("collection_date", sort=True):
        observed = chunk[chunk["relative"].notna()]
        if observed.empty:
            log.warning(
                "imputation date=%s no stratum was observed at all — publishing a gap, "
                "not a number", day,
            )
            continue

        all_items_move = _geomean(observed["relative"])

        for idx in chunk.index[chunk["relative"].isna()]:
            o = out.at[idx, "origin"]
            d = out.at[idx, "destination"]
            lt = out.at[idx, "lead_time_days"]

            route_donors = observed[(observed["origin"] == o) & (observed["destination"] == d)]
            lt_donors = observed[observed["lead_time_days"] == lt]

            route_move = _geomean(route_donors["relative"])
            lt_move = _geomean(lt_donors["relative"])

            if route_move is not None:
                out.at[idx, "relative_used"] = route_move
                out.at[idx, "imputation_tier"] = TIER_ROUTE
            elif lt_move is not None:
                out.at[idx, "relative_used"] = lt_move
                out.at[idx, "imputation_tier"] = TIER_LEADTIME
            elif all_items_move is not None:
                out.at[idx, "relative_used"] = all_items_move
                out.at[idx, "imputation_tier"] = TIER_ALL
            else:
                out.at[idx, "imputation_tier"] = TIER_NONE

    counts = out.groupby(["collection_date", "imputation_tier"]).size().unstack(fill_value=0)
    log.info("imputation tiers by day:\n%s", counts.to_string())
    return out


def summarise(imputed: pd.DataFrame) -> list[ImputationSummary]:
    summaries = []
    for day, chunk in imputed.groupby("collection_date", sort=True):
        t = chunk["imputation_tier"].value_counts()
        summaries.append(
            ImputationSummary(
                collection_date=str(day),
                observed=int(t.get(TIER_OBSERVED, 0)),
                imputed_route=int(t.get(TIER_ROUTE, 0)),
                imputed_leadtime=int(t.get(TIER_LEADTIME, 0)),
                imputed_all=int(t.get(TIER_ALL, 0)),
                no_donor=int(t.get(TIER_NONE, 0)),
            )
        )
    return summaries
