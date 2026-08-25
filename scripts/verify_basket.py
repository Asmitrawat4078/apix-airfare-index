"""Prove the frozen basket still matches the source data it claims to come from.

data/basket.yaml records the SHA-256 of the DGCA file it was built from and the exact
weights it derived. This script recomputes those weights from that file and checks they
still agree. It runs in CI on every push.

The point is not paranoia about tampering. It is that `basket.yaml` is the one file in
this repository where a plausible-looking hand edit — nudging a weight, adding a route
that "should obviously be in there" — would be completely invisible in review and would
silently redefine a live series. This test makes that edit fail loudly instead.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pandas as pd
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from build_basket import (  # noqa: E402
    DGCA_CSV,
    load_dgca,
    route_weights,
    to_directional,
    trailing_window,
)

TOLERANCE = 1e-9


def main() -> int:
    basket = yaml.safe_load((REPO / "data" / "basket.yaml").read_text())
    committed = pd.read_csv(REPO / "data" / "route_weights.csv")
    problems: list[str] = []

    actual_sha = hashlib.sha256(DGCA_CSV.read_bytes()).hexdigest()
    claimed_sha = basket["weight_source"]["file_sha256"]
    if actual_sha != claimed_sha:
        problems.append(
            f"DGCA source file has changed since the basket was frozen.\n"
            f"  basket.yaml claims sha256 {claimed_sha[:16]}...\n"
            f"  file on disk is         {actual_sha[:16]}...\n"
            f"  The basket is frozen against a specific vintage of this data. If you have\n"
            f"  deliberately updated it, that is basket v2 with its own series, not an edit."
        )

    raw = load_dgca(DGCA_CSV)
    win, _ = trailing_window(raw, basket["weight_source"]["months_in_window"])
    directional, _ = to_directional(win)
    pairs = [tuple(p.split("-")) for p in basket["route_pairs"]]
    recomputed = route_weights(directional, pairs)

    a = recomputed.set_index(["origin", "destination"])["weight"].round(8)
    b = committed.set_index(["origin", "destination"])["weight"].round(8)
    if set(a.index) != set(b.index):
        problems.append(
            f"route set differs: only in recomputed {set(a.index) - set(b.index)}, "
            f"only in committed {set(b.index) - set(a.index)}"
        )
    else:
        diff = (a - b).abs()
        drifted = diff[diff > TOLERANCE]
        if len(drifted):
            problems.append(f"{len(drifted)} route weights do not match the source data:\n{drifted}")

    yaml_weights = {(r["origin"], r["destination"]): r["weight"] for r in basket["directed_routes"]}
    for k, v in yaml_weights.items():
        if abs(v - float(a.get(k, float("nan")))) > TOLERANCE:
            problems.append(f"basket.yaml weight for {k} ({v}) disagrees with the source data")

    total = sum(yaml_weights.values())
    if abs(total - 1.0) > 1e-6:
        problems.append(f"basket.yaml weights sum to {total}, not 1")

    expected_cells = len(yaml_weights) * len(basket["lead_times_days"])
    if basket["cells_per_day"] != expected_cells:
        problems.append(
            f"cells_per_day says {basket['cells_per_day']}, routes x lead times is {expected_cells}"
        )

    if problems:
        print("BASKET VERIFICATION FAILED\n")
        for p in problems:
            print(f"  * {p}\n")
        return 1

    print(
        f"basket v{basket['basket_version']} verified: "
        f"{len(yaml_weights)} directed routes x {len(basket['lead_times_days'])} lead times "
        f"= {basket['cells_per_day']} cells/day, weights reproduce from "
        f"{basket['weight_source']['window']} DGCA data, sha256 matches."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
