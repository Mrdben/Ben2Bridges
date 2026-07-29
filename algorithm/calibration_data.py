"""Build official-informed calibration features without changing score weights.

The output combines current eligible bridges with a weak historical NBI
condition trajectory, a partial reconstruction of PennDOT's bridge importance
formula, and a PennEnviroScreen block-group spatial match.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Iterable

import pandas as pd

try:
    from .data_pipeline import DataValidationError, load_algorithm_inputs
except ImportError:  # Support direct execution.
    from data_pipeline import DataValidationError, load_algorithm_inputs


HISTORICAL_COLUMNS = {
    "STRUCTURE_NUMBER_008",
    "LOWEST_RATING",
    "BRIDGE_CONDITION",
    "YEAR_OF_IMP_097",
}
CURRENT_EXTRA_COLUMNS = {
    "STRUCTURE_NUMBER_008",
    "DECK_AREA",
    "PERCENT_ADT_TRUCK_109",
    "HIGHWAY_SYSTEM_104",
}
EJ_FIELDS = {
    "GEOID",
    "COUNTY",
    "FINALSCOREPCTILE",
    "SOCIOECONOMICSCOREPCTILE",
    "EJAREA",
}
GRID_SIZE_DEGREES = 0.1


def _clean_text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    if len(text) >= 2 and text.startswith("'") and text.endswith("'"):
        text = text[1:-1].strip()
    return text


def _clean_ids(series: pd.Series, label: str) -> pd.Series:
    cleaned = series.map(_clean_text).astype("string")
    if cleaned.eq("").any():
        raise DataValidationError(f"{label} contains an empty bridge ID")
    if cleaned.duplicated().any():
        raise DataValidationError(f"{label} contains duplicate bridge IDs")
    return cleaned


def _numeric(series: pd.Series, label: str, *, allow_blank: bool = False) -> pd.Series:
    cleaned = series.map(_clean_text)
    blank = cleaned.eq("")
    parsed = pd.to_numeric(cleaned.mask(blank), errors="coerce")
    if (parsed.isna() & ~blank).any():
        raise DataValidationError(f"{label} contains non-numeric values")
    if not allow_blank and parsed.isna().any():
        raise DataValidationError(f"{label} contains missing values")
    return parsed.astype("float64")


def _read_historical(path: str | Path, year: int) -> pd.DataFrame:
    # FHWA's archived delimited files use a single quote as the text
    # qualifier.  Python's csv module handles these files consistently, while
    # pandas' C parser can incorrectly report an extra field on valid rows.
    # Read only the required columns, but validate every row so malformed
    # bridge records are never silently skipped.
    with Path(path).open("r", encoding="latin-1", newline="") as handle:
        reader = csv.reader(handle, delimiter=",", quotechar="'", strict=False)
        try:
            header = next(reader)
        except StopIteration as error:
            raise DataValidationError(f"Historical NBI {year} is empty") from error

        missing = sorted(HISTORICAL_COLUMNS.difference(header))
        if missing:
            raise DataValidationError(
                f"Historical NBI {year} is missing columns: {', '.join(missing)}"
            )
        positions = {name: header.index(name) for name in HISTORICAL_COLUMNS}
        other_state_structure_index = header.index("OTHR_STATE_STRUC_NO_099")
        records: list[dict[str, str]] = []
        for row_number, row in enumerate(reader, start=2):
            if len(row) > len(header):
                # FHWA PA24 contains at least one unquoted comma in the free-
                # text Other State Structure Number field (for example,
                # "1sDPG, 2sDIB").  Rejoin only that documented text field;
                # all other shape errors remain fatal.
                surplus = len(row) - len(header)
                join_end = other_state_structure_index + surplus + 1
                row = (
                    row[:other_state_structure_index]
                    + [",".join(row[other_state_structure_index:join_end])]
                    + row[join_end:]
                )
            if len(row) != len(header):
                raise DataValidationError(
                    f"Historical NBI {year} row {row_number} has {len(row)} "
                    f"columns; expected {len(header)}"
                )
            records.append({name: row[index] for name, index in positions.items()})

    frame = pd.DataFrame.from_records(records, columns=sorted(HISTORICAL_COLUMNS))
    result = frame[list(HISTORICAL_COLUMNS)].copy()
    result["bridge_id"] = _clean_ids(
        frame["STRUCTURE_NUMBER_008"], f"Historical NBI {year}"
    )
    result[f"lowest_rating_{year}"] = _numeric(
        frame["LOWEST_RATING"], f"LOWEST_RATING {year}"
    )
    result[f"bridge_condition_{year}"] = (
        frame["BRIDGE_CONDITION"].map(_clean_text).str.upper()
    )
    result[f"year_of_improvement_{year}"] = _numeric(
        frame["YEAR_OF_IMP_097"],
        f"YEAR_OF_IMP_097 {year}",
        allow_blank=True,
    )
    return result[
        [
            "bridge_id",
            f"lowest_rating_{year}",
            f"bridge_condition_{year}",
            f"year_of_improvement_{year}",
        ]
    ]


def _read_current_extras(path: str | Path) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype=str, keep_default_na=False, low_memory=False)
    missing = sorted(CURRENT_EXTRA_COLUMNS.difference(frame.columns))
    if missing:
        raise DataValidationError(
            f"Current NBI is missing calibration columns: {', '.join(missing)}"
        )
    result = pd.DataFrame(
        {
            "bridge_id": _clean_ids(frame["STRUCTURE_NUMBER_008"], "Current NBI"),
            "deck_area_sq_m": _numeric(frame["DECK_AREA"], "DECK_AREA"),
            "truck_percent": _numeric(
                frame["PERCENT_ADT_TRUCK_109"],
                "PERCENT_ADT_TRUCK_109",
                allow_blank=True,
            ),
            "is_nhs": frame["HIGHWAY_SYSTEM_104"].map(_clean_text).eq("1"),
        }
    )
    if (result["deck_area_sq_m"] <= 0).any():
        raise DataValidationError("DECK_AREA must be greater than zero")
    invalid_truck = result["truck_percent"].notna() & ~result[
        "truck_percent"
    ].between(0, 100)
    if invalid_truck.any():
        result.loc[invalid_truck, "truck_percent"] = math.nan
    return result


def _detour_factor(detour_km: float | None) -> float:
    """Return the official PennDOT detour factor using mile thresholds."""

    if detour_km is None or pd.isna(detour_km):
        return 1.0
    detour_miles = float(detour_km) / 1.609344
    if detour_miles > 30:
        return 2.0
    if detour_miles >= 10:
        return 1.5
    return 1.0


def _truck_factor(truck_percent: float | None) -> float:
    """Return the official PennDOT annual-average-daily-truck factor."""

    if truck_percent is None or pd.isna(truck_percent):
        return 1.0
    value = float(truck_percent)
    if value > 20:
        return 2.0
    if value >= 10:
        return 1.5
    return 1.0


def _percentile_score(values: pd.Series) -> pd.Series:
    if len(values) <= 1 or values.nunique() <= 1:
        return pd.Series(0.5, index=values.index, dtype="float64")
    ranks = values.rank(method="average")
    return ((ranks - 1) / (len(values) - 1)).clip(0, 1)


def _iter_polygons(geometry: dict[str, object]) -> Iterable[list[list[list[float]]]]:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list):
        return []
    if geometry_type == "Polygon":
        return [coordinates]
    if geometry_type == "MultiPolygon":
        return coordinates
    return []


def _point_on_segment(
    x: float,
    y: float,
    left: list[float],
    right: list[float],
    tolerance: float = 1e-10,
) -> bool:
    x1, y1 = float(left[0]), float(left[1])
    x2, y2 = float(right[0]), float(right[1])
    cross = (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1)
    if abs(cross) > tolerance:
        return False
    return (
        min(x1, x2) - tolerance <= x <= max(x1, x2) + tolerance
        and min(y1, y2) - tolerance <= y <= max(y1, y2) + tolerance
    )


def _point_in_ring(x: float, y: float, ring: list[list[float]]) -> bool:
    inside = False
    for index in range(len(ring)):
        left = ring[index - 1]
        right = ring[index]
        if _point_on_segment(x, y, left, right):
            return True
        x1, y1 = float(left[0]), float(left[1])
        x2, y2 = float(right[0]), float(right[1])
        if (y1 > y) != (y2 > y):
            intersection_x = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < intersection_x:
                inside = not inside
    return inside


def _point_in_polygon(
    x: float, y: float, polygon: list[list[list[float]]]
) -> bool:
    if not polygon or not _point_in_ring(x, y, polygon[0]):
        return False
    return not any(_point_in_ring(x, y, hole) for hole in polygon[1:])


def _feature_bounds(feature: dict[str, object]) -> tuple[float, float, float, float]:
    points = [
        point
        for polygon in _iter_polygons(feature.get("geometry", {}))
        for ring in polygon
        for point in ring
    ]
    if not points:
        raise DataValidationError("PennEnviroScreen contains an empty geometry")
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def _load_ej_features(paths: list[str | Path]) -> list[dict[str, object]]:
    features: list[dict[str, object]] = []
    for path in paths:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("type") != "FeatureCollection":
            raise DataValidationError(f"Invalid PennEnviroScreen GeoJSON: {path}")
        features.extend(data.get("features", []))
    geoids = []
    for feature in features:
        properties = feature.get("properties", {})
        missing = sorted(EJ_FIELDS.difference(properties))
        if missing:
            raise DataValidationError(
                f"PennEnviroScreen feature is missing: {', '.join(missing)}"
            )
        geoids.append(str(properties["GEOID"]))
    if len(geoids) != len(set(geoids)):
        raise DataValidationError("PennEnviroScreen GEOIDs must be unique")
    return features


def _build_spatial_grid(
    features: list[dict[str, object]],
) -> dict[tuple[int, int], list[int]]:
    grid: dict[tuple[int, int], list[int]] = defaultdict(list)
    for feature_index, feature in enumerate(features):
        min_x, min_y, max_x, max_y = _feature_bounds(feature)
        for cell_x in range(
            math.floor(min_x / GRID_SIZE_DEGREES),
            math.floor(max_x / GRID_SIZE_DEGREES) + 1,
        ):
            for cell_y in range(
                math.floor(min_y / GRID_SIZE_DEGREES),
                math.floor(max_y / GRID_SIZE_DEGREES) + 1,
            ):
                grid[(cell_x, cell_y)].append(feature_index)
    return grid


def _spatial_match(
    frame: pd.DataFrame, features: list[dict[str, object]]
) -> pd.DataFrame:
    grid = _build_spatial_grid(features)
    rows: list[dict[str, object]] = []
    for _, bridge in frame.iterrows():
        longitude = float(bridge["longitude"])
        latitude = float(bridge["latitude"])
        cell = (
            math.floor(longitude / GRID_SIZE_DEGREES),
            math.floor(latitude / GRID_SIZE_DEGREES),
        )
        matches: list[dict[str, object]] = []
        for feature_index in grid.get(cell, []):
            feature = features[feature_index]
            if any(
                _point_in_polygon(longitude, latitude, polygon)
                for polygon in _iter_polygons(feature.get("geometry", {}))
            ):
                matches.append(feature)
        matches.sort(key=lambda item: str(item["properties"]["GEOID"]))

        if not matches:
            rows.append(
                {
                    "bridge_id": bridge["bridge_id"],
                    "ej_match_status": "unmatched",
                    "ej_block_group_geoid": None,
                    "ej_area": None,
                    "ej_final_score_percentile": None,
                    "ej_socioeconomic_percentile": None,
                }
            )
            continue

        properties = matches[0]["properties"]
        rows.append(
            {
                "bridge_id": bridge["bridge_id"],
                "ej_match_status": "matched" if len(matches) == 1 else "multiple_boundary_matches",
                "ej_block_group_geoid": str(properties["GEOID"]),
                "ej_area": str(properties["EJAREA"]).strip().lower() == "yes",
                "ej_final_score_percentile": properties["FINALSCOREPCTILE"],
                "ej_socioeconomic_percentile": properties[
                    "SOCIOECONOMICSCOREPCTILE"
                ],
            }
        )
    return pd.DataFrame(rows)


def build_calibration_features(
    nbi_current_path: str | Path,
    predictions_path: str | Path,
    counties_path: str | Path,
    historical_paths: dict[int, str | Path],
    ej_geojson_paths: list[str | Path],
) -> tuple[pd.DataFrame, dict[str, object]]:
    """Return calibration features and a provenance/coverage report."""

    current, validation = load_algorithm_inputs(
        nbi_current_path, predictions_path, counties_path
    )
    features = current.copy()
    features = features.merge(
        _read_current_extras(nbi_current_path),
        on="bridge_id",
        how="left",
        validate="one_to_one",
    )

    historical_match_counts: dict[str, int] = {}
    for year in sorted(historical_paths):
        historical = _read_historical(historical_paths[year], year)
        features = features.merge(
            historical,
            on="bridge_id",
            how="left",
            validate="one_to_one",
        )
        historical_match_counts[str(year)] = int(
            features[f"lowest_rating_{year}"].notna().sum()
        )

    features["lowest_rating_2025"] = features["lowest_rating"].astype("float64")
    features["bridge_condition_2025"] = features["bridge_condition"]
    history_rating_columns = [
        f"lowest_rating_{year}" for year in sorted(historical_paths)
    ] + ["lowest_rating_2025"]
    features["historical_years_matched"] = features[
        history_rating_columns
    ].notna().sum(axis=1)
    features["condition_change_2022_to_2025"] = (
        features["lowest_rating_2025"] - features.get("lowest_rating_2022")
    )
    features["rating_improved_since_2022"] = features[
        "condition_change_2022_to_2025"
    ].ge(1)

    improvement_columns = [
        f"year_of_improvement_{year}" for year in sorted(historical_paths)
    ]
    improvement_years = features[improvement_columns]
    features["documented_improvement_2022_2025"] = improvement_years.apply(
        lambda row: any(
            pd.notna(value) and 2022 <= float(value) <= 2025 for value in row
        ),
        axis=1,
    )
    features["historical_intervention_weak_label"] = (
        features["rating_improved_since_2022"]
        | features["documented_improvement_2022_2025"]
    )

    features["truck_data_missing"] = features["truck_percent"].isna()
    features["detour_factor_official"] = features["detour_km"].map(_detour_factor)
    features["truck_factor_official"] = features["truck_percent"].map(_truck_factor)
    features["partial_penndot_importance_raw"] = (
        (features["deck_area_sq_m"] * features["adt"].astype("float64"))
        .clip(lower=0)
        .map(math.sqrt)
        * features["detour_factor_official"]
        * features["truck_factor_official"]
    )
    features["partial_penndot_importance_score"] = _percentile_score(
        features["partial_penndot_importance_raw"]
    ).round(6)
    features["importance_formula_is_partial"] = True
    features["neutral_missing_official_factors"] = (
        "scour_internal_rating;fracture_critical_internal_rating;flood_history"
    )

    ej_features = _load_ej_features(ej_geojson_paths)
    ej_matches = _spatial_match(features, ej_features)
    features = features.merge(
        ej_matches, on="bridge_id", how="left", validate="one_to_one"
    )

    matched_ej = features["ej_match_status"].ne("unmatched")
    report: dict[str, object] = {
        "status": "ok",
        "eligible_bridge_count": len(features),
        "historical_nbi_years": sorted(historical_paths),
        "historical_match_counts": historical_match_counts,
        "complete_2022_to_2025_trajectory_count": int(
            features["historical_years_matched"].eq(len(history_rating_columns)).sum()
        ),
        "rating_improved_since_2022_count": int(
            features["rating_improved_since_2022"].sum()
        ),
        "documented_improvement_2022_2025_count": int(
            features["documented_improvement_2022_2025"].sum()
        ),
        "historical_intervention_weak_label_count": int(
            features["historical_intervention_weak_label"].sum()
        ),
        "pennenviroscreen_block_group_count": len(ej_features),
        "ej_spatial_match_count": int(matched_ej.sum()),
        "ej_unmatched_count": int((~matched_ej).sum()),
        "ej_multiple_boundary_match_count": int(
            features["ej_match_status"].eq("multiple_boundary_matches").sum()
        ),
        "eligible_bridge_in_ej_area_count": int(
            features.loc[matched_ej, "ej_area"].eq(True).sum()
        ),
        "truck_data_missing_count": int(features["truck_data_missing"].sum()),
        "importance_formula": "sqrt(deck_area * AADT) * detour_factor * truck_factor",
        "importance_formula_is_partial": True,
        "neutral_missing_official_factors": [
            "scour_internal_rating",
            "fracture_critical_internal_rating",
            "flood_history",
        ],
        "warnings": [
            "Historical NBI condition changes and Item 97 form a weak intervention label, not a complete project ground truth.",
            "The PennDOT importance reconstruction is partial because required internal scour, fracture-critical, and flood-history fields are unavailable.",
            "PennEnviroScreen is used for portfolio equity auditing, not as a government-prescribed bridge scoring weight.",
        ],
        "input_validation": validation.to_dict(),
    }
    return features, report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build official-informed Ben2Bridges calibration features"
    )
    parser.add_argument("--nbi-current", required=True)
    parser.add_argument("--predictions", required=True)
    parser.add_argument("--counties", required=True)
    parser.add_argument("--nbi-2022", required=True)
    parser.add_argument("--nbi-2023", required=True)
    parser.add_argument("--nbi-2024", required=True)
    parser.add_argument("--ej-geojson", nargs="+", required=True)
    parser.add_argument("--output-dir", required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    features, report = build_calibration_features(
        args.nbi_current,
        args.predictions,
        args.counties,
        {
            2022: args.nbi_2022,
            2023: args.nbi_2023,
            2024: args.nbi_2024,
        },
        args.ej_geojson,
    )
    feature_path = output_dir / "official_calibration_features.csv"
    report_path = output_dir / "official_calibration_report.json"
    features.to_csv(feature_path, index=False)
    report_path.write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "outputs": {
                    "features": str(feature_path),
                    "report": str(report_path),
                },
                "summary": {
                    key: report[key]
                    for key in (
                        "eligible_bridge_count",
                        "complete_2022_to_2025_trajectory_count",
                        "historical_intervention_weak_label_count",
                        "ej_spatial_match_count",
                        "eligible_bridge_in_ej_area_count",
                    )
                },
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
