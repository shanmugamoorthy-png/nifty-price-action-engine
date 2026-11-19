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


def test_v424_csv_streaming_matches_in_memory_and_resumes(tmp_path):
    from v424_backtest import run_v424_csv_resumable
    spot, opt, schedule = _frames()
    csv_path = tmp_path / "call.csv"
    opt.reset_index(names="timestamp").to_csv(csv_path, index=False)
    expected, expected_metrics, expected_monthly = run_v424_backtest(
        spot, opt, "CALL", schedule, "2026-01-01", "2026-01-31")
    got, metrics, monthly, done, total, missing = run_v424_csv_resumable(
        spot, str(csv_path), "CALL", schedule, "2026-01-01", "2026-01-31",
        str(tmp_path), chunksize=2)
    assert len(got) == len(expected) == 1
    assert metrics["Trades"] == expected_metrics["Trades"]
    assert monthly.equals(expected_monthly)
    assert (done, total) == (1, 1)
    assert missing.empty
    # Second run must load the checkpoint rather than duplicate trades.
    got2, _, _, done2, total2, missing2 = run_v424_csv_resumable(
        spot, str(csv_path), "CALL", schedule, "2026-01-01", "2026-01-31",
        str(tmp_path), chunksize=2)
    assert len(got2) == 1
    assert (done2, total2) == (1, 1)
    assert missing2.empty


def test_v424_missing_option_day_without_signal_is_not_fatal(tmp_path):
    from v424_backtest import run_v424_csv_resumable
    spot, opt, schedule = _frames()
    # Add a second Spot session without a matching option session or signal.
    extra_idx = pd.to_datetime(["2026-01-06 09:15"])
    extra = pd.DataFrame({"open":[100],"high":[101],"low":[99],"close":[100],
        "v413_buy_retest":[False],"v413_sell_retest":[False]}, index=extra_idx)
    spot2 = pd.concat([spot, extra])
    csv_path = tmp_path / "call.csv"
    opt.reset_index(names="timestamp").to_csv(csv_path, index=False)
    got, metrics, monthly, done, total, missing = run_v424_csv_resumable(
        spot2, str(csv_path), "CALL", schedule, "2026-01-01", "2026-01-31",
        str(tmp_path), chunksize=2)
    assert done == total == 2
    assert missing.empty
    assert metrics["Trades"] == 1


def test_v424_missing_option_day_with_signal_is_reported_not_fatal(tmp_path):
    from v424_backtest import run_v424_csv_resumable
    spot, opt, schedule = _frames()
    extra_idx = pd.to_datetime(["2026-01-06 09:15"])
    extra = pd.DataFrame({"open":[100],"high":[101],"low":[99],"close":[100],
        "v413_buy_retest":[True],"v413_sell_retest":[False]}, index=extra_idx)
    spot2 = pd.concat([spot, extra])
    csv_path = tmp_path / "call.csv"
    opt.reset_index(names="timestamp").to_csv(csv_path, index=False)
    got, metrics, monthly, done, total, missing = run_v424_csv_resumable(
        spot2, str(csv_path), "CALL", schedule, "2026-01-01", "2026-01-31",
        str(tmp_path), chunksize=2)
    assert done == total == 2
    assert len(missing) == 1
    assert missing.iloc[0].status == "MISSING_OPTION_DATA"
    assert metrics["Trades"] == 1
