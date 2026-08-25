# APIx scraping policy

**This is a deliverable, not a formality.** If a panel asks whether this project is legal
and ethical, this document plus the `apix.robots_log` table is the answer — a claim you
can query rather than a claim in a slide.

Every rule below is enforced in code. Where a rule is enforced, the file that enforces it
is named, so this document can be checked rather than believed.

---

## 1. We only look at what anyone can look at

- **Public, unauthenticated pages only.** No accounts are created, no logins are performed,
  no session cookies are reused, no paywall is bypassed. Every URL we request is one a
  member of the public reaches by using a site's own search form.
- **No private or undocumented APIs are reverse-engineered from authenticated traffic.**
  Where we read a site's internal JSON endpoint, it is the endpoint that site's own public
  booking widget calls while rendering a public results page, observed by loading that page
  in a browser. `scripts/probe_sources.py` does exactly this and commits its findings.

*Enforced in:* `collector/sources/*.py` (every `search_url` is a public search page),
`collector/fetch.py` (no cookie jar reuse, no credential handling anywhere in the codebase).

---

## 2. robots.txt is parsed before every request, and every decision is logged

- `robots.txt` is fetched per domain, parsed with the standard library's RFC 9309 parser,
  and consulted for **every single URL** before it is requested.
- **We fail closed.** If `robots.txt` cannot be fetched — network error, 5xx, anything —
  we do not scrape that domain on that run. Treating an unreachable `robots.txt` as
  permission is how well-intentioned scrapers end up in incident reports.
- A published `Crawl-delay` **always wins** over our own rate limit when it asks for more
  space than we already give.
- Every decision — URL, domain, user agent, allowed/disallowed, reason, crawl-delay — is
  written to `apix.robots_log`, one row per check, forever.

*Enforced in:* `collector/robots.py`, `db/migrations/002_collection_health.sql`.

---

## 3. We identify ourselves and never pretend to be someone else

The User-Agent on every request names the project, links the public repository, and
carries a contact email:

```
APIx-PriceIndexBot/1.0 (+https://github.com/…; asmitrawat4078@gmail.com)
research crawler for an official-statistics airfare price index (SIH26056)
```

We do **not** spoof a consumer browser identity to avoid detection, rotate user agents,
use residential proxy pools, or otherwise disguise the traffic. If a site wants to block
this project, it must be able to identify it. That is the deal.

*Enforced in:* `collector/fetch.py` (`USER_AGENT`, and a `From:` header on every request).

---

## 4. We are slow on purpose

- One request per domain every **four seconds minimum**, with randomised jitter on top.
- Exponential backoff with randomisation on any 4xx or 5xx, with a longer multiplier on
  429 and on challenge pages.
- A bounded retry budget. We do not hammer a failing endpoint.

The whole daily job needs **120 cells**. There is no version of this project where going
faster helps: the index samples once a day, so throughput buys us nothing and costs us the
entire legal and ethical argument. We need 120 cells a day, not 120 a minute.

*Enforced in:* `collector/ratelimit.py`, `collector/fetch.py`.

---

## 5. We never solve or bypass a CAPTCHA

When a challenge page is detected — CAPTCHA markers, bot-wall vendor signatures, 403 — the
collector **backs off, records the cell as `is_available = false` with
`unavailable_reason = 'blocked'`, and moves on.**

No solver service. No headless-detection evasion beyond running an ordinary browser. No
retrying until it gets through.

This is not only an ethical position, it is the methodologically correct one. A blocked
cell is a *missing observation*, and official statistics has well-developed machinery for
missing observations: impute it by the movement of its donor stratum, flag it, and publish
the availability rate so every consumer can see how much of the index rested on imputation
that day. **Correctly-handled missingness is the answer a statistics agency would give.**
Evaded missingness is a liability that also happens to corrupt the data.

*Enforced in:* `collector/fetch.py` (`BOT_WALL_MARKERS`, `Blocked`),
`collector/run.py`, `index/imputation.py`.

---

## 6. We collect prices, not people

**No personal data is collected, ever.** Not names, not emails, not IP addresses of other
users, not cookies, not device identifiers, not booking records. The collector requests a
public search results page and extracts fare amounts, carrier codes, flight numbers and
departure times.

This is why the **Digital Personal Data Protection Act, 2023 does not attach** to this
pipeline: the DPDP Act governs the processing of personal data, and there is none here.
Stating this explicitly, up front, pre-empts the question rather than waiting for it.

---

## 7. Failures are recorded, never disguised

Four things can stop a cell producing a price, and they mean different things:

| Reason | What it means | Whose problem |
|---|---|---|
| `sold_out` | No economy seat is purchasable at any price | The **market's** — this is a demand signal |
| `no_service` | No flight operates this route on this date | The **market's** |
| `blocked` | The market had a price and we failed to observe it | **Ours** |
| `parse_error` / `timeout` / `rate_limited` | We failed, differently | **Ours** |

These are never merged. Collapsing them would let our own scraping failures masquerade as
market scarcity — the single most dangerous thing a price index can do to itself. The
dashboard charts them separately and the API returns them separately.

*Enforced in:* `collector/schema.py` (`UnavailableReason`, and a database constraint that
an unavailable quote must carry a reason).

---

## 8. If a source asks us to stop, we stop

Any site operator who wants this collector to stop can do so by any of:

- adding a `Disallow` for our paths, or a `Crawl-delay`, to `robots.txt` — honoured
  automatically on the next run, within one collection cycle;
- emailing the address in our User-Agent;
- opening an issue on the public repository.

We will comply and record the removal in the methodology note, because a source that
disappears from the basket changes the series and readers are entitled to know why.

The system is built to survive losing a source: multiple sources per route, graceful
degradation to stratum imputation, a published availability rate, and a health page that
catches it within one cycle.

---

## 9. What we would do differently if this became official

Worth stating, because it is the question that follows.

If this series were ever adopted, the collection design would need to change in ways that
conflict with the transparency above: the exact collection instant would not be published,
the basket would become confidential, a reserve sample would rotate in, and the
randomisation window would widen. That is not hypocrisy — it is the standard response to
strategic behaviour against an official statistic, and it is a well-known problem in
official statistics rather than something we invented. See `docs/limitations.md`.

For a research prototype, transparency is worth more than resistance to gaming, so
everything here is public.

---

*Contact: asmitrawat4078@gmail.com*
