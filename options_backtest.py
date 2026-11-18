"""Intraday NIFTY long-option backtest with absolute-contract pinning."""
import pandas as pd


def _metrics(trades, quantity, brokerage):
    if trades.empty:
        return ({"Trades": 0, "Win rate %": 0.0, "Gross P&L ₹": 0.0,
                 "Net P&L after costs ₹": 0.0, "Profit factor": 0.0,
                 "Max drawdown ₹": 0.0, "Brokerage ₹": 0.0}, trades)
    x=trades.sort_values("exit_time").copy()
    x["gross_pnl_inr"]=(x["raw_exit"]-x["raw_entry"])*quantity
    x["slippage_inr"]=x["slippage_points"]*quantity
    x["net_pnl_inr"]=x["gross_pnl_inr"]-x["slippage_inr"]-brokerage
    wins=x.loc[x.net_pnl_inr>0,"net_pnl_inr"].sum(); losses=abs(x.loc[x.net_pnl_inr<0,"net_pnl_inr"].sum())
    eq=x.net_pnl_inr.cumsum(); dd=eq-eq.cummax()
    return ({"Trades":int(len(x)),"Win rate %":round(float((x.net_pnl_inr>0).mean()*100),2),
             "Gross P&L ₹":round(float(x.gross_pnl_inr.sum()),2),
             "Net P&L after costs ₹":round(float(x.net_pnl_inr.sum()),2),
             "Profit factor":round(float(wins/losses),3) if losses else ("∞" if wins else 0.0),
             "Max drawdown ₹":round(float(dd.min()),2),"Brokerage ₹":round(float(brokerage*len(x)),2)},x)


