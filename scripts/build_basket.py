"""Freeze the APIx basket from real DGCA city-pair passenger volumes.

Run once. The output is committed and never regenerated: the basket is frozen by
CLAUDE.md invariant #3, and re-running this after new DGCA months land would
silently reweight a live series. If the basket genuinely must change it becomes
basket v2 with its own series, and v1 keeps running alongside it.

    python scripts/build_basket.py --write

Source: DGCA monthly domestic city-pair traffic, republished as CSV by
Vonter/india-aviation-traffic (from the DGCA monthly statistics PDFs).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent))
from city_iata import to_iata  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
DGCA_CSV = REPO / "data" / "dgca_domestic_city.csv"

# --- Sampling design. Everything that defines the basket lives here, nowhere else. ---

N_PAIRS = 12
LEAD_TIMES = [1, 7, 15, 30, 45]

# Seeded directly from problem statement SIH26056.
SEED_PAIRS: list[tuple[str, str]] = [
    ("DEL", "BOM"),
    ("DEL", "BLR"),
    ("BOM", "BLR"),
    ("DEL", "CCU"),
    ("BLR", "HYD"),
    ("MAA", "DEL"),
]

# The PS requires at least one thin regional route so the index is not purely a
# metro-trunk measure. DEL-GAU is the North-East gateway: real scheduled service,
# an order of magnitude thinner than DEL-BOM, and a different competitive structure
# (fewer carriers, so we expect a different dynamic-pricing signature).
THIN_REGIONAL: tuple[str, str] = ("DEL", "GAU")

WINDOW_MONTHS = 12


def load_dgca(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    df["ym"] = df["Year"].astype(int) * 100 + df["Month"].astype(int)
    return df


def trailing_window(df: pd.DataFrame, months: int) -> tuple[pd.DataFrame, list[int]]:
    """Last `months` complete calendar months present in the file."""
    periods = sorted(df["ym"].unique())[-months:]
    return df[df["ym"].isin(periods)].copy(), periods


def to_directional(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Collapse the DGCA bidirectional layout into one row per directed IATA route.

    The file gives City1, City2 and the two directional passenger counts. We map both
    ends to IATA, drop any pair with an unmapped end (reported, never guessed), and
    sum. Distinct DGCA labels for the same city (MUMBAI / MUMBAI (MUMBAI)) collapse
    onto the same IATA code here, which is the point of the explicit mapping.
    """
    unmapped: set[str] = set()

    def m(name: str) -> str | None:
        code = to_iata(name)
        if code is None:
            unmapped.add(str(name))
        return code

    df = df.copy()
    df["o1"] = df["City1"].map(m)
    df["o2"] = df["City2"].map(m)
    mapped = df.dropna(subset=["o1", "o2"])

    fwd = mapped.rename(columns={"o1": "origin", "o2": "destination", "PaxToCity2": "pax"})
    bwd = mapped.rename(columns={"o2": "origin", "o1": "destination", "PaxFromCity2": "pax"})
    both = pd.concat([fwd[["origin", "destination", "pax"]], bwd[["origin", "destination", "pax"]]])
    both = both[both["origin"] != both["destination"]]
    out = both.groupby(["origin", "destination"], as_index=False)["pax"].sum()
    return out.sort_values("pax", ascending=False).reset_index(drop=True), sorted(unmapped)


