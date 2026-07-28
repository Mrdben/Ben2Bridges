"""Validate and join NBI bridge data with model predictions.

This module intentionally stops before scoring. Its output is the clean,
bridge-level table that later scoring and budget-allocation code will consume.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd


class DataValidationError(ValueError):
    """Raised when an input file violates the project data contract."""


NBI_COLUMNS = {
    "STRUCTURE_NUMBER_008",
    "HIGHWAY_DISTRICT_002",
    "COUNTY_CODE_003",
    "FACILITY_CARRIED_007",
    "FEATURES_DESC_006A",
    "LOCATION_009",
    "LAT_016",
    "LONG_017",
    "DETOUR_KILOS_019",
    "YEAR_BUILT_027",
    "ADT_029",
    "YEAR_ADT_030",
    "DECK_COND_058",
    "SUPERSTRUCTURE_COND_059",
    "SUBSTRUCTURE_COND_060",
    "CULVERT_COND_062",
    "BRIDGE_CONDITION",
    "LOWEST_RATING",
}

PREDICTION_COLUMNS = {
    "bridge_id",
    "deterioration_probability",
    "predicted_cost",
    "cost_unit",
    "prediction_horizon_years",
    "model_version",
}

COUNTY_COLUMNS = {"county_fips", "county_name", "penndot_district"}
PENNDOT_DISTRICTS = {1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12}


@dataclass(frozen=True)
class ValidationReport:
    """Summary of the validated algorithm input."""

    nbi_bridge_count: int
    prediction_count: int
    eligible_bridge_count: int
    missing_prediction_count: int
    prediction_coverage_percent: float
    invalid_detour_count: int
    invalid_adt_year_count: int
    cost_unit: str
    prediction_horizon_years: int
    model_versions: tuple[str, ...]
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
    except Exception as exc:  # pandas exposes several parser exception types.
        raise DataValidationError(f"Could not read {label} CSV {path}: {exc}") from exc


def _require_columns(
    frame: pd.DataFrame, required: Iterable[str], label: str
) -> None:
    missing = sorted(set(required).difference(frame.columns))
    if missing:
        raise DataValidationError(
            f"{label} is missing required columns: {', '.join(missing)}"
        )


def _clean_text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""

    text = str(value).strip()
    if len(text) >= 2 and text.startswith("'") and text.endswith("'"):
        text = text[1:-1].strip()
    return text


def _clean_text_series(series: pd.Series) -> pd.Series:
    return series.map(_clean_text).astype("string")


def _nonempty_text(series: pd.Series, column: str) -> pd.Series:
    cleaned = _clean_text_series(series)
    empty = cleaned.eq("")
    if empty.any():
        rows = [str(index + 2) for index in cleaned.index[empty][:5]]
        raise DataValidationError(
            f"{column} contains empty values on CSV row(s): {', '.join(rows)}"
        )
    return cleaned


def _numeric(
    series: pd.Series,
    column: str,
    *,
    allow_blank: bool = False,
) -> pd.Series:
    cleaned = _clean_text_series(series)
    blank = cleaned.eq("")
    parsed = pd.to_numeric(cleaned.mask(blank), errors="coerce")
    invalid = parsed.isna() & ~blank
    if invalid.any():
        examples = ", ".join(repr(value) for value in cleaned[invalid].head(5))
        raise DataValidationError(
            f"{column} contains non-numeric value(s): {examples}"
        )
    if not allow_blank and parsed.isna().any():
        raise DataValidationError(f"{column} contains missing numeric values")
    return parsed


def _finite(series: pd.Series, column: str) -> None:
    invalid = series.notna() & ~series.map(math.isfinite)
    if invalid.any():
        raise DataValidationError(f"{column} contains non-finite values")


def _whole_numbers(series: pd.Series, column: str) -> None:
    invalid = series.notna() & series.ne(series.round())
    if invalid.any():
        raise DataValidationError(f"{column} must contain whole numbers")


def _normalize_county_codes(series: pd.Series, column: str) -> pd.Series:
    numeric = _numeric(series, column)
    integers = numeric.astype("int64")
    if not numeric.eq(integers).all() or not integers.between(1, 999).all():
        raise DataValidationError(
            f"{column} must contain positive three-digit-compatible FIPS codes"
        )
    return integers.map(lambda value: f"{value:03d}").astype("string")


def _condition_values(series: pd.Series, column: str) -> pd.Series:
    cleaned = _clean_text_series(series).str.upper()
    applicable = cleaned.ne("N") & cleaned.ne("")
    parsed = pd.to_numeric(cleaned.where(applicable), errors="coerce")
    invalid = applicable & (
        parsed.isna() | ~parsed.between(0, 9) | parsed.ne(parsed.round())
    )
    if invalid.any():
        examples = ", ".join(repr(value) for value in cleaned[invalid].head(5))
        raise DataValidationError(
            f"{column} must contain a rating from 0 to 9 or N; found {examples}"
        )
    return parsed.astype("Int64")


def _dms_to_decimal(value: object, *, western_longitude: bool) -> float:
    text = _clean_text(value)
    if not text:
        return math.nan

    try:
        raw = float(text)
    except ValueError as exc:
        raise DataValidationError(f"Invalid NBI coordinate: {text!r}") from exc

    degrees = math.floor(raw / 1_000_000)
    minutes = math.floor((raw % 1_000_000) / 10_000)
    seconds = (raw % 10_000) / 100
    if not 0 <= minutes < 60 or not 0 <= seconds < 60:
        raise DataValidationError(f"Invalid NBI coordinate: {text!r}")

    decimal = degrees + minutes / 60 + seconds / 3_600
    if western_longitude:
        decimal = -decimal
    return round(decimal, 6)


def _prepare_counties(frame: pd.DataFrame) -> pd.DataFrame:
    _require_columns(frame, COUNTY_COLUMNS, "County mapping")
    counties = pd.DataFrame(
        {
            "county_fips": _normalize_county_codes(
                frame["county_fips"], "county_fips"
            ),
            "county_name": _nonempty_text(frame["county_name"], "county_name"),
            "penndot_district": _numeric(
                frame["penndot_district"], "penndot_district"
            ).astype("int64"),
        }
    )

    duplicated = counties["county_fips"].duplicated(keep=False)
    if duplicated.any():
        values = ", ".join(sorted(counties.loc[duplicated, "county_fips"].unique()))
        raise DataValidationError(f"County mapping has duplicate FIPS codes: {values}")

    invalid_districts = sorted(
        set(counties["penndot_district"]).difference(PENNDOT_DISTRICTS)
    )
    if invalid_districts:
        raise DataValidationError(
            f"County mapping has invalid PennDOT district(s): {invalid_districts}"
        )
    return counties


def _prepare_nbi(
    frame: pd.DataFrame, counties: pd.DataFrame
) -> tuple[pd.DataFrame, int, int]:
    _require_columns(frame, NBI_COLUMNS, "NBI data")

    bridge_id = _nonempty_text(frame["STRUCTURE_NUMBER_008"], "STRUCTURE_NUMBER_008")
    duplicated = bridge_id.duplicated(keep=False)
    if duplicated.any():
        values = ", ".join(sorted(bridge_id[duplicated].unique())[:5])
        raise DataValidationError(f"NBI data has duplicate bridge IDs: {values}")

    lowest_rating = _numeric(frame["LOWEST_RATING"], "LOWEST_RATING")
    _whole_numbers(lowest_rating, "LOWEST_RATING")
    if not lowest_rating.between(0, 9).all():
        raise DataValidationError("LOWEST_RATING must be between 0 and 9")

    bridge_condition = _nonempty_text(
        frame["BRIDGE_CONDITION"], "BRIDGE_CONDITION"
    ).str.upper()
    if not bridge_condition.isin({"G", "F", "P"}).all():
        raise DataValidationError("BRIDGE_CONDITION must contain only G, F, or P")

    expected_condition = pd.Series("F", index=frame.index, dtype="string")
    expected_condition.loc[lowest_rating.ge(7)] = "G"
    expected_condition.loc[lowest_rating.le(4)] = "P"
    if not bridge_condition.eq(expected_condition).all():
        raise DataValidationError(
            "BRIDGE_CONDITION is inconsistent with LOWEST_RATING"
        )

    adt = _numeric(frame["ADT_029"], "ADT_029")
    _whole_numbers(adt, "ADT_029")
    if (adt < 0).any():
        raise DataValidationError("ADT_029 cannot be negative")

    adt_year = _numeric(frame["YEAR_ADT_030"], "YEAR_ADT_030")
    _whole_numbers(adt_year, "YEAR_ADT_030")
    invalid_adt_year = ~adt_year.between(1900, 2025)
    invalid_adt_year_count = int(invalid_adt_year.sum())
    adt_year = adt_year.mask(invalid_adt_year).astype("Int64")

    year_built = _numeric(frame["YEAR_BUILT_027"], "YEAR_BUILT_027")
    _whole_numbers(year_built, "YEAR_BUILT_027")
    if not year_built.between(1600, 2025).all():
        raise DataValidationError("YEAR_BUILT_027 must be between 1600 and 2025")

    detour = _numeric(
        frame["DETOUR_KILOS_019"], "DETOUR_KILOS_019", allow_blank=True
    )
    _whole_numbers(detour, "DETOUR_KILOS_019")
    invalid_detour = detour.notna() & ~detour.between(0, 199)
    invalid_detour_count = int(invalid_detour.sum())
    detour = detour.mask(invalid_detour).astype("Int64")

    nbi = pd.DataFrame(
        {
            "bridge_id": bridge_id,
            "county_fips": _normalize_county_codes(
                frame["COUNTY_CODE_003"], "COUNTY_CODE_003"
            ),
            "nbi_highway_district": _clean_text_series(
                frame["HIGHWAY_DISTRICT_002"]
            ).str.zfill(2),
            "lowest_rating": lowest_rating.astype("int64"),
            "bridge_condition": bridge_condition,
            "deck_condition": _condition_values(
                frame["DECK_COND_058"], "DECK_COND_058"
            ),
            "superstructure_condition": _condition_values(
                frame["SUPERSTRUCTURE_COND_059"], "SUPERSTRUCTURE_COND_059"
            ),
            "substructure_condition": _condition_values(
                frame["SUBSTRUCTURE_COND_060"], "SUBSTRUCTURE_COND_060"
            ),
            "culvert_condition": _condition_values(
                frame["CULVERT_COND_062"], "CULVERT_COND_062"
            ),
            "adt": adt.astype("int64"),
            "adt_year": adt_year,
            "detour_km": detour,
            "year_built": year_built.astype("int64"),
            "latitude": frame["LAT_016"].map(
                lambda value: _dms_to_decimal(value, western_longitude=False)
            ),
            "longitude": frame["LONG_017"].map(
                lambda value: _dms_to_decimal(value, western_longitude=True)
            ),
            "facility_carried": _clean_text_series(frame["FACILITY_CARRIED_007"]),
            "feature_crossed": _clean_text_series(frame["FEATURES_DESC_006A"]),
            "location": _clean_text_series(frame["LOCATION_009"]),
        }
    )

    if not nbi["latitude"].between(39.5, 42.6).all() or not nbi[
        "longitude"
    ].between(-80.7, -74.5).all():
        raise DataValidationError("NBI coordinates fall outside Pennsylvania bounds")

    nbi = nbi.merge(counties, on="county_fips", how="left", validate="many_to_one")
    missing_county = nbi["county_name"].isna()
    if missing_county.any():
        values = ", ".join(sorted(nbi.loc[missing_county, "county_fips"].unique()))
        raise DataValidationError(
            f"County mapping is missing NBI county FIPS code(s): {values}"
        )
    return nbi, invalid_detour_count, invalid_adt_year_count


def _prepare_predictions(frame: pd.DataFrame) -> pd.DataFrame:
    _require_columns(frame, PREDICTION_COLUMNS, "Model predictions")
    if frame.empty:
        raise DataValidationError("Model predictions contain no rows")

    bridge_id = _nonempty_text(frame["bridge_id"], "bridge_id")
    duplicated = bridge_id.duplicated(keep=False)
    if duplicated.any():
        values = ", ".join(sorted(bridge_id[duplicated].unique())[:5])
        raise DataValidationError(
            f"Model predictions have duplicate bridge IDs after cleaning: {values}"
        )

    probability = _numeric(
        frame["deterioration_probability"], "deterioration_probability"
    )
    _finite(probability, "deterioration_probability")
    if not probability.between(0, 1).all():
        raise DataValidationError(
            "deterioration_probability must be between 0 and 1"
        )

    predicted_cost = _numeric(frame["predicted_cost"], "predicted_cost")
    _finite(predicted_cost, "predicted_cost")
    if not predicted_cost.gt(0).all():
        raise DataValidationError("predicted_cost must be greater than zero")

    cost_unit = _nonempty_text(frame["cost_unit"], "cost_unit").str.upper()
    if cost_unit.nunique() != 1:
        raise DataValidationError("cost_unit must be consistent across all predictions")

    horizon = _numeric(
        frame["prediction_horizon_years"], "prediction_horizon_years"
    )
    if not horizon.eq(horizon.astype("int64")).all() or not horizon.gt(0).all():
        raise DataValidationError(
            "prediction_horizon_years must contain positive integers"
        )
    if horizon.nunique() != 1:
        raise DataValidationError(
            "prediction_horizon_years must be consistent across all predictions"
        )

    return pd.DataFrame(
        {
            "bridge_id": bridge_id,
            "deterioration_probability": probability.astype("float64"),
            "predicted_cost": predicted_cost.astype("float64"),
            "cost_unit": cost_unit,
            "prediction_horizon_years": horizon.astype("int64"),
            "model_version": _nonempty_text(
                frame["model_version"], "model_version"
            ),
        }
    )


def load_algorithm_inputs(
    nbi_path: str | Path,
    predictions_path: str | Path,
    counties_path: str | Path,
) -> tuple[pd.DataFrame, ValidationReport]:
    """Return validated, joined bridge data and a coverage report."""

    counties = _prepare_counties(_read_csv(counties_path, "County mapping"))
    nbi, invalid_detour_count, invalid_adt_year_count = _prepare_nbi(
        _read_csv(nbi_path, "NBI data"), counties
    )
    predictions = _prepare_predictions(
        _read_csv(predictions_path, "Model predictions")
    )

    unknown_ids = sorted(set(predictions["bridge_id"]).difference(nbi["bridge_id"]))
    if unknown_ids:
        examples = ", ".join(unknown_ids[:5])
        raise DataValidationError(
            f"Model predictions contain bridge IDs not present in NBI data: {examples}"
        )

    algorithm_input = nbi.merge(
        predictions,
        on="bridge_id",
        how="inner",
        validate="one_to_one",
        sort=False,
    )

    nbi_count = len(nbi)
    prediction_count = len(predictions)
    eligible_count = len(algorithm_input)
    missing_count = nbi_count - eligible_count
    coverage = 100 * eligible_count / nbi_count if nbi_count else 0.0

    warnings: list[str] = []
    if missing_count:
        warnings.append(
            f"{missing_count:,} NBI bridge(s) have no complete model prediction and "
            "are ineligible for automatic selection."
        )
    if invalid_detour_count:
        warnings.append(
            f"{invalid_detour_count:,} NBI bridge(s) have detour values outside "
            "the official 0-199 range; detour_km was set to missing."
        )
    if invalid_adt_year_count:
        warnings.append(
            f"{invalid_adt_year_count:,} NBI bridge(s) have ADT years outside "
            "1900-2025; adt_year was set to missing."
        )

    report = ValidationReport(
        nbi_bridge_count=nbi_count,
        prediction_count=prediction_count,
        eligible_bridge_count=eligible_count,
        missing_prediction_count=missing_count,
        prediction_coverage_percent=round(coverage, 4),
        invalid_detour_count=invalid_detour_count,
        invalid_adt_year_count=invalid_adt_year_count,
        cost_unit=str(predictions["cost_unit"].iloc[0]),
        prediction_horizon_years=int(
            predictions["prediction_horizon_years"].iloc[0]
        ),
        model_versions=tuple(sorted(predictions["model_version"].unique())),
        warnings=tuple(warnings),
    )
    return algorithm_input, report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate and join Ben2Bridges algorithm input data."
    )
    parser.add_argument("--nbi", required=True, help="Path to the NBI CSV")
    parser.add_argument(
        "--predictions", required=True, help="Path to the combined model CSV"
    )
    parser.add_argument(
        "--counties", required=True, help="Path to the Pennsylvania county CSV"
    )
    parser.add_argument(
        "--output", help="Optional path for the validated joined CSV"
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        algorithm_input, report = load_algorithm_inputs(
            args.nbi, args.predictions, args.counties
        )
    except DataValidationError as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, indent=2))
        return 2

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        algorithm_input.to_csv(output_path, index=False)

    print(
        json.dumps(
            {
                "status": "ok",
                "output": args.output,
                "report": report.to_dict(),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