def run_option_buying_backtest(spot_signals, option_bars, option_type, premium_stop, premium_target,
                               quantity=1, slippage_per_side=0.5, brokerage_round_trip=20.0,
                               strike_offset=0, square_off_time="15:15"):
    """Buy next-bar open of the absolute ATM strike selected at signal time.

    Later candles are looked up by the same absolute strike, even when ATM moves.
    `option_bars` must contain multiple offset series with actual `strike` values.
    """
    if option_type not in ("CALL","PUT"): raise ValueError("option_type must be CALL or PUT")
    if premium_stop<=0 or premium_target<=0 or quantity<=0: raise ValueError("Stop, target and quantity must be positive")
    if slippage_per_side<0 or brokerage_round_trip<0: raise ValueError("Costs cannot be negative")
    signal_col="buy_setup" if option_type=="CALL" else "sell_setup"
    if signal_col not in spot_signals: raise ValueError(f"Spot signal column missing: {signal_col}")
    x=option_bars.copy().sort_index()
    required={"open","high","low","close","strike","spot"}
    missing=required-set(x.columns)
    if missing: raise ValueError(f"Option bars missing columns: {sorted(missing)}")
    for c in required: x[c]=pd.to_numeric(x[c],errors="coerce")
    x=x.dropna(subset=["open","high","low","close","strike"])
    if x.empty: return pd.DataFrame(),_metrics(pd.DataFrame(),quantity,brokerage_round_trip)[0]
    x["strike"]=x["strike"].astype(float)
    # Unique (timestamp, absolute strike) contract candles are required.
    x=x.reset_index().rename(columns={x.index.name or "index":"timestamp"})
    if "timestamp" not in x.columns: x=x.rename(columns={x.columns[0]:"timestamp"})
    x["timestamp"]=pd.to_datetime(x["timestamp"])
    x=x.sort_values(["timestamp","strike"]).drop_duplicates(["timestamp","strike"],keep="last")
    by_key={(r.timestamp,float(r.strike)):r for r in x.itertuples(index=False)}
    # Use Dhan's actual ATM series strike at the signal timestamp, not a locally
    # rounded spot approximation. This pins the exact contract selected by Dhan.
    if "offset" in x.columns:
        atm_rows=x.loc[pd.to_numeric(x["offset"],errors="coerce")==0]
    else:
        atm_rows=pd.DataFrame()
    atm_by_time={pd.Timestamp(r.timestamp):float(r.strike) for r in atm_rows.itertuples(index=False)}
    times=sorted(x.timestamp.unique())
    spot=spot_signals.copy().sort_index()
    spot=spot.loc[spot[signal_col].fillna(False).astype(bool)]
    signal_times=set(spot.index)
    if not signal_times: return pd.DataFrame(),_metrics(pd.DataFrame(),quantity,brokerage_round_trip)[0]
    trades=[]; pos=None; last_ts_by_strike={}
    for i,ts in enumerate(times):
        ts=pd.Timestamp(ts)
        # Manage the already-open absolute strike first.
        if pos is not None:
            if ts.date()!=pos["entry_time"].date():
                prev=last_ts_by_strike.get(pos["strike"])
                if prev is not None:
                    rr=by_key.get((prev,pos["strike"]))
                    if rr is not None: trades.append(_close(pos,prev,float(rr.close),"Intraday session square-off",quantity,slippage_per_side,brokerage_round_trip))
                pos=None
            else:
                rr=by_key.get((ts,pos["strike"]))
                if rr is None:
                    # Missing candle for the held contract is not a strike roll. Keep
                    # the position open and wait for its next actual contract candle.
                    pass
                else:
                    op,hi,lo,cl=map(float,(rr.open,rr.high,rr.low,rr.close))
                    sl,tgt=pos["sl"],pos["target"]
                    exit_px=reason=None
                    if op<=sl: exit_px,reason=op,"Premium stop gap"
                    elif op>=tgt: exit_px,reason=tgt,"Premium target gap"
                    elif lo<=sl and hi>=tgt: exit_px,reason=sl,"Premium stop (both touched; conservative)"
                    elif lo<=sl: exit_px,reason=sl,"Premium stop"
                    elif hi>=tgt: exit_px,reason=tgt,"Premium target"
                    elif ts.strftime("%H:%M")>=square_off_time: exit_px,reason=cl,"Intraday square-off"
                    if exit_px is not None:
                        trades.append(_close(pos,ts,exit_px,reason,quantity,slippage_per_side,brokerage_round_trip)); pos=None
                    else: last_ts_by_strike[pos["strike"]]=ts
        # Enter only on the next option candle after a completed spot signal.
        if pos is None and i>0:
            prev_ts=pd.Timestamp(times[i-1])
            if prev_ts in signal_times and ts.date()==prev_ts.date() and ts.strftime("%H:%M")<"15:00":
                # ATM series is the row whose spot-derived ATM equals its actual strike.
                spot_close=float(spot_signals.loc[prev_ts,"close"])
                atm_strike=atm_by_time.get(prev_ts)
                if atm_strike is None:
                    # Legacy/synthetic datasets without offset metadata: use nearest
                    # listed strike to the spot close, but real V4.8 downloads include offset=0.
                    candidates=x.loc[x.timestamp==prev_ts,"strike"]
                    atm_strike=float(candidates.iloc[(candidates-spot_close).abs().argmin()]) if not candidates.empty else None
                # Select the same absolute strike at the next option candle.
                entry_row=by_key.get((ts,float(atm_strike))) if atm_strike is not None else None
                if entry_row is not None:
                    raw=float(entry_row.open); fill=raw+slippage_per_side
                    pos={"option_type":option_type,"direction":f"BUY {option_type}","signal_time":prev_ts,
                         "entry_time":ts,"raw_entry":raw,"entry":fill,"strike":float(atm_strike),
                         "sl":fill-premium_stop,"target":fill+premium_target,
                         "spot_at_signal":spot_close,"signal_reason":str(spot_signals.loc[prev_ts].get("signal_reason",""))}
                    last_ts_by_strike[pos["strike"]]=ts
    if pos is not None:
        last=last_ts_by_strike.get(pos["strike"])
        if last is not None:
            rr=by_key.get((last,pos["strike"]))
            if rr is not None: trades.append(_close(pos,last,float(rr.close),"End of available session data",quantity,slippage_per_side,brokerage_round_trip))
    tdf=pd.DataFrame(trades)
    if not tdf.empty:
        tdf["quantity_units"] = int(quantity)
    metrics,tdf=_metrics(tdf,quantity,brokerage_round_trip)
    return tdf,metrics


def _close(pos,ts,raw_exit,reason,quantity,slippage,brokerage):
    return {**pos,"exit_time":ts,"raw_exit":float(raw_exit),"exit":float(raw_exit)-slippage,
            "reason":reason,"gross_points":float(raw_exit)-float(pos["raw_entry"]),
            "slippage_points":2*slippage,"gross_pnl_inr":(float(raw_exit)-float(pos["raw_entry"]))*quantity,
            "slippage_inr":2*slippage*quantity,"brokerage_inr":float(brokerage),
            "net_pnl_inr":(float(raw_exit)-float(pos["raw_entry"]))*quantity-2*slippage*quantity-brokerage}
