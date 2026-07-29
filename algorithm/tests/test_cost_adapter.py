from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from algorithm.cost_adapter import COST_METHOD, build_combined_predictions
from algorithm.data_pipeline import DataValidationError


NBI = """STRUCTURE_NUMBER_008,COUNTY_CODE_003,FACILITY_CARRIED_007,FEATURES_DESC_006A,LOCATION_009,LOWEST_RATING,BRIDGE_CONDITION,CULVERT_COND_062
A,1,ROAD A,CREEK A,NORTH,4,P,N
B,1,ROAD B,CREEK B,SOUTH,7,G,N
C,3,ROAD C,CREEK C,EAST,5,F,5
"""

RISK = """bridge_id,deterioration_risk_score,source_risk_rank,risk_percentile,risk_group
A,0.9,1,100,Very high
B,0.5,2,50,Moderate
C,0.2,3,0,Very low
"""

CATALOG = """STRUCTURE_NUMBER_008,COMPONENT,CONDITIONAL_WHOLE_PROJECT_COST_APPROXIMATION,LOWER_80_MODEL_INTERVAL,UPPER_80_MODEL_INTERVAL,PREDICTED_HIGH_COST_PROBABILITY,COST_MEANING
A,DECK,100,80,130,0.1,Whole-project scenario
A,SUPERSTRUCTURE,150,110,210,0.2,Whole-project scenario
B,DECK,200,150,280,0.3,Whole-project scenario
X,DECK,300,200,450,0.4,Whole-project scenario
"""


class CostAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.nbi = root / "nbi.csv"
        self.risk = root / "risk.csv"
        self.catalog = root / "catalog.csv"
        self.nbi.write_text(NBI, encoding="utf-8")
        self.risk.write_text(RISK, encoding="utf-8")
        self.catalog.write_text(CATALOG, encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def build(self):
        return build_combined_predictions(
            self.nbi,
            self.risk,
            self.catalog,
            cost_reference_year=2025,
        )

    def test_maximum_component_scenario_becomes_bridge_cost(self) -> None:
        combined, unknown, missing, report = self.build()

        bridge_a = combined.loc[combined["bridge_id"].eq("A")].iloc[0]
        self.assertEqual(bridge_a["predicted_cost"], 150)
        self.assertEqual(bridge_a["cost_source_component"], "SUPERSTRUCTURE")
        self.assertEqual(bridge_a["cost_lower_80"], 110)
        self.assertEqual(bridge_a["cost_upper_80"], 210)
        self.assertEqual(bridge_a["cost_method"], COST_METHOD)
        self.assertTrue(bridge_a["cost_is_derived"])
        self.assertEqual(set(combined["bridge_id"]), {"A", "B"})
        self.assertEqual(set(unknown["STRUCTURE_NUMBER_008"]), {"X"})
        self.assertEqual(set(missing["bridge_id"]), {"C"})
        self.assertEqual(report.usable_combined_prediction_count, 2)
        self.assertEqual(report.final_ineligible_nbi_count, 1)

    def test_component_scenarios_are_not_summed(self) -> None:
        combined, _, _, _ = self.build()
        bridge_a = combined.loc[combined["bridge_id"].eq("A")].iloc[0]

        self.assertNotEqual(bridge_a["predicted_cost"], 250)

    def test_duplicate_bridge_component_is_rejected(self) -> None:
        self.catalog.write_text(
            CATALOG + "A,DECK,120,90,160,0.1,Duplicate\n", encoding="utf-8"
        )

        with self.assertRaisesRegex(DataValidationError, "duplicate"):
            self.build()

    def test_invalid_interval_is_rejected(self) -> None:
        self.catalog.write_text(
            CATALOG.replace("A,DECK,100,80,130", "A,DECK,100,120,130"),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(DataValidationError, "80% model interval"):
            self.build()


if __name__ == "__main__":
    unittest.main()
