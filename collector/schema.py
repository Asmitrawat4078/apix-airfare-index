"""The quote record. This schema is the contract between every source and the database.

A source's only job is to return `Quote` objects. If a source cannot fill a field
honestly it returns None for it — never a guess, never a zero standing in for unknown.
`base_fare`, `taxes` and `fees` are frequently unavailable on OTA listing pages; that is
fine and expected. `total_fare` is not optional when `is_available` is true, because an
available cell with no price is a bug, not an observation.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any


class UnavailableReason(StrEnum):
    """Why a cell has no price.

    These are not interchangeable. SOLD_OUT is information about the market:
    the product genuinely is not purchasable at any price, which is itself a
    signal about demand. BLOCKED is information about us: the market had a price
    and we failed to observe it. Collapsing them would let our own scraping
    failures masquerade as market scarcity, which is the single most dangerous
    thing a price index can do to itself.
    """

    SOLD_OUT = "sold_out"          # flights exist, no economy seat purchasable
    NO_SERVICE = "no_service"      # no flight operates this route on this date
    BLOCKED = "blocked"            # bot wall, CAPTCHA, 403 — our failure, not the market's
    RATE_LIMITED = "rate_limited"  # we backed off deliberately
    PARSE_ERROR = "parse_error"    # response received but not understood
    TIMEOUT = "timeout"
    ROBOTS_DISALLOWED = "robots_disallowed"  # we were told not to look, and didn't

    @property
    def is_market_signal(self) -> bool:
        """True when the missingness tells us about fares rather than about our scraper."""
        return self in (UnavailableReason.SOLD_OUT, UnavailableReason.NO_SERVICE)


@dataclass(slots=True)
class Quote:
    collection_ts_utc: datetime
    source: str
    url: str
    origin: str
    destination: str
    lead_time_days: int
    dep_date: str                      # YYYY-MM-DD, local to origin
    is_available: bool
    basket_version: int = 1
    run_id: str | None = None
    carrier: str | None = None
    flight_no: str | None = None
    dep_ts: datetime | None = None
    arr_ts: datetime | None = None
    stops: int | None = None
    fare_class: str = "economy"
    base_fare: Decimal | None = None
    taxes: Decimal | None = None
    fees: Decimal | None = None
    total_fare: Decimal | None = None
    currency: str = "INR"
    is_cheapest_in_cell: bool = False
    unavailable_reason: str | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.origin = self.origin.upper()
        self.destination = self.destination.upper()
        if self.is_available and self.total_fare is None:
            raise ValueError(
                f"{self.source} {self.cell_id}: available quote with no total_fare — "
                "a source must not report availability it cannot price"
            )
        if not self.is_available and not self.unavailable_reason:
            raise ValueError(
                f"{self.source} {self.cell_id}: unavailable quote with no reason — "
                "sold out and blocked mean opposite things downstream"
            )
        if self.lead_time_days not in (1, 7, 15, 30, 45):
            raise ValueError(f"lead_time_days {self.lead_time_days} is not a basket stratum")

    @property
    def cell_id(self) -> str:
        """The stratum this quote belongs to. Goes on every log line."""
        return f"{self.origin}->{self.destination}/T+{self.lead_time_days}"

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        for k in ("base_fare", "taxes", "fees", "total_fare"):
            if row[k] is not None:
                row[k] = str(row[k])
        for k in ("collection_ts_utc", "dep_ts", "arr_ts"):
            if row[k] is not None:
                row[k] = row[k].isoformat()
        return row
