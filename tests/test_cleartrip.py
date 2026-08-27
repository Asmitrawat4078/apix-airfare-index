"""Cleartrip extraction — and the one rule that matters most.

The prototype this source replaces recorded the carrier as "IndiGo" whenever it could not
identify the airline. These tests exist mainly to make sure that never happens again: an
invented carrier corrupts the matched model on both the real airline and the one it was
mislabelled as, and it is invisible in the output.
"""

from __future__ import annotations

from datetime import date

from collector.sources.base import Cell
from collector.sources.cleartrip import Cleartrip, carrier_from_text

CELL = Cell("DEL", "BOM", 7, date(2026, 9, 2))


def test_air_india_express_is_not_read_as_air_india():
    """ "Air India Express" contains "Air India". Order of matching is load-bearing: get it
    wrong and every Express fare is filed under the wrong airline."""
    assert carrier_from_text("Air India Express · AI") == "IX"
    assert carrier_from_text("Air India") == "AI"


def test_an_unknown_airline_returns_none_never_a_default():
    assert carrier_from_text("Some New Airline") is None
    assert carrier_from_text("") is None


def test_a_row_with_airline_and_price_is_read():
    html = """
    <div><div class="row"><span>IndiGo</span><span>06:10 - 08:20</span>
    <span>₹5,432</span></div></div>
    """
    offers = Cleartrip().extract_from_dom(html, CELL)
    assert offers
    top = offers[0]
    assert top["carrier"] == "6E"
    assert top["total_fare"] == 5432
    assert top["raw"]["carrier_identified"] is True


def test_a_price_with_no_airline_is_recorded_with_carrier_none():
    """The fare is a real observation and is kept. The airline is unknown and is recorded
    as unknown. The prototype wrote "IndiGo" here; that is the bug this test pins shut."""
    html = '<div><div class="row"><span>Non-stop</span><span>₹6,179</span></div></div>'
    offers = Cleartrip().extract_from_dom(html, CELL)
    assert offers
    assert offers[0]["total_fare"] == 6179
    assert offers[0]["carrier"] is None
    assert offers[0]["carrier"] != "6E"


def test_identified_carriers_sort_ahead_of_unknown_ones():
    html = """
    <div>
      <div class="row"><span>Non-stop</span><span>₹4,100</span></div>
      <div class="row"><span>Akasa Air</span><span>₹5,900</span></div>
    </div>
    """
    offers = Cleartrip().extract_from_dom(html, CELL)
    assert offers[0]["carrier"] == "QP", "an identified carrier should win the matched slot"


def test_prices_outside_a_plausible_range_are_ignored():
    """A ₹299 seat-selection fee and a ₹4,00,000 group total are both excluded by magnitude."""
    html = """
    <div><div class="row"><span>IndiGo</span><span>₹299</span></div>
    <div class="row"><span>IndiGo</span><span>₹4,00,000</span></div></div>
    """
    assert Cleartrip().extract_from_dom(html, CELL) == []


def test_a_whole_results_container_is_not_treated_as_one_row():
    """A wrapper holding every result would pair the first airline with the cheapest price
    on the page, which is how proximity-based extraction goes wrong."""
    rows = "".join(f"<div><span>IndiGo</span><span>₹{6000 + i}</span></div>" for i in range(30))
    offers = Cleartrip().extract_from_dom(f"<div id='results'>{rows}</div>", CELL)
    assert len(offers) >= 20, "individual rows should still be read"


def test_the_source_is_off_by_default_so_the_cloud_runner_never_touches_it():
    """Cleartrip returns 403 on robots.txt to a US datacentre. The cloud collector must not
    go near it; only the self-hosted Indian runner opts in explicitly."""
    assert Cleartrip.spec.enabled is False
    assert Cleartrip.spec.confidence == "india_only"
