from __future__ import annotations

import unittest

import pandas as pd

from algorithm.budget_allocation import allocate_budget
from algorithm.data_pipeline import DataValidationError


def allocation_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "bridge_id": ["A", "B", "C"],
            "priority_rank": [1, 2, 3],
            "priority_score": [95.0, 70.0, 65.0],
            "deterioration_probability": [0.8, 0.75, 0.4],
            "predicted_cost": [10_000_000, 5_000_000, 5_000_000],
            "cost_unit": ["USD", "USD", "USD"],
            "bridge_condition": ["P", "F", "G"],
            "adt": [1000, 2000, 3000],
            "detour_km": [10.0, 20.0, 30.0],
            "county_fips": ["001", "001", "003"],
            "penndot_district": [8, 8, 11],
        }
    )


class BudgetAllocationTests(unittest.TestCase):
    def test_exact_optimizer_beats_simple_top_rank_selection(self) -> None:
        allocation, summary = allocate_budget(
            allocation_frame(), budget=10_000_000
        )
        selected = set(
            allocation.loc[allocation["selected_for_repair"], "bridge_id"]
        )

        self.assertEqual(selected, {"B", "C"})
        self.assertEqual(summary.total_selected_priority_score, 135.0)
        self.assertEqual(summary.total_predicted_cost, 10_000_000)
        self.assertEqual(summary.solver_status, "optimal:milp")

    def test_zero_budget_selects_no_bridges(self) -> None:
        allocation, summary = allocate_budget(allocation_frame(), budget=0)

        self.assertFalse(allocation["selected_for_repair"].any())
        self.assertEqual(summary.selected_bridge_count, 0)
        self.assertEqual(summary.remaining_budget, 0)

    def test_large_budget_selects_all_bridges(self) -> None:
        allocation, summary = allocate_budget(
            allocation_frame(), budget=25_000_000
        )

        self.assertTrue(allocation["selected_for_repair"].all())
        self.assertEqual(summary.selected_bridge_count, 3)
        self.assertEqual(summary.solver_status, "optimal:all_bridges_affordable")

    def test_county_filter_is_applied_before_optimization(self) -> None:
        allocation, summary = allocate_budget(
            allocation_frame(), budget=5_000_000, county_fips="001"
        )

        self.assertEqual(set(allocation["bridge_id"]), {"A", "B"})
        self.assertEqual(
            set(allocation.loc[allocation["selected_for_repair"], "bridge_id"]),
            {"B"},
        )
        self.assertEqual(summary.region, "county:001")

    def test_district_filter_is_applied(self) -> None:
        allocation, summary = allocate_budget(
            allocation_frame(), budget=5_000_000, penndot_district=11
        )

        self.assertEqual(list(allocation["bridge_id"]), ["C"])
        self.assertTrue(allocation.loc[0, "selected_for_repair"])
        self.assertEqual(summary.region, "district:11")

    def test_high_risk_counts_are_reported_but_do_not_change_selection(self) -> None:
        _, summary = allocate_budget(
            allocation_frame(), budget=10_000_000, high_risk_threshold=0.70
        )

        self.assertEqual(summary.selected_high_risk_count, 1)
        self.assertEqual(summary.unfunded_high_risk_count, 1)

    def test_budget_must_be_nonnegative(self) -> None:
        with self.assertRaisesRegex(DataValidationError, "nonnegative"):
            allocate_budget(allocation_frame(), budget=-1)

    def test_geographic_filters_are_mutually_exclusive(self) -> None:
        with self.assertRaisesRegex(DataValidationError, "mutually exclusive"):
            allocate_budget(
                allocation_frame(),
                budget=10_000_000,
                county_fips="001",
                penndot_district=8,
            )

    def test_mixed_cost_units_are_rejected(self) -> None:
        frame = allocation_frame()
        frame.loc[1, "cost_unit"] = "relative"

        with self.assertRaisesRegex(DataValidationError, "cost_unit"):
            allocate_budget(frame, budget=10_000_000)


if __name__ == "__main__":
    unittest.main()
