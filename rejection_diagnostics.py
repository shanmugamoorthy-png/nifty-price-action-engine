"""V4.23 per-signal rejection ledger for the unchanged V4.13/V4.18 engine.

The module runs the production option backtester and labels every V4.13 signal.
A residual label is deliberately retained when the engine's internal state cannot
be proven from exported trades alone; the report never invents a rejection cause.
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


def build_v423_signal_ledger(spot_with_signals: pd.DataFrame, option_bars: pd.DataFrame,
                             side: str, start_date, end_date,
                             premium_stop: float = 20.0, premium_target: float = 40.0,
                             square_off_time: str = "15:15") -> tuple[pd.DataFrame, pd.DataFrame]:
    if side not in ("CALL", "PUT"):
        raise ValueError("side must be CALL or PUT")
    spot = _normalise(spot_with_signals)
    opt = _normalise(option_bars)
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize() + pd.Timedelta(days=1)
    spot = spot.loc[(spot.index >= start) & (spot.index < end)].copy()
    opt = opt.loc[(opt.index >= start) & (opt.index < end)].copy()
    sig_col = "v413_buy_retest" if side == "CALL" else "v413_sell_retest"
    setup_col = "buy_setup" if side == "CALL" else "sell_setup"
    if sig_col not in spot:
        raise ValueError(f"Missing signal column: {sig_col}")
    for c in ("open", "high", "low", "close", "strike"):
        if c not in opt:
            raise ValueError(f"Option data missing required column: {c}")
    signals = pd.DatetimeIndex(spot.index[spot[sig_col].fillna(False).astype(bool)])
    opt = opt.dropna(subset=["open", "high", "low", "close", "strike"]).copy()
    opt["strike"] = pd.to_numeric(opt["strike"], errors="coerce")
    if "offset" in opt:
        atm = opt.loc[pd.to_numeric(opt["offset"], errors="coerce").eq(0)]
        atm_map = atm.groupby(level=0).strike.first().to_dict()
    else:
        atm_map = {}
    # Match the production engine's documented legacy fallback: if offset=0 is
    # absent, choose the listed strike nearest to Spot close at the signal time.
    strike_map = {}
    strike_method = {}
    for ts in signals:
        if ts in atm_map:
            strike_map[ts] = float(atm_map[ts]); strike_method[ts] = "Dhan offset=0"
        else:
            listed = opt.loc[opt.index == ts, "strike"]
            if not listed.empty and ts in spot.index:
                spot_close = float(spot.loc[ts, "close"])
                strike_map[ts] = float(listed.iloc[(listed - spot_close).abs().argmin()])
                strike_method[ts] = "Nearest listed strike fallback"
            else:
                strike_map[ts] = None; strike_method[ts] = "Unavailable"
    times = pd.DatetimeIndex(opt.index.unique()).sort_values()
    next_map = {times[i]: times[i+1] for i in range(len(times)-1)
                if times[i].date() == times[i+1].date()}
    # Run exactly the production entry/exit function with costs set to zero.
    s = spot.copy()
    s["buy_setup"] = s[sig_col].fillna(False) if side == "CALL" else False
    s["sell_setup"] = s[sig_col].fillna(False) if side == "PUT" else False
    trades, _ = run_option_buying_backtest(s, opt, side, premium_stop, premium_target,
                                           quantity=1, slippage_per_side=0.0,
                                           brokerage_round_trip=0.0,
                                           max_trades_per_day=1,
                                           square_off_time=square_off_time)
    accepted = {}
    if not trades.empty:
        for r in trades.to_dict("records"):
            accepted[pd.Timestamp(r["signal_time"])] = r
    entered_signal_times = sorted(accepted)
    rows = []
    strike_sets = opt.groupby(level=0).strike.apply(lambda s: set(pd.to_numeric(s, errors="coerce").dropna().astype(float))).to_dict()
    for ts in signals:
        strike = strike_map.get(ts)
        nxt = next_map.get(ts)
        reason = "ACCEPTED"
        candidate = False
        if ts in accepted:
            tr = accepted[ts]
            entry_ts = pd.Timestamp(tr["entry_time"])
            exit_ts = pd.Timestamp(tr["exit_time"])
            entry_strike = float(tr["strike"])
        else:
            entry_ts = nxt
            exit_ts = pd.NaT
            entry_strike = float(strike) if strike is not None else None
            if strike is None:
                reason = "MISSING_ATM_CONTRACT_AT_SIGNAL"
            elif nxt is None:
                reason = "NO_NEXT_OPTION_CANDLE_IN_SESSION"
            elif float(strike) not in strike_sets.get(nxt, set()):
                reason = "SAME_STRIKE_CANDLE_MISSING_ON_NEXT_BAR"
            elif nxt.strftime("%H:%M") >= "15:00":
                reason = "ENTRY_CUTOFF_15_00_OR_LATER"
            elif any(a.date() == ts.date() and a < ts for a in entered_signal_times):
                reason = "DAILY_ENTRY_LIMIT_ALREADY_USED"
            else:
                # Check whether a previously accepted trade occupied the intended entry bar.
                occupying = False
                if not trades.empty:
                    for tr in trades.to_dict("records"):
                        et, xt = pd.Timestamp(tr["entry_time"]), pd.Timestamp(tr["exit_time"])
                        if et <= nxt <= xt:
                            occupying = True
                            break
                reason = "POSITION_ALREADY_OPEN_AT_NEXT_BAR" if occupying else "ENGINE_SEQUENCE_OR_UNEXPORTED_STATE"
                candidate = True
        rows.append({
            "signal_time": ts, "month": str(ts.to_period("M")), "side": side,
            "spot_close_at_signal": float(spot.loc[ts, "close"]),
            "selected_atm_strike": entry_strike,
            "strike_selection_method": strike_method.get(ts, "Unavailable"),
            "expected_next_option_time": nxt,
            "actual_entry_time": entry_ts if ts in accepted else pd.NaT,
            "actual_exit_time": exit_ts,
            "actual_entry_strike": entry_strike if ts in accepted else None,
            "status": "ENTERED" if ts in accepted else "NOT_ENTERED",
            "rejection_reason": reason,
            "next_bar_candidate": bool(candidate or ts in accepted),
            "actual_exit_reason": accepted.get(ts, {}).get("reason", "")
        })
    ledger = pd.DataFrame(rows)
    if ledger.empty:
        summary = pd.DataFrame(columns=["month", "side", "signals", "entered", "not_entered", "missing_atm", "missing_next_timestamp", "missing_same_strike", "daily_limit", "position_open", "cutoff", "engine_residual"])
    else:
        ledger["missing_atm"] = ledger.rejection_reason.eq("MISSING_ATM_CONTRACT_AT_SIGNAL")
        ledger["missing_next_timestamp"] = ledger.rejection_reason.eq("NO_NEXT_OPTION_CANDLE_IN_SESSION")
        ledger["missing_same_strike"] = ledger.rejection_reason.eq("SAME_STRIKE_CANDLE_MISSING_ON_NEXT_BAR")
        ledger["daily_limit"] = ledger.rejection_reason.eq("DAILY_ENTRY_LIMIT_ALREADY_USED")
        ledger["position_open"] = ledger.rejection_reason.eq("POSITION_ALREADY_OPEN_AT_NEXT_BAR")
        ledger["cutoff"] = ledger.rejection_reason.eq("ENTRY_CUTOFF_15_00_OR_LATER")
        ledger["engine_residual"] = ledger.rejection_reason.eq("ENGINE_SEQUENCE_OR_UNEXPORTED_STATE")
        summary = ledger.groupby(["month", "side"], as_index=False).agg(
            signals=("signal_time", "count"), entered=("status", lambda x: int((x == "ENTERED").sum())),
            not_entered=("status", lambda x: int((x == "NOT_ENTERED").sum())),
            missing_atm=("missing_atm", "sum"), missing_next_timestamp=("missing_next_timestamp", "sum"),
            missing_same_strike=("missing_same_strike", "sum"), daily_limit=("daily_limit", "sum"),
            position_open=("position_open", "sum"), cutoff=("cutoff", "sum"), engine_residual=("engine_residual", "sum"))
    return ledger, summary
