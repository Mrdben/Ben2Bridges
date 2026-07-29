from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from algorithm.calibration_data import (
    _detour_factor,
    _point_in_polygon,
    _read_historical,
    _truck_factor,
)


class CalibrationDataTests(unittest.TestCase):
    def test_official_detour_factor_uses_mile_thresholds(self) -> None:
        self.assertEqual(_detour_factor(None), 1.0)
        self.assertEqual(_detour_factor(16.0), 1.0)
        self.assertEqual(_detour_factor(16.09344), 1.5)
        self.assertEqual(_detour_factor(48.4), 2.0)

    def test_official_truck_factor(self) -> None:
        self.assertEqual(_truck_factor(None), 1.0)
        self.assertEqual(_truck_factor(9.9), 1.0)
        self.assertEqual(_truck_factor(10), 1.5)
        self.assertEqual(_truck_factor(20), 1.5)
        self.assertEqual(_truck_factor(20.1), 2.0)

    def test_point_in_polygon_respects_holes(self) -> None:
        polygon = [
            [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
            [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]],
        ]
        self.assertTrue(_point_in_polygon(2, 2, polygon))
        self.assertFalse(_point_in_polygon(5, 5, polygon))
        self.assertFalse(_point_in_polygon(12, 2, polygon))

    def test_historical_reader_repairs_unquoted_comma_in_other_state_id(self) -> None:
        header = (
            "STRUCTURE_NUMBER_008,LOWEST_RATING,OTHR_STATE_STRUC_NO_099,"
            "BRIDGE_CONDITION,YEAR_OF_IMP_097\n"
        )
        row = "0001,5,1sDPG, 2sDIB,F,2024\n"
        with TemporaryDirectory() as directory:
            path = Path(directory) / "history.txt"
            path.write_text(header + row, encoding="latin-1")
            result = _read_historical(path, 2024)

        self.assertEqual(result.loc[0, "bridge_id"], "0001")
        self.assertEqual(result.loc[0, "lowest_rating_2024"], 5)
        self.assertEqual(result.loc[0, "bridge_condition_2024"], "F")
        self.assertEqual(result.loc[0, "year_of_improvement_2024"], 2024)


if __name__ == "__main__":
    unittest.main()
