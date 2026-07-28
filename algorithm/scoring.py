"""Normalize decision indicators and calculate bridge priority scores.

The policy profiles in this module are provisional. They express different
planning priorities and will be evaluated through sensitivity analysis after
the budget-allocation module is implemented.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping

import pandas as pd

try:
    from .data_pipeline import DataValidationError
except ImportError:  # Support direct execution: python algorithm/scoring.py
    from data_pipeline import DataValidationError


SCORING_COLUMNS = {
    "bridge_id",
    "deterioration_probability",
    "lowest_rating",
    "adt",
    "detour_km",
    "predicted_cost",
}

INDICATORS = ("deterioration", "condition", "traffic", "detour")


@dataclass(frozen=True)
class ScoreWeights:
    deterioration: float
    condition: float
    traffic: float
    detour: float

    def __post_init__(self) -> None:
        values = asdict(self)
        if any(not math.isfinite(value) or value < 0 for value in values.values()):
            raise DataValidationError("Scoring weights must be finite and nonnegative")
        if not math.isclose(sum(values.values()), 1.0, abs_tol=1e-9):
            raise DataValidationError("Scoring weights must sum to 1.0")

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


PROVISIONAL_PROFILES: dict[str, ScoreWeights] = {
    "balanced": ScoreWeights(
        deterioration=0.40,
        condition=0.15,
        traffic=0.30,
        detour=0.15,
    ),
    "safety": ScoreWeights(
        deterioration=0.55,
        condition=0.25,
        traffic=0.10,
        detour=0.10,
    ),
    "traffic": ScoreWeights(
        deterioration=0.25,
        condition=0.10,
        traffic=0.50,
        detour=0.15,
    ),
}


@dataclass(frozen=True)
class ScoringReport:
    strategy: str
    weights: dict[str, float]
    bridge_count: int
    normalization_scope: str
    missing_detour_count: int
    minimum_priority_score: float
    mean_priority_score: float
    maximum_priority_score: float
    provisional_weights: bool = True

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _require_columns(frame: pd.DataFrame) -> None:
    missing = sorted(SCORING_COLUMNS.difference(frame.columns))
    if missing:
        raise DataValidationError(
            f"Scoring input is missing required columns: {', '.join(missing)}"
        )
    if frame.empty:
        raise DataValidationError("Scoring input contains no eligible bridges")


def _numeric(frame: pd.DataFrame, column: str, *, allow_missing: bool = False) -> pd.Series:
    parsed = pd.to_numeric(frame[column], errors="coerce")
    if not allow_missing and parsed.isna().any():
        raise DataValidationError(f"{column} contains missing or non-numeric values")
    invalid_finite = parsed.notna() & ~parsed.map(math.isfinite)
    if invalid_finite.any():
        raise DataValidationError(f"{column} contains non-finite values")
    return parsed.astype("float64")


def _percentile_score(values: pd.Series) -> pd.Series:
    """Map nonmissing values to 0-1 percentile scores, preserving ties."""

    result = pd.Series(math.nan, index=values.index, dtype="float64")
    valid = values.dropna()
    if valid.empty:
        return result
    if len(valid) == 1 or valid.nunique() == 1:
        result.loc[valid.index] = 0.5
        return result

    ranks = valid.rank(method="average")
    result.loc[valid.index] = (ranks - 1) / (len(valid) - 1)
    return result.clip(0, 1)


def resolve_weights(
    strategy: str = "balanced",
    custom_weights: Mapping[str, float] | None = None,
) -> tuple[str, ScoreWeights]:
    """Resolve either a named provisional profile or explicit custom weights."""

    if custom_weights is not None:
        missing = sorted(set(INDICATORS).difference(custom_weights))
        extra = sorted(set(custom_weights).difference(INDICATORS))
        if missing or extra:
            details = []
            if missing:
                details.append(f"missing {missing}")
            if extra:
                details.append(f"unexpected {extra}")
            raise DataValidationError(
                "Custom scoring weights have invalid keys: " + "; ".join(details)
            )
        return "custom", ScoreWeights(**dict(custom_weights))

    normalized_strategy = strategy.strip().lower()
    if normalized_strategy not in PROVISIONAL_PROFILES:
        choices = ", ".join(sorted(PROVISIONAL_PROFILES))
        raise DataValidationError(
            f"Unknown strategy {strategy!r}; expected one of: {choices}"
        )
    return normalized_strategy, PROVISIONAL_PROFILES[normalized_strategy]


def score_bridges(
    frame: pd.DataFrame,
    *,
    strategy: str = "balanced",
    custom_weights: Mapping[str, float] | None = None,
) -> tuple[pd.DataFrame, ScoringReport]:
    """Calculate component scores, contributions, total score, and rank.

    Normalization is performed over the complete frame passed to this function.
    For stable website scores, call this function on the statewide eligible
    bridge table before applying county or district filters.
    """

    _require_columns(frame)
    strategy_name, weights = resolve_weights(strategy, custom_weights)

    probability = _numeric(frame, "deterioration_probability")
    if not probability.between(0, 1).all():
        raise DataValidationError(
            "deterioration_probability must be between 0 and 1"
        )

    lowest_rating = _numeric(frame, "lowest_rating")
    if not lowest_rating.between(0, 9).all():
        raise DataValidationError("lowest_rating must be between 0 and 9")

    adt = _numeric(frame, "adt")
    if (adt < 0).any():
        raise DataValidationError("adt cannot be negative")

    detour = _numeric(frame, "detour_km", allow_missing=True)
    if (detour.dropna() < 0).any():
        raise DataValidationError("detour_km cannot be negative")

    predicted_cost = _numeric(frame, "predicted_cost")
    if not predicted_cost.gt(0).all():
        raise DataValidationError("predicted_cost must be greater than zero")

    scored = frame.copy()
    scored["deterioration_score"] = probability
    scored["condition_score"] = ((9 - lowest_rating) / 9).clip(0, 1)

    # Log compression prevents a few extremely busy bridges from dominating.
    log_adt = adt.map(math.log1p)
    scored["traffic_score"] = _percentile_score(log_adt)

    detour_score = _percentile_score(detour)
    missing_detour = detour_score.isna()
    scored["detour_score"] = detour_score.fillna(0.5)
    scored["detour_score_imputed"] = missing_detour

    weight_values = weights.to_dict()
    for indicator in INDICATORS:
        scored[f"{indicator}_contribution"] = (
            scored[f"{indicator}_score"] * weight_values[indicator] * 100
        )

    contribution_columns = [
        f"{indicator}_contribution" for indicator in INDICATORS
    ]
    scored["priority_score"] = scored[contribution_columns].sum(axis=1)

    scored = scored.sort_values(
        by=[
            "priority_score",
            "deterioration_probability",
            "lowest_rating",
            "predicted_cost",
            "bridge_id",
        ],
        ascending=[False, False, True, True, True],
        kind="mergesort",
    ).reset_index(drop=True)
    scored.insert(0, "priority_rank", range(1, len(scored) + 1))

    numeric_output_columns = [
        "deterioration_score",
        "condition_score",
        "traffic_score",
        "detour_score",
        *contribution_columns,
        "priority_score",
    ]
    scored[numeric_output_columns] = scored[numeric_output_columns].round(4)

    report = ScoringReport(
        strategy=strategy_name,
        weights=weight_values,
        bridge_count=len(scored),
        normalization_scope="all eligible bridges supplied before geographic filtering",
        missing_detour_count=int(missing_detour.sum()),
        minimum_priority_score=round(float(scored["priority_score"].min()), 4),
        mean_priority_score=round(float(scored["priority_score"].mean()), 4),
        maximum_priority_score=round(float(scored["priority_score"].max()), 4),
        provisional_weights=custom_weights is None,
    )
    return scored, report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Score and rank validated Ben2Bridges input data."
    )
    parser.add_argument("--input", required=True, help="Validated algorithm input CSV")
    parser.add_argument(
        "--strategy",
        default="balanced",
        choices=sorted(PROVISIONAL_PROFILES),
        help="Provisional policy profile",
    )
    parser.add_argument("--output", required=True, help="Output scored CSV")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        frame = pd.read_csv(
            args.input,
            dtype={"bridge_id": str, "county_fips": str},
            low_memory=False,
        )
        scored, report = score_bridges(frame, strategy=args.strategy)
    except (DataValidationError, OSError, pd.errors.ParserError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, indent=2))
        return 2

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    scored.to_csv(output_path, index=False)
    print(
        json.dumps(
            {
                "status": "ok",
                "output": str(output_path),
                "report": report.to_dict(),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
