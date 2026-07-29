"""Search transparent, official-informed Balanced scoring weights.

This module does not treat incomplete project history as ground truth.  It
searches a bounded policy grid and chooses the candidate whose weakest
relative performance is strongest across official importance alignment,
model high-risk coverage, poor-condition coverage, and perturbation stability.
Historical interventions, strict BridgeCare proxies, and environmental-justice
representation are reported as audits only.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Iterable, Mapping

import numpy as np
import pandas as pd

try:
    from .budget_allocation import allocate_budget
    from .data_pipeline import DataValidationError
    from .scoring import score_bridges
except ImportError:  # Support direct execution.
    from budget_allocation import allocate_budget
    from data_pipeline import DataValidationError
    from scoring import score_bridges


CALIBRATION_COLUMNS = {
    "bridge_id",
    "deterioration_risk_score",
    "lowest_rating",
    "bridge_condition",
    "adt",
    "detour_km",
    "predicted_cost",
    "cost_unit",
    "county_fips",
    "penndot_district",
    "partial_penndot_importance_raw",
    "is_nhs",
    "historical_intervention_weak_label",
    "ej_area",
}
SEARCH_RANGES = {
    "deterioration": (0.30, 0.55),
    "condition": (0.10, 0.25),
    "traffic": (0.15, 0.40),
    "detour": (0.05, 0.20),
}
SEARCH_STEP = 0.05
DEFAULT_CUTOFFS = (0.05, 0.10, 0.20)
CORE_METRICS = (
    "official_importance_overlap_mean",
    "high_risk_recall_mean",
    "poor_condition_recall_mean",
    "ranking_stability_mean",
)
BASELINE_BALANCED_WEIGHTS = {
    "deterioration": 0.40,
    "condition": 0.15,
    "traffic": 0.30,
    "detour": 0.15,
}


def generate_candidate_weights() -> list[dict[str, float]]:
    """Generate the bounded 5-percentage-point policy grid."""

    candidates: list[dict[str, float]] = []
    for deterioration in range(30, 56, 5):
        for condition in range(10, 26, 5):
            for traffic in range(15, 41, 5):
                detour = 100 - deterioration - condition - traffic
                if 5 <= detour <= 20:
                    candidates.append(
                        {
                            "deterioration": deterioration / 100,
                            "condition": condition / 100,
                            "traffic": traffic / 100,
                            "detour": detour / 100,
                        }
                    )
    return candidates


def _require_columns(frame: pd.DataFrame) -> None:
    missing = sorted(CALIBRATION_COLUMNS.difference(frame.columns))
    if missing:
        raise DataValidationError(
            "Calibration input is missing columns: " + ", ".join(missing)
        )
    if frame.empty:
        raise DataValidationError("Calibration input contains no bridges")
    ids = frame["bridge_id"].astype("string").str.strip()
    if ids.eq("").any() or ids.duplicated().any():
        raise DataValidationError("Calibration bridge_id must be nonempty and unique")


def _as_bool(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series.dtype):
        return series.fillna(False).astype(bool)
    return (
        series.astype("string")
        .str.strip()
        .str.lower()
        .isin({"true", "1", "yes", "y"})
    )


def _top_ids(scored: pd.DataFrame, count: int) -> set[str]:
    return set(scored.head(count)["bridge_id"].astype(str))


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return len(left & right) / len(union) if union else 1.0


def _recall(selected: set[str], target: set[str]) -> float:
    return len(selected & target) / len(target) if target else 1.0


def _mean(values: Iterable[float]) -> float:
    materialized = list(values)
    return float(sum(materialized) / len(materialized))


def _official_top_ids(frame: pd.DataFrame, count: int) -> set[str]:
    ordered = frame.sort_values(
        ["partial_penndot_importance_raw", "bridge_id"],
        ascending=[False, True],
        kind="mergesort",
    )
    return set(ordered.head(count)["bridge_id"].astype(str))


def _target_sets(frame: pd.DataFrame) -> dict[str, set[str]]:
    ids = frame["bridge_id"].astype(str)
    is_nhs = _as_bool(frame["is_nhs"])
    rating = pd.to_numeric(frame["lowest_rating"], errors="raise")
    importance = pd.to_numeric(
        frame["partial_penndot_importance_raw"], errors="raise"
    )
    strict_bridgecare = (is_nhs & rating.le(3.5)) | (
        ~is_nhs & importance.ge(15_000) & rating.le(3.5)
    )
    return {
        "high_risk": set(ids[pd.to_numeric(
            frame["deterioration_risk_score"], errors="raise"
        ).ge(0.70)]),
        "poor_condition": set(ids[
            frame["bridge_condition"].astype("string").str.upper().eq("P")
        ]),
        "strict_bridgecare_proxy": set(ids[strict_bridgecare]),
        "historical_weak_label": set(ids[
            _as_bool(frame["historical_intervention_weak_label"])
        ]),
        "ej_area": set(ids[_as_bool(frame["ej_area"])]),
    }


def _perturbed_frames(
    frame: pd.DataFrame, *, count: int, seed: int
) -> list[pd.DataFrame]:
    """Return deterministic, small input perturbations for rank stability."""

    rng = np.random.default_rng(seed)
    perturbations: list[pd.DataFrame] = []
    for _ in range(count):
        perturbed = frame.copy()
        risk = pd.to_numeric(
            perturbed["deterioration_risk_score"], errors="raise"
        ).to_numpy(dtype=float)
        perturbed["deterioration_risk_score"] = np.clip(
            risk + rng.normal(0, 0.025, len(perturbed)), 0, 1
        )

        rating = pd.to_numeric(
            perturbed["lowest_rating"], errors="raise"
        ).to_numpy(dtype=float)
        perturbed["lowest_rating"] = np.clip(
            rating + rng.normal(0, 0.25, len(perturbed)), 0, 9
        )

        adt = pd.to_numeric(perturbed["adt"], errors="raise").to_numpy(dtype=float)
        perturbed["adt"] = np.maximum(
            0, adt * rng.lognormal(0, 0.08, len(perturbed))
        )

        detour = pd.to_numeric(perturbed["detour_km"], errors="coerce")
        perturbed["detour_km"] = detour.astype("float64")
        valid_detour = detour.notna()
        perturbed.loc[valid_detour, "detour_km"] = (
            detour.loc[valid_detour].to_numpy(dtype=float)
            * rng.lognormal(0, 0.08, int(valid_detour.sum()))
        )
        perturbations.append(perturbed)
    return perturbations


def evaluate_weight_candidates(
    frame: pd.DataFrame,
    *,
    cutoffs: tuple[float, ...] = DEFAULT_CUTOFFS,
    perturbation_count: int = 5,
    seed: int = 20260729,
) -> tuple[pd.DataFrame, dict[str, object]]:
    """Evaluate all candidates and return a selected robust compromise."""

    _require_columns(frame)
    if not cutoffs or any(not 0 < cutoff < 1 for cutoff in cutoffs):
        raise DataValidationError("Calibration cutoffs must be between 0 and 1")
    if perturbation_count <= 0:
        raise DataValidationError("perturbation_count must be positive")

    targets = _target_sets(frame)
    counts = [max(1, round(len(frame) * cutoff)) for cutoff in cutoffs]
    official_sets = {
        count: _official_top_ids(frame, count) for count in counts
    }
    perturbed = _perturbed_frames(frame, count=perturbation_count, seed=seed)
    ej_population_share = len(targets["ej_area"]) / len(frame)

    rows: list[dict[str, object]] = []
    for weights in generate_candidate_weights():
        scored, _ = score_bridges(frame, custom_weights=weights)
        tops = {count: _top_ids(scored, count) for count in counts}
        ten_percent_count = max(1, round(len(frame) * 0.10))
        top_ten = _top_ids(scored, ten_percent_count)

        stability_sets = []
        baseline_stability_top = top_ten
        for perturbed_frame in perturbed:
            perturbed_scored, _ = score_bridges(
                perturbed_frame, custom_weights=weights
            )
            stability_sets.append(
                _jaccard(
                    baseline_stability_top,
                    _top_ids(perturbed_scored, ten_percent_count),
                )
            )

        ej_top_share = _recall(top_ten, targets["ej_area"]) * (
            len(targets["ej_area"]) / len(top_ten)
            if targets["ej_area"]
            else 0
        )
        rows.append(
            {
                **weights,
                "official_importance_overlap_mean": _mean(
                    _recall(tops[count], official_sets[count]) for count in counts
                ),
                "high_risk_recall_mean": _mean(
                    _recall(tops[count], targets["high_risk"])
                    for count in counts
                ),
                "poor_condition_recall_mean": _mean(
                    _recall(tops[count], targets["poor_condition"])
                    for count in counts
                ),
                "ranking_stability_mean": _mean(stability_sets),
                "strict_bridgecare_recall_mean_audit": _mean(
                    _recall(tops[count], targets["strict_bridgecare_proxy"])
                    for count in counts
                ),
                "historical_weak_label_recall_mean_audit": _mean(
                    _recall(tops[count], targets["historical_weak_label"])
                    for count in counts
                ),
                "ej_share_top_10pct_audit": ej_top_share,
                "ej_representation_ratio_audit": (
                    ej_top_share / ej_population_share
                    if ej_population_share
                    else math.nan
                ),
            }
        )

    results = pd.DataFrame(rows)
    relative_columns = []
    for metric in CORE_METRICS:
        best = float(results[metric].max())
        relative = f"relative_{metric}"
        results[relative] = results[metric] / best if best > 0 else 1.0
        relative_columns.append(relative)
    results["robust_maximin_score"] = results[relative_columns].min(axis=1)
    results["mean_relative_core_score"] = results[relative_columns].mean(axis=1)

    current = BASELINE_BALANCED_WEIGHTS
    results["distance_from_baseline_weights"] = sum(
        (results[indicator] - value).abs() for indicator, value in current.items()
    )
    results = results.sort_values(
        [
            "robust_maximin_score",
            "mean_relative_core_score",
            "ranking_stability_mean",
            "distance_from_baseline_weights",
            "deterioration",
            "condition",
            "traffic",
            "detour",
        ],
        ascending=[False, False, False, True, True, True, True, True],
        kind="mergesort",
    ).reset_index(drop=True)
    results.insert(0, "search_rank", range(1, len(results) + 1))

    selected = results.iloc[0]
    selected_weights = {
        indicator: float(selected[indicator])
        for indicator in ("deterioration", "condition", "traffic", "detour")
    }
    metadata = {
        "candidate_count": len(results),
        "cutoffs": list(cutoffs),
        "perturbation_count": perturbation_count,
        "perturbation_seed": seed,
        "selection_rule": (
            "maximize the minimum relative performance across official importance "
            "overlap, high-risk recall, poor-condition recall, and rank stability"
        ),
        "selected_weights": selected_weights,
        "baseline_weights": current,
        "target_counts": {key: len(value) for key, value in targets.items()},
        "audit_metrics_not_used_for_selection": [
            "strict_bridgecare_recall_mean_audit",
            "historical_weak_label_recall_mean_audit",
            "ej_share_top_10pct_audit",
            "ej_representation_ratio_audit",
        ],
        "search_ranges": SEARCH_RANGES,
        "search_step": SEARCH_STEP,
    }
    return results, metadata


def compare_budget_portfolios(
    frame: pd.DataFrame,
    *,
    selected_weights: Mapping[str, float],
    budgets: list[float],
) -> pd.DataFrame:
    """Run exact MILP portfolios for current and selected Balanced weights."""

    targets = _target_sets(frame)
    population_ej_share = len(targets["ej_area"]) / len(frame)
    profiles = {
        "baseline_balanced": BASELINE_BALANCED_WEIGHTS,
        "recommended_balanced": dict(selected_weights),
    }
    official_top = _official_top_ids(frame, max(1, round(len(frame) * 0.10)))
    rows: list[dict[str, object]] = []
    for profile_name, weights in profiles.items():
        scored, _ = score_bridges(frame, custom_weights=weights)
        for budget in sorted(budgets):
            allocation, summary = allocate_budget(scored, budget=budget)
            selected = allocation.loc[allocation["selected_for_repair"]]
            selected_ids = set(selected["bridge_id"].astype(str))
            ej_share = _recall(selected_ids, targets["ej_area"]) * (
                len(targets["ej_area"]) / len(selected_ids)
                if selected_ids and targets["ej_area"]
                else 0
            )
            rows.append(
                {
                    "profile": profile_name,
                    "budget_usd": budget,
                    **weights,
                    "solver_status": summary.solver_status,
                    "selected_bridge_count": summary.selected_bridge_count,
                    "total_predicted_cost": summary.total_predicted_cost,
                    "budget_utilization_percent": summary.budget_utilization_percent,
                    "selected_high_risk_count": len(
                        selected_ids & targets["high_risk"]
                    ),
                    "selected_poor_condition_count": len(
                        selected_ids & targets["poor_condition"]
                    ),
                    "selected_official_top_10pct_count": len(
                        selected_ids & official_top
                    ),
                    "selected_strict_bridgecare_count_audit": len(
                        selected_ids & targets["strict_bridgecare_proxy"]
                    ),
                    "selected_historical_weak_label_count_audit": len(
                        selected_ids & targets["historical_weak_label"]
                    ),
                    "selected_total_adt": summary.selected_total_adt,
                    "selected_ej_share_audit": ej_share,
                    "ej_representation_ratio_audit": (
                        ej_share / population_ej_share
                        if population_ej_share
                        else math.nan
                    ),
                    "within_budget": summary.total_predicted_cost <= budget + 0.01,
                }
            )
    return pd.DataFrame(rows).sort_values(
        ["budget_usd", "profile"], kind="mergesort"
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Calibrate official-informed Ben2Bridges Balanced weights"
    )
    parser.add_argument("--features", required=True)
    parser.add_argument("--budgets", nargs="+", type=float, required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--perturbations", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20260729)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if not args.budgets or any(budget <= 0 for budget in args.budgets):
        raise DataValidationError("Budgets must be positive")
    frame = pd.read_csv(
        args.features,
        dtype={"bridge_id": str, "county_fips": str},
        low_memory=False,
    )
    results, metadata = evaluate_weight_candidates(
        frame,
        perturbation_count=args.perturbations,
        seed=args.seed,
    )
    portfolios = compare_budget_portfolios(
        frame,
        selected_weights=metadata["selected_weights"],
        budgets=args.budgets,
    )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    search_path = output_dir / "weight_search_results.csv"
    portfolio_path = output_dir / "portfolio_weight_comparison.csv"
    report_path = output_dir / "weight_calibration_report.json"
    results.to_csv(search_path, index=False)
    portfolios.to_csv(portfolio_path, index=False)

    current_mask = np.logical_and.reduce(
        [
            np.isclose(results[indicator], value)
            for indicator, value in metadata["baseline_weights"].items()
        ]
    )
    current_row = results.loc[current_mask].iloc[0]
    report = {
        "status": "ok",
        "methodology": metadata,
        "recommended_candidate": results.iloc[0].to_dict(),
        "baseline_candidate": current_row.to_dict(),
        "portfolio_integrity": {
            "all_solvers_optimal": bool(
                portfolios["solver_status"].astype(str).str.startswith("optimal:").all()
            ),
            "all_within_budget": bool(portfolios["within_budget"].all()),
        },
        "limitations": [
            "Selected weights are an official-informed robust compromise, not government-approved optimal weights.",
            "PennDOT importance is partial because internal scour, fracture-critical, and flood-history factors are unavailable.",
            "Historical intervention labels are incomplete and are audit-only.",
            "PennEnviroScreen is audit-only and does not prescribe a bridge scoring weight.",
        ],
    }
    report_path.write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": "ok",
                "selected_weights": metadata["selected_weights"],
                "candidate_count": metadata["candidate_count"],
                "portfolio_integrity": report["portfolio_integrity"],
                "outputs": {
                    "search": str(search_path),
                    "portfolios": str(portfolio_path),
                    "report": str(report_path),
                },
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