def pair_key(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def select_pairs(directional: pd.DataFrame) -> tuple[list[tuple[str, str]], dict]:
    """Seeded pairs first, then the largest remaining pairs by two-way volume,
    with the thin regional route guaranteed a slot."""
    pair_vol = directional.copy()
    pair_vol["pair"] = [pair_key(o, d) for o, d in zip(pair_vol.origin, pair_vol.destination)]
    twoway = pair_vol.groupby("pair", as_index=False)["pax"].sum().sort_values("pax", ascending=False)

    chosen: list[tuple[str, str]] = []
    provenance: dict[str, str] = {}

    for p in SEED_PAIRS:
        k = pair_key(*p)
        if k not in chosen:
            chosen.append(k)
            provenance["-".join(k)] = "seeded by problem statement SIH26056"

    thin = pair_key(*THIN_REGIONAL)
    if thin not in chosen:
        chosen.append(thin)
        provenance["-".join(thin)] = "thin regional route, required by the PS"

    for _, row in twoway.iterrows():
        if len(chosen) >= N_PAIRS:
            break
        if row["pair"] in chosen:
            continue
        chosen.append(row["pair"])
        provenance["-".join(row["pair"])] = "top remaining city pair by DGCA two-way passengers"

    if len(chosen) != N_PAIRS:
        raise SystemExit(f"selected {len(chosen)} pairs, expected {N_PAIRS}")

    total_network_pax = float(directional["pax"].sum())
    basket_pax = float(
        pair_vol[pair_vol["pair"].isin(chosen)]["pax"].sum()
    )
    stats = {
        "network_pax_in_window": total_network_pax,
        "basket_pax_in_window": basket_pax,
        "basket_share_of_domestic_pax": basket_pax / total_network_pax,
    }
    return chosen, {"provenance": provenance, "coverage": stats}


def route_weights(directional: pd.DataFrame, pairs: list[tuple[str, str]]) -> pd.DataFrame:
    """Directional weights, normalised across the 24 directed routes in the basket.

    Directional matters: DEL->BOM and BOM->DEL are different products with different
    load factors and different fare surfaces. Averaging them away would be the exact
    mistake the PS is asking us not to make.
    """
    wanted = {(o, d) for p in pairs for o, d in ((p[0], p[1]), (p[1], p[0]))}
    sel = directional[
        [(o, d) in wanted for o, d in zip(directional.origin, directional.destination)]
    ].copy()

    missing = wanted - set(zip(sel.origin, sel.destination))
    if missing:
        raise SystemExit(f"no DGCA volume found for directed routes: {sorted(missing)}")

    sel["weight"] = sel["pax"] / sel["pax"].sum()
    sel["pair"] = ["-".join(pair_key(o, d)) for o, d in zip(sel.origin, sel.destination)]
    return sel.sort_values("weight", ascending=False).reset_index(drop=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="write basket.yaml and route_weights.csv")
    args = ap.parse_args()

    raw = load_dgca(DGCA_CSV)
    win, periods = trailing_window(raw, WINDOW_MONTHS)
    directional, unmapped = to_directional(win)
    pairs, meta = select_pairs(directional)
    weights = route_weights(directional, pairs)

    src_hash = hashlib.sha256(DGCA_CSV.read_bytes()).hexdigest()

    print(f"DGCA window: {periods[0]} .. {periods[-1]}  ({len(periods)} months)")
    print(f"source sha256: {src_hash[:16]}...")
    print(f"unmapped city labels dropped: {len(unmapped)}")
    if unmapped:
        print("  " + ", ".join(unmapped[:12]) + (" ..." if len(unmapped) > 12 else ""))
    print(
        f"basket covers {meta['coverage']['basket_share_of_domestic_pax']:.1%} "
        f"of mapped domestic passengers in the window"
    )
    print()
    print(weights[["origin", "destination", "pax", "weight"]].to_string(index=False))
    print(f"\nweights sum to {weights.weight.sum():.10f}")

    if not args.write:
        print("\n(dry run — pass --write to freeze)")
        return

    basket = {
        "basket_version": 1,
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "problem_statement": "SIH26056",
        "offer_definition": {
            "cabin": "economy",
            "passengers": {"adults": 1, "children": 0, "infants": 0},
            "trip_type": "one_way",
            "stops": "non_stop_where_available",
            "baggage_addon": False,
            "refundable": False,
            "selection_rule": "cheapest matching offer in the cell",
            "currency": "INR",
            "note": (
                "This is the matched model. Every element is held constant for the life "
                "of basket v1. Changing any of it creates basket v2 with a new series."
            ),
        },
        "collection": {
            "runs_per_day": 1,
            "scheduled_time_ist": "02:00",
            "randomisation_window_minutes": 20,
            "randomisation_rationale": (
                "Publishing an exact collection instant invites strategic pricing against it. "
                "A short jitter window preserves comparability while removing a fixed target."
            ),
            "timezone": "Asia/Kolkata",
        },
        "lead_times_days": LEAD_TIMES,
        "lead_time_note": (
            "Strata, never averaged into one another. Departure date is derived: on "
            "collection date d, the T+15 cell means departure on d+15."
        ),
        "carriers_tracked": {
            "6E": "IndiGo",
            "AI": "Air India",
            "IX": "Air India Express",
            "QP": "Akasa Air",
            "SG": "SpiceJet",
        },
        "route_pairs": ["-".join(p) for p in pairs],
        "directed_routes": [
            {"origin": r.origin, "destination": r.destination, "weight": round(float(r.weight), 8)}
            for r in weights.itertuples()
        ],
        "cells_per_day": len(weights) * len(LEAD_TIMES),
        "weight_source": {
            "dataset": "DGCA monthly domestic city-pair passenger traffic",
            "via": "github.com/Vonter/india-aviation-traffic → aggregated/domestic/city.csv",
            "window": f"{periods[0]} to {periods[-1]}",
            "months_in_window": len(periods),
            "file_sha256": src_hash,
        },
        "selection_provenance": meta["provenance"],
        "coverage": {k: round(v, 6) for k, v in meta["coverage"].items()},
        "unmapped_city_labels_dropped": len(unmapped),
    }

    (REPO / "data" / "basket.yaml").write_text(
        "# APIx basket v1 — FROZEN. Generated by scripts/build_basket.py.\n"
        "# Do not hand-edit and do not regenerate: this file defines a live series.\n"
        + yaml.safe_dump(basket, sort_keys=False, allow_unicode=True)
    )
    weights[["origin", "destination", "pair", "pax", "weight"]].to_csv(
        REPO / "data" / "route_weights.csv", index=False
    )
    (REPO / "data" / "basket_build_report.json").write_text(
        json.dumps({"unmapped_labels": unmapped, "periods": [int(p) for p in periods]}, indent=2)
    )
    print("\nfrozen: data/basket.yaml, data/route_weights.csv")


if __name__ == "__main__":
    main()
