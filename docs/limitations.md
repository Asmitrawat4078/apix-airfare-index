# Limitations

Read this before quoting a number from APIx.

This document exists because the fastest way to lose credibility in front of a statistical
audience is to be told about your own limitation by someone else. Everything below is a
real constraint on what this index can support, stated plainly, with what we did about it.

---

## 1. These are quoted fares, not transaction prices

**The limitation.** We observe what a consumer is *offered*, not what anyone *paid*. We
cannot see booking-class inventory, so we cannot weight a fare by how many seats sold at
it. If ninety people buy the ₹4,000 bucket and two buy the ₹19,000 bucket, our index treats
both offers as observations of the same weight within their stratum.

**Why it is still a defensible measurement.** The CPI already uses offer prices for most of
its basket — a shelf price in a market is not a transaction price either, and nobody
regards that as disqualifying. A quoted fare is what the consumer faces at the point of
decision, which is precisely the price that enters a household's purchasing decision.

**What we did about it.** The API has a documented, versioned ingestion point for airline
MIS data (`/v1/ingest/mis`) and the weighting layer already accepts an external weight
source. If an airline or MoSPI supplies booking-class inventory, seat-weighted aggregation
drops in without redesigning the index. It returns 501 today, deliberately, rather than
being omitted.

**What we cannot claim.** That this is a transaction-price index. It is not, and no amount
of daily frequency makes it one.

---

## 2. The lead-time weights are unknown, not estimated

**The limitation.** India's booking-curve distribution is not public. The headline index
assumes uniform weights across T+1/7/15/30/45, and that assumption is not a finding — it is
the absence of one.

**What we did about it.** Published three scenarios rather than one number, and report the
spread as a band. Where the band is wide, the headline depends on an assumption nobody has
data for, and the reader can see it.

**What we cannot claim.** That the headline is *the* airfare inflation rate. It is the rate
under a stated assumption, with the sensitivity to that assumption published beside it.

---

## 3. The basket is small, and 12 pairs are not India

**The limitation.** 24 directed routes covering 22.2% of domestic passengers. Thin regional
routes are represented by exactly one pair. Seasonal and tourist-heavy sectors — Leh,
Port Blair, the Andamans — are absent entirely, and they behave differently from trunk routes.

**Why it is sized this way.** A basket you can actually collect every single day for
months, ethically and at a polite request rate, beats a larger one that breaks. Coverage
can grow; a broken series cannot be recovered retrospectively.

**What we cannot claim.** That the index is representative of all Indian domestic air
travel. It is representative of the high-volume network plus one regional route, which is
what it says it is.

---

## 4. Short series, and every day is unrecoverable

**The limitation.** The series began recently. Chained indices need length before their
movements mean much, seasonal adjustment needs more length still, and the chain-drift
diagnostic in §3.5 of the methodology only becomes informative over dozens of periods.

**The asymmetry that matters.** You cannot go back and scrape what a fare was last Tuesday.
Every missed collection day is a permanent hole. This is why uptime is prime directive #1
and why no feature is worth delaying a collection cycle for.

**What we cannot claim.** Anything seasonal, anything about year-on-year movement, or any
inference that requires a long series, until the series is long.

---

## 5. Web collection is fragile and the fragility is not random

**The limitation.** Sources re-platform, add bot protection, and change their JSON shapes
without notice. Worse, this fragility is **correlated with what we are trying to measure**:
sites are most likely to throttle during high-traffic periods, which are exactly the
high-demand periods when fares move most. Missingness is therefore not missing-at-random,
and the imputation in §3.2 assumes more randomness than reality provides.

**What we did about it.** Multiple sources per route so no single failure blanks a cell;
graceful degradation to stratum imputation; a published availability rate; a health page
that catches a dead source within one collection cycle; and blocked cells counted
separately from sold-out cells so a scraping failure can never masquerade as market
scarcity.

**What we cannot claim.** That imputed cells are as good as observed ones. They are flagged
individually and `observed_weight_share` is published so a reader can discount accordingly.

---

## 6. "Cheapest non-stop economy" is one offer among many

**The limitation.** The matched model tracks the cheapest offer. It says nothing about the
distribution above it. If carriers hold entry-level fares steady while repricing everything
above, this index will report stability while the average fare paid rises.

**What we did about it.** The hedonic model uses **all** observed quotes, not just the
cheapest, so it does see the distribution. Divergence between the two series is exactly the
signal that this is happening — which is a large part of why both are published.

---

## 7. One collector, in one place

**The limitation.** Collection runs from a single environment. Fares can vary by point of
sale, currency, device and cookie state; we hold those constant, which is right for a
matched model but means we observe one vantage point rather than the market as a whole. If
that vantage point is systematically offered different prices, the level is biased even if
the movement is not.

**What we cannot claim.** That the *level* of these fares is the level any given Indian
consumer sees. The index measures **change**, and change is far more robust to a constant
vantage-point offset than level is.

---

## 8. Success would break it

**The limitation.** If this series became official, publishing the basket, the collection
window and the methodology would hand carriers a specification to price against. Our
transparency is only safe because nobody has an incentive to game a student project.

**What would have to change.** Stop publishing the exact collection instant; widen the
randomisation window; make the basket confidential; rotate a reserve sample. These are the
standard responses to strategic behaviour against an official statistic — a known problem
in official statistics, not something we discovered.

**Why we are transparent anyway.** For a research prototype, auditability is worth more
than resistance to gaming. An index nobody can check is worth nothing regardless of how
un-gameable it is.

---

## 9. What we would need to make this official

Stated so the gap is visible rather than implied:

1. Airline booking-class inventory, for seat-weighted aggregation and a real booking curve.
2. A basket several times larger, including seasonal and regional sectors.
3. At least a year of collection, for seasonal adjustment.
4. Formal data-sharing agreements with sources, replacing scraping entirely.
5. A validated bridge to the CPI transport division's existing air-transport sub-class,
   so this augments the published series rather than competing with it.

None of these is a reason not to build the first four. All of them are reasons this is a
prototype for a method, not a replacement for an official statistic.
