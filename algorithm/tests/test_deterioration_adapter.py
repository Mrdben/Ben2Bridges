from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from algorithm.data_pipeline import DataValidationError
from algorithm.deterioration_adapter import adapt_deterioration_output


NBI = """STRUCTURE_NUMBER_008,COUNTY_CODE_003,FACILITY_CARRIED_007,FEATURES_DESC_006A,LOCATION_009,LOWEST_RATING,BRIDGE_CONDITION,CULVERT_COND_062,YEAR_BUILT_027,ADT_029
A,1,ROAD A,CREEK A,NORTH,4,P,N,1960,1000
B,1,ROAD B,CREEK B,SOUTH,7,G,N,2000,5000
C,3,'ROAD C','CREEK C','EAST',5,F,5,1980,2500
"""

RANKINGS = """STRUCTURE_NUMBER_008,MODEL_DETERIORATION_RISK_SCORE,RISK_RANK,RISK_PERCENTILE,RISK_GROUP
A,0.9,1,100,Very high
X,0.8,2,75,High
B,0.2,3,25,Low
"""


class DeteriorationAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.nbi = root / "nbi.csv"
        self.rankings = root / "rankings.csv"
        self.nbi.write_text(NBI, encoding="utf-8")
        self.rankings.write_text(RANKINGS, encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_records_are_separated_without_imputation(self) -> None:
        usable, unknown, unmodeled, report = adapt_deterioration_output(
            self.nbi, self.rankings
        )

        self.assertEqual(list(usable["bridge_id"]), ["A", "B"])
        self.assertEqual(list(unknown["STRUCTURE_NUMBER_008"]), ["X"])
        self.assertEqual(list(unmodeled["bridge_id"]), ["C"])
        self.assertEqual(list(unmodeled["county_fips"]), ["003"])
        self.assertEqual(list(unmodeled["facility_carried"]), ["ROAD C"])
        self.assertNotIn("deterioration_risk_score", unmodeled.columns)
        self.assertEqual(report.usable_prediction_count, 2)
        self.assertEqual(report.unknown_model_record_count, 1)
        self.assertEqual(report.unmodeled_nbi_bridge_count, 1)
        self.assertEqual(report.unmodeled_culvert_count, 1)
        self.assertEqual(report.missing_score_imputation, "none")
        self.assertAlmostEqual(report.prediction_coverage_percent, 66.6667)

    def test_out_of_range_risk_score_is_rejected(self) -> None:
        self.rankings.write_text(
            RANKINGS.replace("A,0.9", "A,1.1"), encoding="utf-8"
        )

        with self.assertRaisesRegex(DataValidationError, "at most 1"):
            adapt_deterioration_output(self.nbi, self.rankings)

    def test_duplicate_model_bridge_ids_are_rejected(self) -> None:
        self.rankings.write_text(
            RANKINGS + "A,0.1,4,0,Very low\n", encoding="utf-8"
        )

        with self.assertRaisesRegex(DataValidationError, "duplicate bridge IDs"):
            adapt_deterioration_output(self.nbi, self.rankings)

    def test_missing_required_column_is_rejected(self) -> None:
        self.rankings.write_text(
            "STRUCTURE_NUMBER_008,MODEL_DETERIORATION_RISK_SCORE\nA,0.9\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(DataValidationError, "missing required columns"):
            adapt_deterioration_output(self.nbi, self.rankings)


if __name__ == "__main__":
    unittest.main()
