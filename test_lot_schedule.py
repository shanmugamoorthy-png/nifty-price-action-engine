import unittest
from datetime import date
import pandas as pd
from lot_schedule import validate_lot_schedule

class LotScheduleTests(unittest.TestCase):
    def test_valid_contiguous_schedule(self):
        df = pd.DataFrame([
            {"start_date": "2025-12-01", "end_date": "2025-12-31", "lot_size": 75},
            {"start_date": "2026-01-01", "end_date": "2026-05-31", "lot_size": 65},
        ])
        out = validate_lot_schedule(df, date(2025,12,1), date(2026,5,31))
        self.assertEqual(list(out.lot_size), [75,65])

    def test_gap_rejected(self):
        df = pd.DataFrame([
            {"start_date": "2025-12-01", "end_date": "2025-12-20", "lot_size": 75},
            {"start_date": "2025-12-22", "end_date": "2025-12-31", "lot_size": 75},
        ])
        with self.assertRaisesRegex(ValueError, "gap"):
            validate_lot_schedule(df, date(2025,12,1), date(2025,12,31))

    def test_overlap_rejected(self):
        df = pd.DataFrame([
            {"start_date": "2025-12-01", "end_date": "2025-12-20", "lot_size": 75},
            {"start_date": "2025-12-20", "end_date": "2025-12-31", "lot_size": 75},
        ])
        with self.assertRaisesRegex(ValueError, "overlap"):
            validate_lot_schedule(df, date(2025,12,1), date(2025,12,31))

    def test_zero_lot_rejected(self):
        df = pd.DataFrame([{"start_date":"2025-12-01", "end_date":"2025-12-31", "lot_size":0}])
        with self.assertRaisesRegex(ValueError, "positive"):
            validate_lot_schedule(df, date(2025,12,1), date(2025,12,31))

if __name__ == "__main__":
    unittest.main()
