# APIx — Real-time Airfare Price Index for India

**Smart India Hackathon 2026 · Problem statement SIH26056 · MoSPI, Data Informatics & Innovation Division**

A daily, matched-model price index for Indian domestic airfares, built to a standard the
National Statistical Office could consume to augment the CPI transport division.

---

## The argument

The CPI is not a report. It is the number the RBI targets under flexible inflation
targeting, and an input to interest-rate decisions. Transport carries **8.796%** of the
new CPI 2024 series (base 2024=100, COICOP 2018).

MoSPI already collects airfares online — it counts 12 "online markets" alongside 1,465
rural and 1,395 urban physical markets. So whether web data belongs in the CPI is settled.
The open question is **sampling design**.

Indian domestic fares move 200–400% within a single day on the same sector under dynamic
pricing. A monthly spot price from a handful of outlets cannot represent that. Eurostat's
HICP web-scraping guidelines describe INSEE collecting French rail fares at 2, 10, 30 and
60 days before departure for precisely this reason — yield management makes a single spot
price meaningless. The T+1/7/15/30/45 windows here are the same design.

> When a market prices dynamically, the moment you choose to sample effectively determines
> the inflation number you print. Right now that choice is implicit. This makes it
> explicit, auditable and reproducible.

**The index is the deliverable. The scraper is plumbing.**

---

## What is actually here

| Layer | What it does |
|---|---|
| `collector/` | Collects 120 basket cells daily under hard ethical limits. Two extraction strategies per source, and a row written for every cell whether or not it produced a price. |
| `index/` | Jevons elementary aggregates, DGCA route weights, three-scenario lead-time band, donor imputation, chaining, **GEKS multilateral**, hedonic time-dummy, revision vintages. |
| `api/` | FastAPI with versioned endpoints and a published OpenAPI spec. Every value carries its availability rate. |
| `dashboard/` | Streamlit: index and band, lead-time curve, route heatmap, availability, robustness, health. |
| `db/migrations/` | Postgres schema where `raw_quotes` is append-only **enforced by the database**, not by convention. |
| `docs/` | Methodology, scraping policy, limitations. Deliverables, not formalities. |
| `data/` | The frozen basket, route weights, and every day's raw observations as committed CSV. |

### Four index constructions, published together

Because the honest answer to "why should we trust your number?" is not "trust it".

1. **Chained Jevons** — the headline. Standard CPI construction, revises cleanly.
2. **Direct fixed-base Jevons** — immune to chain drift; its matched sample shrinks over time.
3. **GEKS-Jevons, rolling window with mean splice** — the multilateral method Eurostat and
   the ILO recommend for high-frequency scanner and web data. Transitive, so drift has
   nowhere to accumulate.
4. **Hedonic time-dummy** — quality-adjusted, basket-weighted, controlling for route,
   carrier, lead time, day of week, departure-time band and Indian public holidays.

The gap between the chained headline and GEKS is published as a **measured chain-drift
diagnostic**, so we report drift as a number rather than asserting there is none.

---

## Reproduce every published number from this repository alone

No credentials, no database, no infrastructure:

```bash
pip install -e ".[dev]"
python -m index.build --source csv --out data/index
```

Every daily observation is committed as CSV under `data/raw/`, so `git log data/raw/` *is*
the collection history. If an index cannot be recomputed from its raw observations, it
cannot be audited — and an index that cannot be audited has no business near a CPI.

Run the service and dashboard:

```bash
docker compose up api dashboard      # http://localhost:8000/docs and :8501
```

Run the tests, including the index maths checked against a hand computation to ten
decimal places:

```bash
pytest -q
python scripts/verify_basket.py      # proves the frozen basket still reproduces from DGCA data
```

---

## The basket — frozen, and verifiably so

| Dimension | Specification |
|---|---|
| Routes | 12 city pairs, **directional** (DEL→BOM ≠ BOM→DEL) = 24 directed routes |
| Lead times | T+1, T+7, T+15, T+30, T+45 — **strata, never averaged together** |
| Departure date | **Derived, never fixed.** On collection day *d*, the T+15 cell means departure *d*+15 |
| The offer | Cheapest economy, 1 adult, one-way, non-stop where available, no baggage add-on, non-refundable |
| Timing | One run/day at a fixed IST time, with a short randomisation window |
| Volume | **120 route-cells/day** |

Route weights come from DGCA monthly domestic city-pair passenger volumes over a trailing
12-month window. The 12 pairs cover **22.2% of India's domestic passengers**.

`scripts/verify_basket.py` recomputes the weights from the DGCA file, checks its SHA-256,
and fails CI if anything drifted. `basket.yaml` is the one file where a plausible-looking
hand edit would be invisible in review and would silently redefine a live series.

**Lead-time weights are not in that table, deliberately.** No public source gives how far
in advance Indians book. Rather than invent a booking curve, the index is computed under
three defensible scenarios and the spread is published as a sensitivity band.

---

## Ethical scraping — the short version

Full policy in [`docs/scraping-policy.md`](docs/scraping-policy.md).

- **robots.txt parsed per domain before every request, and every decision logged** to a
  queryable table. When someone asks whether this is legal, the answer is a query.
- Fail-closed: an unreachable robots.txt means we do not scrape that domain.
- Identified User-Agent naming the project and a contact email. We never spoof a browser
  identity to hide.
- One request per domain every few seconds, randomised, with exponential backoff. We need
  120 cells a day, not 120 a minute — going faster buys nothing and costs the argument.
- **Public, unauthenticated pages only.** No accounts, no logins, no paywall bypass.
- **We never solve or bypass a CAPTCHA.** We back off and record the cell as `blocked`.
  Correctly-handled missingness is the answer a statistics agency would give.
- **No personal data, ever.** Prices only — which is why the DPDP Act does not attach.

---

## Non-negotiables

From `CLAUDE.md`, enforced in code and in CI:

1. **The collector runs every day without anyone watching.** Uptime beats features.
2. **Raw quotes are immutable** — enforced by database rule, tested.
3. **The basket is frozen** — verified against source data in CI.
4. **Never fabricate a number.** No placeholder weights, no synthetic fares, no `np.random`
   anywhere near the index. `scripts/simulate.py` exists for pipeline testing and has five
   independent safeguards keeping its output out of anything anyone could mistake for a
   measurement.
5. **Never report an index value without its availability rate.**

---

## References

- Eurostat, *Practical guidelines on web scraping for the HICP*, November 2020
- MoSPI, *FAQs on the CPI 2024 series* (base 2024=100, COICOP 2018)
- ILO/IMF/OECD/UNECE/Eurostat/World Bank, *Consumer Price Index Manual: Concepts and Methods* (2020)
- DGCA monthly domestic city-pair traffic, via [Vonter/india-aviation-traffic](https://github.com/Vonter/india-aviation-traffic)
- MoSPI eSankhyiki MCP — [nso-india/esankhyiki-mcp](https://github.com/nso-india/esankhyiki-mcp)

MIT licensed.
