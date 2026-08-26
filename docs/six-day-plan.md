# The six-day plan

A compressed build. The original brief spread this over 26 days; this is the same work in
six, with one non-negotiable constraint that shapes the whole ordering:

> **You cannot go back and scrape what a fare was last Tuesday.**

Every day the collector is not running is a day permanently missing from the end of your
series. So the collector starts on day 0, ugly if necessary, and everything else is built
around a clock that is already ticking. On 20 September the series will be as long as the
number of days since the collector first ran — nothing you do in week two can change that.

Status markers: ✅ done · ⏳ in progress · ⬜ not started

---

## Day 0 — the clock starts ✅

The whole day judged by one question: *is a scheduled job writing real rows?*

- ✅ Monorepo pushed to GitHub, public — the Actions run history doubles as a public audit
  log of when collection happened
- ✅ **Basket frozen from real DGCA city-pair volumes.** 12 pairs, 24 directed routes,
  120 cells/day, 22.2% of domestic passengers. Required merging the DGCA's duplicate
  `MUMBAI` / `MUMBAI (MUMBAI)` labels and its three-way Goa split — without that, DEL–BOM's
  weight is understated by ~40%
- ✅ `verify_basket.py` recomputes the weights from source and checks its SHA-256, in CI
- ✅ **Supabase live** (free tier, Mumbai). `raw_quotes` append-only enforced by database
  rule and *tested* — a deliberate UPDATE and DELETE both left the row untouched. All five
  data-contract constraints verified to reject bad rows
- ✅ **Index engine complete**: Jevons, donor imputation, chaining, DGCA route weights,
  three-scenario band, GEKS multilateral, direct fixed-base, hedonic time-dummy, revision
  vintages. 35 tests green including the index maths against a hand computation to 10 dp
- ✅ Collector with the full ethical stack: robots gate with logged audit trail, per-domain
  rate limiting with jitter, exponential backoff, CAPTCHA detection that backs off
- ✅ FastAPI + OpenAPI, Streamlit dashboard, dbt layer, pandera contracts
- ✅ `docs/`: methodology, scraping policy, limitations, this plan, the deck
- ⏳ **Source probe running on GitHub Actions** — the one thing that cannot be determined
  from a development machine

**The open risk.** Every source adapter is marked `unverified`. The URL patterns are the
sites' own public search deep links, but nobody has confirmed what an Indian OTA does when
the request arrives from an Azure US datacentre. That is what the probe answers, and it is
the risk that quietly kills most teams attempting this problem statement around week three.

---

## Day 1 — make one source boring ⬜

Read the probe report. Pick the **single** highest-scoring source and make it work end to
end. Not three sources badly — one source you would bet the month on.

- ⬜ Pin the JSON fare endpoint the probe found; write a real `extract_from_json` for it
  instead of relying on the generic structural harvester
- ⬜ Run `python -m collector.run --limit 10 -v` until ten cells come back priced
- ⬜ Flip that source's `confidence` to `verified` — and **only** that one
- ⬜ Let the 02:00 IST cron fire unattended. Confirm the next morning that `data/raw/`
  gained a file and the run is green
- ⬜ Wire `DATABASE_URL` into repository secrets so rows land in Postgres as well as CSV

**Done when:** two consecutive unattended runs have written real rows. Nothing else counts
today. Resist every temptation to add a second source before this is boring.

**If the probe says every source walls the runner:** this is the fork in the road, and it is
better to hit it on day 1 than day 5. Options in order of preference — (a) a self-hosted
runner on an Indian IP, which is free on any machine you leave on; (b) airline sites
directly rather than OTAs, which are often far less defended than metasearch; (c) reduce to
whichever single source does answer and be explicit in the write-up about single-source
risk. Do not respond by evading the block. A project that documents being blocked is
credible; one caught working around it is finished.

---

## Day 2 — the second and third sources ⬜

Only now. A second source is what turns a single point of failure into a system.

- ⬜ Add source two, then source three. Different underlying inventory where possible —
  a metasearch aggregator and a direct OTA give a genuinely independent second reading;
  two aggregators reselling the same feed give you one reading twice
- ⬜ Full 120-cell run. Target availability > 80%
- ⬜ Tune `--min-interval` upward if anything returns 429. Slower is always the right answer
- ⬜ Turn on `--min-availability 0.5` so a collapsed run fails the workflow **red** rather
  than silently writing a near-empty day

