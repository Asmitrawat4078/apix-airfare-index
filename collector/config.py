"""Load the frozen basket. No sampling decision is allowed to live anywhere but here."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import yaml

from .sources.base import Cell

REPO = Path(__file__).resolve().parents[1]
BASKET_PATH = REPO / "data" / "basket.yaml"


@dataclass(frozen=True, slots=True)
class Basket:
    version: int
    lead_times: tuple[int, ...]
    directed_routes: tuple[tuple[str, str, float], ...]
    scheduled_time_ist: str
    randomisation_window_minutes: int
    carriers: dict[str, str]
    offer: dict

    @property
    def cells_per_day(self) -> int:
        return len(self.directed_routes) * len(self.lead_times)

    def cells_for(self, collection_day: date) -> list[Cell]:
        """The 120 cells for one collection day.

        Departure date is derived, never fixed: on collection day d the T+15 cell means
        departure on d+15. That is what keeps lead time constant as calendar time advances,
        and it is the single most important line in this file.
        """
        cells = []
        for origin, destination, _w in self.directed_routes:
            for lt in self.lead_times:
                cells.append(Cell(origin, destination, lt, collection_day + timedelta(days=lt)))
        return cells


@lru_cache(maxsize=1)
def load_basket(path: Path | None = None) -> Basket:
    raw = yaml.safe_load((path or BASKET_PATH).read_text())
    return Basket(
        version=int(raw["basket_version"]),
        lead_times=tuple(int(x) for x in raw["lead_times_days"]),
        directed_routes=tuple(
            (r["origin"], r["destination"], float(r["weight"])) for r in raw["directed_routes"]
        ),
        scheduled_time_ist=raw["collection"]["scheduled_time_ist"],
        randomisation_window_minutes=int(raw["collection"]["randomisation_window_minutes"]),
        carriers=dict(raw["carriers_tracked"]),
        offer=dict(raw["offer_definition"]),
    )
