from __future__ import annotations

import unittest

import pandas as pd

from algorithm.data_pipeline import DataValidationError
from algorithm.scoring import ScoreWeights, score_bridges


def scoring_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "bridge_id": ["A", "B", "C"],
            "deterioration_risk_score": [0.9, 0.4, 0.6],
            "lowest_rating": [6, 4, 7],
            "adt": [100, 10000, 1000],
            "detour_km": [5.0, 10.0, 20.0],
            "predicted_cost": [1_000_000, 1_000_000, 1_000_000],
        }
    )


class ScoringTests(unittest.TestCase):
    def test_balanced_scoring_produces_components_and_rank(self) -> None:
        scored, report = score_bridges(scoring_frame(), strategy="balanced")

        self.assertEqual(list(scored["priority_rank"]), [1, 2, 3])
        self.assertEqual(set(scored["bridge_id"]), {"A", "B", "C"})
        self.assertTrue(scored["priority_score"].between(0, 100).all())
        self.assertAlmostEqual(sum(report.weights.values()), 1.0)
        self.assertEqual(
            report.weights,
            {
                "deterioration": 0.45,
                "condition": 0.25,
                "traffic": 0.25,
                "detour": 0.05,
            },
        )
        self.assertEqual(report.weight_status, "official_informed_calibrated")
        self.assertFalse(report.provisional_weights)
        for indicator in ("deterioration", "condition", "traffic", "detour"):
            self.assertIn(f"{indicator}_score", scored.columns)
            self.assertIn(f"{indicator}_contribution", scored.columns)

    def test_safety_and_traffic_profiles_can_change_top_bridge(self) -> None:
        frame = pd.DataFrame(
            {
                "bridge_id": ["HIGH_RISK", "HIGH_TRAFFIC"],
                "deterioration_risk_score": [0.95, 0.20],
                "lowest_rating": [4, 7],
                "adt": [100, 100000],
                "detour_km": [5, 5],
                "predicted_cost": [1_000_000, 1_000_000],
            }
        )

        safety, safety_report = score_bridges(frame, strategy="safety")
        traffic, _ = score_bridges(frame, strategy="traffic")

        self.assertEqual(safety.loc[0, "bridge_id"], "HIGH_RISK")
        self.assertEqual(traffic.loc[0, "bridge_id"], "HIGH_TRAFFIC")
        self.assertEqual(safety_report.weight_status, "provisional_policy_profile")
        self.assertTrue(safety_report.provisional_weights)

    def test_equity_profile_prioritizes_detour_burden(self) -> None:
        frame = pd.DataFrame(
            {
                "bridge_id": ["HIGH_DETOUR", "HIGH_TRAFFIC"],
                "deterioration_risk_score": [0.5, 0.5],
                "lowest_rating": [5, 5],
                "adt": [100, 100000],
                "detour_km": [100, 1],
                "predicted_cost": [1_000_000, 1_000_000],
            }
        )

        equity, report = score_bridges(frame, strategy="equity")

        self.assertEqual(equity.loc[0, "bridge_id"], "HIGH_DETOUR")
        self.assertEqual(
            report.weights,
            {
                "deterioration": 0.30,
                "condition": 0.20,
                "traffic": 0.10,
                "detour": 0.40,
            },
        )
        self.assertEqual(report.weight_status, "provisional_policy_profile")
        self.assertTrue(report.provisional_weights)

    def test_cost_does_not_change_priority_score(self) -> None:
        frame = pd.DataFrame(
            {
                "bridge_id": ["CHEAP", "EXPENSIVE"],
                "deterioration_risk_score": [0.7, 0.7],
                "lowest_rating": [5, 5],
                "adt": [1000, 1000],
                "detour_km": [10, 10],
                "predicted_cost": [100, 10_000_000],
            }
        )

        scored, _ = score_bridges(frame)
        scores = scored.set_index("bridge_id")["priority_score"]

        self.assertEqual(scores["CHEAP"], scores["EXPENSIVE"])

    def test_missing_detour_receives_neutral_score_and_flag(self) -> None:
        frame = scoring_frame()
        frame.loc[1, "detour_km"] = pd.NA

        scored, report = score_bridges(frame)
        row = scored.set_index("bridge_id").loc["B"]

        self.assertEqual(row["detour_score"], 0.5)
        self.assertTrue(row["detour_score_imputed"])
        self.assertEqual(report.missing_detour_count, 1)

    def test_custom_weights_are_supported_and_marked_nonprovisional(self) -> None:
        weights = {
            "deterioration": 1.0,
            "condition": 0.0,
            "traffic": 0.0,
            "detour": 0.0,
        }

        scored, report = score_bridges(scoring_frame(), custom_weights=weights)

        self.assertEqual(scored.loc[0, "bridge_id"], "A")
        self.assertEqual(report.strategy, "custom")
        self.assertEqual(report.weight_status, "custom_user_weights")
        self.assertFalse(report.provisional_weights)

    def test_weights_must_sum_to_one(self) -> None:
        with self.assertRaisesRegex(DataValidationError, "sum to 1.0"):
            ScoreWeights(0.4, 0.2, 0.2, 0.1)

    def test_invalid_risk_score_is_rejected(self) -> None:
        frame = scoring_frame()
        frame.loc[0, "deterioration_risk_score"] = 1.1

        with self.assertRaisesRegex(
            DataValidationError, "deterioration_risk_score must be between"
        ):
            score_bridges(frame)


if __name__ == "__main__":
    unittest.main()
