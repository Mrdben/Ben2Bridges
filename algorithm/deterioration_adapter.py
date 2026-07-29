"""Validate and align deterioration-model rankings to the current NBI universe.

This adapter does not invent scores for unmodeled bridges. It separates source
rows into usable predictions, model-only records, and current NBI bridges with
no model result so downstream decisions remain transparent and auditable.
"""

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


RANKING_COLUMNS = {
    "STRUCTURE_NUMBER_008",
    "MODEL_DETERIORATION_RISK_SCORE",
    "RISK_RANK",
    "RISK_PERCENTILE",
    "RISK_GROUP",
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
    "YEAR_BUILT_027",
    "ADT_029",
}


@dataclass(frozen=True)
class DeteriorationCoverageReport:
    nbi_bridge_count: int
    source_prediction_count: int
    usable_prediction_count: int
    unknown_model_record_count: int
    unmodeled_nbi_bridge_count: int
    prediction_coverage_percent: float
    unmodeled_culvert_count: int
    minimum_risk_score: float
    mean_risk_score: float
    maximum_risk_score: float
    score_semantics: str
    missing_score_imputation: str
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


def _prepare_rankings(frame: pd.DataFrame) -> pd.DataFrame:
    _require_columns(frame, RANKING_COLUMNS, "Deterioration rankings")
    if frame.empty:
        raise DataValidationError("Deterioration rankings contain no rows")

    bridge_ids = _unique_ids(
        frame, "STRUCTURE_NUMBER_008", "Deterioration rankings"
    )
    scores = _numeric(
        frame["MODEL_DETERIORATION_RISK_SCORE"],
        "MODEL_DETERIORATION_RISK_SCORE",
        minimum=0,
        maximum=1,
    )
    ranks = _numeric(
        frame["RISK_RANK"], "RISK_RANK", minimum=1, whole=True
    ).astype("int64")
    if ranks.duplicated().any():
        raise DataValidationError("RISK_RANK must be unique")
    percentiles = _numeric(
        frame["RISK_PERCENTILE"],
        "RISK_PERCENTILE",
        minimum=0,
        maximum=100,
    )
    risk_groups = _clean_text(frame["RISK_GROUP"])
    if risk_groups.eq("").any():
        raise DataValidationError("RISK_GROUP contains empty values")

    prepared = frame.copy()
    prepared["STRUCTURE_NUMBER_008"] = bridge_ids
    prepared["MODEL_DETERIORATION_RISK_SCORE"] = scores
    prepared["RISK_RANK"] = ranks
    prepared["RISK_PERCENTILE"] = percentiles
    prepared["RISK_GROUP"] = risk_groups

    sorted_scores = prepared.sort_values(
        ["RISK_RANK", "STRUCTURE_NUMBER_008"], kind="mergesort"
    )["MODEL_DETERIORATION_RISK_SCORE"]
    if not sorted_scores.is_monotonic_decreasing:
        raise DataValidationError(
            "Risk scores must be nonincreasing when ordered by RISK_RANK"
        )
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


