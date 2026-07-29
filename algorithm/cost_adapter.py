"""Derive one conservative bridge-level cost from component cost scenarios."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd

try:
    from .data_pipeline import DataValidationError
except ImportError:  # Support direct execution.
    from data_pipeline import DataValidationError


COST_METHOD = "max_conditional_component_scenario"
RISK_SEMANTICS = "normalized_model_risk_score_unconfirmed_probability"

RISK_COLUMNS = {
    "bridge_id",
    "deterioration_risk_score",
    "source_risk_rank",
    "risk_percentile",
    "risk_group",
}

CATALOG_COLUMNS = {
    "STRUCTURE_NUMBER_008",
    "COMPONENT",
    "CONDITIONAL_WHOLE_PROJECT_COST_APPROXIMATION",
    "LOWER_80_MODEL_INTERVAL",
    "UPPER_80_MODEL_INTERVAL",
    "PREDICTED_HIGH_COST_PROBABILITY",
    "COST_MEANING",
}

NBI_COLUMNS = {
    "STRUCTURE_NUMBER_008",
    "COUNTY_CODE_003",
    "FACILITY_CARRIED_007",
    "FEATURES_DESC_006A",
    "LOCATION_009",
    "LOWEST_RATING",
    "BRIDGE_CONDITION",
    "CULVERT_COND_062",
}

ALLOWED_COMPONENTS = {"DECK", "SUPERSTRUCTURE", "SUBSTRUCTURE"}


@dataclass(frozen=True)
class CostCoverageReport:
    nbi_bridge_count: int
    usable_deterioration_count: int
    source_catalog_row_count: int
    source_catalog_bridge_count: int
    usable_combined_prediction_count: int
    unknown_catalog_bridge_count: int
    risk_without_cost_count: int
    final_ineligible_nbi_count: int
    final_coverage_percent: float
    minimum_derived_cost: float
    median_derived_cost: float
    mean_derived_cost: float
    maximum_derived_cost: float
    cost_unit: str
    cost_reference_year: int
    cost_method: str
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _read_csv(path: str | Path, label: str) -> pd.DataFrame:
    path = Path(path)
    if not path.is_file():
        raise DataValidationError(f"{label} file does not exist: {path}")
    try:
        return pd.read_csv(
            path,
            dtype=str,
            keep_default_na=False,
            low_memory=False,
        )
    except Exception as exc:
        raise DataValidationError(f"Could not read {label} CSV {path}: {exc}") from exc


def _require_columns(
    frame: pd.DataFrame, required: Iterable[str], label: str
) -> None:
    missing = sorted(set(required).difference(frame.columns))
    if missing:
        raise DataValidationError(
            f"{label} is missing required columns: {', '.join(missing)}"
        )


def _clean_value(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    if len(text) >= 2 and text.startswith("'") and text.endswith("'"):
        text = text[1:-1].strip()
    return text


def _clean_text(series: pd.Series) -> pd.Series:
    return series.map(_clean_value).astype("string")


def _unique_ids(frame: pd.DataFrame, column: str, label: str) -> pd.Series:
    ids = _clean_text(frame[column])
    if ids.eq("").any():
        raise DataValidationError(f"{label} contains empty bridge IDs")
    duplicated = ids.duplicated(keep=False)
    if duplicated.any():
        examples = ", ".join(sorted(ids[duplicated].unique())[:5])
        raise DataValidationError(
            f"{label} contains duplicate bridge IDs: {examples}"
        )
    return ids


def _numeric(
    series: pd.Series,
    column: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    whole: bool = False,
) -> pd.Series:
    cleaned = _clean_text(series)
    parsed = pd.to_numeric(cleaned.mask(cleaned.eq("")), errors="coerce")
    if parsed.isna().any():
        raise DataValidationError(f"{column} contains missing or non-numeric values")
    if not parsed.map(math.isfinite).all():
        raise DataValidationError(f"{column} contains non-finite values")
    if minimum is not None and parsed.lt(minimum).any():
        raise DataValidationError(f"{column} must be at least {minimum}")
    if maximum is not None and parsed.gt(maximum).any():
        raise DataValidationError(f"{column} must be at most {maximum}")
    if whole and parsed.ne(parsed.round()).any():
        raise DataValidationError(f"{column} must contain whole numbers")
    return parsed.astype("float64")


def _prepare_risk(frame: pd.DataFrame) -> pd.DataFrame:
    _require_columns(frame, RISK_COLUMNS, "Usable deterioration predictions")
    if frame.empty:
        raise DataValidationError("Usable deterioration predictions contain no rows")
    prepared = frame.copy()
    prepared["bridge_id"] = _unique_ids(
        frame, "bridge_id", "Usable deterioration predictions"
    )
    prepared["deterioration_risk_score"] = _numeric(
        frame["deterioration_risk_score"],
        "deterioration_risk_score",
        minimum=0,
        maximum=1,
    )
    prepared["source_risk_rank"] = _numeric(
        frame["source_risk_rank"], "source_risk_rank", minimum=1, whole=True
    ).astype("int64")
    prepared["risk_percentile"] = _numeric(
        frame["risk_percentile"], "risk_percentile", minimum=0, maximum=100
    )
    prepared["risk_group"] = _clean_text(frame["risk_group"])
    return prepared


def _prepare_catalog(frame: pd.DataFrame) -> pd.DataFrame:
    _require_columns(frame, CATALOG_COLUMNS, "Cost catalog")
    if frame.empty:
        raise DataValidationError("Cost catalog contains no rows")
    prepared = frame.copy()
    prepared["STRUCTURE_NUMBER_008"] = _clean_text(
        frame["STRUCTURE_NUMBER_008"]
    )
    if prepared["STRUCTURE_NUMBER_008"].eq("").any():
        raise DataValidationError("Cost catalog contains empty bridge IDs")
    prepared["COMPONENT"] = _clean_text(frame["COMPONENT"]).str.upper()
    invalid_components = sorted(
        set(prepared["COMPONENT"]).difference(ALLOWED_COMPONENTS)
    )
    if invalid_components:
        raise DataValidationError(
            f"Cost catalog contains invalid components: {invalid_components}"
        )
    duplicated = prepared.duplicated(
        ["STRUCTURE_NUMBER_008", "COMPONENT"], keep=False
    )
    if duplicated.any():
        raise DataValidationError(
            "Cost catalog contains duplicate bridge-component scenarios"
        )

    prepared["derived_cost"] = _numeric(
        frame["CONDITIONAL_WHOLE_PROJECT_COST_APPROXIMATION"],
        "CONDITIONAL_WHOLE_PROJECT_COST_APPROXIMATION",
        minimum=0,
    )
    if prepared["derived_cost"].eq(0).any():
        raise DataValidationError("Cost catalog scenario costs must be greater than zero")
    prepared["cost_lower_80"] = _numeric(
        frame["LOWER_80_MODEL_INTERVAL"], "LOWER_80_MODEL_INTERVAL", minimum=0
    )
    prepared["cost_upper_80"] = _numeric(
        frame["UPPER_80_MODEL_INTERVAL"], "UPPER_80_MODEL_INTERVAL", minimum=0
    )
    prepared["cost_high_probability"] = _numeric(
        frame["PREDICTED_HIGH_COST_PROBABILITY"],
        "PREDICTED_HIGH_COST_PROBABILITY",
        minimum=0,
        maximum=1,
    )
    invalid_interval = prepared["cost_lower_80"].gt(prepared["derived_cost"]) | prepared[
        "derived_cost"
    ].gt(prepared["cost_upper_80"])
    if invalid_interval.any():
        raise DataValidationError(
            "Each cost point estimate must fall within its 80% model interval"
        )
    prepared["COST_MEANING"] = _clean_text(frame["COST_MEANING"])
    if prepared["COST_MEANING"].eq("").any():
        raise DataValidationError("COST_MEANING contains empty values")
    return prepared


def _prepare_nbi(frame: pd.DataFrame) -> pd.DataFrame:
    _require_columns(frame, NBI_COLUMNS, "NBI data")
    if frame.empty:
        raise DataValidationError("NBI data contain no rows")
    prepared = frame.copy()
    prepared["STRUCTURE_NUMBER_008"] = _unique_ids(
        frame, "STRUCTURE_NUMBER_008", "NBI data"
    )
    return prepared


def build_combined_predictions(
    nbi_path: str | Path,
    risk_path: str | Path,
    catalog_path: str | Path,
    *,
    cost_reference_year: int,
    prediction_horizon: str = "next_inspection",
    model_version: str = "risk-ranking__cost-catalog-max-v1",
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, CostCoverageReport]:
    """Build one auditable risk-and-cost row per fully modeled current bridge."""

    try:
        year = int(cost_reference_year)
    except (TypeError, ValueError) as exc:
        raise DataValidationError("cost_reference_year must be a four-digit year") from exc
    if not 1900 <= year <= 2100:
        raise DataValidationError("cost_reference_year must be between 1900 and 2100")
    horizon = str(prediction_horizon).strip()
    version = str(model_version).strip()
    if not horizon or not version:
        raise DataValidationError("prediction_horizon and model_version must be nonempty")

    nbi = _prepare_nbi(_read_csv(nbi_path, "NBI data"))
    risk = _prepare_risk(_read_csv(risk_path, "Usable deterioration predictions"))
    catalog = _prepare_catalog(_read_csv(catalog_path, "Cost catalog"))

    nbi_ids = set(nbi["STRUCTURE_NUMBER_008"])
    risk_ids = set(risk["bridge_id"])
    catalog_ids = set(catalog["STRUCTURE_NUMBER_008"])

    # Sort first so the first retained row is the maximum-cost component. The
    # component name is a deterministic tie-breaker.
    selected_costs = (
        catalog.sort_values(
            ["STRUCTURE_NUMBER_008", "derived_cost", "COMPONENT"],
            ascending=[True, False, True],
            kind="mergesort",
        )
        .drop_duplicates("STRUCTURE_NUMBER_008", keep="first")
        .reset_index(drop=True)
    )
    selected_cost_ids = set(selected_costs["STRUCTURE_NUMBER_008"])
    usable_ids = nbi_ids & risk_ids & selected_cost_ids
    if not usable_ids:
        raise DataValidationError(
            "No bridge IDs are shared by NBI, deterioration risk, and cost catalog"
        )

    combined = risk.loc[risk["bridge_id"].isin(usable_ids)].merge(
        selected_costs,
        left_on="bridge_id",
        right_on="STRUCTURE_NUMBER_008",
        how="inner",
        validate="one_to_one",
    )
    combined_output = pd.DataFrame(
        {
            "bridge_id": combined["bridge_id"],
            "deterioration_risk_score": combined["deterioration_risk_score"],
            "predicted_cost": combined["derived_cost"].round(2),
            "cost_unit": "USD",
            "prediction_horizon": horizon,
            "model_version": version,
            "risk_score_semantics": RISK_SEMANTICS,
            "cost_reference_year": year,
            "cost_method": COST_METHOD,
            "cost_source_component": combined["COMPONENT"],
            "cost_lower_80": combined["cost_lower_80"].round(2),
            "cost_upper_80": combined["cost_upper_80"].round(2),
            "cost_high_probability": combined["cost_high_probability"].round(6),
            "cost_is_derived": True,
            "source_risk_rank": combined["source_risk_rank"],
            "risk_percentile": combined["risk_percentile"],
            "risk_group": combined["risk_group"],
        }
    ).sort_values(["source_risk_rank", "bridge_id"], kind="mergesort")
    combined_output = combined_output.reset_index(drop=True)

    unknown_catalog = selected_costs.loc[
        selected_costs["STRUCTURE_NUMBER_008"].isin(catalog_ids - nbi_ids)
    ].copy()
    unknown_catalog["exclusion_reason"] = (
        "Bridge ID not found in the current NBI input."
    )
    unknown_catalog = unknown_catalog.sort_values(
        ["STRUCTURE_NUMBER_008"], kind="mergesort"
    ).reset_index(drop=True)

    risk_without_cost_ids = (nbi_ids & risk_ids) - selected_cost_ids
    risk_without_cost = risk.loc[risk["bridge_id"].isin(risk_without_cost_ids)].merge(
        nbi,
        left_on="bridge_id",
        right_on="STRUCTURE_NUMBER_008",
        how="left",
        validate="one_to_one",
    )
    risk_without_cost["exclusion_reason"] = (
        "No applicable component cost scenario was found for this bridge."
    )
    risk_without_cost = risk_without_cost.sort_values(
        ["source_risk_rank", "bridge_id"], kind="mergesort"
    ).reset_index(drop=True)

    costs = combined_output["predicted_cost"]
    warnings = [
        "Bridge-level predicted_cost is conservatively derived as the maximum "
        "conditional whole-project cost among available component scenarios; "
        "component scenario costs are not summed."
    ]
    if len(unknown_catalog):
        warnings.append(
            f"{len(unknown_catalog):,} cost catalog bridge(s) were excluded because "
            "their IDs were not found in the current NBI data."
        )
    if len(risk_without_cost):
        warnings.append(
            f"{len(risk_without_cost):,} current bridge(s) have deterioration risk "
            "but no applicable component cost and remain ineligible for automatic "
            "budget allocation."
        )

    report = CostCoverageReport(
        nbi_bridge_count=len(nbi),
        usable_deterioration_count=len(risk),
        source_catalog_row_count=len(catalog),
        source_catalog_bridge_count=len(selected_costs),
        usable_combined_prediction_count=len(combined_output),
        unknown_catalog_bridge_count=len(unknown_catalog),
        risk_without_cost_count=len(risk_without_cost),
        final_ineligible_nbi_count=len(nbi) - len(combined_output),
        final_coverage_percent=round(100 * len(combined_output) / len(nbi), 4),
        minimum_derived_cost=round(float(costs.min()), 2),
        median_derived_cost=round(float(costs.median()), 2),
        mean_derived_cost=round(float(costs.mean()), 2),
        maximum_derived_cost=round(float(costs.max()), 2),
        cost_unit="USD",
        cost_reference_year=year,
        cost_method=COST_METHOD,
        warnings=tuple(warnings),
    )
    return combined_output, unknown_catalog, risk_without_cost, report


def write_cost_outputs(
    output_dir: str | Path,
    combined: pd.DataFrame,
    unknown_catalog: pd.DataFrame,
    risk_without_cost: pd.DataFrame,
    report: CostCoverageReport,
) -> dict[str, str]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "combined_predictions": output_dir / "combined_model_predictions.csv",
        "unknown_catalog_bridges": output_dir / "unknown_cost_catalog_bridges.csv",
        "risk_without_cost": output_dir / "risk_without_cost.csv",
        "coverage_report": output_dir / "cost_coverage_report.json",
    }
    combined.to_csv(paths["combined_predictions"], index=False)
    unknown_catalog.to_csv(paths["unknown_catalog_bridges"], index=False)
    risk_without_cost.to_csv(paths["risk_without_cost"], index=False)
    Path(paths["coverage_report"]).write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return {key: str(value) for key, value in paths.items()}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create one conservative project cost per modeled bridge."
    )
    parser.add_argument("--nbi", required=True)
    parser.add_argument("--risk", required=True, help="Usable deterioration CSV")
    parser.add_argument("--catalog", required=True, help="Part-wise cost catalog CSV")
    parser.add_argument("--cost-reference-year", required=True, type=int)
    parser.add_argument("--prediction-horizon", default="next_inspection")
    parser.add_argument(
        "--model-version", default="risk-ranking__cost-catalog-max-v1"
    )
    parser.add_argument("--output-dir", required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        combined, unknown_catalog, risk_without_cost, report = (
            build_combined_predictions(
                args.nbi,
                args.risk,
                args.catalog,
                cost_reference_year=args.cost_reference_year,
                prediction_horizon=args.prediction_horizon,
                model_version=args.model_version,
            )
        )
        outputs = write_cost_outputs(
            args.output_dir,
            combined,
            unknown_catalog,
            risk_without_cost,
            report,
        )
    except (DataValidationError, OSError, pd.errors.ParserError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, indent=2))
        return 2

    print(
        json.dumps(
            {"status": "ok", "outputs": outputs, "report": report.to_dict()},
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
