import unittest
import pandas as pd
from rejection_diagnostics import build_v423_signal_ledger

class V423Tests(unittest.TestCase):
    def test_signal_is_accepted_on_next_option_bar(self):
        idx=pd.date_range('2026-01-05 09:15',periods=4,freq='5min')
        spot=pd.DataFrame({'open':[100]*4,'high':[102]*4,'low':[99]*4,'close':[101]*4,
            'v413_buy_retest':[True,False,False,False],'v413_sell_retest':[False]*4},index=idx)
        opt=pd.DataFrame({'open':[10,11,12,13],'high':[11,12,13,14],'low':[9,10,11,12],
            'close':[10,11,12,13],'strike':[100]*4,'spot':[101]*4,'offset':[0]*4},index=idx)
        ledger,summary=build_v423_signal_ledger(spot,opt,'CALL','2026-01-05','2026-01-05')
        self.assertEqual(ledger.iloc[0].status,'ENTERED')
        self.assertEqual(int(summary.iloc[0].entered),1)

    def test_missing_offset_zero_uses_production_nearest_strike_fallback(self):
        idx=pd.date_range('2026-01-05 09:15',periods=3,freq='5min')
        spot=pd.DataFrame({'open':[100]*3,'high':[102]*3,'low':[99]*3,'close':[101]*3,
            'v413_buy_retest':[True,False,False],'v413_sell_retest':[False]*3},index=idx)
        opt=pd.DataFrame({'open':[10]*3,'high':[11]*3,'low':[9]*3,'close':[10]*3,
            'strike':[100]*3,'spot':[101]*3,'offset':[1]*3},index=idx)
        ledger,_=build_v423_signal_ledger(spot,opt,'CALL','2026-01-05','2026-01-05')
        self.assertEqual(ledger.iloc[0].status,'ENTERED')
        self.assertEqual(ledger.iloc[0].strike_selection_method,'Nearest listed strike fallback')

if __name__=='__main__': unittest.main()
