# APIx — pitch

Slide content and speaker notes. Twelve slides, built for a **three-minute** delivery with
a longer version available if the panel wants it. Timings assume you talk at a normal pace;
if you are rushing, cut slides 8 and 10 first, never 4 or 6.

The governing principle: **a dozen teams will demo a scraper. Almost none will be able to
defend how their number was computed.** Every slide here spends its seconds on the part
nobody else can defend.

---

## 1 · The number that moves interest rates — 15s

> The CPI is not a report. It is the number the RBI targets under flexible inflation
> targeting — an input to rate decisions. Transport carries 8.796% of the new CPI 2024
> series.

**Speaker note.** Do not open with the problem statement. Open with what is at stake. The
panel knows the PS; they do not necessarily have "this feeds monetary policy" front of mind,
and every subsequent methodological choice you make sounds proportionate once they do.

---

## 2 · The argument is already settled — except the hard half — 20s

> MoSPI already collects airfares online. It counts twelve "online markets" alongside 1,465
> rural and 1,395 urban physical markets. So whether web data belongs in the CPI is settled.
>
> **The open question is sampling design.**

**Speaker note.** This disarms the objection you would otherwise spend a minute defending,
and it signals you read the source material rather than the problem statement. Pause here.

---

## 3 · Why a monthly spot price cannot work — 25s

> Indian domestic fares move 200–400% within a single day on the same sector under dynamic
> pricing.
>
> Eurostat's HICP guidelines describe INSEE collecting French rail fares at 2, 10, 30 and 60
> days before departure — precisely because yield management makes a single spot price
> meaningless. Our T+1/7/15/30/45 windows are the same design.

**Visual.** One route, one day, the fare at each of the five lead times. Nothing else.

**Speaker note.** The Eurostat/INSEE precedent is the single highest-leverage sentence in
the deck. It converts "clever student idea" into "established international practice, not
yet applied here". Say the word *precedent* out loud.

---

## 4 · The thesis — 15s · **do not cut**

> When a market prices dynamically, the moment you choose to sample effectively determines
> the inflation number you print.
>
> Right now that choice is implicit. **This makes it explicit, auditable and reproducible.**

**Speaker note.** Stop talking after "reproducible". Let it sit for a beat. This is the
sentence they will repeat to each other afterwards, and it only lands if you give it room.

---

## 5 · The basket, frozen on day one — 20s

> 12 city pairs, **directional** — DEL→BOM and BOM→DEL are different products.
> 5 lead-time strata. **120 cells collected every day at a fixed IST time.**
> Departure date is *derived*, never fixed: on day *d*, the T+15 cell means departure *d*+15.
>
> Route weights are real DGCA passenger volumes. The basket covers **22.2% of India's
> domestic passengers**.

**Speaker note.** If asked why only 12: a basket you can collect every day for months,
ethically, beats a bigger one that breaks. Coverage can grow; a broken series cannot be
recovered retrospectively — you cannot go back and scrape last Tuesday's fare.

---

## 6 · The number we refuse to invent — 25s · **do not cut**

> To weight T+1 against T+45 you need India's booking curve. **No public source publishes
> it.** Airlines have it and don't release it.
>
> We could have picked a plausible split and never mentioned it again. Instead: three
> scenarios, and we publish the spread as a band.

**Visual.** The index with the sensitivity band shaded around it.

**Speaker note.** This is the slide that separates you from the field, and it works because
it is an admission. Most teams hide their weakest input; showing it, quantified, reads as
maturity rather than weakness. Land the line: **"the band width is itself a finding"** —
where the scenarios agree, the headline is robust to what we don't know; where they diverge,
the number depends on an assumption nobody has data for.

---

## 7 · Jevons, and why not the alternatives — 20s

> Within each stratum: **geometric mean of price relatives.** Eurostat's recommended
> elementary aggregate for web-scraped data.
>
> Dutot lets a ₹22,000 last-minute fare dominate a ₹6,000 one. Carli fails time reversal and
> carries a known upward bias. Jevons is base-invariant, symmetric in time, and damps the
> downward bias from non-random missingness — which for airfares is severe.

**Speaker note.** Do not read the formula aloud. Name the two alternatives and why each
fails; that is what demonstrates you chose rather than copied. Our test suite includes an
explicit time-reversal test so an accidental regression to Carli fails CI.

---

## 8 · We found chain drift, and fixed it — 25s

> We built the chained index. We built the hedonic regression. Over a long fixture they
> disagreed far more than two measurements of the same thing should.
>
> That is **chain drift** — chaining multiplies daily relatives, so sampling noise
> accumulates as a random walk even when prices don't move. It's why Eurostat and the ILO
> moved to multilateral methods for scanner and web data.
>
> So we implemented **GEKS-Jevons on a rolling window**, and we publish the drift as a
> measured number with its trend per day.

**Visual.** Four lines — chained, GEKS, direct fixed-base, hedonic — and the gap annotated.

**Speaker note.** This is the strongest slide you have and it is strongest because it is a
*story about finding a bug*, not a feature list. Say "we found this by building it, not by
reading about it". If they ask why the headline is still chained: because chained revises
cleanly and each day's movement is attributable to that day's observations — and if the gap
keeps growing we promote GEKS, which is why we measure it.

---

## 9 · Missing data is a methodology, not an accident — 20s

> A sold-out flight is a market signal. A blocked scrape is our failure. **They are never
> merged** — collapsing them would let our scraping failures masquerade as market scarcity.
>
> Missing cells are imputed by their donor stratum's movement, flagged individually, and
> **every index value is published with its availability rate.** Carry-forward is prohibited:
> repeating yesterday's price asserts zero change on no evidence, and flattens the series
> exactly when the market is moving most.

