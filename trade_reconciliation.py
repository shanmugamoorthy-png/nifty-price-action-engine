"""V4.21 monthly reconciliation of V4.13 signals to actual backtest entries.
Read-only: uses saved frames and the production backtest function; makes no API calls.
"""
from __future__ import annotations
import pandas as pd
from options_backtest import run_option_buying_backtest


def _normalise(frame: pd.DataFrame) -> pd.DataFrame:
    x = frame.copy()
    x.index = pd.to_datetime(x.index)
    if getattr(x.index, "tz", None) is not None:
        x.index = x.index.tz_convert("Asia/Kolkata").tz_localize(None)
    x.columns = [str(c).lower().strip() for c in x.columns]
    return x.sort_index()


def _side(spot: pd.DataFrame, options: pd.DataFrame, side: str, start, end,
          premium_stop: float, premium_target: float) -> pd.DataFrame:
    sig_col = "v413_buy_retest" if side == "CALL" else "v413_sell_retest"
    setup_col = "buy_setup" if side == "CALL" else "sell_setup"
    signals = pd.DatetimeIndex(spot.index[spot[sig_col].fillna(False).astype(bool)])
    opt = _normalise(options)
    opt = opt.loc[(opt.index >= start) & (opt.index < end)]
    opt = opt.dropna(subset=[c for c in ("open", "high", "low", "close", "strike") if c in opt.columns])
    if "offset" in opt.columns:
        atm = opt.loc[pd.to_numeric(opt.offset, errors="coerce").eq(0)]
        atm_map = atm.groupby(level=0).strike.first().to_dict()
    else:
        atm_map = {}
    strike_sets = opt.groupby(level=0).strike.apply(lambda s: set(pd.to_numeric(s, errors="coerce").dropna().astype(float))).to_dict() if "strike" in opt else {}
    times = pd.DatetimeIndex(opt.index.unique()).sort_values()
    next_map = {times[i]: times[i+1] for i in range(len(times)-1) if times[i].date() == times[i+1].date()}
    counts = {"signals": len(signals), "atm_missing": 0, "next_timestamp_missing": 0,
              "same_strike_next_bar_missing": 0, "candidate_next_bar_available": 0}
    for ts in signals:
        strike = atm_map.get(ts)
        if strike is None:
            counts["atm_missing"] += 1
            continue
        nxt = next_map.get(ts)
        if nxt is None:
            counts["next_timestamp_missing"] += 1
        elif float(strike) not in strike_sets.get(nxt, set()):
            counts["same_strike_next_bar_missing"] += 1
        else:
            counts["candidate_next_bar_available"] += 1
    # Run the exact production entry/exit engine with zero costs and one unit.
    # Costs do not affect whether an entry occurs; one-trade-per-day matches V4.18.
    s = spot.copy()
    s["buy_setup"] = s["v413_buy_retest"].fillna(False) if side == "CALL" else False
    s["sell_setup"] = s["v413_sell_retest"].fillna(False) if side == "PUT" else False
    trades, _ = run_option_buying_backtest(s, opt, side, premium_stop, premium_target,
                                          quantity=1, slippage_per_side=0.0,
                                          brokerage_round_trip=0.0,
                                          max_trades_per_day=1)
    entries = pd.DatetimeIndex(pd.to_datetime(trades["entry_time"])) if not trades.empty else pd.DatetimeIndex([])
    entry_month_counts = pd.Series(entries.to_period("M")).value_counts().to_dict() if len(entries) else {}
    rows=[]
    for period in pd.period_range(start=start.to_period("M"), end=(end-pd.Timedelta(nanoseconds=1)).to_period("M"), freq="M"):
        ms, me = period.start_time, period.end_time + pd.Timedelta(nanoseconds=1)
        month_signals = signals[(signals >= ms) & (signals < me)]
        month_entries = int(sum(1 for t in entries if ms <= t < me))
        # Matchability counts are re-evaluated by timestamp to retain monthly attribution.
        a=n=sm=0
        for ts in month_signals:
            strike=atm_map.get(ts)
            if strike is None: continue
            a += 1
            nxt=next_map.get(ts)
            if nxt is None: n += 1
            elif float(strike) not in strike_sets.get(nxt,set()): sm += 1
        rows.append({"month":str(period),"side":side,"spot_signals":len(month_signals),
                     "atm_missing":len(month_signals)-a,"next_timestamp_missing":n,
                     "same_strike_next_bar_missing":sm,
                     "next_bar_candidates":max(0,a-n-sm),"actual_backtest_entries":month_entries,
                     "signals_not_entered_by_full_backtest":max(0,len(month_signals)-month_entries)})
    return pd.DataFrame(rows)


def build_v421_reconciliation(spot_with_signals: pd.DataFrame, call_bars: pd.DataFrame,
                              put_bars: pd.DataFrame, start_date, end_date,
                              premium_stop=20.0, premium_target=40.0) -> pd.DataFrame:
    spot=_normalise(spot_with_signals)
    start=pd.Timestamp(start_date).normalize()
    end=pd.Timestamp(end_date).normalize()+pd.Timedelta(days=1)
    spot=spot.loc[(spot.index>=start)&(spot.index<end)]
    call=_side(spot,call_bars,"CALL",start,end,premium_stop,premium_target)
    put=_side(spot,put_bars,"PUT",start,end,premium_stop,premium_target)
    return pd.concat([call,put],ignore_index=True)
