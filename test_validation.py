import unittest
import pandas as pd
from validation import max_consecutive_losses, monthly_summary, run_walk_forward

class ValidationTests(unittest.TestCase):
    def test_max_consecutive_losses(self):
        trades = pd.DataFrame({"exit_time": pd.date_range("2025-01-01", periods=5),
                               "net_points": [2, -1, -3, 4, -2]})
        self.assertEqual(max_consecutive_losses(trades), 2)

    def test_monthly_summary(self):
        trades = pd.DataFrame({"exit_time": pd.to_datetime(["2025-01-02", "2025-01-03", "2025-02-01"]),
                               "net_points": [5, -2, 3], "brokerage_inr": [20, 20, 20]})
        out = monthly_summary(trades)
        self.assertEqual(list(out["Month"]), ["2025-01", "2025-02"])
        self.assertEqual(int(out.iloc[0]["Trades"]), 2)
        self.assertEqual(float(out.iloc[0]["Net points"]), 3.0)

    def test_monthly_empty(self):
        self.assertTrue(monthly_summary(pd.DataFrame()).empty)

if __name__ == "__main__":
    unittest.main()
