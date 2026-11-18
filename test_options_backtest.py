import unittest
import pandas as pd
from options_backtest import run_option_buying_backtest

class OptionBacktestTests(unittest.TestCase):
    def setUp(self):
        self.idx=pd.date_range('2025-01-02 09:15',periods=5,freq='5min')
        self.spot=pd.DataFrame({'buy_setup':[False,True,False,False,False],
            'sell_setup':[False,False,False,False,False],
            'close':[24000,24020,24030,24040,24050],
            'signal_reason':['','bull','','','']},index=self.idx)
        self.opt=pd.DataFrame({'open':[100,101,102,103,104],
            'high':[101,103,103,125,105],'low':[99,100,101,102,103],
            'close':[100,102,120,103,104],'strike':[24000]*5,
            'spot':[24000,24020,24030,24040,24050],'offset':[0]*5},index=self.idx)

    def test_bullish_signal_buys_call_and_target(self):
        trades,metrics=run_option_buying_backtest(self.spot,self.opt,'CALL',10,15,
            quantity=50,slippage_per_side=0.5,brokerage_round_trip=40)
        self.assertEqual(len(trades),1)
        self.assertEqual(trades.iloc[0]['direction'],'BUY CALL')
        self.assertEqual(trades.iloc[0]['reason'],'Premium target')
        self.assertAlmostEqual(metrics['Net P&L after costs ₹'],685.0)

    def test_put_uses_bearish_signal(self):
        spot=self.spot.copy(); spot['sell_setup']=[False,True,False,False,False]
        trades,_=run_option_buying_backtest(spot,self.opt,'PUT',10,50,quantity=1,slippage_per_side=0,brokerage_round_trip=0)
        self.assertEqual(len(trades),1); self.assertEqual(trades.iloc[0]['direction'],'BUY PUT')

    def test_absolute_strike_remains_open_when_atm_rolls(self):
        # At signal time the ATM contract is 24000. At later timestamps the
        # ATM moves to 24050, but the 24000 contract candles remain available.
        rows=[]
        for ts,atm,price in zip(self.idx,[24000,24000,24050,24050,24050],[100,101,102,103,104]):
            rows.append({'timestamp':ts,'strike':24000,'offset':int((24000-atm)/50),'open':price,'high':price+1,'low':price-1,'close':price,'spot':atm})
            rows.append({'timestamp':ts,'strike':atm,'offset':0,'open':price+5,'high':price+6,'low':price+4,'close':price+5,'spot':atm})
        opt=pd.DataFrame(rows).set_index('timestamp').sort_index()
        trades,_=run_option_buying_backtest(self.spot,opt,'CALL',50,100,quantity=1,slippage_per_side=0,brokerage_round_trip=0)
        self.assertEqual(len(trades),1)
        self.assertEqual(float(trades.iloc[0]['strike']),24000.0)
        self.assertNotIn('strike changed',trades.iloc[0]['reason'].lower())

    def test_empty_trades_returns_valid_metrics(self):
        spot=self.spot.copy(); spot['buy_setup']=False
        trades,metrics=run_option_buying_backtest(spot,self.opt,'CALL',10,20)
        self.assertTrue(trades.empty); self.assertEqual(metrics['Trades'],0)

if __name__=='__main__': unittest.main()

class LotSizingTests(unittest.TestCase):
    def test_pnl_scales_with_contract_quantity(self):
        idx=pd.date_range('2025-01-02 09:15',periods=4,freq='5min')
        spot=pd.DataFrame({'buy_setup':[False,True,False,False], 'sell_setup':[False]*4,
                           'close':[24000,24020,24030,24040], 'signal_reason':['','bull','','']},index=idx)
        opt=pd.DataFrame({'open':[100,101,102,103], 'high':[101,103,103,125],
                          'low':[99,100,101,102], 'close':[100,102,120,103],
                          'strike':[24000]*4,'spot':[24000,24020,24030,24040],
                          'offset':[0]*4},index=idx)
        trades,metrics=run_option_buying_backtest(spot,opt,'CALL',10,15,quantity=65,
                                                   slippage_per_side=0.5,brokerage_round_trip=40)
        self.assertEqual(len(trades),1)
        self.assertEqual(int(trades.iloc[0]['quantity_units']),65)
        self.assertAlmostEqual(metrics['Gross P&L ₹'],1007.5)
        self.assertAlmostEqual(metrics['Brokerage ₹'],40.0)