**Done when:** a full 120-cell run completes above 80% availability, and the per-source
health breakdown shows where the losses are.

---

## Day 3 — the index against real data ⬜

The engine is built and tested. Today it meets actual fares, which is different.

- ⬜ `python -m index.build --source csv --out data/index` on three real days
- ⬜ Read the imputation tier table. High tier-2/3 imputation means a source is failing on
  specific strata, not that the market went quiet — check before you interpret anything
- ⬜ Sanity-check levels by eye against a manual search on two cells. **This is the check
  that catches a collector reading the wrong number off the page**, and no unit test
  substitutes for it
- ⬜ Record the first real chain-drift diagnostic. Note it; it only becomes meaningful with
  length, but the baseline matters
- ⬜ Update `docs/methodology.md` with anything real data taught you — same day, while the
  decision is fresh

**Done when:** a real index series exists, and you can explain every movement in it.

---

## Day 4 — make it visible ⬜

- ⬜ Deploy the dashboard to Streamlit Community Cloud (free, connects straight to the repo)
- ⬜ Deploy the API — Fly.io or Render free tier — and check `/openapi.json` renders
- ⬜ Pull the CPI benchmark: `scripts/fetch_cpi_benchmark.py --csv <eSankhyiki export>`.
  Export the **air-transport sub-class**, not the Transport division; the division is
  dominated by fuel and moves for unrelated reasons
- ⬜ Screenshot the dashboard with real data on it. September's submission needs one
  credible image more than it needs a sixth feature

**Done when:** you can send someone a URL.

---

## Day 5 — evidence and honesty ⬜

- ⬜ `python scripts/backtest.py` — three comparisons, each reported as it comes out
- ⬜ **Parallel manual collection.** Pick five cells, search them by hand, compare against
  what the collector recorded that day. Eurostat recommends exactly this when introducing
  web-scraped data into an official series, and it is the strongest single piece of
  validation available to you. Put the table in the deck
- ⬜ Re-read `docs/limitations.md` against what you now know and add anything real
  collection revealed
- ⬜ Rehearse the panel questions in `docs/pitch.md` until the first sentence of each is
  automatic

**Done when:** you have a table showing the collector reads what a human sees.

---

## Day 6 — ship ⬜

- ⬜ Deck from `docs/pitch.md`. Twelve slides, three minutes, rehearsed to time
- ⬜ Two-minute screen recording: dashboard → API docs → the Actions run history showing
  daily green ticks → the robots log table. **The run history is the most persuasive thing
  you own** — it is unfakeable evidence that this ran every day without you
- ⬜ **Submit early.** The portal will be slow on 20 September and that is not a risk worth
  taking for a few extra hours of polish

---

## Then: 25 August → 20 September, and on to December

The collector keeps running. That is the whole job.

By the submission deadline you will have around three and a half weeks of continuous daily
collection; by the December finale, well over ninety days. Ninety days changes what you are
allowed to claim — seasonal patterns become visible, the chain-drift diagnostic becomes
informative, and the CPI correlation is computed on enough months to mean something.

Between now and then, in priority order:

1. **Keep the collector green.** Check the Actions tab weekly. A red run caught within a day
   costs you one cell; caught after a fortnight it costs you a fortnight.
2. Add a fourth source only once the first three are boring.
3. Rebuild the dashboard in Next.js for the finale — Streamlit is right for September and
   looks like a prototype in December.
4. Add seasonal adjustment once there is enough series to support it.
5. Rehearse to three minutes flat.

---

## What to do when something breaks

**A source stops parsing.** Run the probe workflow — it will tell you what the site moved
to. This is why the probe is a committed script rather than something someone did once by
hand in devtools.

**Availability drops below 80%.** Check the per-source health table first. One source dying
is a different problem from every source degrading, and the fix is different.

**The workflow goes red.** Look at whether it failed or whether it *deliberately* failed —
the runner exits non-zero below the availability floor, which is a feature. A collapsed run
that reports success is the failure mode worth fearing.

**Supabase pauses.** Free-tier projects pause after a week idle; daily writes prevent it.
If it happens anyway, nothing is lost — the CSVs in `data/raw/` are the authoritative copy
and the index rebuilds from them with no database at all.

**You miss a day.** Record it. Do not backfill, do not interpolate across it, do not
pretend. A gap that is labelled is fine; a gap that is filled quietly is fraud, and the
index publishes gaps by design.
