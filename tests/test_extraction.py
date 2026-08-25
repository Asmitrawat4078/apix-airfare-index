"""Extraction tests.

The payloads below are *shapes*, not scraped data — deliberately written to look like
three different OTAs' conventions (camelCase nesting, flat SOAP-ish keys, and a
deeply-buried results array) so we know the structural harvester survives all three.
No fare in this file is presented as an observation; nothing here goes anywhere near
the index. These are fixtures for a parser, and that is all they are.
"""

from __future__ import annotations

from decimal import Decimal

from collector.sources.base import (
    detect_carrier,
    detect_flight_no,
    harvest_offers_from_dom,
    harvest_offers_from_json,
    parse_inr,
)

CAMEL_NESTED = {
    "data": {
        "searchResult": {
            "tripInfos": {
                "ONWARD": [
                    {
                        "sI": [
                            {
                                "fD": {"aI": {"code": "6E", "name": "IndiGo"}, "fN": "2043"},
                                "stops": 0,
                                "departureTime": "2026-09-09T06:10:00",
                            }
                        ],
                        "totalPriceList": [
                            {"fd": {"ADULT": {"fC": {"TF": 5432, "BF": 4100, "TAF": 1332}}}}
                        ],
                    },
                    {
                        "sI": [
                            {
                                "fD": {"aI": {"code": "AI", "name": "Air India"}, "fN": "805"},
                                "stops": 0,
                                "departureTime": "2026-09-09T09:00:00",
                            }
                        ],
                        "totalPriceList": [{"fd": {"ADULT": {"fC": {"TF": 7890}}}}],
                    },
                ]
            }
        }
    }
}

FLAT_KEYS = {
    "Results": [
        {"AirlineCode": "SG", "FlightNumber": "SG-8169", "TotalFare": "6,245", "Stops": 0},
        {"AirlineCode": "QP", "FlightNumber": "QP-1102", "TotalFare": 4875.0, "Stops": 0},
    ]
}

BURIED = {
    "a": {
        "b": {
            "c": {
                "d": {
                    "e": {
                        "offers": [
                            {
                                "marketingAirline": "Akasa Air QP-1301",
                                "displayPrice": {"amount": 5199, "currency": "INR"},
                            }
                        ]
                    }
                }
            }
        }
    }
}


def test_harvester_reads_camelcase_nested_payloads():
    offers = harvest_offers_from_json(CAMEL_NESTED)
    fares = sorted(o["total_fare"] for o in offers)
    assert Decimal("5432") in fares and Decimal("7890") in fares
    carriers = {o["carrier"] for o in offers}
    assert {"6E", "AI"} <= carriers


def test_harvester_never_mistakes_a_base_fare_for_the_total():
    """The IndiGo offer carries BF 4100, TAF 1332 and TF 5432. `total_fare` in our schema
    means what a traveller actually pays, so it must be 5432. Picking the smallest number
    in sight would understate every single fare by its tax component — a bias that would
    be invisible in the index and enormous in level terms."""
    offers = harvest_offers_from_json(CAMEL_NESTED)
    indigo = [o for o in offers if o["carrier"] == "6E"]
    assert indigo, "IndiGo offer not found"
    assert indigo[0]["total_fare"] == Decimal("5432")
    assert indigo[0]["total_fare"] != Decimal("4100")


def test_harvester_reads_flat_key_payloads():
    offers = harvest_offers_from_json(FLAT_KEYS)
    assert {o["carrier"] for o in offers} == {"SG", "QP"}
    assert {o["flight_no"] for o in offers} == {"SG8169", "QP1102"}
    assert sorted(o["total_fare"] for o in offers) == [Decimal("4875"), Decimal("6245")]


def test_harvester_finds_deeply_buried_offers():
    offers = harvest_offers_from_json(BURIED)
    assert len(offers) == 1
    assert offers[0]["carrier"] == "QP"
    assert offers[0]["total_fare"] == Decimal("5199")


def test_harvester_ignores_payloads_with_no_flight_context():
    """Analytics and config JSON also contain numbers in the fare range. Without an
    airline or flight identifier on the same object, nothing is emitted."""
    noise = {"config": {"price": 4999, "timeout": 3000}, "metrics": {"amount": 12000}}
    assert harvest_offers_from_json(noise) == []


def test_harvester_rejects_implausible_magnitudes():
    """A '299' loyalty fee and a '999999' internal id are both excluded by magnitude."""
    payload = {"r": [{"airline": "IndiGo", "fare": 299}, {"airline": "IndiGo", "fare": 999999}]}
    assert harvest_offers_from_json(payload) == []


def test_dom_fallback_needs_a_flight_number_and_a_price_together():
    good = """
    <div><div class="row"><span>IndiGo 6E-2043</span><span>06:10 - 08:20</span>
    <span>₹5,432</span></div>
    <div class="row"><span>SpiceJet SG-8169</span><span>₹6,245</span></div></div>
    """
    offers = harvest_offers_from_dom(good)
    assert {o["flight_no"] for o in offers} >= {"6E2043", "SG8169"}
    assert all(o["raw"]["extraction"] == "dom_fallback" for o in offers)


def test_dom_fallback_returns_nothing_rather_than_guessing():
    """A page with prices but no flight identifiers yields no offers. Silence here becomes
    a parse_error in the health table, which is recoverable. A guess would not be."""
    ambiguous = "<div><p>Fares from ₹4,999</p><p>Save ₹1,200 today</p></div>"
    assert harvest_offers_from_dom(ambiguous) == []


def test_price_parsing_handles_indian_formatting():
    assert parse_inr("₹4,999") == Decimal("4999")
    assert parse_inr("Rs. 12,345") == Decimal("12345")
    assert parse_inr("INR 7890") == Decimal("7890")
    assert parse_inr(5400) == Decimal("5400")
    assert parse_inr("not a price") is None
    assert parse_inr(None) is None
    assert isinstance(parse_inr("4999"), Decimal), "money must never become a float"


def test_air_india_express_is_not_mistaken_for_air_india():
    """IX and AI are different carriers with different pricing. Order of pattern
    matching matters and this test pins it."""
    assert detect_carrier("Air India Express") == "IX"
    assert detect_carrier("Air India") == "AI"
    assert detect_flight_no("IX-1234") == "IX1234"
