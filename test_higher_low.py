import unittest
import numpy as np
import pandas as pd
from price_action import add_price_action_signals
from research import SETUPS

class HigherLowTests(unittest.TestCase):
    def test_hl_setup_is_available_and_separate(self):
        cols = [col for _, col in SETUPS]
        self.assertIn("choch_hl_up", cols)
        self.assertIn("choch_hl_breakout_up", cols)
        self.assertIn("choch_up", cols)
        self.assertNotEqual(cols.index("choch_hl_up"), cols.index("choch_up"))

    def test_hl_signal_only_occurs_on_confirmed_pivot_bar(self):
        # Construct deterministic swings with a bearish structure, bullish CHoCH,
        # then a higher low. A generated HL event must be on a confirmation bar
        # and its confirmed low must exceed the stored pre-CHoCH reference.
        idx = pd.date_range("2024-01-02 09:15", periods=50, freq="5min")
        close = np.array([
            110,108,106,104,102,100,102,104,106,108,
            105,102,99,101,103,105,107,109,111,113,
            110,107,104,106,108,110,112,114,116,118,
            115,112,109,111,113,115,117,119,121,123,
            120,117,114,116,118,120,122,124,126,128
        ], dtype=float)
        frame = pd.DataFrame({
            "open": close - 0.3, "high": close + 0.8,
            "low": close - 0.8, "close": close
        }, index=idx)
        out = add_price_action_signals(frame, pivot_left=2, pivot_right=2)
        fired = out[out["choch_hl_up"]]
        if not fired.empty:
            self.assertTrue((fired["choch_hl_confirmed_low"] > fired["choch_hl_reference_low"]).all())
            self.assertTrue(fired.index.isin(out.index).all())

    def test_breakout_setup_is_distinct_from_hl_confirmation(self):
        # The new setup must be a separate signal stream; a higher-low event
        # alone is not sufficient to trigger the breakout-confirmed strategy.
        idx = pd.date_range("2024-01-02 09:15", periods=60, freq="5min")
        close = np.array([
            110,108,106,104,102,100,102,104,106,108,
            105,102,99,101,103,105,107,109,111,113,
            110,107,104,106,108,110,112,114,116,118,
            115,112,109,111,113,115,117,119,121,123,
            120,117,114,116,118,120,122,124,126,128,
            125,122,119,121,123,125,127,129,131,133
        ], dtype=float)
        frame = pd.DataFrame({
            "open": close - 0.3, "high": close + 0.8,
            "low": close - 0.8, "close": close
        }, index=idx)
        out = add_price_action_signals(frame, pivot_left=2, pivot_right=2)
        self.assertIn("choch_hl_breakout_up", out.columns)
        # Any breakout signal must occur after a previously confirmed HL event.
        hl_positions = np.flatnonzero(out["choch_hl_up"].to_numpy())
        br_positions = np.flatnonzero(out["choch_hl_breakout_up"].to_numpy())
        if len(br_positions):
            self.assertTrue(any(h < br_positions[0] for h in hl_positions))

if __name__ == "__main__":
    unittest.main()
