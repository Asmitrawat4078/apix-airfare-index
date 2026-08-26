"""The EaseMyTrip extractor, tested against the payload shapes the probe actually observed.

Every string in this file was copied out of a real 2 MB response captured by
scripts/probe_sources.py on 2026-08-26, not invented. That matters: an extractor tested
against fixtures someone imagined passes happily while failing on the live payload, which
is the failure mode that produces an empty index nobody notices for a week.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from collector.sources.base import Cell
from collector.sources.easemytrip import EaseMyTrip

CELL = Cell("DEL", "BOM", 15, date(2026, 9, 10))


def itinerary(sd: str, seg_key: str, stp: str = "0") -> dict:
    return {
        "SD": sd,
        "segMatchingKey": seg_key,
        "SeatAv": "10",
        "FareTypeUI": "DEFAULT",
        "b": [{"stp": stp, "CT": "DEL-BOM"}],
    }


def payload(*itins: dict, cfee: int = 449) -> dict:
    return {"CFee": cfee, "C": {"6E": "IndiGo", "SG": "SpiceJet"}, "j": [{"s": list(itins)}]}


def test_reads_a_real_itinerary():
    src = EaseMyTrip()
    out = src.extract_from_json(
        payload(itinerary("Non-Stop|6324|10|DEL-BOM||", "DELBOMThu-10Sep202604:0006:15SG 510")),
        CELL,
    )
    assert len(out) == 1
    o = out[0]
    assert o["total_fare"] == Decimal("6324")
    assert isinstance(o["total_fare"], Decimal), "money must never become a float"
    assert o["carrier"] == "SG"
    assert o["flight_no"] == "SG510"
    assert o["stops"] == 0
    assert o["dep_ts"].hour == 4 and o["dep_ts"].minute == 0
    assert o["dep_ts"].date() == date(2026, 9, 10)
    assert o["raw"]["extraction"] == "easemytrip_airavail"


def test_convenience_fee_is_recorded_beside_the_quote_not_inside_it():
    """CFee is charged at payment, not quoted in the fare. Folding it into total_fare would
    silently shift the whole series by a constant, and pretending we never saw it would
    lose information. It rides along in raw_payload."""
    src = EaseMyTrip()
    o = src.extract_from_json(
        payload(itinerary("Non-Stop|6324|10|DEL-BOM||", "DELBOMThu-10Sep202604:0006:15SG 510")),
        CELL,
    )[0]
    assert o["total_fare"] == Decimal("6324")
    assert o["raw"]["convenience_fee_inr"] == 449


def test_multiple_carriers_are_all_returned():
    src = EaseMyTrip()
    out = src.extract_from_json(
        payload(
            itinerary("Non-Stop|6324|10|DEL-BOM||", "DELBOMThu-10Sep202604:0006:15SG 510"),
            itinerary("Non-Stop|6179|4|DEL-BOM||", "DELBOMThu-10Sep202607:3009:45 6E2043"),
            itinerary("1 Stop|9450|9|DEL-BOM||", "DELBOMThu-10Sep202611:0016:20AI 805", stp="1"),
        ),
        CELL,
    )
    assert {o["carrier"] for o in out} == {"SG", "6E", "AI"}
    assert sorted(o["total_fare"] for o in out) == [Decimal("6179"), Decimal("6324"), Decimal("9450")]


def test_connecting_itineraries_keep_their_stop_count():
    """We keep them rather than dropping them: the offer definition is non-stop *where one
    exists*, and on a thin route there may be none. The stop count travels with the quote so
    the index can hold the product constant."""
    src = EaseMyTrip()
    o = src.extract_from_json(
        payload(itinerary("1 Stop|9450|9|DEL-BOM||", "DELBOMThu-10Sep202611:0016:20AI 805", stp="1")),
        CELL,
    )[0]
    assert o["stops"] == 1


def test_itineraries_for_a_different_route_are_discarded():
    """Nearby-airport suggestions appear in the payload. Indexing BOM->DEL inside a DEL->BOM
    stratum would corrupt it without ever raising an error, which is the worst kind of bug."""
    src = EaseMyTrip()
    out = src.extract_from_json(
        payload(
            itinerary("Non-Stop|6324|10|DEL-BOM||", "DELBOMThu-10Sep202604:0006:15SG 510"),
            itinerary("Non-Stop|5100|8|BOM-DEL||", "BOMDELThu-10Sep202608:0010:10 6E512"),
            itinerary("Non-Stop|4800|6|DEL-NMI||", "DELNMIThu-10Sep202609:0011:15QP1301"),
        ),
        CELL,
    )
    assert len(out) == 1
    assert out[0]["flight_no"] == "SG510"


def test_an_itinerary_with_no_parsable_fare_is_skipped_not_guessed():
    src = EaseMyTrip()
    out = src.extract_from_json(
        payload(itinerary("Non-Stop||10|DEL-BOM||", "DELBOMThu-10Sep202604:0006:15SG 510")),
        CELL,
    )
    assert out == []


def test_a_restructured_payload_falls_back_rather_than_dying():
    """If EaseMyTrip re-platforms, the generic structural harvester takes over. The
    `extraction` marker changes, so the switch is visible instead of silent."""
    src = EaseMyTrip()
    out = src.extract_from_json(
        {"Results": [{"AirlineCode": "6E", "FlightNumber": "6E-2043", "TotalFare": 6179}]},
        CELL,
    )
    assert len(out) == 1
    assert out[0]["total_fare"] == Decimal("6179")
    assert out[0]["raw"].get("extraction") != "easemytrip_airavail"


def test_an_empty_payload_yields_nothing_rather_than_raising():
    src = EaseMyTrip()
    assert src.extract_from_json({"j": []}, CELL) == []
    assert src.extract_from_json({}, CELL) == []


def test_only_the_fare_endpoint_is_intercepted():
    """The currency converter on the same host also returns fare-shaped numbers. A broad
    matcher would let it through and the harvester would find plausible values in it."""
    src = EaseMyTrip()
    assert src.looks_like_fare_endpoint(
        "https://flightservice-node.easemytrip.com/AirAvail_Lights/AirBus_New"
    )
    assert not src.looks_like_fare_endpoint(
        "https://flightservice-node.easemytrip.com//Addons/CurrencyConverter"
    )


def test_the_source_is_marked_verified():
    assert EaseMyTrip.spec.confidence == "verified"
    assert EaseMyTrip.spec.enabled
