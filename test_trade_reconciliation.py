import unittest
import pandas as pd
from trade_reconciliation import build_v421_reconciliation

class TradeReconciliationTests(unittest.TestCase):
    def test_empty_signal_months_are_reported(self):
        idx = pd.date_range('2026-01-05 09:15', periods=3, freq='5min')
        spot = pd.DataFrame({'open':[100,101,102], 'high':[101,102,103], 'low':[99,100,101],
                             'close':[100,101,102], 'spot':[100,101,102],
                             'v413_buy_retest':[False]*3, 'v413_sell_retest':[False]*3}, index=idx)
        opt = pd.DataFrame({'open':[10,11,12], 'high':[11,12,13], 'low':[9,10,11],
                            'close':[10,11,12], 'strike':[100,100,100], 'spot':[100,101,102],
                            'offset':[0,0,0]}, index=idx)
        result = build_v421_reconciliation(spot, opt, opt, '2026-01-01', '2026-01-31')
        self.assertEqual(len(result), 2)
        self.assertEqual(int(result['spot_signals'].sum()), 0)
        self.assertEqual(int(result['actual_backtest_entries'].sum()), 0)

    def test_signal_with_missing_atm_is_counted(self):
        idx = pd.date_range('2026-01-05 09:15', periods=3, freq='5min')
        spot = pd.DataFrame({'open':[100,101,102], 'high':[101,102,103], 'low':[99,100,101],
                             'close':[100,101,102], 'spot':[100,101,102],
                             'v413_buy_retest':[True,False,False], 'v413_sell_retest':[False]*3}, index=idx)
        opt = pd.DataFrame({'open':[10,11,12], 'high':[11,12,13], 'low':[9,10,11],
                            'close':[10,11,12], 'strike':[100,100,100], 'spot':[100,101,102],
                            'offset':[1,1,1]}, index=idx)
        result = build_v421_reconciliation(spot, opt, opt, '2026-01-01', '2026-01-31')
        call = result[result.side == 'CALL'].iloc[0]
        self.assertEqual(int(call.spot_signals), 1)
        self.assertEqual(int(call.atm_missing), 1)

if __name__ == '__main__':
    unittest.main()