**Speaker note.** The sell-out frequency chart is a genuine contribution the CPI cannot
currently produce. Mention that; it reframes a data-quality slide as a new capability.

---

## 10 · Ethics, as an engineering artifact — 15s

> robots.txt parsed before every request and **every decision logged to a queryable table**.
> Identified User-Agent with a contact email. Rate-limited to one request per domain every
> few seconds. Public pages only. **We never solve a CAPTCHA** — we back off and record the
> cell as blocked.
>
> Correctly-handled missingness is the answer a statistics agency would give.

**Speaker note.** If asked "is this legal?", do not argue — hand them
`docs/scraping-policy.md` and offer to run the robots-log query live. A claim you can query
beats a claim in a slide, and no personal data is collected, which is why DPDP doesn't attach.

---

## 11 · Built to be consumed, and to be wrong in public — 20s

> Versioned API with a published OpenAPI spec. **Revision history as a first-class
> resource** — recomputing a day creates a new vintage beside the old one; nothing is ever
> overwritten. `raw_quotes` is append-only enforced by database rule, and we tested it by
> trying to UPDATE and DELETE a row.
>
> And `/v1/ingest/mis` — the documented extension point for airline booking data we don't
> have.

**Speaker note.** Revisions are the detail that reads as *built by someone who has shipped
official statistics*. Agencies care about revisions more than almost anything and no student
project implements them.

---

## 12 · Close — 15s

> Four index constructions, published side by side. A sensitivity band around the one input
> nobody publishes. An availability rate on every number. A revision history. And a
> collector that has run every day without anyone watching.
>
> **You cannot go back and scrape what a fare was last Tuesday.** That's why the collector
> ran from day zero.

---

# The questions they will ask

Rehearse these until the first sentence is automatic. In every case, **concede the real
limit first, then show the architecture that anticipates it** — that ordering is what reads
as maturity.

### "You're collecting quoted fares. The CPI wants transaction prices."

The hardest question — answer it head-on, do not deflect.

> Quoted fares are what the consumer faces at the point of decision, and the CPI already
> uses offer prices for most of its basket — a shelf price is not a transaction price
> either. But you're right about the real limit: without airline booking-class inventory we
> cannot weight by seats sold. So we designed the API to accept an airline MIS feed —
> `/v1/ingest/mis` returns 501 today and the weighting layer already takes an external
> weight source. Naming the gap and having architected for it is the best we can honestly do.

### "Your index moved 8% — price change or basket change?"

> That's exactly what the matched model and the hedonic exist to separate. The matched model
> compares the identical offer over time. The hedonic uses all observed quotes and absorbs
> route, carrier, lead time, day of week, departure hour and holidays into fixed effects, so
> its date coefficients are quality-adjusted. Here they are tracking each other — and here's
> the availability rate for that period, which tells you how much of it rested on imputation.

### "Why the geometric mean?"

> Jevons, per Eurostat's recommendation for web-scraped data. Base-period invariant, passes
> time reversal — we have a unit test for that specifically — doesn't let high-priced cells
> dominate the way Dutot does, and damps the downward bias from missing observations.

### "What happens when MakeMyTrip blocks you?"

> Then that cell is `blocked`, which is a different row from `sold_out`, and the health page
> catches it within one collection cycle. Multiple sources per route, graceful degradation to
> stratum imputation, and a published availability rate so the reader can see it happened.
> A system that survives losing a source is the answer — and we do not work around the block.

### "Is this legal?"

> Public unauthenticated pages, robots.txt respected *and logged*, rate-limited far below
> any load threshold, identified User-Agent with a contact email, and no personal data at
> all — which is why DPDP doesn't attach. Here's the policy document, and here's the table
> of every robots decision we've ever made.

### "If this became official, wouldn't airlines game it?"

Engage with this properly; it is a good question and dismissing it costs you.

> Yes, and it's a known problem in official statistics rather than a new one. The responses
> are standard: stop publishing the exact collection instant, widen the randomisation
> window, make the basket confidential, rotate a reserve sample. We already randomise within
> a window. For a research prototype we chose transparency over resistance to gaming,
> because an index nobody can check is worth nothing — but the design change is a
> configuration switch, not a rewrite.

### "How do we know your scraper reads the right number?"

> Three ways. The extraction layer explicitly refuses to mistake a base fare for a total —
> that's a tested behaviour, because taking the smallest number in sight would understate
> every fare by its tax component. Data contracts fail the pipeline on implausible fares.
> And the check that would actually falsify us is parallel manual collection on a handful of
> cells, which Eurostat recommends when introducing web-scraped data — it's in the plan.

### "Only 12 routes?"

> 24 directed routes covering 22.2% of domestic passengers, including one thin regional route
> so it isn't purely a metro-trunk measure. The constraint is politeness, not engineering:
> at one request per domain every few seconds we can sustain 120 cells a day indefinitely
> without ever being a load a booking site would notice. Coverage scales linearly with
> collection time; credibility doesn't scale back once you've been blocked.

### "What's the weakest part of this?"

Do not deflect. Having a real answer here is worth more than the answer itself.

> Two things. The lead-time weights are unknown and the band is honest about that but
> doesn't solve it. And missingness is not missing-at-random — sites throttle hardest during
> high-traffic periods, which are exactly the high-demand periods when fares move most, so
> our imputation assumes more randomness than reality provides. Both are in
> `docs/limitations.md`, which we wrote before you asked.
