import unittest
import pandas as pd
from options_backtest import run_option_buying_backtest

class OptionBacktestTests(unittest.TestCase):
    def setUp(self):
        self.idx = pd.date_range('2025-01-02 09:15', periods=5, freq='5min')
        self.spot = pd.DataFrame({
            'buy_setup': [False, True, False, False, False],
            'sell_setup': [False, False, False, False, False],
            'close': [24000, 24020, 24030, 24040, 24050],
            'signal_reason': ['', 'bull', '', '', ''],
        }, index=self.idx)
        self.opt = pd.DataFrame({
            'open': [100, 101, 102, 103, 104],
            'high': [101, 103, 103, 125, 105],
            'low': [99, 100, 101, 102, 103],
            'close': [100, 102, 120, 103, 104],
            'strike': [24000]*5,
            'spot': [24000, 24020, 24030, 24040, 24050],
        }, index=self.idx)

    def test_bullish_signal_buys_call_and_target(self):
        trades, metrics = run_option_buying_backtest(
            self.spot, self.opt, 'CALL', premium_stop=10, premium_target=15,
            quantity=50, slippage_per_side=0.5, brokerage_round_trip=40)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades.iloc[0]['direction'], 'BUY CALL')
        self.assertEqual(trades.iloc[0]['reason'], 'Premium target')
        self.assertAlmostEqual(metrics['Net P&L after costs ₹'], 685.0)

    def test_put_uses_bearish_signal(self):
        spot = self.spot.copy()
        spot['sell_setup'] = [False, True, False, False, False]
        trades, _ = run_option_buying_backtest(
            spot, self.opt, 'PUT', premium_stop=10, premium_target=50,
            quantity=1, slippage_per_side=0, brokerage_round_trip=0)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades.iloc[0]['direction'], 'BUY PUT')

    def test_no_overnight_hold_on_strike_change(self):
        opt = self.opt.copy()
        opt.loc[self.idx[3], 'strike'] = 24050
        trades, _ = run_option_buying_backtest(
            self.spot, opt, 'CALL', premium_stop=50, premium_target=100,
            quantity=1, slippage_per_side=0, brokerage_round_trip=0)
        self.assertEqual(len(trades), 1)
        self.assertIn('strike changed', trades.iloc[0]['reason'])

if __name__ == '__main__':
    unittest.main()
