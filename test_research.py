import unittest
import pandas as pd
from research import SETUPS, run_setup_isolation

class ResearchTests(unittest.TestCase):
    def test_setup_list_has_directional_separate_streams(self):
        cols = [column for _, column in SETUPS]
        self.assertEqual(len(cols), 17)
        self.assertEqual(len(set(cols)), 17)
        self.assertIn("choch_up", cols)
        self.assertIn("bos_down", cols)

    def test_research_returns_independent_rows(self):
        idx = pd.date_range("2026-01-05 09:15", periods=100, freq="5min")
        vals = [100 + (i % 13) * 0.4 for i in range(100)]
        frame = pd.DataFrame({
            "open": vals, "high": [v+1 for v in vals],
            "low": [v-1 for v in vals], "close": vals
        }, index=idx)
        summary, details = run_setup_isolation(
            frame, modes=("Intraday",), stop_points=3, target_points=5,
            brokerage_per_round_trip_inr=20, slippage_points_per_side=0
        )
        self.assertEqual(len(summary), 17)
        self.assertEqual(len(details), 17)
        self.assertTrue((summary["Brokerage estimate ₹"] >= 0).all())

if __name__ == "__main__":
    unittest.main()
