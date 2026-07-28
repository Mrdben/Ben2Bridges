"""Convert the Pennsylvania NBI CSV into compact, map-ready GeoJSON."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path


WEBSITE_DIR = Path(__file__).resolve().parents[1]
INPUT_PATH = WEBSITE_DIR / "Data" / "PA 2025.csv"
OUTPUT_PATH = WEBSITE_DIR / "Data" / "pa_bridges_2025.geojson"
MAP_OUTPUT_PATH = WEBSITE_DIR / "Data" / "pa_bridges_2025_map.geojson"

REQUIRED_COLUMNS = {
    "STRUCTURE_NUMBER_008",
    "COUNTY_CODE_003",
    "FEATURES_DESC_006A",
    "FACILITY_CARRIED_007",
    "LOCATION_009",
    "LAT_016",
    "LONG_017",
    "DETOUR_KILOS_019",
    "YEAR_BUILT_027",
    "ADT_029",
    "YEAR_ADT_030",
    "STRUCTURE_LEN_MT_049",
    "STRUCTURE_KIND_043A",
    "STRUCTURE_TYPE_043B",
    "DECK_AREA",
    "DECK_COND_058",
    "SUPERSTRUCTURE_COND_059",
    "SUBSTRUCTURE_COND_060",
    "DATE_OF_INSPECT_090",
    "BRIDGE_CONDITION",
    "LOWEST_RATING",
}


def clean_text(value: str | None) -> str | None:
    if value is None:
        return None

    cleaned = value.strip()
    if len(cleaned) >= 2 and cleaned.startswith("'") and cleaned.endswith("'"):
        cleaned = cleaned[1:-1].strip()

    return cleaned or None


def parse_number(value: str | None, *, integer: bool = False) -> int | float | None:
    cleaned = clean_text(value)
    if cleaned is None:
        return None

    number = float(cleaned)
    if integer and number.is_integer():
        return int(number)
    return number


def parse_rating(value: str | None) -> int | str | None:
    cleaned = clean_text(value)
    if cleaned is None:
        return None

    try:
        number = float(cleaned)
    except ValueError:
        return cleaned

    return int(number) if number.is_integer() else number


def dms_to_decimal(value: str, *, western_longitude: bool = False) -> float:
    """Convert NBI DDMMSSss/DDDMMSSss coordinates to decimal degrees."""

    raw = float(value)
    degrees = math.floor(raw / 1_000_000)
    minutes = math.floor((raw % 1_000_000) / 10_000)
    seconds = (raw % 10_000) / 100

    if not 0 <= minutes < 60 or not 0 <= seconds < 60:
        raise ValueError(f"Invalid NBI coordinate: {value!r}")

    decimal = degrees + minutes / 60 + seconds / 3_600
    if western_longitude:
        decimal = -decimal
    return round(decimal, 6)


def inspection_date_code(value: str | None) -> str | None:
    cleaned = clean_text(value)
    if cleaned is None:
        return None
    return cleaned.zfill(4)


def convert() -> dict[str, object]:
    features: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    min_lon = math.inf
    min_lat = math.inf
    max_lon = -math.inf
    max_lat = -math.inf

    with INPUT_PATH.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        missing_columns = REQUIRED_COLUMNS.difference(reader.fieldnames or [])
        if missing_columns:
            missing = ", ".join(sorted(missing_columns))
            raise ValueError(f"Source CSV is missing required columns: {missing}")

        for row_number, row in enumerate(reader, start=2):
            bridge_id = clean_text(row["STRUCTURE_NUMBER_008"])
            if bridge_id is None:
                raise ValueError(f"Missing bridge ID on CSV row {row_number}")
            if bridge_id in seen_ids:
                raise ValueError(f"Duplicate bridge ID {bridge_id!r} on CSV row {row_number}")
            seen_ids.add(bridge_id)

            latitude = dms_to_decimal(row["LAT_016"])
            longitude = dms_to_decimal(row["LONG_017"], western_longitude=True)
            if not 39.5 <= latitude <= 42.6 or not -80.7 <= longitude <= -74.5:
                raise ValueError(
                    f"Bridge {bridge_id!r} has coordinates outside Pennsylvania: "
                    f"{latitude}, {longitude}"
                )

            min_lon = min(min_lon, longitude)
            min_lat = min(min_lat, latitude)
            max_lon = max(max_lon, longitude)
            max_lat = max(max_lat, latitude)

            county_code = clean_text(row["COUNTY_CODE_003"])
            properties = {
                "countyCode": county_code.zfill(3) if county_code else None,
                "facility": clean_text(row["FACILITY_CARRIED_007"]),
                "featureCrossed": clean_text(row["FEATURES_DESC_006A"]),
                "location": clean_text(row["LOCATION_009"]),
                "condition": clean_text(row["BRIDGE_CONDITION"]),
                "lowestRating": parse_rating(row["LOWEST_RATING"]),
                "deckCondition": parse_rating(row["DECK_COND_058"]),
                "superstructureCondition": parse_rating(row["SUPERSTRUCTURE_COND_059"]),
                "substructureCondition": parse_rating(row["SUBSTRUCTURE_COND_060"]),
                "yearBuilt": parse_number(row["YEAR_BUILT_027"], integer=True),
                "adt": parse_number(row["ADT_029"], integer=True),
                "adtYear": parse_number(row["YEAR_ADT_030"], integer=True),
                "detourKm": parse_number(row["DETOUR_KILOS_019"]),
                "lengthM": parse_number(row["STRUCTURE_LEN_MT_049"]),
                "materialKind": parse_number(row["STRUCTURE_KIND_043A"], integer=True),
                "structureType": parse_number(row["STRUCTURE_TYPE_043B"], integer=True),
                "deckArea": parse_number(row["DECK_AREA"]),
                "inspectionDate": inspection_date_code(row["DATE_OF_INSPECT_090"]),
            }

            features.append(
                {
                    "type": "Feature",
                    "id": bridge_id,
                    "geometry": {
                        "type": "Point",
                        "coordinates": [longitude, latitude],
                    },
                    "properties": properties,
                }
            )

    collection: dict[str, object] = {
        "type": "FeatureCollection",
        "name": "Pennsylvania Bridges 2025",
        "bbox": [
            round(min_lon, 6),
            round(min_lat, 6),
            round(max_lon, 6),
            round(max_lat, 6),
        ],
        "metadata": {
            "source": INPUT_PATH.name,
            "featureCount": len(features),
            "joinKey": "Feature.id corresponds to STRUCTURE_NUMBER_008",
            "coordinatePrecision": 6,
        },
        "features": features,
    }

    with OUTPUT_PATH.open("w", encoding="utf-8", newline="\n") as destination:
        json.dump(
            collection,
            destination,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        destination.write("\n")

    map_collection = {
        "type": "FeatureCollection",
        "name": "Pennsylvania Bridge Map Points 2025",
        "bbox": collection["bbox"],
        "features": [
            {
                "type": "Feature",
                "id": feature["id"],
                "geometry": feature["geometry"],
                "properties": {
                    key: feature["properties"][key]
                    for key in (
                        "countyCode",
                        "facility",
                        "featureCrossed",
                        "location",
                        "condition",
                        "yearBuilt",
                        "adt",
                        "materialKind",
                        "structureType",
                        "deckArea",
                    )
                },
            }
            for feature in features
        ],
    }

    with MAP_OUTPUT_PATH.open("w", encoding="utf-8", newline="\n") as destination:
        json.dump(
            map_collection,
            destination,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        destination.write("\n")

    return collection


if __name__ == "__main__":
    result = convert()
    print(f"Wrote {len(result['features']):,} bridges to {OUTPUT_PATH}")
    print(f"Wrote lightweight map data to {MAP_OUTPUT_PATH}")
    print(f"Bounds: {result['bbox']}")
