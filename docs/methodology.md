# APIx methodology note

**A daily price index for Indian domestic airfares**
Basket version 1 · Problem statement SIH26056 · MoSPI, Data Informatics & Innovation Division

This note documents every decision that affects a published number, and it was written
while those decisions were being made rather than reconstructed afterwards. Where a choice
was arbitrary, it says so. Where a number is unknown, it says that too, and shows what was
done instead of inventing one.

---

## 1. What is being measured

The price of a **fixed, repeatedly-purchasable offer** — the cheapest economy seat, one
adult, one-way, non-stop where one exists, no baggage add-on, non-refundable — observed on
the same routes, at the same advance-purchase windows, at the same time every day.

This is a **matched model**. The point of holding every characteristic of the offer
constant is that when the number moves, a price has moved, and not the product.

### 1.1 The unit of observation

| Level | Definition |
|---|---|
| **Cell / stratum** | one (origin, destination, lead time). 24 directed routes × 5 lead times = **120 cells per day** |
| **Item** | (source, origin, destination, lead time, carrier) — the thing whose price is compared over time |
| **Observation** | the cheapest fare for one item on one collection day |

The item key deliberately **excludes flight number**. A carrier's cheapest economy seat on
a route at a given lead time is the product a traveller actually shops for; which specific
departure that turns out to be is the carrier's inventory decision, not a change in the
thing being priced. Pinning to flight number would collapse the matched sample to almost
nothing — flights are retimed and renumbered constantly — and a matched model that matches
nothing is noise with a formula attached.

### 1.2 Time

- **One collection run per day** at a fixed IST time, with a short randomisation window.
  Eurostat is explicit that web-scraped prices must be collected at identical points in
  time; a fare series collected at 02:00 one day and 14:00 the next is measuring the
  intraday cycle, not inflation.
- **Departure date is derived, never fixed.** On collection day *d*, the T+15 cell means
  departure on *d*+15. This is what keeps the advance-purchase window constant as calendar
  time advances, and it is the single most important line in `collector/config.py`.

---

## 2. The basket

### 2.1 Routes

12 city pairs, **directional** — DEL→BOM and BOM→DEL are separate cells, because they are
separate products with separate load factors and separate fare surfaces. Averaging them
would discard exactly the asymmetry a route-level index exists to capture.

Six pairs were seeded by the problem statement (DEL-BOM, DEL-BLR, BOM-BLR, DEL-CCU,
BLR-HYD, MAA-DEL). One slot is reserved for a **thin regional route** (DEL-GAU), so the
index is not purely a metro-trunk measure: the North-East gateway has genuine scheduled
service, an order of magnitude less traffic, and fewer competing carriers, so we expect a
different dynamic-pricing signature. The remaining five were selected by DGCA two-way
passenger volume.

The resulting basket covers **22.2%** of India's mapped domestic passengers.

### 2.2 Route weights, and a data-quality problem worth naming

Weights are **DGCA monthly domestic city-pair passenger volumes**, directional, summed over
a trailing 12-month window, normalised across the 24 directed routes. Real, public,
monthly data — not a proxy and not a guess.

Getting this right required work that is easy to skip. The DGCA source publishes *city*
names, not airport codes, and the strings are not clean:

- **Mumbai appears under both `MUMBAI` and `MUMBAI (MUMBAI)`.** Treat them as different
  cities and DEL-BOM's weight is understated by roughly 40%.
- **Goa appears under three labels** across the window (`DABOLIM`, `GOA (DABOLIM, SOUTH GOA)`,
  `GOA (MOPA, NORTH GOA)`) covering two genuinely different airports.
- Navi Mumbai, Hirasar and several others opened mid-series and appear under multiple names.

`scripts/city_iata.py` is therefore a hand-checked explicit mapping, not a fuzzy matcher —
a silent mis-match would corrupt every weight downstream, and a route weight is not the
kind of number you want to discover was wrong in December. Distinct *airports* stay
distinct IATA codes (BOM ≠ NMI, GOI ≠ GOX) because the matched model compares an identical
product; only duplicate *labels for the same airport* are merged. After this, unmapped
labels account for **0.7%** of traffic in the window, and that residual is reported in
`data/basket_build_report.json` rather than swallowed.

`scripts/verify_basket.py` recomputes the weights from the DGCA file, checks its SHA-256
against the value recorded in `basket.yaml`, and fails CI on any drift.

### 2.3 Lead-time weights — the number we refuse to invent

To weight the T+1 stratum against the T+45 stratum, you need the **booking curve**: how far
in advance Indians actually book. No public source publishes it. Airlines have it and do
not release it.

There are two things you can do about that. You can pick a plausible-looking split, put it
in a config file, and never mention it again. Or you can say out loud that the number is
unknown.

