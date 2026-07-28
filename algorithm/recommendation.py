"""Run the complete Ben2Bridges decision workflow and return website-ready JSON.

The public ``generate_recommendation`` function is intentionally independent of
any web framework.  A later website API can call it without duplicating data
validation, scoring, allocation, or explanation logic.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

try:
    from .budget_allocation import allocate_budget
    from .data_pipeline import DataValidationError, load_algorithm_inputs
    from .scoring import INDICATORS, score_bridges
except ImportError:  # Support direct execution: python algorithm/recommendation.py
    from budget_allocation import allocate_budget
    from data_pipeline import DataValidationError, load_algorithm_inputs
    from scoring import INDICATORS, score_bridges


SCHEMA_VERSION = "1.0"
INDICATOR_LABELS = {
    "deterioration": "Predicted deterioration",
    "condition": "Current condition",
    "traffic": "Traffic volume",
    "detour": "Detour impact",
}


def _json_value(value: Any) -> Any:
    """Convert pandas/NumPy scalar values to strict JSON-compatible values."""

    if value is None or value is pd.NA:
        return None
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def _development_model_versions(model_versions: tuple[str, ...]) -> bool:
    markers = ("mock", "test", "demo", "synthetic", "scale-test")
    return any(
        marker in version.lower()
        for version in model_versions
        for marker in markers
    )


def _reason_text(row: pd.Series, indicator: str) -> str:
    if indicator == "deterioration":
        probability = 100 * float(row["deterioration_probability"])
        return f"Predicted deterioration probability is {probability:.1f}%."
    if indicator == "condition":
        return (
            "Lowest applicable FHWA condition rating is "
            f"{int(row['lowest_rating'])}/9."
        )
    if indicator == "traffic":
        return f"Carries approximately {float(row['adt']):,.0f} vehicles per day."
    return f"Closure would add approximately {float(row['detour_km']):,.0f} km."


def _reasons(row: pd.Series, limit: int = 2) -> list[dict[str, object]]:
    contributions: list[tuple[str, float]] = []
    for indicator in INDICATORS:
        if indicator == "detour" and bool(row["detour_score_imputed"]):
            continue
        contributions.append(
            (indicator, float(row[f"{indicator}_contribution"]))
        )
    contributions.sort(key=lambda item: (-item[1], INDICATORS.index(item[0])))

    return [
        {
            "indicator": indicator,
            "label": INDICATOR_LABELS[indicator],
            "score_contribution": round(contribution, 4),
            "text": _reason_text(row, indicator),
        }
        for indicator, contribution in contributions[:limit]
    ]


def _bridge_record(
    row: pd.Series,
    *,
    regional_rank: int,
) -> dict[str, object]:
    selected = bool(row["selected_for_repair"])
    if selected:
        explanation = (
            "Included in the highest-scoring portfolio under the stated budget, "
            "strategy, region, and model assumptions."
        )
    else:
        explanation = (
            "Not included because another feasible combination produced a higher "
            "total priority score within the stated budget and assumptions."
        )

    return {
        "bridge_id": str(row["bridge_id"]),
        "facility_carried": _json_value(row.get("facility_carried")),
        "feature_crossed": _json_value(row.get("feature_crossed")),
        "location": _json_value(row.get("location")),
        "county_fips": str(row["county_fips"]).zfill(3),
        "county_name": _json_value(row.get("county_name")),
        "penndot_district": int(row["penndot_district"]),
        "latitude": _json_value(row.get("latitude")),
        "longitude": _json_value(row.get("longitude")),
        "year_built": int(row["year_built"]),
        "priority_rank_statewide": int(row["priority_rank"]),
        "priority_rank_in_region": regional_rank,
        "funded_rank": _json_value(row["funded_rank"]),
        "priority_score": float(row["priority_score"]),
        "selected_for_repair": selected,
        "funding_status": str(row["funding_status"]),
        "deterioration_probability": float(row["deterioration_probability"]),
        "prediction_horizon_years": int(row["prediction_horizon_years"]),
        "model_version": str(row["model_version"]),
        "predicted_cost": float(row["predicted_cost"]),
        "cost_unit": str(row["cost_unit"]),
        "bridge_condition": str(row["bridge_condition"]),
        "lowest_rating": int(row["lowest_rating"]),
        "adt": int(row["adt"]),
        "adt_year": _json_value(row["adt_year"]),
        "detour_km": _json_value(row["detour_km"]),
        "component_scores": {
            indicator: float(row[f"{indicator}_score"])
            for indicator in INDICATORS
        },
        "score_contributions": {
            indicator: float(row[f"{indicator}_contribution"])
            for indicator in INDICATORS
        },
        "detour_score_imputed": bool(row["detour_score_imputed"]),
        "reasons": _reasons(row),
        "selection_explanation": explanation,
    }


def generate_recommendation(
    nbi_path: str | Path,
    predictions_path: str | Path,
    counties_path: str | Path,
    *,
    budget: float,
    strategy: str = "balanced",
    county_fips: str | int | None = None,
    penndot_district: int | None = None,
    high_risk_threshold: float = 0.70,
    unfunded_limit: int = 20,
) -> dict[str, object]:
    """Return a complete recommendation response suitable for a website API."""

    if isinstance(unfunded_limit, bool) or not isinstance(unfunded_limit, int):
        raise DataValidationError("unfunded_limit must be a nonnegative integer")
    if unfunded_limit < 0:
        raise DataValidationError("unfunded_limit must be a nonnegative integer")

    algorithm_input, validation_report = load_algorithm_inputs(
        nbi_path, predictions_path, counties_path
    )

    # Scoring occurs statewide before geographic filtering so a bridge's score
    # does not change when a website user switches between geographic views.
    scored, scoring_report = score_bridges(algorithm_input, strategy=strategy)
    allocation, allocation_summary = allocate_budget(
        scored,
        budget=budget,
        county_fips=county_fips,
        penndot_district=penndot_district,
        high_risk_threshold=high_risk_threshold,
    )

    regional_order = allocation.sort_values(
        ["priority_rank", "bridge_id"], kind="mergesort"
    )
    regional_ranks = {
        str(bridge_id): rank
        for rank, bridge_id in enumerate(regional_order["bridge_id"], start=1)
    }

    selected = allocation.loc[allocation["selected_for_repair"]].sort_values(
        ["funded_rank", "bridge_id"], kind="mergesort"
    )
    unfunded = allocation.loc[~allocation["selected_for_repair"]].sort_values(
        ["priority_rank", "bridge_id"], kind="mergesort"
    )

    selected_records = [
        _bridge_record(
            row,
            regional_rank=regional_ranks[str(row["bridge_id"])],
        )
        for _, row in selected.iterrows()
    ]
    unfunded_records = [
        _bridge_record(
            row,
            regional_rank=regional_ranks[str(row["bridge_id"])],
        )
        for _, row in unfunded.head(unfunded_limit).iterrows()
    ]

    development_data = _development_model_versions(validation_report.model_versions)
    warnings = list(validation_report.warnings)
    if development_data:
        warnings.insert(
            0,
            "Development model predictions are in use; recommendations are not "
            "suitable for real funding decisions.",
        )

    request_region: dict[str, object] = {"type": "statewide", "value": None}
    if county_fips is not None:
        request_region = {
            "type": "county",
            "value": allocation_summary.region.split(":", 1)[1],
        }
    elif penndot_district is not None:
        request_region = {
            "type": "district",
            "value": int(penndot_district),
        }

    response: dict[str, object] = {
        "status": "ok",
        "schema_version": SCHEMA_VERSION,
        "development_data": development_data,
        "request": {
            "budget": float(budget),
            "strategy": scoring_report.strategy,
            "region": request_region,
            "high_risk_threshold": float(high_risk_threshold),
        },
        "warnings": warnings,
        "data_validation": validation_report.to_dict(),
        "scoring": scoring_report.to_dict(),
        "summary": allocation_summary.to_dict(),
        "selected_bridge_ids": [record["bridge_id"] for record in selected_records],
        "selected_bridges": selected_records,
        "high_priority_unfunded": unfunded_records,
        "high_priority_unfunded_returned": len(unfunded_records),
    }

    # This is also an internal assertion that API callers never receive NaN.
    return json.loads(json.dumps(response, allow_nan=False))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a website-ready Ben2Bridges recommendation JSON file."
    )
    parser.add_argument("--nbi", required=True, help="Path to the NBI CSV")
    parser.add_argument(
        "--predictions", required=True, help="Path to the combined model CSV"
    )
    parser.add_argument("--counties", required=True, help="Path to county mapping CSV")
    parser.add_argument("--budget", required=True, type=float, help="Available budget")
    parser.add_argument(
        "--strategy",
        default="balanced",
        choices=("balanced", "safety", "traffic"),
    )
    region = parser.add_mutually_exclusive_group()
    region.add_argument("--county-fips")
    region.add_argument("--district", type=int)
    parser.add_argument("--high-risk-threshold", type=float, default=0.70)
    parser.add_argument("--unfunded-limit", type=int, default=20)
    parser.add_argument("--output", required=True, help="Output JSON path")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        response = generate_recommendation(
            args.nbi,
            args.predictions,
            args.counties,
            budget=args.budget,
            strategy=args.strategy,
            county_fips=args.county_fips,
            penndot_district=args.district,
            high_risk_threshold=args.high_risk_threshold,
            unfunded_limit=args.unfunded_limit,
        )
    except (DataValidationError, OSError, pd.errors.ParserError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, indent=2))
        return 2

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(response, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": "ok",
                "output": str(output_path),
                "selected_bridge_count": response["summary"][
                    "selected_bridge_count"
                ],
                "total_predicted_cost": response["summary"][
                    "total_predicted_cost"
                ],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
