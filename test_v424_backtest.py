import pandas as pd
import pytest
from v424_backtest import run_v424_backtest


def _frames():
    idx = pd.to_datetime(["2026-01-05 09:15", "2026-01-05 09:20", "2026-01-05 09:25"])
    spot = pd.DataFrame({"open":[100,101,102],"high":[102,103,104],"low":[99,100,101],"close":[101,102,103],
                         "v413_buy_retest":[True,False,False],"v413_sell_retest":[False,False,False]}, index=idx)
    opt = pd.DataFrame({"open":[50,51,52],"high":[51,53,54],"low":[49,50,51],"close":[50,52,53],
                        "strike":[100,100,100],"spot":[101,102,103],"offset":[0,0,0]}, index=idx)
    schedule = pd.DataFrame([{"start_date":"2026-01-01","end_date":"2026-01-31","lot_size":65}])
    return spot, opt, schedule


def test_v424_call_trade_applies_historical_lot_size_and_costs():
    spot, opt, schedule = _frames()
    trades, metrics, monthly = run_v424_backtest(spot, opt, "CALL", schedule, "2026-01-01", "2026-01-31",
        lots=1, premium_stop=20, premium_target=40, slippage_per_side=0.5, brokerage_round_trip=40)
    assert len(trades) == 1
    assert trades.iloc[0].lot_size == 65
    assert trades.iloc[0].quantity_units == 65
    assert metrics["Trades"] == 1
    assert metrics["Slippage ₹"] == 65
    assert metrics["Brokerage ₹"] == 40
    assert len(monthly) == 1


def test_v424_rejects_missing_option_trading_day():
    spot, opt, schedule = _frames()
    opt = opt.iloc[:0]
    with pytest.raises(ValueError, match="No CALL weekly option candles"):
        run_v424_backtest(spot, opt, "CALL", schedule, "2026-01-01", "2026-01-31")


def test_v424_rejects_lot_schedule_gap():
    spot, opt, schedule = _frames()
    schedule.loc[0, "end_date"] = "2026-01-10"
    with pytest.raises(ValueError, match="does not cover"):
        run_v424_backtest(spot, opt, "CALL", schedule, "2026-01-01", "2026-01-31")
