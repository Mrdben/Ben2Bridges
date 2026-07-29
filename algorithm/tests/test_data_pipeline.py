from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from algorithm.data_pipeline import DataValidationError, load_algorithm_inputs


NBI_HEADER = """STRUCTURE_NUMBER_008,HIGHWAY_DISTRICT_002,COUNTY_CODE_003,FACILITY_CARRIED_007,FEATURES_DESC_006A,LOCATION_009,LAT_016,LONG_017,DETOUR_KILOS_019,YEAR_BUILT_027,ADT_029,YEAR_ADT_030,DECK_COND_058,SUPERSTRUCTURE_COND_059,SUBSTRUCTURE_COND_060,CULVERT_COND_062,BRIDGE_CONDITION,LOWEST_RATING
"""

NBI_ROW = """        0001,08,1,'TEST ROAD','TEST CREEK','TEST LOCATION',39432979,077181454,999,1963,1226,2024,7,7,7,N,G,7
"""

PREDICTION_HEADER = """bridge_id,deterioration_risk_score,predicted_cost,cost_unit,prediction_horizon,model_version,risk_score_semantics,cost_reference_year,cost_method,cost_source_component,cost_lower_80,cost_upper_80,cost_high_probability,cost_is_derived
"""

COUNTIES = """county_fips,county_name,penndot_district
001,Adams County,8
"""


class DataPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.nbi_path = self._write("nbi.csv", NBI_HEADER + NBI_ROW)
        self.counties_path = self._write("counties.csv", COUNTIES)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write(self, name: str, content: str) -> Path:
        path = self.root / name
        path.write_text(content, encoding="utf-8")
        return path

    def _prediction_row(
        self,
        bridge_id: str,
        risk_score: float,
        cost: float,
        cost_unit: str = "USD",
    ) -> str:
        return (
            f"{bridge_id},{risk_score},{cost},{cost_unit},next_inspection,test-v1,"
            "normalized_model_score,2025,test_method,DECK,"
            f"{cost * 0.8},{cost * 1.2},0.1,true\n"
        )

    def _prediction_path(self, row: str) -> Path:
        return self._write("predictions.csv", PREDICTION_HEADER + row)

    def test_valid_data_is_cleaned_joined_and_reported(self) -> None:
        predictions = self._prediction_path(
            self._prediction_row("0001", 0.72, 1_250_000, "usd")
        )

        result, report = load_algorithm_inputs(
            self.nbi_path, predictions, self.counties_path
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result.loc[0, "bridge_id"], "0001")
        self.assertEqual(result.loc[0, "county_fips"], "001")
        self.assertEqual(result.loc[0, "county_name"], "Adams County")
        self.assertEqual(result.loc[0, "penndot_district"], 8)
        self.assertEqual(result.loc[0, "cost_unit"], "USD")
        self.assertTrue(result["detour_km"].isna().all())
        self.assertAlmostEqual(result.loc[0, "latitude"], 39.724942, places=6)
        self.assertAlmostEqual(result.loc[0, "longitude"], -77.304039, places=6)
        self.assertEqual(report.invalid_detour_count, 1)
        self.assertEqual(report.invalid_adt_year_count, 0)
        self.assertEqual(report.prediction_coverage_percent, 100.0)
        self.assertEqual(len(report.warnings), 2)
        self.assertTrue(report.derived_costs)
        self.assertEqual(report.prediction_horizon, "next_inspection")

    def test_risk_score_outside_zero_to_one_is_rejected(self) -> None:
        predictions = self._prediction_path(
            self._prediction_row("0001", 1.2, 1_250_000)
        )

        with self.assertRaisesRegex(
            DataValidationError, "deterioration_risk_score must be between"
        ):
            load_algorithm_inputs(self.nbi_path, predictions, self.counties_path)

    def test_nonpositive_cost_is_rejected(self) -> None:
        predictions = self._prediction_path(self._prediction_row("0001", 0.5, 0))

        with self.assertRaisesRegex(
            DataValidationError, "predicted_cost must be greater than zero"
        ):
            load_algorithm_inputs(self.nbi_path, predictions, self.counties_path)

    def test_duplicate_prediction_ids_after_cleaning_are_rejected(self) -> None:
        predictions = self._prediction_path(
            self._prediction_row("0001", 0.5, 100)
            + self._prediction_row(" 0001 ", 0.6, 200)
        )

        with self.assertRaisesRegex(DataValidationError, "duplicate bridge IDs"):
            load_algorithm_inputs(self.nbi_path, predictions, self.counties_path)

    def test_unknown_prediction_id_is_rejected(self) -> None:
        predictions = self._prediction_path(
            self._prediction_row("9999", 0.5, 100)
        )

        with self.assertRaisesRegex(DataValidationError, "not present in NBI data"):
            load_algorithm_inputs(self.nbi_path, predictions, self.counties_path)

    def test_mixed_cost_units_are_rejected(self) -> None:
        nbi = self._write(
            "nbi.csv",
            NBI_HEADER
            + NBI_ROW
            + "        0002,08,1,'SECOND ROAD','SECOND CREEK','SECOND LOCATION',39432979,077181454,10,1970,2000,2024,6,6,6,N,F,6\n",
        )
        predictions = self._prediction_path(
            self._prediction_row("0001", 0.5, 100)
            + self._prediction_row("0002", 0.6, 200, "relative")
        )

        with self.assertRaisesRegex(DataValidationError, "cost_unit must be consistent"):
            load_algorithm_inputs(nbi, predictions, self.counties_path)

    def test_missing_required_prediction_column_is_rejected(self) -> None:
        predictions = self._write(
            "predictions.csv",
            "bridge_id,deterioration_risk_score,predicted_cost\n"
            "0001,0.5,100\n",
        )

        with self.assertRaisesRegex(DataValidationError, "missing required columns"):
            load_algorithm_inputs(self.nbi_path, predictions, self.counties_path)


if __name__ == "__main__":
    unittest.main()
