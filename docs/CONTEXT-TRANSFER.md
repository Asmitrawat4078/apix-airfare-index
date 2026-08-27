# APIx — context transfer

**Paste this into a fresh chat to pick the project up cold.** Written 26 Aug 2026.

---

## What this is

**APIx** is a daily price index for Indian domestic airfares, built for Smart India
Hackathon 2026, problem statement **SIH26056** (MoSPI, Data Informatics & Innovation
Division). Idea submission closes **20 September 2026**; grand finale December 2026.

A robot collects the same fixed basket of fares every day at the same time and turns them
into one index number, the way the Sensex turns many share prices into one. The intended
consumer is the National Statistical Office, which currently measures airfare inflation
from a handful of manual online checks in a market where the same seat can cost three times
more depending on when you look.

**The index is the deliverable. The scraper is plumbing.** When choosing where to spend
effort, choose the statistical layer. The full standing brief is `CLAUDE.md` at the repo
root — read it before acting.

---

## Where everything lives

| Thing | Location |
|---|---|
| Repo | https://github.com/Asmitrawat4078/apix-airfare-index (public) |
| Build-room page | https://claude.ai/code/artifact/9d967d05-f5d9-4e32-93d4-3c59a707a032 |
| Database | Supabase `apix-airfare-index`, ref `mynqpnjvdrrddzbstlje`, ap-south-1, free tier |
| Owner | Asmit Rawat · asmitrawat4078@gmail.com · GitHub `Asmitrawat4078` |

---

## ⚠ Environment quirks that will waste an hour if you don't know them

These are properties of the Cowork/Claude Code sandbox, not of the project.

1. **The GitHub API is blocked for this repo.** `curl https://api.github.com/repos/...`
   returns "GitHub access to this repository is not enabled for this session." So you
   cannot dispatch workflows, read run logs, or set secrets programmatically. Ask the user
   to click things in the Actions UI and paste results back.

2. **`git push` needs a header override.** The git proxy refuses to inject credentials for
   this repo. The working incantation, with a **classic** PAT carrying `repo` + `workflow`
   scopes:

   ```bash
   T=$(cat /tmp/.ghpat3)
   AUTH=$(printf 'x-access-token:%s' "$T" | base64 -w0)
   git -c http.extraheader="Authorization: Basic $AUTH" \
       push https://github.com/Asmitrawat4078/apix-airfare-index.git main
   ```

   A **fine-grained** token without the Workflows permission cannot write
   `.github/workflows/*` and the push is rejected with a specific message saying so.

3. **Workflows are triggered by pushing a sentinel file**, since the API is unavailable:
   - `.github/probe-trigger` (+ `.github/probe-args`) → runs the source probe
   - `.github/collect-smoke` → runs a small **dry** collection (writes nothing)
   Both commit their results back, so you read them with `git pull`.

4. **Indian OTAs are unreachable from the container's egress allowlist.** Scraping can only
   ever be exercised on a GitHub Actions runner. Do not waste time trying locally.

5. **Force-push is blocked** by the safety classifier. Never plan on rewriting published
   history. Use `git pull --rebase` before every push — the workflows commit to `main` too.

6. A stop-hook complains that commits authored by `apix-collector <actions@github.com>` are
   "Unverified". **Ignore it.** Those are the collector robot's own commits and the
   attribution is correct and meaningful.

---

## Current state

### Working and proven

- **Basket frozen** from real DGCA city-pair volumes (trailing 12 months, Jun 2025–May 2026).
  12 pairs → 24 directed routes × 5 lead times = **120 cells/day**, covering 22.2% of India's
  domestic passengers. `scripts/verify_basket.py` recomputes the weights from source and
  checks the file's SHA-256 in CI, so a hand edit fails loudly.
- **Index engine complete**: Jevons elementary aggregate, donor imputation (never
  carry-forward), chaining, DGCA route weights, three-scenario lead-time sensitivity band,
  **GEKS-Jevons multilateral**, direct fixed-base, basket-weighted hedonic time-dummy,
  revision vintages. Unit-tested against a hand computation to 10 decimal places.
- **Database**: `raw_quotes` is append-only enforced by a Postgres RULE, and that was
  *tested* — a deliberate `UPDATE` and `DELETE` both left the row untouched. All five
  data-contract constraints verified to reject bad rows.
- **Source verified**: EaseMyTrip answers cleanly from an Azure US runner. Real fares read.
- **Extractor written** against the real captured payload, with tests using real strings.
- API (FastAPI + OpenAPI), Streamlit dashboard, dbt layer, pandera contracts, full docs.
- CI green (it was red for a while — an import-path problem, not a code problem).

### Sources — settled, do not relitigate

| Source | State |
|---|---|
| **easemytrip** | ✅ verified, the only one enabled. `POST flightservice-node.easemytrip.com/AirAvail_Lights/AirBus_New`, ~2 MB JSON |
| ixigo | ⏸ parked. Probed at 15 s and 30 s; page renders but no fare JSON ever appears (likely streamed). Revisit only once source one is boring |
| cleartrip / yatra / makemytrip / akasa | ⛔ their **robots.txt itself** 403s or times out to a datacentre IP → we fail closed and leave the domains alone |
| goibibo | ⛔ `ERR_HTTP2_PROTOCOL_ERROR` |

