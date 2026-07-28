from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from algorithm.data_pipeline import DataValidationError
from algorithm.recommendation import generate_recommendation


NBI = """STRUCTURE_NUMBER_008,HIGHWAY_DISTRICT_002,COUNTY_CODE_003,FACILITY_CARRIED_007,FEATURES_DESC_006A,LOCATION_009,LAT_016,LONG_017,DETOUR_KILOS_019,YEAR_BUILT_027,ADT_029,YEAR_ADT_030,DECK_COND_058,SUPERSTRUCTURE_COND_059,SUBSTRUCTURE_COND_060,CULVERT_COND_062,BRIDGE_CONDITION,LOWEST_RATING
A,08,1,ROAD A,CREEK A,NORTH,39432979,077181454,10,1960,1000,2024,4,5,5,N,P,4
B,08,1,ROAD B,CREEK B,SOUTH,39432979,077181454,20,1970,5000,2024,6,6,6,N,F,6
C,11,3,ROAD C,RIVER C,EAST,39432979,077181454,30,1980,10000,2024,7,7,7,N,G,7
"""

PREDICTIONS = """bridge_id,deterioration_probability,predicted_cost,cost_unit,prediction_horizon_years,model_version
A,0.90,10000000,USD,1,mock-v1
B,0.70,5000000,USD,1,mock-v1
C,0.60,5000000,USD,1,mock-v1
"""

COUNTIES = """county_fips,county_name,penndot_district
001,Adams County,8
003,Allegheny County,11
"""


class RecommendationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.nbi = root / "nbi.csv"
        self.predictions = root / "predictions.csv"
        self.counties = root / "counties.csv"
        self.nbi.write_text(NBI, encoding="utf-8")
        self.predictions.write_text(PREDICTIONS, encoding="utf-8")
        self.counties.write_text(COUNTIES, encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def generate(self, **kwargs: object) -> dict[str, object]:
        return generate_recommendation(
            self.nbi,
            self.predictions,
            self.counties,
            budget=10_000_000,
            **kwargs,
        )

    def test_response_is_strict_json_and_respects_budget(self) -> None:
        response = self.generate()

        serialized = json.dumps(response, allow_nan=False)
        self.assertTrue(serialized)
        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["schema_version"], "1.0")
        self.assertLessEqual(
            response["summary"]["total_predicted_cost"],
            response["summary"]["budget"],
        )

    def test_bridge_records_contain_ranks_reasons_and_map_fields(self) -> None:
        response = self.generate()
        record = response["selected_bridges"][0]

        self.assertIn("priority_rank_statewide", record)
        self.assertIn("priority_rank_in_region", record)
        self.assertIn("latitude", record)
        self.assertIn("longitude", record)
        self.assertEqual(len(record["reasons"]), 2)
        self.assertIn("highest-scoring portfolio", record["selection_explanation"])

    def test_mock_predictions_are_clearly_flagged(self) -> None:
        response = self.generate()

        self.assertTrue(response["development_data"])
        self.assertIn("Development model predictions", response["warnings"][0])

    def test_county_filter_returns_only_that_county_and_regional_ranks(self) -> None:
        response = self.generate(county_fips="001")
        records = (
            response["selected_bridges"] + response["high_priority_unfunded"]
        )

        self.assertEqual(response["request"]["region"], {"type": "county", "value": "001"})
        self.assertEqual({record["county_fips"] for record in records}, {"001"})
        self.assertEqual(
            sorted(record["priority_rank_in_region"] for record in records),
            [1, 2],
        )

    def test_unfunded_limit_is_validated(self) -> None:
        with self.assertRaisesRegex(DataValidationError, "unfunded_limit"):
            self.generate(unfunded_limit=-1)


if __name__ == "__main__":
    unittest.main()
