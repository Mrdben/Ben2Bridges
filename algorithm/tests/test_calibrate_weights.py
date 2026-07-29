from __future__ import annotations

import unittest

import pandas as pd

from algorithm.calibrate_weights import (
    _jaccard,
    evaluate_weight_candidates,
    generate_candidate_weights,
)


def calibration_frame() -> pd.DataFrame:
    rows = []
    for index in range(40):
        rows.append(
            {
                "bridge_id": f"B{index:03d}",
                "deterioration_risk_score": (index + 1) / 41,
                "lowest_rating": 3 + (index % 6),
                "bridge_condition": "P" if index % 7 == 0 else "F",
                "adt": 100 + index * 200,
                "detour_km": index + 1,
                "predicted_cost": 100_000 + index * 1_000,
                "cost_unit": "USD",
                "county_fips": "001",
                "penndot_district": 8,
                "partial_penndot_importance_raw": 500 + index * 1_000,
                "is_nhs": index % 2 == 0,
                "historical_intervention_weak_label": index % 9 == 0,
                "ej_area": index % 5 == 0,
            }
        )
    return pd.DataFrame(rows)


class CalibrateWeightsTests(unittest.TestCase):
    def test_candidate_grid_has_expected_bounds_and_current_weights(self) -> None:
        candidates = generate_candidate_weights()
        self.assertEqual(len(candidates), 76)
        self.assertIn(
            {
                "deterioration": 0.40,
                "condition": 0.15,
                "traffic": 0.30,
                "detour": 0.15,
            },
            candidates,
        )
        for weights in candidates:
            self.assertAlmostEqual(sum(weights.values()), 1.0)
            self.assertGreaterEqual(weights["deterioration"], 0.30)
            self.assertLessEqual(weights["condition"], 0.25)

    def test_jaccard(self) -> None:
        self.assertEqual(_jaccard(set(), set()), 1.0)
        self.assertAlmostEqual(_jaccard({"A", "B"}, {"B", "C"}), 1 / 3)

    def test_evaluation_is_deterministic_and_selects_one_candidate(self) -> None:
        first, first_metadata = evaluate_weight_candidates(
            calibration_frame(), perturbation_count=1, seed=7
        )
        second, second_metadata = evaluate_weight_candidates(
            calibration_frame(), perturbation_count=1, seed=7
        )
        self.assertEqual(len(first), 76)
        self.assertEqual(first_metadata["selected_weights"], second_metadata["selected_weights"])
        self.assertEqual(first.iloc[0]["robust_maximin_score"], second.iloc[0]["robust_maximin_score"])
        self.assertTrue(first["search_rank"].is_unique)


if __name__ == "__main__":
    unittest.main()