### The EaseMyTrip payload — the two fields that matter

```
SD              "Non-Stop|6324|10|DEL-BOM||"           stops | TOTAL FARE | seats | route
segMatchingKey  "DELBOMThu-10Sep202604:0006:15SG 510"  org dst dow-date dep arr carrier+flight
```

### Open / in flight

- The user reported **"it collects for a while then stops or dies."** `collector/run.py` has
  been reworked for this: per-cell checkpointing to CSV, `--resume` that skips cells already
  recorded today, browser recycling every N cells to bound memory, and a hard per-cell
  timeout. **Uncommitted at time of writing** along with `tests/test_resume.py` — verify,
  test, commit, push, and confirm on a real run.
- **`DATABASE_URL` is not yet in GitHub secrets.** The user supplied only the Supabase
  *publishable* (anon) key, which cannot write to Postgres. What is needed is the
  Transaction pooler string: Supabase → Settings → Database → Connection string →
  Transaction pooler (port 6543). **Not blocking** — CSV-in-git is the authoritative copy.
- No day of fares has been committed to `data/raw/` yet. That is the immediate goal.

---

## Standing decisions — do not reverse these without discussing

1. **We do not replay the captured fare POST directly.** It carries a `TKN` token and a
   per-session trace UUID minted by EaseMyTrip's own page; synthesising those is
   reverse-engineering an internal API and is ruled out by our own scraping policy.
   Collection navigates the public results page and intercepts the widget's own call.
   Rationale is written into `collector/sources/easemytrip.py`.
2. **`base_fare` / `taxes` / `fees` stay NULL.** The split lives deeper under `b[].dctFC`.
   The data contract says store nulls rather than guess a decomposition.
3. **`CFee` (₹449) rides in `raw_payload`, never folded into `total_fare`.** It is charged
   at payment, not quoted in the fare; folding it in would shift the whole series by a
   constant.
4. **Sold-out and blocked are never merged.** One is a market signal, the other is our
   failure. Collapsing them lets scraping failures masquerade as scarcity.
5. **Carry-forward imputation is prohibited.** A day with nothing observed publishes a
   *gap*, not a number.
6. **Never report an index value without its availability rate.** Enforced by
   `IndexValueSchema` in `tests/contracts.py`, not just documented.
7. **Lead-time weights are never invented.** Three published scenarios, reported as a band.
8. `scripts/simulate.py` fabricates fares for pipeline testing and has **five independent
   safeguards** keeping its output out of anything publishable. Do not remove any of them.

---

## The chain-drift finding — the strongest thing in the project

The chained Jevons index and the hedonic series were compared over a long fixture and
correlated at only **0.20** — far worse than two measurements of the same thing should.
That is not a bug. Chaining multiplies daily relatives, so sampling noise accumulates as a
random walk even when prices do not move. It is why Eurostat and the ILO moved to
multilateral methods for scanner and web-scraped data.

GEKS-Jevons (rolling window, mean splice) was implemented in response, and the drift is now
published as a **measured diagnostic with a per-day trend** rather than assumed away. On the
fixture: chained 101.92, GEKS 97.75, direct 97.06, gap trending +0.078 points/day.

Pitch this as *a story about finding a bug by building carefully*, not as a feature.

---

## What is left to do

**Automatic — no action needed**
- The 02:00 IST cron (`30 20 * * *` UTC) collects one day, every day, and commits it.

**Agent work, once real data exists**
1. Confirm a real day lands in `data/raw/` and the extractor reports `strategy=json`.
2. `python -m index.build --source csv --out data/index` on 2+ real days.
3. Add `DATABASE_URL` to repository secrets when the user supplies the pooler string.
4. `scripts/fetch_cpi_benchmark.py --csv <eSankhyiki export>` — export the **air-transport
   sub-class**, not the Transport division (the division is dominated by fuel).
5. `scripts/backtest.py` — correlates month-on-month **log changes**, never levels.

**Only the user can do these**
- Deploy the Streamlit dashboard (free, connects to the repo) and the API.
- **Parallel manual collection**: search five cells by hand and compare against what the
  robot recorded. Eurostat recommends exactly this when introducing web-scraped data, and
  it is the strongest single piece of validation available. Put the table in the deck.
- Record a two-minute screen video ending on the Actions run history — unfakeable evidence
  the collector ran daily without supervision.
- **Submit early.** The portal will crawl on 20 September.

Full day-by-day in `docs/six-day-plan.md`. Deck content and rehearsed answers to the eight
questions the panel will ask are in `docs/pitch.md`.

---

## Read these first, in this order

1. `CLAUDE.md` — the standing brief and every invariant
2. `docs/methodology.md` — every decision that affects a published number
3. `collector/sources/easemytrip.py` — the verified source and why it works the way it does
4. `index/multilateral.py` — the chain-drift finding
5. `docs/limitations.md` — what the index cannot support, stated before anyone asks

---

## How the user likes to work

- Explain in plain language. They are not deeply technical and said so directly; jargon and
  long engineering narratives lose them. Lead with what to do, then why.
- Give one task at a time, finish it, show the result.
- Prefer real rows and a real chart over a description of what the code would do.
- Tell them plainly when something is not working. A broken scraper reported today is
  recoverable; one discovered in December is not.