def adapt_deterioration_output(
    nbi_path: str | Path,
    rankings_path: str | Path,
) -> tuple[
    pd.DataFrame,
    pd.DataFrame,
    pd.DataFrame,
    DeteriorationCoverageReport,
]:
    """Return usable, unknown, and unmodeled records plus a coverage report."""

    nbi = _prepare_nbi(_read_csv(nbi_path, "NBI data"))
    rankings = _prepare_rankings(
        _read_csv(rankings_path, "Deterioration rankings")
    )

    nbi_ids = set(nbi["STRUCTURE_NUMBER_008"])
    ranking_ids = set(rankings["STRUCTURE_NUMBER_008"])
    usable_ids = nbi_ids.intersection(ranking_ids)
    unknown_ids = ranking_ids.difference(nbi_ids)
    unmodeled_ids = nbi_ids.difference(ranking_ids)
    if not usable_ids:
        raise DataValidationError(
            "No deterioration model bridge IDs match the current NBI data"
        )

    usable_source = rankings.loc[
        rankings["STRUCTURE_NUMBER_008"].isin(usable_ids)
    ].copy()
    usable = pd.DataFrame(
        {
            "bridge_id": usable_source["STRUCTURE_NUMBER_008"],
            "deterioration_risk_score": usable_source[
                "MODEL_DETERIORATION_RISK_SCORE"
            ],
            "source_risk_rank": usable_source["RISK_RANK"],
            "risk_percentile": usable_source["RISK_PERCENTILE"],
            "risk_group": usable_source["RISK_GROUP"],
        }
    ).sort_values(["source_risk_rank", "bridge_id"], kind="mergesort")
    usable = usable.reset_index(drop=True)

    unknown = rankings.loc[
        rankings["STRUCTURE_NUMBER_008"].isin(unknown_ids)
    ].copy()
    unknown.insert(
        len(unknown.columns),
        "exclusion_reason",
        "Bridge ID not found in the current NBI input.",
    )
    unknown = unknown.sort_values(
        ["RISK_RANK", "STRUCTURE_NUMBER_008"], kind="mergesort"
    ).reset_index(drop=True)

    unmodeled_source = nbi.loc[
        nbi["STRUCTURE_NUMBER_008"].isin(unmodeled_ids)
    ].copy()
    county_codes = _numeric(
        unmodeled_source["COUNTY_CODE_003"],
        "COUNTY_CODE_003",
        minimum=1,
        maximum=999,
        whole=True,
    ).astype("int64")
    unmodeled = pd.DataFrame(
        {
            "bridge_id": unmodeled_source["STRUCTURE_NUMBER_008"],
            "county_fips": county_codes.map(lambda value: f"{value:03d}"),
            "facility_carried": _clean_text(
                unmodeled_source["FACILITY_CARRIED_007"]
            ),
            "feature_crossed": _clean_text(
                unmodeled_source["FEATURES_DESC_006A"]
            ),
            "location": _clean_text(unmodeled_source["LOCATION_009"]),
            "lowest_rating": _clean_text(unmodeled_source["LOWEST_RATING"]),
            "bridge_condition": _clean_text(
                unmodeled_source["BRIDGE_CONDITION"]
            ),
            "culvert_condition": _clean_text(
                unmodeled_source["CULVERT_COND_062"]
            ),
            "year_built": _clean_text(unmodeled_source["YEAR_BUILT_027"]),
            "adt": _clean_text(unmodeled_source["ADT_029"]),
            "unmodeled_reason": (
                "No deterioration model output matched this current NBI bridge ID."
            ),
        }
    ).reset_index(drop=True)

    culvert_values = _clean_text(unmodeled["culvert_condition"]).str.upper()
    unmodeled_culverts = int(
        (culvert_values.ne("N") & culvert_values.ne("")).sum()
    )
    coverage = 100 * len(usable) / len(nbi)
    warnings: list[str] = []
    if len(unknown):
        warnings.append(
            f"{len(unknown):,} model record(s) were excluded because their bridge "
            "IDs were not found in the current NBI data."
        )
    if len(unmodeled):
        warnings.append(
            f"{len(unmodeled):,} current NBI bridge(s) have no deterioration model "
            "result and remain ineligible for automatic scoring; no risk score was "
            "imputed."
        )
    if len(unmodeled) and unmodeled_culverts == len(unmodeled):
        warnings.append(
            "All unmodeled current NBI records are culverts; results therefore do "
            "not represent the complete culvert population."
        )
    warnings.append(
        "MODEL_DETERIORATION_RISK_SCORE is treated as a normalized model risk "
        "score, not as a calibrated probability unless the model team confirms it."
    )

    report = DeteriorationCoverageReport(
        nbi_bridge_count=len(nbi),
        source_prediction_count=len(rankings),
        usable_prediction_count=len(usable),
        unknown_model_record_count=len(unknown),
        unmodeled_nbi_bridge_count=len(unmodeled),
        prediction_coverage_percent=round(coverage, 4),
        unmodeled_culvert_count=unmodeled_culverts,
        minimum_risk_score=round(
            float(usable["deterioration_risk_score"].min()), 6
        ),
        mean_risk_score=round(
            float(usable["deterioration_risk_score"].mean()), 6
        ),
        maximum_risk_score=round(
            float(usable["deterioration_risk_score"].max()), 6
        ),
        score_semantics=(
            "Normalized deterioration model risk score; probability calibration "
            "has not been confirmed."
        ),
        missing_score_imputation="none",
        warnings=tuple(warnings),
    )
    return usable, unknown, unmodeled, report


def write_adapter_outputs(
    output_dir: str | Path,
    usable: pd.DataFrame,
    unknown: pd.DataFrame,
    unmodeled: pd.DataFrame,
    report: DeteriorationCoverageReport,
) -> dict[str, str]:
    """Write auditable adapter outputs and return their paths."""

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "usable_predictions": output_dir / "usable_deterioration_predictions.csv",
        "unknown_model_records": output_dir / "unknown_model_records.csv",
        "unmodeled_nbi_bridges": output_dir / "unmodeled_nbi_bridges.csv",
        "coverage_report": output_dir / "deterioration_coverage_report.json",
    }
    usable.to_csv(paths["usable_predictions"], index=False)
    unknown.to_csv(paths["unknown_model_records"], index=False)
    unmodeled.to_csv(paths["unmodeled_nbi_bridges"], index=False)
    Path(paths["coverage_report"]).write_text(
        json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return {key: str(value) for key, value in paths.items()}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Align deterioration rankings to the current NBI bridge set."
    )
    parser.add_argument("--nbi", required=True, help="Current NBI CSV")
    parser.add_argument(
        "--rankings", required=True, help="Full deterioration model rankings CSV"
    )
    parser.add_argument("--output-dir", required=True, help="Adapter output directory")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        usable, unknown, unmodeled, report = adapt_deterioration_output(
            args.nbi, args.rankings
        )
        outputs = write_adapter_outputs(
            args.output_dir, usable, unknown, unmodeled, report
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
