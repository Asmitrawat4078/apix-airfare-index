"""The hedonic time-dummy index — the robustness check that answers the hardest question.

The panel's sharpest question about any matched-model index is: *your index moved 8%, was
that a price change or a composition change?* On airfares this is not a pedantic objection.
As departure approaches, cheap fare buckets sell out and the mix of what is purchasable
shifts toward expensive product. A naive average would record that as inflation. The
matched model handles it by comparing like with like — but the matched sample itself
shrinks as flights sell out, and a shrinking sample has its own selection problem.

The hedonic time-dummy regression attacks the same question from the other direction. Fit

    log(total_fare) ~ route + carrier + lead_time + dep_dow + dep_hour + is_holiday
                      + C(collection_date)

over *all* observed quotes, not just matched pairs. Every characteristic that makes one
fare structurally different from another is absorbed by its own fixed effect. What is left
in the collection-date coefficients is the movement in price common to everything, holding
the product mix constant. Exponentiate them and you have a quality-adjusted index.

Two series that are constructed on completely different principles, from overlapping but
non-identical samples, and that track each other, are far more convincing than either one
alone. If they diverge, that divergence is the story — and it is a story about the basket,
which is exactly what a statistical agency wants to be told.

The headline stays Jevons. This is the robustness check, always reported beside it,
never instead of it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

log = logging.getLogger("apix.index.hedonic")

BASE_VALUE = 100.0


@dataclass(frozen=True, slots=True)
class HedonicResult:
    series: pd.DataFrame  # collection_date, hedonic_index, se, ci_low, ci_high
    r_squared: float
    n_observations: int
    n_parameters: int
    formula: str
    diagnostics: dict

    def summary_line(self) -> str:
        return (
            f"hedonic: n={self.n_observations} params={self.n_parameters} "
            f"R2={self.r_squared:.3f} days={len(self.series)}"
        )


def indian_holiday_flags(dates: pd.Series) -> pd.Series:
    """Real Indian public holidays, from the `holidays` package's India calendar.

    Not invented, not a hand-typed list of the ones I happened to remember. If the package
    is unavailable the flag is dropped from the model entirely rather than defaulted to
    False, because a silently all-False holiday dummy would let genuine Diwali fare spikes
    leak into the collection-date coefficients and be reported as inflation.
    """
    import holidays as _holidays

    d = pd.to_datetime(dates)
    years = sorted({int(y) for y in d.dt.year.dropna().unique()})
    cal = _holidays.India(years=years)
    return d.dt.date.map(lambda x: x in cal).astype(int)


def prepare(quotes: pd.DataFrame) -> pd.DataFrame:
    """Build the regression frame from available, priced quotes."""
    df = quotes[quotes["is_available"] & quotes["total_fare"].notna()].copy()
    if df.empty:
        return df

    df["total_fare"] = df["total_fare"].astype(float)
    df = df[df["total_fare"] > 0]
    df["log_fare"] = np.log(df["total_fare"])
    df["route"] = df["origin"].astype(str) + "-" + df["destination"].astype(str)
    df["lead_time_days"] = df["lead_time_days"].astype(int)
    df["collection_date"] = df["collection_date"].astype(str)

    dep = pd.to_datetime(df["dep_ts"], errors="coerce", utc=True)
    if dep.notna().any():
        dep_local = dep.dt.tz_convert("Asia/Kolkata")
        df["dep_dow"] = dep_local.dt.dayofweek
        df["dep_hour_band"] = pd.cut(
            dep_local.dt.hour,
            bins=[-1, 5, 9, 12, 17, 21, 24],
            labels=["red_eye", "early_morning", "morning", "afternoon", "evening", "night"],
        ).astype(str)
    else:
        # No departure timestamps yet (early days of collection, or a source that does not
        # expose them). Fall back to the departure date, which we always have.
        dep_date = pd.to_datetime(df["dep_date"], errors="coerce")
        df["dep_dow"] = dep_date.dt.dayofweek
        df["dep_hour_band"] = "unknown"

    try:
        df["is_holiday"] = indian_holiday_flags(df["dep_date"])
    except Exception as exc:  # noqa: BLE001
        log.warning("hedonic: Indian holiday calendar unavailable (%s); dropping the term", exc)
        df["is_holiday"] = np.nan

    return df


def _drop_degenerate(df: pd.DataFrame, terms: list[str]) -> list[str]:
    """Keep only terms that actually vary. A fixed effect with one level is not identified,
    and statsmodels will happily produce a rank-deficient fit rather than complain."""
    kept = []
    for t in terms:
        col = t.replace("C(", "").replace(")", "")
        if col not in df.columns:
            continue
        if df[col].nunique(dropna=True) < 2:
            log.info("hedonic: dropping %s — only %d level(s) present", t, df[col].nunique(dropna=True))
            continue
        kept.append(t)
    return kept


def attach_regression_weights(
    df: pd.DataFrame, route_weights: pd.DataFrame | None, lead_time_weights: dict[int, float] | None
) -> pd.Series:
    """Weight each observation so the hedonic answers the same question as the headline.

    This matters more than it looks. An unweighted OLS gives every *quote* equal say, so a
    thin route on which four carriers happen to publish fares counts four times as much as
    a trunk route on which one does. The Jevons headline, by contrast, weights DEL-BOM at
    8.8% because that is its share of Indian domestic passengers. Compare the two without
    fixing this and they will disagree for a reason that has nothing to do with quality
    adjustment — you are comparing a passenger-weighted index against a quote-weighted one
    and calling the difference a finding.

    So each observation is weighted by (route weight x lead-time weight), divided by the
    number of observations sharing that cell on that day, so every cell carries its basket
    weight regardless of how many carriers happened to be visible in it.
    """
    if route_weights is None:
        return pd.Series(1.0, index=df.index)

    rw = {(r.origin, r.destination): float(r.weight) for r in route_weights.itertuples()}
    ltw = lead_time_weights or {}

    cell_weight = pd.Series(
        [
            rw.get((o, d), 0.0) * ltw.get(int(lt), 1.0)
            for o, d, lt in zip(df["origin"], df["destination"], df["lead_time_days"], strict=False)
        ],
        index=df.index,
    )
    per_cell_count = df.groupby(["collection_date", "origin", "destination", "lead_time_days"])[
        "log_fare"
    ].transform("size")
    w = cell_weight / per_cell_count.replace(0, 1)
    if w.sum() <= 0:
        log.warning("hedonic: all regression weights are zero — falling back to unweighted OLS")
        return pd.Series(1.0, index=df.index)
    return w / w.mean()


def fit(
    quotes: pd.DataFrame,
    min_observations: int = 60,
    route_weights: pd.DataFrame | None = None,
    lead_time_weights: dict[int, float] | None = None,
) -> HedonicResult | None:
    """Fit the time-dummy model and extract the quality-adjusted index.

    Returns None — not a fabricated flat line — when there is not enough data to identify
    the model. Early in collection that is the honest answer, and the dashboard says so.
    """
    import statsmodels.formula.api as smf

    df = prepare(quotes)
    if len(df) < min_observations:
        log.warning(
            "hedonic: %d usable observations is below the %d minimum — not fitting",
            len(df),
            min_observations,
        )
        return None
    if df["collection_date"].nunique() < 2:
        log.warning("hedonic: needs at least two collection days")
        return None

    candidate_terms = [
        "C(route)",
        "C(carrier)",
        "C(lead_time_days)",
        "C(dep_dow)",
        "C(dep_hour_band)",
        "is_holiday",
    ]
    terms = _drop_degenerate(df, candidate_terms)
    formula = "log_fare ~ " + " + ".join(terms + ["C(collection_date)"])

    df = df.assign(_w=attach_regression_weights(df, route_weights, lead_time_weights))
    weighted = route_weights is not None
    model = (smf.wls(formula, data=df, weights=df["_w"]) if weighted else smf.ols(formula, data=df)).fit(
        cov_type="HC1"
    )  # heteroskedasticity-robust standard errors either way

    base_day = sorted(df["collection_date"].unique())[0]
    rows = [{"collection_date": base_day, "log_coef": 0.0, "se": 0.0}]
    for name in model.params.index:
        if not name.startswith("C(collection_date)[T."):
            continue
        day = name.split("[T.")[1].rstrip("]")
        rows.append(
            {"collection_date": day, "log_coef": float(model.params[name]), "se": float(model.bse[name])}
        )

    series = pd.DataFrame(rows).sort_values("collection_date").reset_index(drop=True)
    series["hedonic_index"] = BASE_VALUE * np.exp(series["log_coef"])
    # 95% interval on the index, propagated through the exponential.
    series["ci_low"] = BASE_VALUE * np.exp(series["log_coef"] - 1.96 * series["se"])
    series["ci_high"] = BASE_VALUE * np.exp(series["log_coef"] + 1.96 * series["se"])

    result = HedonicResult(
        series=series[["collection_date", "hedonic_index", "se", "ci_low", "ci_high"]],
        r_squared=float(model.rsquared),
        n_observations=int(model.nobs),
        n_parameters=int(len(model.params)),
        formula=formula,
        diagnostics={
            "condition_number": float(model.condition_number),
            "f_pvalue": float(model.f_pvalue) if model.f_pvalue is not None else None,
            "weighted": weighted,
            "weighting": (
                "basket weights (route x lead-time), normalised per cell-day"
                if weighted
                else "unweighted — not comparable to the headline"
            ),
            "terms_used": terms,
            "terms_dropped": [t for t in candidate_terms if t not in terms],
            "base_day": base_day,
        },
    )
    log.info(result.summary_line())
    return result


def compare_to_headline(hedonic: pd.DataFrame, headline: pd.DataFrame) -> dict:
    """How closely do the two constructions agree? This number goes on a slide."""
    m = hedonic.merge(headline, on="collection_date", how="inner")
    if len(m) < 3:
        return {"n": len(m), "note": "too few overlapping days to correlate"}
    corr = float(np.corrcoef(m["hedonic_index"], m["index_value"])[0, 1])
    diff = m["hedonic_index"] - m["index_value"]
    return {
        "n": len(m),
        "pearson_r": round(corr, 4),
        "mean_abs_divergence_pts": round(float(diff.abs().mean()), 4),
        "max_abs_divergence_pts": round(float(diff.abs().max()), 4),
    }
