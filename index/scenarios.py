"""Lead-time weights, and why there are three sets of them rather than one.

Route weights are easy: DGCA publishes monthly city-pair passenger counts, so we know
how much of India's domestic flying happens on DEL-BOM. That is real data.

Lead-time weights are the opposite. To weight the T+1 stratum against the T+45 stratum we
would need to know how far in advance Indians actually book — the booking-curve
distribution. No public source publishes it. Airlines have it and do not release it.

There are two things you can do about that. You can invent a plausible-looking split,
put it in a config file, and never mention it again. Or you can say out loud that the
number is unknown, run the index under three defensible assumptions that bracket the
plausible range, and publish the spread as a band.

We do the second. The band width is itself a finding: where the three scenarios agree,
the index is robust to the thing we don't know; where they diverge, they are telling you
that the headline number depends on an assumption nobody has data for — which is exactly
the kind of caveat a statistical agency needs stated rather than buried.

If MoSPI or an airline later supplies a real booking-curve distribution, it drops in here
as a fourth, preferred scenario and the band becomes a robustness check around it.
"""

from __future__ import annotations

from dataclasses import dataclass

LEAD_TIMES = (1, 7, 15, 30, 45)


@dataclass(frozen=True, slots=True)
class LeadTimeScenario:
    name: str
    label: str
    weights: dict[int, float]
    rationale: str

    def __post_init__(self) -> None:
        if set(self.weights) != set(LEAD_TIMES):
            raise ValueError(f"scenario {self.name} must weight exactly {LEAD_TIMES}")
        total = sum(self.weights.values())
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"scenario {self.name} weights sum to {total}, not 1")


SCENARIOS: tuple[LeadTimeScenario, ...] = (
    LeadTimeScenario(
        name="near_term_heavy",
        label="Near-term heavy",
        weights={1: 0.35, 7: 0.30, 15: 0.20, 30: 0.10, 45: 0.05},
        rationale=(
            "Assumes Indian domestic demand is dominated by short-horizon booking — "
            "business travel, VFR, and the well-documented tendency of leisure travellers "
            "in price-sensitive markets to book late. This scenario gives the most weight "
            "to the strata where dynamic pricing is most violent, so it produces the most "
            "volatile series. Treat it as the upper bound on measured volatility."
        ),
    ),
    LeadTimeScenario(
        name="uniform",
        label="Uniform",
        weights={1: 0.20, 7: 0.20, 15: 0.20, 30: 0.20, 45: 0.20},
        rationale=(
            "The agnostic choice: every advance-purchase window counts equally. This is not "
            "a claim that bookings are uniformly distributed — it is a deliberate refusal to "
            "claim anything. It is the natural headline when the true distribution is unknown, "
            "and it is the scenario we lead with for exactly that reason."
        ),
    ),
    LeadTimeScenario(
        name="advance_heavy",
        label="Advance heavy",
        weights={1: 0.05, 7: 0.10, 15: 0.20, 30: 0.30, 45: 0.35},
        rationale=(
            "Assumes planning-led demand, closer to the booking curves seen in mature leisure "
            "markets and in Eurostat's treatment of rail fares. Damps last-minute spikes and "
            "produces the smoothest series. Treat it as the lower bound on measured volatility."
        ),
    ),
)

HEADLINE_SCENARIO = "uniform"


def by_name(name: str) -> LeadTimeScenario:
    for s in SCENARIOS:
        if s.name == name:
            return s
    raise KeyError(f"unknown lead-time scenario {name!r}; have {[s.name for s in SCENARIOS]}")
