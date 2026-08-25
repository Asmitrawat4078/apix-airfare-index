"""The data contracts must reject what they promise to reject."""

from __future__ import annotations

import pandas as pd
import pandera.errors
import pytest

from tests.contracts import IndexValueSchema, RawQuoteSchema


def good_quote(**over):
    base = {
        "collection_date": "2026-09-01",
        "source": "ixigo",
        "origin": "DEL",
        "destination": "BOM",
        "lead_time_days": 7,
        "dep_date": "2026-09-08",
        "is_available": True,
        "total_fare": 5432.0,
        "currency": "INR",
        "unavailable_reason": None,
    }
    return {**base, **over}


def test_a_clean_batch_passes():
    RawQuoteSchema.validate(pd.DataFrame([good_quote(), good_quote(carrier="AI")]))


@pytest.mark.parametrize(
    "override, why",
    [
        ({"lead_time_days": 3, "dep_date": "2026-09-04"}, "lead time outside the frozen strata"),
        ({"origin": "del"}, "lowercase IATA"),
        ({"origin": "BOM", "destination": "BOM"}, "journey to the same airport"),
        ({"total_fare": 120.0}, "fare far below any plausible domestic ticket"),
        ({"total_fare": 900_000.0}, "fare far above any plausible domestic ticket"),
        ({"is_available": True, "total_fare": None}, "available quote with no price"),
        ({"is_available": False, "unavailable_reason": "dunno"}, "unrecognised unavailable reason"),
        ({"currency": "USD"}, "wrong currency"),
        ({"dep_date": "2026-09-20"}, "departure not lead_time_days after collection"),
    ],
)
def test_bad_rows_are_rejected(override, why):
    df = pd.DataFrame([good_quote(**override)])
    with pytest.raises((pandera.errors.SchemaError, pandera.errors.SchemaErrors)):
        RawQuoteSchema.validate(df, lazy=False)


def test_sold_out_rows_need_no_price():
    """A genuinely sold-out cell is a valid observation with no fare. It must pass."""
    RawQuoteSchema.validate(
        pd.DataFrame([good_quote(is_available=False, total_fare=None, unavailable_reason="sold_out")])
    )


def test_index_values_must_carry_an_availability_rate():
    """The rule CLAUDE.md cares about most, enforced rather than documented."""
    ok = pd.DataFrame(
        [
            {
                "collection_date": "2026-09-02",
                "scenario": "uniform",
                "index_value": 101.2,
                "availability_rate": 0.93,
                "observed_weight_share": 0.88,
            }
        ]
    )
    IndexValueSchema.validate(ok)

    naked = ok.assign(availability_rate=None)
    with pytest.raises((pandera.errors.SchemaError, pandera.errors.SchemaErrors)):
        IndexValueSchema.validate(naked, lazy=False)


def test_a_day_cannot_have_two_values_for_one_scenario():
    dupes = pd.DataFrame(
        [
            {
                "collection_date": "2026-09-02",
                "scenario": "uniform",
                "index_value": 101.2,
                "availability_rate": 0.93,
                "observed_weight_share": 0.88,
            },
            {
                "collection_date": "2026-09-02",
                "scenario": "uniform",
                "index_value": 104.9,
                "availability_rate": 0.93,
                "observed_weight_share": 0.88,
            },
        ]
    )
    with pytest.raises((pandera.errors.SchemaError, pandera.errors.SchemaErrors)):
        IndexValueSchema.validate(dupes, lazy=False)