We publish **three scenarios** and report the spread as a sensitivity band:

| Scenario | T+1 | T+7 | T+15 | T+30 | T+45 | Reading |
|---|---|---|---|---|---|---|
| Near-term heavy | 0.35 | 0.30 | 0.20 | 0.10 | 0.05 | Upper bound on measured volatility |
| **Uniform (headline)** | 0.20 | 0.20 | 0.20 | 0.20 | 0.20 | A deliberate refusal to claim anything |
| Advance heavy | 0.05 | 0.10 | 0.20 | 0.30 | 0.35 | Lower bound on measured volatility |

The headline is uniform precisely *because* it asserts nothing. **The band width is itself
a finding**: where the three scenarios agree, the headline is robust to the thing we don't
know; where they diverge, the number depends on an assumption nobody has data for, and a
statistical agency needs that stated rather than buried.

If MoSPI or an airline later supplies a real booking-curve distribution, it drops in as a
fourth, preferred scenario and the band becomes a robustness check around it.
`/v1/ingest/mis` is the documented extension point.

---

## 3. The index

### 3.1 Elementary aggregate: Jevons

Within each stratum, the **geometric mean of matched price relatives**:

$$J_s(t) = \prod_{i \in M_s(t)} \left(\frac{p_{i,t}}{p_{i,t-1}}\right)^{1/|M_s(t)|}$$

where $M_s(t)$ is the set of items observed in stratum *s* on both day *t* and day *t−1*.
Computed in logs for numerical stability — a product of several hundred relatives underflows
long before the log sum does.

**Why geometric rather than arithmetic:**

- **Dutot** (ratio of mean prices) lets an expensive item dominate. On DEL-BOM at T+1 a
  ₹22,000 last-minute Air India fare and a ₹6,000 IndiGo fare are equally *informative*
  about price change, but Dutot weights the first nearly four times as heavily.
- **Carli** (mean of relatives) fails the time-reversal test and carries a known upward
  bias. `tests/test_index_maths.py` includes a time-reversal test specifically so that an
  accidental regression to Carli is caught.
- **Jevons** is invariant to the base period, symmetric in time, and — the reason Eurostat
  recommends it specifically for web-scraped data — damps the downward bias that appears
  when observations go missing non-randomly, which for airfares they very much do.

### 3.2 Missing cells: donor imputation, never carry-forward

**Carry-forward is prohibited.** Repeating yesterday's price when a cell goes missing
asserts zero price change on no evidence. Do that across a sold-out weekend and the series
flattens exactly when the market is moving most — the index would understate inflation
precisely in the periods it most needs to capture it.

Instead a missing stratum is assumed to have moved as its nearest observed neighbours moved:

1. **same route, other lead times, same day** — captures a route-specific shock
2. **same lead time, other routes, same day** — captures a lead-time-wide shock
3. **all observed strata that day** — last resort
4. **nothing observed at all that day** — refuse. No index value is published, and the
   series shows a **gap**. An index value computed from no observations is not a number.

Every imputed cell is flagged with the tier used, and `observed_weight_share` — the share
of basket weight that was directly observed rather than imputed — is published alongside
every index value.

### 3.3 Higher-level aggregation

$$I(t) = \frac{\sum_{s \in C(t)} w_s \, I_s(t)}{\sum_{s \in C(t)} w_s}, \qquad w_s = w_{\text{route}(s)} \times w_{\text{lead}(s)}$$

where $C(t)$ is the set of strata with a computable level on day *t*.

Arithmetic at this level is not an inconsistency with the Jevons below it. This is standard
CPI construction: the geometric mean is the right tool for combining *unweighted,
homogeneous* items inside an elementary aggregate, where you have no expenditure
information and want base invariance. Above the elementary level you *do* have weights —
real ones, from DGCA — and a weighted arithmetic mean of index levels keeps the published
number interpretable as "the cost of the fixed basket, relative to day one". Eurostat's
HICP works this way, and so does MoSPI's CPI.

**Renormalising over contributing weight matters.** If 8 of 120 strata have no level on a
given day, dividing by the full basket weight would silently drag the index toward zero.
We divide by the weight that actually contributed, and publish that share.

### 3.4 Availability rate

The share of the 120 daily cells that returned a real quote. **Published with every index
value, without exception** — an index built from 40 cells and one built from 118 are not
the same claim, and a reader cannot tell them apart from the level alone.

Sold-out cells and blocked cells are counted and reported **separately**, because they mean
opposite things. Sell-out frequency is a genuine market signal the CPI cannot currently
produce, and it is charted in its own right.

### 3.5 Chaining, and the chain-drift problem we found

The headline is a **chained daily index**: $I_s(t) = I_s(t-1) \cdot J_s(t)$, rebased to 100
on the first collection day, aggregated up to weekly and monthly (geometric mean of daily
levels within the period).

