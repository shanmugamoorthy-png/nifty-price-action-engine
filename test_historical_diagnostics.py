import unittest
import pandas as pd
from historical_diagnostics import build_monthly_historical_diagnostic

class HistoricalDiagnosticTests(unittest.TestCase):
    def test_monthly_signal_and_entry_matching_counts(self):
        idx = pd.to_datetime(["2026-01-05 09:15", "2026-01-05 09:20", "2026-01-05 09:25"])
        spot = pd.DataFrame({"v413_buy_retest": [True, False, False], "v413_sell_retest": [False, True, False]}, index=idx)
        def opt(offset):
            return pd.DataFrame({"strike": [25000, 25000, 25000], "offset": [offset, offset, offset], "open": [100, 101, 102], "high": [102, 103, 104], "low": [99, 100, 101], "close": [101, 102, 103], "spot": [25000]*3}, index=idx)
        result = build_monthly_historical_diagnostic(spot, opt(0), opt(0), "2026-01-01", "2026-01-31")
        jan = result.iloc[0]
        self.assertEqual(jan["CALL_spot_signals"], 1)
        self.assertEqual(jan["CALL_signals_with_next_candle_same_strike"], 1)
        self.assertEqual(jan["PUT_spot_signals"], 1)
        self.assertEqual(jan["PUT_signals_with_atm_at_signal"], 1)

if __name__ == "__main__":
    unittest.main()
