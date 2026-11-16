import unittest
import numpy as np
import pandas as pd
from price_action import add_price_action_signals
from research import SETUPS

class V45Tests(unittest.TestCase):
    def frame(self):
        idx = pd.date_range('2026-01-05 09:15', periods=240, freq='5min')
        base = 100 + np.sin(np.arange(240) / 4) * 5 + np.arange(240) * 0.015
        return pd.DataFrame({
            'open': base - 0.2,
            'high': base + 1.0,
            'low': base - 1.0,
            'close': base + np.where(np.arange(240) % 2, 0.6, -0.1),
        }, index=idx)

    def test_threshold_columns_exist_and_are_nested(self):
        out = add_price_action_signals(self.frame())
        cols = [f'choch_hl_strong_breakout_{n}_up' for n in (40, 50, 60)]
        for c in cols:
            self.assertIn(c, out.columns)
            self.assertEqual(out[c].dtype, bool)
        self.assertTrue((out[cols[1]] <= out[cols[0]]).all())
        self.assertTrue((out[cols[2]] <= out[cols[1]]).all())

    def test_v44_benchmark_is_exact_50_percent_alias(self):
        out = add_price_action_signals(self.frame())
        self.assertTrue((out['choch_hl_strong_breakout_up'] == out['choch_hl_strong_breakout_50_up']).all())

    def test_retest_and_threshold_setups_registered(self):
        registered = dict(SETUPS)
        for c in ['choch_hl_breakout_retest_up', 'choch_hl_strong_breakout_40_up',
                  'choch_hl_strong_breakout_50_up', 'choch_hl_strong_breakout_60_up']:
            self.assertIn(c, registered.values())

if __name__ == '__main__':
    unittest.main()