While validating the pipeline over a long fixture, the chained headline and the hedonic
series disagreed far more than two measurements of the same thing should — correlation
around 0.2. That is not a coding bug. It is **chain drift**: each day's Jevons relative
carries sampling noise, chaining multiplies those relatives together, and the noise
accumulates as a random walk even when the underlying price level does not move. It is the
central reason Eurostat and the ILO moved to multilateral methods for scanner and
web-scraped data.

So three constructions are published beside the headline:

| Series | Construction | Property |
|---|---|---|
| **Chained Jevons** | day-on-day relatives multiplied | headline; revises cleanly, each day's move attributable to that day |
| **Direct fixed-base** | every day compared straight to the base day | immune to drift; matched sample shrinks over time |
| **GEKS-Jevons** | geometric mean of *all* bilateral paths within a rolling window, mean-spliced | transitive — drift has nowhere to accumulate |
| **Hedonic time-dummy** | see §3.6 | quality-adjusted, uses the unmatched sample too |

The **gap between the chained headline and GEKS is published as a measured drift
diagnostic**, including its trend per day. If that gap grows systematically with series
length — the signature of accumulated noise rather than genuine divergence — GEKS should be
promoted to headline, and we will see it coming rather than discover it later.

### 3.6 Hedonic time-dummy: the quality-adjusted robustness check

$$\log(\text{total fare}) \sim \text{route} + \text{carrier} + \text{lead time} + \text{dep dow} + \text{dep hour band} + \text{is holiday} + C(\text{collection date})$$

Fitted by weighted least squares with heteroskedasticity-robust (HC1) standard errors over
**all** observed quotes, not just matched pairs. Every characteristic that makes one fare
structurally different is absorbed by its own fixed effect; what remains in the
collection-date coefficients is the price movement common to everything, holding product
mix constant. Exponentiate, and that is a quality-adjusted index with a confidence interval.

Two details that matter:

- **Holidays are real.** Indian public holidays come from the `holidays` package's India
  calendar. If that calendar is unavailable the term is *dropped*, not defaulted to False —
  a silently all-False holiday dummy would let genuine Diwali fare spikes leak into the
  date coefficients and be reported as inflation.
- **Observations are basket-weighted.** An unweighted OLS gives every *quote* equal say, so
  a thin route on which four carriers happen to publish counts four times as much as a
  trunk route on which one does. The headline weights DEL-BOM at 8.8% because that is its
  passenger share. Comparing an unweighted hedonic against a passenger-weighted Jevons and
  calling the difference a finding would be a mistake; each observation is therefore
  weighted by (route weight × lead-time weight), normalised by the number of observations
  in its cell that day.

**The headline stays Jevons.** The hedonic is reported beside it, never instead of it. Two
series built on different principles from overlapping but non-identical samples that track
each other are far more convincing than either alone. If they diverge, the divergence is a
statement about basket composition, and *that* is the finding.

### 3.7 Revisions

Every published value is stored with its **computation timestamp**. Recomputing a day —
because a late scrape landed, or a bug was fixed — writes a **new vintage** beside the old
one. Nothing is ever overwritten.

`apix.v_index_revisions` and `/v1/revisions` expose every value that has ever changed and
by how much. Statistics agencies care about revisions more than almost anything, and it is
the part of a compilation system that never gets built until someone has been embarrassed
by not having it.

---

## 4. Validation

Three checks, all runnable:

1. **Against MoSPI's published CPI air-transport sub-class** (via eSankhyiki), at monthly
   frequency once a full month of collection exists.
2. **Against DGCA average-fare data** for the same sectors, as a level check rather than a
   movement check.
3. **Parallel manual collection** on a handful of cells, proving the collector reads what a
   human sees. Eurostat recommends exactly this when introducing web-scraped data into an
   official series.

A high correlation with the CPI sub-class is supporting evidence, **not** proof of
correctness — the two measure related but distinct things at different frequencies from
different samples. A *low* correlation is the more interesting result and would need
explaining rather than hiding: most likely it would mean the monthly spot-price approach is
missing intra-month movement that daily collection captures, which is the entire premise of
this project.

---

## 5. What this index cannot tell you

See `docs/limitations.md`. It is not an appendix; read it before quoting a number.

---

## References

- Eurostat, *Practical guidelines on web scraping for the HICP*, November 2020
- MoSPI, *FAQs on the CPI 2024 series* (base 2024=100, COICOP 2018), Annexure V
- ILO/IMF/OECD/UNECE/Eurostat/World Bank, *Consumer Price Index Manual: Concepts and
  Methods* (2020) — elementary aggregates, multilateral methods, chain drift
- DGCA monthly domestic city-pair traffic
