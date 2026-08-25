"""The APIx API.

Designed for one consumer: a statistical agency that wants to ingest this series
programmatically. That shapes every decision here.

  * **Versioned from day one** (`/v1/...`). A statistics office that builds against an
    unversioned endpoint and gets a breaking change has to explain a broken publication.
  * **Every index value carries its availability rate and its computation timestamp.**
    There is no endpoint that returns a bare number, because CLAUDE.md forbids reporting
    one, and because a consumer who cannot see the data quality behind a value will
    eventually use one they should not have.
  * **Revisions are a first-class resource**, not a footnote. `/v1/revisions` answers
    "what did you publish on the 3rd, and what do you say now" — the question agencies
    ask most and student projects never anticipate.
  * **The methodology is served over the wire** at `/v1/methodology`, so a consumer never
    has to go and find a PDF to know what the number means.
  * **An explicit extension point for airline MIS data.** The known limitation of this
    index is that quoted fares cannot be weighted by seats actually sold. `/v1/ingest/mis`
    is where that data would enter if an airline or MoSPI supplied it. It is documented and
    stubbed rather than pretended-away, because "we designed for the data we don't have"
    is a much stronger answer than "we hadn't thought about it".
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

REPO = Path(__file__).resolve().parents[1]
INDEX_DIR = REPO / "data" / "index"
DATA_DIR = REPO / "data"

app = FastAPI(
    title="APIx — Real-time Airfare Price Index for India",
    version="1.0.0",
    description=(
        "A daily, matched-model price index for Indian domestic airfares, built to a "
        "standard the National Statistical Office could consume to augment the CPI "
        "transport division.\n\n"
        "**Every index value is returned with the availability rate behind it and the "
        "timestamp at which it was computed.** Values are never overwritten: recomputing a "
        "day produces a new vintage, and `/v1/revisions` exposes the difference.\n\n"
        "Built for SIH 2026, problem statement SIH26056 (MoSPI, Data Informatics & "
        "Innovation Division)."
    ),
    contact={"name": "APIx", "email": "asmitrawat4078@gmail.com"},
    license_info={"name": "MIT"},
)


# --------------------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------------------


class IndexPoint(BaseModel):
    collection_date: date
    scenario: str = Field(description="Lead-time weighting scenario this value assumes")
    index_value: float = Field(description="Index level, 100 = first collection day")
    availability_rate: float | None = Field(
        description="Share of the 120 basket cells that returned a real quote that day. "
        "Always read this alongside the index value."
    )
    observed_weight_share: float | None = Field(
        description="Share of basket weight that was directly observed rather than imputed"
    )
    strata_contributing: int | None = None
    strata_in_basket: int | None = None
    computed_at_utc: datetime | None = None
    code_git_sha: str | None = None


class SeriesResponse(BaseModel):
    series: Literal["jevons_headline", "hedonic", "geks", "direct"]
    scenario: str
    basket_version: int
    base_period: str
    n_points: int
    caveats: list[str]
    points: list[IndexPoint]


class BandPoint(BaseModel):
    collection_date: date
    near_term_heavy: float | None = None
    uniform: float | None = None
    advance_heavy: float | None = None
    band_low: float | None = None
    band_high: float | None = None
    band_width_pts: float | None = None


# --------------------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------------------


def _read(name: str) -> pd.DataFrame:
    p = INDEX_DIR / name
    if not p.exists():
        raise HTTPException(
            status_code=503,
            detail=(
                f"{name} has not been built yet. The index requires at least two collection "
                "days; before that there is no price change to measure. This is the honest "
                "state of the service, not an error."
            ),
        )
    return pd.read_csv(p)


def _meta() -> dict[str, Any]:
    p = INDEX_DIR / "index_meta.json"
    return json.loads(p.read_text()) if p.exists() else {}


STANDARD_CAVEATS = [
    "These are quoted fares, not transaction prices. They are what a consumer faces at the "
    "point of decision, which is also what most of the CPI basket uses (a shelf price is not "
    "a transaction price either), but they cannot be weighted by seats actually sold.",
    "Lead-time strata weights are unknown for India and are reported as three scenarios, not "
    "one number. Read the band, not just the headline.",
    "Availability below roughly 80% on a given day means a material share of the index is "
    "imputed by stratum movement. The availability_rate field tells you when this applies.",
]


# --------------------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------------------


@app.get("/", include_in_schema=False)
def root() -> dict:
    m = _meta()
    return {
        "service": "APIx — Real-time Airfare Price Index for India",
        "problem_statement": "SIH26056",
        "docs": "/docs",
        "openapi": "/openapi.json",
        "collection_days": m.get("collection_days"),
        "latest": m.get("latest"),
        "note": "Every index value is published with its availability rate. See /v1/methodology.",
    }


@app.get("/v1/health", tags=["operations"])
def health() -> dict:
    """Collection health. Rows per day, availability, and time since the last good run.

    Eurostat recommends monitoring the collection itself as a first-class output; this is
    that monitor. A statistics office consuming this series needs to know the pipeline is
    alive *before* it notices the numbers look odd.
    """
    p = DATA_DIR / "collection_health.json"
    if not p.exists():
        return {"status": "no runs recorded yet"}
    history = json.loads(p.read_text())
    if not history:
        return {"status": "no runs recorded yet"}
    last = history[-1]
    age_days = (date.today() - date.fromisoformat(last["collection_date"])).days
    return {
        "status": (
            "healthy"
            if age_days <= 1 and last["availability_rate"] >= 0.8
            else "degraded" if age_days <= 2 else "stale"
        ),
        "last_collection_date": last["collection_date"],
        "days_since_last_collection": age_days,
        "last_availability_rate": last["availability_rate"],
        "last_cells_priced": last["cells_available"],
        "cells_expected": last["cells_expected"],
        "per_source": last.get("per_source", {}),
        "robots_checks_last_run": last.get("robots_checks"),
        "robots_disallowed_last_run": last.get("robots_disallowed"),
        "total_collection_days": len(history),
    }


@app.get("/v1/series", response_model=SeriesResponse, tags=["index"])
def series(
    scenario: Literal["uniform", "near_term_heavy", "advance_heavy"] = Query(
        "uniform", description="Lead-time weighting assumption. `uniform` is the headline."
    ),
    start: date | None = None,
    end: date | None = None,
) -> SeriesResponse:
    """The headline chained-Jevons index.

    `uniform` is the headline scenario, and it is uniform because no public source gives
    India's booking-lead-time distribution and we decline to invent one. Compare against
    `/v1/band` before quoting a single number.
    """
    df = _read(f"index_{scenario}.csv")
    if start is not None:
        df = df[pd.to_datetime(df.collection_date).dt.date >= start]
    if end is not None:
        df = df[pd.to_datetime(df.collection_date).dt.date <= end]
    m = _meta()
    return SeriesResponse(
        series="jevons_headline",
        scenario=scenario,
        basket_version=m.get("basket_version", 1),
        base_period=m.get("first_day", ""),
        n_points=len(df),
        caveats=STANDARD_CAVEATS,
        points=[IndexPoint(**r) for r in df.to_dict("records")],
    )


@app.get("/v1/band", tags=["index"])
def band() -> dict:
    """The sensitivity band across all three lead-time weighting scenarios.

    The width of this band is the honest uncertainty introduced by the one input nobody
    in India publishes. Where the band is narrow the headline is robust; where it is wide,
    the number depends on an assumption and the reader deserves to know.
    """
    df = _read("index_band.csv")
    return {
        "explanation": (
            "No public source gives how far in advance Indians book flights. Rather than "
            "invent a distribution, the index is computed under three defensible ones and "
            "the spread is published."
        ),
        "scenarios": ["near_term_heavy", "uniform", "advance_heavy"],
        "points": df.to_dict("records"),
    }


@app.get("/v1/series/hedonic", tags=["index"])
def hedonic() -> dict:
    """The hedonic time-dummy series — the quality-adjusted robustness check.

    Reported beside the headline, never instead of it. If the two diverge, the divergence
    is a statement about basket composition and should be read as a finding.
    """
    df = _read("index_hedonic.csv")
    m = _meta()
    return {
        "series": "hedonic",
        "specification": m.get("hedonic", {}).get("formula"),
        "r_squared": m.get("hedonic", {}).get("r_squared"),
        "n_observations": m.get("hedonic", {}).get("n_observations"),
        "weighting": m.get("hedonic", {}).get("diagnostics", {}).get("weighting"),
        "agreement_with_headline": m.get("hedonic_vs_headline"),
        "points": df.to_dict("records"),
    }


@app.get("/v1/series/multilateral", tags=["index"])
def multilateral() -> dict:
    """GEKS-Jevons and the direct fixed-base index, plus the measured chain drift.

    Chaining a daily index accumulates sampling noise. These two constructions do not, so
    the gap between them and the headline is an estimate of how much drift the headline has
    picked up. We publish the estimate rather than asserting there is none.
    """
    m = _meta()
    return {
        "geks": _read("index_geks.csv").to_dict("records"),
        "direct": _read("index_direct.csv").to_dict("records"),
        "chain_drift_diagnostic": m.get("chain_drift"),
        "note": (
            "If the chained-vs-GEKS gap trends upward with series length, that is chain "
            "drift and GEKS should be promoted to headline."
        ),
    }


@app.get("/v1/availability", tags=["quality"])
def availability() -> dict:
    """Daily availability rate, and the split between sold-out cells and blocked cells.

    These are opposite facts. A sold-out cell is information about the market. A blocked
    cell is information about our scraper. Any consumer of this index needs to be able to
    tell them apart, so we never merge them.
    """
    df = _read("availability.csv")
    return {
        "definition": "share of the 120 daily basket cells that returned a real quote",
        "why_it_matters": (
            "Sell-out frequency is itself a market signal the CPI cannot currently produce. "
            "Blocked cells are a data-quality problem. They are reported separately."
        ),
        "points": df.to_dict("records"),
    }


@app.get("/v1/revisions", tags=["index"])
def revisions(limit: int = Query(200, le=2000)) -> dict:
    """Every time a published value changed, and by how much.

    A late-arriving scrape or a corrected computation produces a new vintage sitting beside
    the old one, never an overwrite. Requires the database; the CSV outputs carry only the
    latest vintage.
    """
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        return {
            "available": False,
            "reason": "DATABASE_URL not configured. Revision history lives in Postgres; "
            "the CSV outputs in the repo carry only the current vintage.",
        }
    import psycopg

    with psycopg.connect(dsn, connect_timeout=15) as conn, conn.cursor() as cur:
        cur.execute(
            "select collection_date, scenario, series, first_computed_at, first_value, "
            "later_computed_at, later_value, revision_points "
            "from apix.v_index_revisions order by later_computed_at desc limit %s",
            (limit,),
        )
        cols = [c.name for c in cur.description]
        rows = [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]
    return {"available": True, "n_revisions": len(rows), "revisions": rows}


@app.get("/v1/basket", tags=["methodology"])
def basket() -> dict:
    """The frozen sampling design, in full.

    Published deliberately. A price index whose basket is secret cannot be audited. (There
    is a real counter-argument once a series becomes official — a published basket is a
    target for strategic pricing — and the methodology note engages with it.)
    """
    import yaml

    return yaml.safe_load((DATA_DIR / "basket.yaml").read_text())


@app.get("/v1/methodology", tags=["methodology"])
def methodology() -> dict:
    """What the number means, served over the wire so no consumer has to hunt for a PDF."""
    m = _meta()
    return {
        "elementary_aggregate": {
            "formula": "Jevons — geometric mean of price relatives within each stratum",
            "stratum": "(origin, destination, lead_time_days)",
            "matched_item": "(source, origin, destination, lead_time_days, carrier)",
            "why": "Eurostat's recommended elementary aggregate for web-scraped data: "
            "base-period invariant, passes time reversal, damps the downward bias "
            "from non-random missingness.",
        },
        "weights": {
            "routes": "DGCA monthly domestic city-pair passengers, directional, "
            "trailing 12 months, frozen with the basket",
            "lead_times": "unknown for India — published as three scenarios, never invented",
        },
        "missing_data": {
            "rule": "imputed by the movement of the nearest observed donor stratum "
            "(route, then lead time, then all items)",
            "never": "carry-forward, which would assert zero price change on no evidence",
            "published_alongside": "availability_rate and observed_weight_share",
        },
        "chaining": "daily chained, rebased to 100 on the first collection day, "
        "with GEKS and direct fixed-base indices published as drift diagnostics",
        "revisions": "every computation is a new vintage; nothing is overwritten",
        "quality_adjustment": m.get("hedonic", {}).get("formula"),
        "known_limitations": STANDARD_CAVEATS,
        "references": [
            "Eurostat, Practical guidelines on web scraping for the HICP, November 2020",
            "MoSPI, FAQs on the CPI 2024 series (base 2024=100, COICOP 2018)",
            "ILO et al., Consumer Price Index Manual: Concepts and Methods (2020), "
            "chapters on elementary aggregates and multilateral methods",
        ],
    }


class MISRecord(BaseModel):
    """One airline management-information record: what actually sold, not what was quoted."""

    origin: str
    destination: str
    dep_date: date
    carrier: str
    booking_class: str
    seats_sold: int
    revenue_inr: float


@app.post("/v1/ingest/mis", tags=["extension"], status_code=501)
def ingest_mis(records: list[MISRecord]) -> JSONResponse:
    """Reserved: airline MIS ingestion. Not implemented, and deliberately documented.

    The honest limitation of this index is that it observes quoted fares and therefore
    cannot weight by seats actually sold. That gap closes only with booking-class inventory
    from the airlines or from MoSPI. This endpoint is the shape that data would take, and
    the index module's weighting layer already accepts an external weight source.

    Returning 501 rather than omitting the endpoint is the point: the architecture has a
    named place for the data it does not have.
    """
    return JSONResponse(
        status_code=501,
        content={
            "detail": "Not implemented. This endpoint documents the extension point for "
            "airline MIS data, which would allow seat-weighted rather than "
            "quote-weighted aggregation.",
            "records_received": len(records),
            "would_enable": [
                "load-factor weighting within lead-time strata",
                "replacing the three-scenario band with an observed booking curve",
                "transaction-price validation of the quoted-fare series",
            ],
        },
    )
