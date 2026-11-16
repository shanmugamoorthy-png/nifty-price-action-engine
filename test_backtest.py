import unittest
import pandas as pd
from backtest import run_backtest
from price_action import add_price_action_signals

class EngineTests(unittest.TestCase):
    def make_data(self):
        idx = pd.date_range("2026-01-05 09:15", periods=80, freq="5min")
        close = [100 + (i % 10) * 0.2 for i in range(80)]
        return pd.DataFrame({
            "open": close,
            "high": [v + 1 for v in close],
            "low": [v - 1 for v in close],
            "close": close,
            "volume": 0,
        }, index=idx)

    def test_signals_columns(self):
        out = add_price_action_signals(self.make_data())
        self.assertIn("choch_up", out.columns)
        self.assertIn("liquidity_sweep_down", out.columns)
        self.assertIn("signal", out.columns)

    def test_brokerage_not_deducted_from_points(self):
        data = self.make_data()
        _, trades, metrics = run_backtest(
            data, mode="Positional", brokerage_per_round_trip_inr=20,
            slippage_points_per_side=0, stop_points=3, target_points=3
        )
        if not trades.empty:
            self.assertTrue((trades["net_points"] == trades["gross_points"]).all())
            self.assertAlmostEqual(metrics["Brokerage estimate ₹"], len(trades) * 20)

if __name__ == "__main__":
    unittest.main()
