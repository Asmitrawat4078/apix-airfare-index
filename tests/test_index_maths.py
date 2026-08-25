"""The index maths, checked against a worked example computed by hand.

This is the one part of the codebase where a bug is both invisible and fatal. A broken
scraper announces itself — the rows stop arriving. A Jevons that quietly computes an
arithmetic mean produces a number that looks entirely reasonable and is wrong, every day,
forever. So the arithmetic below is written out longhand in the docstrings and the code
is required to reproduce it to 10 decimal places.

Worked example
--------------
Two strata, both at lead time T+7, over three collection days.

  A = DEL->BOM     route weight 0.6
  B = DEL->BLR     route weight 0.4

  Day 1 (base)   A: 6E 4000, AI 8000      B: 6E 3000, AI 6000
  Day 2          A: 6E 5000, AI 8000      B: 6E 3300, AI 5400
  Day 3          A: 6E 5500, AI 8000      B: sold out, nothing observed

Day 2 relatives
  A: relatives 5000/4000 = 1.25 and 8000/8000 = 1.00
     Jevons = (1.25 * 1.00)^(1/2) = sqrt(1.25)  = 1.1180339887498949
     (Dutot would be 13000/12000 = 1.0833..., Carli would be 1.125 — both wrong here,
      and both differ from Jevons in the direction the literature predicts.)
  B: relatives 3300/3000 = 1.10 and 5400/6000 = 0.90
     Jevons = (1.10 * 0.90)^(1/2) = sqrt(0.99)  = 0.9949874371066200

  Levels: A = 100 * sqrt(1.25) = 111.8033988749894900
          B = 100 * sqrt(0.99) =  99.4987437106620000
  Index  = 0.6 * 111.80339887498949 + 0.4 * 99.49874371066200
         = 67.0820393249936900 + 39.7994974842648000
         = 106.8815368092584900

Day 3 relatives
  A: relatives 5500/5000 = 1.10 and 8000/8000 = 1.00
     Jevons = sqrt(1.10) = 1.0488088481701516
  B: no matched pair at all. Donor hierarchy: no other lead time on DEL->BLR is observed,
     so tier 1 fails; tier 2 (same lead time T+7, other routes) gives A's movement.
     B is imputed at 1.0488088481701516 and flagged imputed_leadtime_movement.

  Levels: A = 100 * sqrt(1.25) * sqrt(1.10) = 100 * sqrt(1.375) = 117.2603939955857400
          B = 100 * sqrt(0.99) * sqrt(1.10) = 100 * sqrt(1.089) = 104.3551627855565200
  Index  = 0.6 * 117.26039399558574 + 0.4 * 104.35516278555652
         = 70.3562363973514500 + 41.7420651142226100
         = 112.0983015115740600
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from index.aggregate import BASE_VALUE, aggregate, build_weights, chain_strata
from index.elementary import collapse_to_items, jevons_relatives
from index.imputation import TIER_LEADTIME, impute_relatives
from index.scenarios import LEAD_TIMES, SCENARIOS, by_name

PLACES = 10


def _q(day, o, d, carrier, fare, available=True, source="probe"):
    return {
        "collection_date": day,
        "source": source,
        "origin": o,
        "destination": d,
        "lead_time_days": 7,
        "carrier": carrier,
        "total_fare": fare,
        "is_available": available,
    }


@pytest.fixture
def worked_example_quotes() -> pd.DataFrame:
    rows = [
        _q("2026-09-01", "DEL", "BOM", "6E", 4000),
        _q("2026-09-01", "DEL", "BOM", "AI", 8000),
        _q("2026-09-01", "DEL", "BLR", "6E", 3000),
        _q("2026-09-01", "DEL", "BLR", "AI", 6000),
        _q("2026-09-02", "DEL", "BOM", "6E", 5000),
        _q("2026-09-02", "DEL", "BOM", "AI", 8000),
        _q("2026-09-02", "DEL", "BLR", "6E", 3300),
        _q("2026-09-02", "DEL", "BLR", "AI", 5400),
        _q("2026-09-03", "DEL", "BOM", "6E", 5500),
        _q("2026-09-03", "DEL", "BOM", "AI", 8000),
    ]
    # Day 3: DEL->BLR is genuinely sold out. It must appear as an unavailable observation,
    # not as an absent row — the difference is the whole point of the availability rate.
    rows += [
        {**_q("2026-09-03", "DEL", "BLR", "6E", None, available=False), "total_fare": np.nan},
        {**_q("2026-09-03", "DEL", "BLR", "AI", None, available=False), "total_fare": np.nan},
    ]
    return pd.DataFrame(rows)


@pytest.fixture
def worked_weights() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"origin": "DEL", "destination": "BOM", "lead_time_days": 7, "weight": 0.6},
            {"origin": "DEL", "destination": "BLR", "lead_time_days": 7, "weight": 0.4},
        ]
    )


def test_jevons_is_the_geometric_mean_not_the_arithmetic_one(worked_example_quotes):
    items = collapse_to_items(worked_example_quotes)
    rel = jevons_relatives(items)

    a2 = rel[(rel.origin == "DEL") & (rel.destination == "BOM") & (rel.collection_date == "2026-09-02")]
    assert len(a2) == 1
    got = float(a2.relative.iloc[0])

    assert round(got, PLACES) == round(math.sqrt(1.25), PLACES)
    # And is materially different from the alternatives we rejected.
    dutot = (5000 + 8000) / (4000 + 8000)
    carli = (1.25 + 1.00) / 2
    assert abs(got - dutot) > 0.03
    assert abs(got - carli) > 0.006
    assert dutot < got < carli  # the ordering the index-number literature predicts


def test_second_stratum_relative_matches_hand_computation(worked_example_quotes):
    rel = jevons_relatives(collapse_to_items(worked_example_quotes))
    b2 = rel[(rel.destination == "BLR") & (rel.collection_date == "2026-09-02")]
    assert round(float(b2.relative.iloc[0]), PLACES) == round(math.sqrt(0.99), PLACES)
    assert int(b2.matched_items.iloc[0]) == 2


def test_jevons_passes_time_reversal():
    """J(t0->t1) * J(t1->t0) == 1. Carli famously fails this; if we ever regress to Carli
    by accident, this test is what catches it."""
    up = pd.DataFrame(
        [
            _q("d1", "DEL", "BOM", "6E", 4000),
            _q("d1", "DEL", "BOM", "AI", 8000),
            _q("d2", "DEL", "BOM", "6E", 5000),
            _q("d2", "DEL", "BOM", "AI", 8000),
        ]
    )
    down = up.copy()
    down["collection_date"] = down["collection_date"].map({"d1": "d2", "d2": "d1"})

    f = float(jevons_relatives(collapse_to_items(up)).relative.iloc[0])
    b = float(jevons_relatives(collapse_to_items(down)).relative.iloc[0])
    assert round(f * b, PLACES) == 1.0


def test_cheapest_offer_wins_within_an_item(worked_example_quotes):
    """Two sources quoting the same carrier/route/lead-time collapse to the cheaper one."""
    extra = pd.concat(
        [
            worked_example_quotes,
            pd.DataFrame([_q("2026-09-01", "DEL", "BOM", "6E", 3500, source="probe")]),
        ]
    )
    items = collapse_to_items(extra)
    row = items[
        (items.collection_date == "2026-09-01") & (items.carrier == "6E") & (items.destination == "BOM")
    ]
    assert len(row) == 1
    assert float(row.total_fare.iloc[0]) == 3500.0


def test_missing_stratum_is_imputed_by_lead_time_donors_not_carried_forward(worked_example_quotes):
    rel = jevons_relatives(collapse_to_items(worked_example_quotes))
    imp = impute_relatives(rel)

    b3 = imp[(imp.destination == "BLR") & (imp.collection_date == "2026-09-03")]
    assert len(b3) == 1
    assert pd.isna(b3.relative.iloc[0]), "day 3 DEL->BLR must have no *observed* relative"
    assert b3.imputation_tier.iloc[0] == TIER_LEADTIME
    # Carry-forward would have produced exactly 1.0. It must not.
    assert round(float(b3.relative_used.iloc[0]), PLACES) == round(math.sqrt(1.10), PLACES)
    assert float(b3.relative_used.iloc[0]) != 1.0


def test_full_chain_and_aggregate_matches_hand_computation(worked_example_quotes, worked_weights):
    rel = jevons_relatives(collapse_to_items(worked_example_quotes))
    imp = impute_relatives(rel)
    chained = chain_strata(imp, base_date="2026-09-01")
    # Compare at full precision. `as_row()` rounds for publication; the arithmetic itself
    # must be exact, so the test reads the IndexPoint rather than its rounded row.
    by_day = {p.collection_date: p.index_value for p in aggregate(chained, worked_weights, "test")}

    assert round(by_day["2026-09-01"], 6) == BASE_VALUE
    assert round(by_day["2026-09-02"], PLACES) == round(
        0.6 * 100 * math.sqrt(1.25) + 0.4 * 100 * math.sqrt(0.99), PLACES
    )
    assert round(by_day["2026-09-02"], 6) == 106.881537
    assert round(by_day["2026-09-03"], PLACES) == round(
        0.6 * 100 * math.sqrt(1.375) + 0.4 * 100 * math.sqrt(1.089), PLACES
    )
    assert round(by_day["2026-09-03"], 6) == 112.098302


def test_observed_weight_share_falls_when_a_stratum_is_imputed(worked_example_quotes, worked_weights):
    rel = jevons_relatives(collapse_to_items(worked_example_quotes))
    chained = chain_strata(impute_relatives(rel), base_date="2026-09-01")
    points = {p.collection_date: p for p in aggregate(chained, worked_weights, "test")}

    assert round(points["2026-09-02"].observed_weight_share, 6) == 1.0
    # Day 3: only DEL->BOM (weight 0.6) was actually observed.
    assert round(points["2026-09-03"].observed_weight_share, 6) == 0.6


def test_index_refuses_to_publish_when_nothing_is_observed():
    """A day with no observations anywhere produces no index value, not a flat line."""
    quotes = pd.DataFrame(
        [
            _q("d1", "DEL", "BOM", "6E", 4000),
            {**_q("d2", "DEL", "BOM", "6E", None, available=False), "total_fare": np.nan},
        ]
    )
    strata = quotes[["origin", "destination", "lead_time_days"]].drop_duplicates()
    rel = jevons_relatives(
        collapse_to_items(quotes),
        collection_dates=sorted(quotes.collection_date.unique()),
        strata=strata,
    )
    imp = impute_relatives(rel)
    assert len(imp) == 1, "d2 must appear in the relatives table as an explicit hole"
    assert imp.relative_used.isna().all()

    chained = chain_strata(imp, base_date="d1")
    w = pd.DataFrame([{"origin": "DEL", "destination": "BOM", "lead_time_days": 7, "weight": 1.0}])
    days = {p.collection_date for p in aggregate(chained, w, "test")}
    assert days == {"d1"}, "d2 must be a published gap, not an invented number"


def test_non_positive_fares_are_rejected_loudly():
    bad = pd.DataFrame([_q("d1", "DEL", "BOM", "6E", 0)])
    with pytest.raises(ValueError, match="non-positive"):
        collapse_to_items(bad)


def test_every_scenario_is_a_valid_probability_distribution():
    for s in SCENARIOS:
        assert set(s.weights) == set(LEAD_TIMES)
        assert abs(sum(s.weights.values()) - 1.0) < 1e-9
        assert all(w > 0 for w in s.weights.values())
        assert s.rationale.strip(), f"{s.name} must justify itself in prose"


def test_basket_weights_multiply_out_to_one():
    routes = pd.DataFrame(
        [
            {"origin": "DEL", "destination": "BOM", "weight": 0.7},
            {"origin": "BOM", "destination": "DEL", "weight": 0.3},
        ]
    )
    for s in SCENARIOS:
        w = build_weights(routes, s)
        assert len(w) == 2 * len(LEAD_TIMES)
        assert abs(w.weight.sum() - 1.0) < 1e-9


def test_scenarios_bracket_each_other():
    """near-term-heavy and advance-heavy must actually sit on opposite sides of uniform,
    or the 'band' is decorative."""
    near, uni, adv = by_name("near_term_heavy"), by_name("uniform"), by_name("advance_heavy")
    assert near.weights[1] > uni.weights[1] > adv.weights[1]
    assert near.weights[45] < uni.weights[45] < adv.weights[45]
