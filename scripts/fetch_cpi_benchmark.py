"""Pull MoSPI's own published CPI air-transport figures, to benchmark APIx against.

Why this matters more than it looks
-----------------------------------
Correlating a new series against the official one it hopes to augment is the single most
persuasive piece of evidence available, and it is also the easiest thing to do dishonestly.
A high correlation is *supporting evidence*, not proof: the two series measure related but
distinct things, at different frequencies, from different samples. A LOW correlation is the
more interesting result and must be explained rather than hidden — most likely it would
mean the monthly spot-price approach is missing intra-month movement that daily collection
captures, which is the entire premise of this project.

So this script does not "find the correlation". It fetches the official figures, records
exactly where they came from, and hands both series to `scripts/backtest.py`.

Two ways in, and the manual one is the primary
----------------------------------------------
1. **A CSV exported from eSankhyiki** (``--csv path/to/download.csv``). This is the default
   and the one that always works: MoSPI's portal offers direct CSV export, the file is
   committed to the repo, and the provenance is unambiguous. Use this.

2. **The API** (``--api``). MoSPI publishes a CPI API on api.mospi.gov.in and an MCP server
   at mcp.mospi.gov.in. Endpoint shapes are not hard-coded here as though they were known
   facts — the script probes a small set of documented candidates, reports which responded,
   and writes what it found. Run it once from an environment with open internet (a GitHub
   Actions runner will do) and pin the endpoint that worked.

Usage
-----
    python scripts/fetch_cpi_benchmark.py --csv ~/Downloads/cpi_transport.csv
    python scripts/fetch_cpi_benchmark.py --api --discover
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
OUT_CSV = REPO / "data" / "index" / "cpi_benchmark.csv"
PROVENANCE = REPO / "data" / "cpi_benchmark_provenance.json"

# The COICOP 2018 location of the thing we are trying to benchmark against. Recorded here
# so that whoever exports the CSV knows exactly which series to export, rather than
# grabbing "Transport" and quietly comparing airfares against petrol prices.
TARGET_SERIES = {
    "division": "07 — Transport",
    "note": (
        "The CPI 2024 series (base 2024=100, COICOP 2018) has 12 divisions, 43 groups, "
        "92 classes and 162 sub-classes. Transport carries 8.796% of the all-India weight. "
        "The correct comparator is the passenger-transport-by-air sub-class, NOT the "
        "Transport division as a whole — the division is dominated by fuel and personal "
        "vehicle costs, which move for entirely different reasons."
    ),
    "fallback": (
        "If the air sub-class is not separately published at the frequency needed, use the "
        "passenger transport group and say so explicitly in the write-up. Comparing against "
        "a broader aggregate than intended is defensible; doing it without saying so is not."
    ),
}

# Documented entry points. Probed, never assumed.
API_CANDIDATES = [
    "https://api.mospi.gov.in/API/CPIIndex",
    "https://api.mospi.gov.in/API/CPI",
    "https://esankhyiki.mospi.gov.in/api/cpi",
    "https://esankhyiki.mospi.gov.in/apiv1/cpi",
]

USER_AGENT = (
    "APIx-PriceIndexBot/1.0 (+https://github.com/Asmitrawat4078/apix-airfare-index; "
    "asmitrawat4078@gmail.com) fetching published official statistics for benchmarking"
)


def normalise_export(df: pd.DataFrame) -> pd.DataFrame:
    """Coax an eSankhyiki CSV export into (month, cpi_index).

    Column names differ between exports and between series, so we match on meaning rather
    than on an exact header string, and we fail loudly rather than guessing which numeric
    column is the index.
    """
    cols = {c.lower().strip(): c for c in df.columns}

    def find(*needles: str) -> str | None:
        for low, orig in cols.items():
            if any(n in low for n in needles):
                return orig
        return None

    year_col = find("year")
    month_col = find("month")
    value_col = find("index", "value")

    if value_col is None:
        raise SystemExit(
            "Could not identify the index column in this export.\n"
            f"Columns present: {list(df.columns)}\n"
            "Rename the index column to something containing 'index' or 'value', or pass "
            "--value-column. This script will not guess which number is the CPI."
        )

    out = pd.DataFrame()
    if year_col and month_col:
        out["month"] = pd.to_datetime(
            df[year_col].astype(str) + "-" + df[month_col].astype(str).str.zfill(2) + "-01",
            errors="coerce",
        )
    else:
        date_col = find("date", "period", "month")
        if date_col is None:
            raise SystemExit(f"No date column found. Columns: {list(df.columns)}")
        out["month"] = pd.to_datetime(df[date_col], errors="coerce")

    out["cpi_index"] = pd.to_numeric(df[value_col], errors="coerce")
    out = out.dropna().sort_values("month").reset_index(drop=True)

    if out.empty:
        raise SystemExit("Export parsed to zero usable rows — check the file.")
    return out


def discover_api() -> list[dict]:
    """Probe the documented endpoints and report what answers. No endpoint is assumed."""
    import httpx

    findings = []
    for url in API_CANDIDATES:
        record: dict = {"url": url}
        try:
            r = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=25, follow_redirects=True)
            record.update(
                status=r.status_code,
                content_type=r.headers.get("content-type", ""),
                bytes=len(r.content),
                looks_like_json="json" in r.headers.get("content-type", "").lower(),
                preview=r.text[:400],
            )
        except Exception as exc:  # noqa: BLE001
            record.update(status=None, error=f"{type(exc).__name__}: {exc}"[:200])
        findings.append(record)
        print(f"  {url} -> {record.get('status')} {record.get('content_type', '')}")
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch the CPI benchmark series")
    ap.add_argument("--csv", help="Path to a CSV exported from eSankhyiki (preferred)")
    ap.add_argument("--api", action="store_true", help="Try the MoSPI API instead")
    ap.add_argument("--discover", action="store_true", help="Probe API endpoints and report")
    ap.add_argument("--value-column", help="Explicit index column name")
    ap.add_argument(
        "--label",
        default="CPI air transport sub-class",
        help="What this series actually is — goes into the provenance record",
    )
    args = ap.parse_args()

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)

    if args.api or args.discover:
        print("Probing MoSPI API endpoints. None of these is assumed to work.\n")
        findings = discover_api()
        PROVENANCE.write_text(
            json.dumps(
                {
                    "attempted_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
                    "mode": "api_discovery",
                    "findings": findings,
                    "target_series": TARGET_SERIES,
                },
                indent=2,
            )
        )
        print(f"\nWrote discovery report to {PROVENANCE}")
        print(
            "\nPin whichever endpoint returned usable JSON, then re-run with --csv against a "
            "downloaded export for the committed benchmark. The CSV path is the one with "
            "unambiguous provenance, and provenance is the point."
        )
        return 0

    if not args.csv:
        print(__doc__)
        print("\nNothing to do. Pass --csv with an eSankhyiki export, or --discover.\n")
        print("Which series to export:")
        print(json.dumps(TARGET_SERIES, indent=2))
        return 1

    src = Path(args.csv)
    if not src.exists():
        raise SystemExit(f"{src} does not exist")

    raw = pd.read_csv(src)
    if args.value_column:
        raw = raw.rename(columns={args.value_column: "index_value"})
    tidy = normalise_export(raw)
    tidy.to_csv(OUT_CSV, index=False)

    import hashlib

    PROVENANCE.write_text(
        json.dumps(
            {
                "fetched_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
                "mode": "manual_csv_export",
                "source_file": src.name,
                "source_sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
                "series_label": args.label,
                "rows": len(tidy),
                "period_covered": [str(tidy.month.min().date()), str(tidy.month.max().date())],
                "target_series": TARGET_SERIES,
                "caveat": (
                    "A high correlation with APIx is supporting evidence, not proof of correctness. "
                    "A low correlation needs explaining, not hiding."
                ),
            },
            indent=2,
        )
    )

    print(f"wrote {len(tidy)} monthly points to {OUT_CSV}")
    print(tidy.tail(8).to_string(index=False))
    print(f"\nprovenance recorded in {PROVENANCE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
