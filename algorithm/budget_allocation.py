"""Select an exact budget-constrained portfolio of scored bridges."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, milp

try:
    from .data_pipeline import DataValidationError
except ImportError:  # Support direct execution.
    from data_pipeline import DataValidationError


ALLOCATION_COLUMNS = {
    "bridge_id",
    "priority_rank",
    "priority_score",
    "deterioration_probability",
    "predicted_cost",
    "cost_unit",
    "bridge_condition",
    "adt",
    "detour_km",
    "county_fips",
    "penndot_district",
}


@dataclass(frozen=True)
class AllocationSummary:
    region: str
    budget: float
    cost_unit: str
    eligible_bridge_count: int
    selected_bridge_count: int
    unfunded_bridge_count: int
    total_predicted_cost: float
    remaining_budget: float
    budget_utilization_percent: float
    total_selected_priority_score: float
    mean_selected_priority_score: float
    high_risk_threshold: float
    selected_high_risk_count: int
    unfunded_high_risk_count: int
    selected_poor_condition_count: int
    selected_total_adt: float
    selected_total_detour_km: float
    selected_missing_detour_count: int
    solver_status: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _require_columns(frame: pd.DataFrame) -> None:
    missing = sorted(ALLOCATION_COLUMNS.difference(frame.columns))
    if missing:
        raise DataValidationError(
            f"Allocation input is missing required columns: {', '.join(missing)}"
        )
    if frame.empty:
        raise DataValidationError("Allocation input contains no scored bridges")


def _numeric(
    frame: pd.DataFrame, column: str, *, allow_missing: bool = False
) -> pd.Series:
    parsed = pd.to_numeric(frame[column], errors="coerce")
    if not allow_missing and parsed.isna().any():
        raise DataValidationError(f"{column} contains missing or non-numeric values")
    nonfinite = parsed.notna() & ~parsed.map(math.isfinite)
    if nonfinite.any():
        raise DataValidationError(f"{column} contains non-finite values")
    return parsed.astype("float64")


def _normalize_county_fips(value: str | int) -> str:
    text = str(value).strip()
    try:
        numeric = float(text)
    except ValueError as exc:
        raise DataValidationError(f"Invalid county FIPS code: {value!r}") from exc
    if not numeric.is_integer() or not 1 <= numeric <= 999:
        raise DataValidationError(f"Invalid county FIPS code: {value!r}")
    return f"{int(numeric):03d}"


def _filter_region(
    frame: pd.DataFrame,
    *,
    county_fips: str | int | None,
    penndot_district: int | None,
) -> tuple[pd.DataFrame, str]:
    if county_fips is not None and penndot_district is not None:
        raise DataValidationError(
            "county_fips and penndot_district filters are mutually exclusive"
        )

    candidates = frame.copy()
    if county_fips is not None:
        normalized = _normalize_county_fips(county_fips)
        frame_counties = (
            frame["county_fips"]
            .astype("string")
            .str.strip()
            .str.replace(r"\.0$", "", regex=True)
            .str.zfill(3)
        )
        candidates = frame.loc[frame_counties.eq(normalized)].copy()
        region = f"county:{normalized}"
    elif penndot_district is not None:
        try:
            district = int(penndot_district)
        except (TypeError, ValueError) as exc:
            raise DataValidationError(
                f"Invalid PennDOT district: {penndot_district!r}"
            ) from exc
        if district not in {1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12}:
            raise DataValidationError(f"Invalid PennDOT district: {district}")
        district_values = pd.to_numeric(frame["penndot_district"], errors="coerce")
        candidates = frame.loc[district_values.eq(district)].copy()
        region = f"district:{district}"
    else:
        region = "statewide"

    if candidates.empty:
        raise DataValidationError(f"No eligible bridges match region {region}")
    return candidates.reset_index(drop=True), region


def _solve_selection(
    priority_scores: np.ndarray,
    costs: np.ndarray,
    budget: float,
) -> tuple[np.ndarray, str]:
    bridge_count = len(priority_scores)
    if budget <= 0 or budget < float(costs.min()):
        return np.zeros(bridge_count, dtype=bool), "optimal:no_affordable_bridge"
    if float(costs.sum()) <= budget:
        return np.ones(bridge_count, dtype=bool), "optimal:all_bridges_affordable"

    # Scaling costs and budget improves numerical conditioning without changing
    # the feasible portfolios.
    cost_scale = max(float(costs.max()), budget, 1.0)
    scaled_costs = costs / cost_scale
    scaled_budget = budget / cost_scale

    result = milp(
        c=-priority_scores,
        integrality=np.ones(bridge_count, dtype=int),
        bounds=Bounds(np.zeros(bridge_count), np.ones(bridge_count)),
        constraints=LinearConstraint(
            scaled_costs.reshape(1, -1),
            -np.inf,
            scaled_budget,
        ),
        options={"mip_rel_gap": 0.0},
    )
    if not result.success or result.x is None:
        raise DataValidationError(
            f"Budget optimizer did not find an optimal portfolio: {result.message}"
        )
    return result.x > 0.5, "optimal:milp"


def allocate_budget(
    frame: pd.DataFrame,
    *,
    budget: float,
    county_fips: str | int | None = None,
    penndot_district: int | None = None,
    high_risk_threshold: float = 0.70,
) -> tuple[pd.DataFrame, AllocationSummary]:
    """Return candidate bridges marked Selected or Unfunded plus a summary."""

    _require_columns(frame)
    try:
        budget_value = float(budget)
        risk_threshold = float(high_risk_threshold)
    except (TypeError, ValueError) as exc:
        raise DataValidationError("Budget and high-risk threshold must be numeric") from exc
    if not math.isfinite(budget_value) or budget_value < 0:
        raise DataValidationError("Budget must be finite and nonnegative")
    if not math.isfinite(risk_threshold) or not 0 <= risk_threshold <= 1:
        raise DataValidationError("High-risk threshold must be between 0 and 1")

    bridge_ids = frame["bridge_id"].astype("string").str.strip()
    if bridge_ids.eq("").any() or bridge_ids.duplicated().any():
        raise DataValidationError("bridge_id must be nonempty and unique")

    cost_units = frame["cost_unit"].astype("string").str.strip().str.upper()
    if cost_units.eq("").any() or cost_units.nunique() != 1:
        raise DataValidationError("cost_unit must be nonempty and consistent")

    priority_ranks = _numeric(frame, "priority_rank")
    if (
        priority_ranks.ne(priority_ranks.round()).any()
        or priority_ranks.le(0).any()
        or priority_ranks.duplicated().any()
    ):
        raise DataValidationError("priority_rank must contain unique positive integers")

    bridge_conditions = (
        frame["bridge_condition"].astype("string").str.strip().str.upper()
    )
    if not bridge_conditions.isin({"G", "F", "P"}).all():
        raise DataValidationError("bridge_condition must contain only G, F, or P")

    validated = frame.copy()
    validated["bridge_id"] = bridge_ids
    validated["cost_unit"] = cost_units
    validated["priority_rank"] = priority_ranks.astype("int64")
    validated["bridge_condition"] = bridge_conditions

    candidates, region = _filter_region(
        validated,
        county_fips=county_fips,
        penndot_district=penndot_district,
    )

    priority_scores = _numeric(candidates, "priority_score")
    if not priority_scores.between(0, 100).all():
        raise DataValidationError("priority_score must be between 0 and 100")

    costs = _numeric(candidates, "predicted_cost")
    if not costs.gt(0).all():
        raise DataValidationError("predicted_cost must be greater than zero")

    probabilities = _numeric(candidates, "deterioration_probability")
    if not probabilities.between(0, 1).all():
        raise DataValidationError(
            "deterioration_probability must be between 0 and 1"
        )

    adt = _numeric(candidates, "adt")
    if (adt < 0).any():
        raise DataValidationError("adt cannot be negative")
    detour = _numeric(candidates, "detour_km", allow_missing=True)

    selected_mask, solver_status = _solve_selection(
        priority_scores.to_numpy(), costs.to_numpy(), budget_value
    )

    allocation = candidates.copy()
    allocation["selected_for_repair"] = selected_mask
    allocation["funding_status"] = np.where(
        selected_mask, "Selected", "Unfunded"
    )
    allocation["funded_rank"] = pd.Series(pd.NA, index=allocation.index, dtype="Int64")

    selected_indices = allocation.index[selected_mask]
    selected_by_priority = allocation.loc[selected_indices].sort_values(
        ["priority_rank", "bridge_id"], kind="mergesort"
    )
    allocation.loc[selected_by_priority.index, "funded_rank"] = range(
        1, len(selected_by_priority) + 1
    )

    selected = allocation.loc[selected_mask]
    unfunded = allocation.loc[~selected_mask]
    total_cost = float(costs[selected_mask].sum())
    tolerance = max(1e-6, abs(budget_value) * 1e-9)
    if total_cost > budget_value + tolerance:
        raise DataValidationError("Optimizer returned a portfolio over budget")

    selected_detour = detour[selected_mask]
    selected_score_total = float(priority_scores[selected_mask].sum())
    summary = AllocationSummary(
        region=region,
        budget=round(budget_value, 2),
        cost_unit=str(cost_units.iloc[0]),
        eligible_bridge_count=len(allocation),
        selected_bridge_count=len(selected),
        unfunded_bridge_count=len(unfunded),
        total_predicted_cost=round(total_cost, 2),
        remaining_budget=round(max(0.0, budget_value - total_cost), 2),
        budget_utilization_percent=round(
            100 * total_cost / budget_value if budget_value > 0 else 0.0, 4
        ),
        total_selected_priority_score=round(selected_score_total, 4),
        mean_selected_priority_score=round(
            selected_score_total / len(selected) if len(selected) else 0.0, 4
        ),
        high_risk_threshold=risk_threshold,
        selected_high_risk_count=int(
            probabilities[selected_mask].ge(risk_threshold).sum()
        ),
        unfunded_high_risk_count=int(
            probabilities[~selected_mask].ge(risk_threshold).sum()
        ),
        selected_poor_condition_count=int(
            selected["bridge_condition"]
            .astype("string")
            .str.upper()
            .eq("P")
            .sum()
        ),
        selected_total_adt=round(float(adt[selected_mask].sum()), 2),
        selected_total_detour_km=round(float(selected_detour.sum()), 2),
        selected_missing_detour_count=int(selected_detour.isna().sum()),
        solver_status=solver_status,
    )

    allocation = allocation.sort_values(
        ["selected_for_repair", "priority_rank", "bridge_id"],
        ascending=[False, True, True],
        kind="mergesort",
    ).reset_index(drop=True)
    return allocation, summary


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Allocate a budget across scored Ben2Bridges candidates."
    )
    parser.add_argument("--input", required=True, help="Scored bridge CSV")
    parser.add_argument("--budget", required=True, type=float, help="Available budget")
    region = parser.add_mutually_exclusive_group()
    region.add_argument("--county-fips", help="Three-digit Pennsylvania county FIPS")
    region.add_argument("--district", type=int, help="PennDOT district")
    parser.add_argument(
        "--high-risk-threshold",
        type=float,
        default=0.70,
        help="Reporting threshold; does not change optimization",
    )
    parser.add_argument("--output", required=True, help="Allocation output CSV")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        frame = pd.read_csv(
            args.input,
            dtype={"bridge_id": str, "county_fips": str},
            low_memory=False,
        )
        allocation, summary = allocate_budget(
            frame,
            budget=args.budget,
            county_fips=args.county_fips,
            penndot_district=args.district,
            high_risk_threshold=args.high_risk_threshold,
        )
    except (DataValidationError, OSError, pd.errors.ParserError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, indent=2))
        return 2

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    allocation.to_csv(output_path, index=False)
    print(
        json.dumps(
            {
                "status": "ok",
                "output": str(output_path),
                "summary": summary.to_dict(),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
