"""Evaluate scoring profiles and budget portfolios on real model outputs.

This module does not tune or modify scoring weights. It repeatedly calls the
production scoring and allocation functions and records sensitivity metrics
that can be used in the project report or presentation.
"""

from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

import pandas as pd

try:
    from .budget_allocation import allocate_budget
    from .data_pipeline import DataValidationError, load_algorithm_inputs
    from .scoring import PROVISIONAL_PROFILES, score_bridges
except ImportError:  # Support direct execution.
    from budget_allocation import allocate_budget
    from data_pipeline import DataValidationError, load_algorithm_inputs
    from scoring import PROVISIONAL_PROFILES, score_bridges


def _percent(numerator: int | float, denominator: int | float) -> float:
    return round(100 * numerator / denominator, 4) if denominator else 0.0


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    return round(len(left & right) / len(union), 6) if union else 1.0


def evaluate_scenarios(
    nbi_path: str | Path,
    predictions_path: str | Path,
    counties_path: str | Path,
    *,
    budgets: list[float],
    strategies: list[str],
    high_risk_threshold: float = 0.70,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, object]]:
    """Run all statewide scenarios and return evaluation tables and metadata."""

    if not budgets or any(budget <= 0 for budget in budgets):
        raise DataValidationError("Evaluation budgets must be positive")
    if len(set(budgets)) != len(budgets):
        raise DataValidationError("Evaluation budgets must be unique")
    unknown = sorted(set(strategies).difference(PROVISIONAL_PROFILES))
    if unknown:
        raise DataValidationError(f"Unknown evaluation strategies: {unknown}")

    algorithm_input, validation = load_algorithm_inputs(
        nbi_path, predictions_path, counties_path
    )
    total_high_risk = int(
        algorithm_input["deterioration_risk_score"]
        .astype("float64")
        .ge(high_risk_threshold)
        .sum()
    )
    total_poor = int(
        algorithm_input["bridge_condition"]
        .astype("string")
        .str.upper()
        .eq("P")
        .sum()
    )

    scenario_rows: list[dict[str, object]] = []
    selections: dict[tuple[str, float], set[str]] = {}
    scoring_reports: dict[str, dict[str, object]] = {}

    for strategy in strategies:
        scored, scoring_report = score_bridges(
            algorithm_input, strategy=strategy
        )
        scoring_reports[strategy] = scoring_report.to_dict()

        for budget in sorted(budgets):
            allocation, summary = allocate_budget(
                scored,
                budget=budget,
                high_risk_threshold=high_risk_threshold,
            )
            selected = allocation.loc[allocation["selected_for_repair"]].copy()
            selected_ids = set(selected["bridge_id"].astype(str))
            selections[(strategy, budget)] = selected_ids

            selected_costs = selected["predicted_cost"].astype("float64")
            scenario_rows.append(
                {
                    "strategy": strategy,
                    "budget_usd": budget,
                    "solver_status": summary.solver_status,
                    "eligible_bridge_count": summary.eligible_bridge_count,
                    "selected_bridge_count": summary.selected_bridge_count,
                    "total_predicted_cost": summary.total_predicted_cost,
                    "remaining_budget": summary.remaining_budget,
                    "budget_utilization_percent": summary.budget_utilization_percent,
                    "priority_protection_fraction": summary.priority_protection_fraction,
                    "priority_protected_bridge_count": summary.priority_protected_bridge_count,
                    "priority_protected_budget_used": summary.priority_protected_budget_used,
                    "priority_protected_budget_percent": summary.priority_protected_budget_percent,
                    "selected_high_risk_count": summary.selected_high_risk_count,
                    "total_eligible_high_risk_count": total_high_risk,
                    "high_risk_coverage_percent": _percent(
                        summary.selected_high_risk_count, total_high_risk
                    ),
                    "selected_poor_condition_count": summary.selected_poor_condition_count,
                    "total_eligible_poor_condition_count": total_poor,
                    "poor_condition_coverage_percent": _percent(
                        summary.selected_poor_condition_count, total_poor
                    ),
                    "selected_total_adt": summary.selected_total_adt,
                    "total_selected_priority_score": summary.total_selected_priority_score,
                    "mean_selected_priority_score": summary.mean_selected_priority_score,
                    "median_selected_cost": round(float(selected_costs.median()), 2),
                    "maximum_selected_cost": round(float(selected_costs.max()), 2),
                    "unique_selected_bridge_count": len(selected_ids),
                    "within_budget": summary.total_predicted_cost <= budget + 0.01,
                }
            )

    scenario_summary = pd.DataFrame(scenario_rows).sort_values(
        ["budget_usd", "strategy"], kind="mergesort"
    )

    strategy_overlap_rows: list[dict[str, object]] = []
    for budget in sorted(budgets):
        for left, right in itertools.combinations(strategies, 2):
            left_ids = selections[(left, budget)]
            right_ids = selections[(right, budget)]
            intersection = len(left_ids & right_ids)
            strategy_overlap_rows.append(
                {
                    "budget_usd": budget,
                    "strategy_a": left,
                    "strategy_b": right,
                    "selected_a": len(left_ids),
                    "selected_b": len(right_ids),
                    "shared_selected_count": intersection,
                    "jaccard_overlap": _jaccard(left_ids, right_ids),
                    "percent_of_a_shared": _percent(intersection, len(left_ids)),
                    "percent_of_b_shared": _percent(intersection, len(right_ids)),
                }
            )
    strategy_overlap = pd.DataFrame(strategy_overlap_rows)

    budget_retention_rows: list[dict[str, object]] = []
    sorted_budgets = sorted(budgets)
    for strategy in strategies:
        for lower, higher in zip(sorted_budgets, sorted_budgets[1:]):
            lower_ids = selections[(strategy, lower)]
            higher_ids = selections[(strategy, higher)]
            retained = len(lower_ids & higher_ids)
            budget_retention_rows.append(
                {
                    "strategy": strategy,
                    "lower_budget_usd": lower,
                    "higher_budget_usd": higher,
                    "lower_selected_count": len(lower_ids),
                    "higher_selected_count": len(higher_ids),
                    "retained_from_lower_count": retained,
                    "lower_portfolio_retention_percent": _percent(
                        retained, len(lower_ids)
                    ),
                    "jaccard_overlap": _jaccard(lower_ids, higher_ids),
                }
            )
    budget_retention = pd.DataFrame(budget_retention_rows)

    integrity = {
        "all_solvers_optimal": bool(
            scenario_summary["solver_status"].astype(str).str.startswith("optimal:").all()
        ),
        "all_portfolios_within_budget": bool(scenario_summary["within_budget"].all()),
        "all_selected_ids_unique": bool(
            scenario_summary["selected_bridge_count"]
            .eq(scenario_summary["unique_selected_bridge_count"])
            .all()
        ),
        "selected_counts_nondecreasing_with_budget": all(
            scenario_summary.loc[
                scenario_summary["strategy"].eq(strategy)
            ]
            .sort_values("budget_usd")["selected_bridge_count"]
            .is_monotonic_increasing
            for strategy in strategies
        ),
    }
    report = {
        "status": "ok" if all(integrity.values()) else "review_required",
        "methodology": (
            "fixed-weight sensitivity evaluation with a 25% strict priority "
            "prefix and exact residual MILP; no weight tuning performed"
        ),
        "budgets_usd": sorted(budgets),
        "strategies": strategies,
        "scenario_count": len(scenario_summary),
        "high_risk_threshold": high_risk_threshold,
        "eligible_high_risk_count": total_high_risk,
        "eligible_poor_condition_count": total_poor,
        "validation": validation.to_dict(),
        "scoring_reports": scoring_reports,
        "integrity_checks": integrity,
    }
    return scenario_summary, strategy_overlap, budget_retention, report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate Ben2Bridges scenarios")
    parser.add_argument("--nbi", required=True)
    parser.add_argument("--predictions", required=True)
    parser.add_argument("--counties", required=True)
    parser.add_argument("--budgets", nargs="+", required=True, type=float)
    parser.add_argument(
        "--strategies",
        nargs="+",
        default=["balanced", "safety", "traffic"],
    )
    parser.add_argument("--output-dir", required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    scenarios, overlaps, retention, report = evaluate_scenarios(
        args.nbi,
        args.predictions,
        args.counties,
        budgets=args.budgets,
        strategies=args.strategies,
    )
    output_paths = {
        "scenario_summary": output_dir / "scenario_summary.csv",
        "strategy_overlap": output_dir / "strategy_overlap.csv",
        "budget_retention": output_dir / "budget_retention.csv",
        "evaluation_report": output_dir / "evaluation_report.json",
    }
    scenarios.to_csv(output_paths["scenario_summary"], index=False)
    overlaps.to_csv(output_paths["strategy_overlap"], index=False)
    retention.to_csv(output_paths["budget_retention"], index=False)
    output_paths["evaluation_report"].write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "status": report["status"],
                "scenario_count": report["scenario_count"],
                "integrity_checks": report["integrity_checks"],
                "outputs": {key: str(path) for key, path in output_paths.items()},
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
