import unittest
import pandas as pd
from price_action import add_price_action_signals

class V44VariantTests(unittest.TestCase):
    def test_v44_variant_columns_exist_and_are_boolean(self):
        idx = pd.date_range("2026-01-05 09:15", periods=120, freq="5min")
        vals = [100 + (i % 17) * 0.6 + (i // 17) * 0.1 for i in range(120)]
        frame = pd.DataFrame({
            "open": vals,
            "high": [v + 1.2 for v in vals],
            "low": [v - 1.2 for v in vals],
            "close": [v + (0.25 if i % 2 else -0.15) for i, v in enumerate(vals)],
        }, index=idx)
        out = add_price_action_signals(frame)
        for col in ("choch_hl_breakout_up", "choch_hl_breakout_retest_up", "choch_hl_strong_breakout_up"):
            self.assertIn(col, out.columns)
            self.assertEqual(out[col].dtype, bool)
        self.assertTrue((out["choch_hl_strong_breakout_up"] <= out["choch_hl_breakout_up"]).all())

if __name__ == "__main__":
    unittest.main()
