"""V4.19 monthly signal-to-option matching diagnostics; read-only."""
from __future__ import annotations
import pandas as pd


def _normalise_option_frame(frame: pd.DataFrame) -> pd.DataFrame:
    x = frame.copy()
    x.index = pd.to_datetime(x.index)
    if getattr(x.index, "tz", None) is not None:
        x.index = x.index.tz_convert("Asia/Kolkata").tz_localize(None)
    x.columns = [str(c).lower() for c in x.columns]
    return x.sort_index()


def _side_month_stats(signals: pd.DatetimeIndex, options: pd.DataFrame, month_start, month_end):
    o = options.loc[(options.index >= month_start) & (options.index < month_end)].copy()
    sig = pd.DatetimeIndex(signals)
    sig = sig[(sig >= month_start) & (sig < month_end)]
    if o.empty:
        return {
            "option_rows": 0, "option_dates": 0, "unique_option_timestamps": 0,
            "unique_strikes": 0, "atm_rows_offset0": 0, "signals": len(sig),
            "signals_with_atm_at_signal": 0, "signals_with_next_candle_same_strike": 0,
            "signals_missing_atm": len(sig), "signals_missing_next_entry": 0,
        }
    for col in ("strike", "offset"):
        if col in o.columns:
            o[col] = pd.to_numeric(o[col], errors="coerce")
    times = pd.DatetimeIndex(o.index.unique()).sort_values()
    unique_strikes = int(o["strike"].nunique()) if "strike" in o.columns else 0
    atm_rows = o[pd.to_numeric(o["offset"], errors="coerce").eq(0)] if "offset" in o.columns else o.iloc[0:0]
    atm_map = atm_rows.groupby(level=0)["strike"].first().to_dict() if "strike" in atm_rows.columns else {}
    time_set = set(times)
    next_map = {}
    for i, ts in enumerate(times[:-1]):
        nxt = times[i + 1]
        if nxt.date() == ts.date():
            next_map[ts] = nxt
    strike_sets = o.groupby(level=0)["strike"].apply(lambda s: set(pd.to_numeric(s, errors="coerce").dropna().astype(float))) if "strike" in o.columns else {}
    matched_atm = 0
    matched_next = 0
    missing_atm = 0
    missing_next = 0
    for ts in sig:
        atm = atm_map.get(ts)
        if atm is None:
            missing_atm += 1
            continue
        matched_atm += 1
        nxt = next_map.get(ts)
        if nxt is not None and float(atm) in strike_sets.get(nxt, set()):
            matched_next += 1
        else:
            missing_next += 1
    return {
        "option_rows": int(len(o)), "option_dates": int(o.index.normalize().nunique()),
        "unique_option_timestamps": int(o.index.nunique()), "unique_strikes": unique_strikes,
        "atm_rows_offset0": int(len(atm_rows)), "signals": int(len(sig)),
        "signals_with_atm_at_signal": int(matched_atm),
        "signals_with_next_candle_same_strike": int(matched_next),
        "signals_missing_atm": int(missing_atm), "signals_missing_next_entry": int(missing_next),
    }


def build_monthly_historical_diagnostic(spot_with_signals: pd.DataFrame, call_bars: pd.DataFrame,
                                       put_bars: pd.DataFrame, start_date, end_date) -> pd.DataFrame:
    """Return month rows for spot bars/signals and CALL/PUT option matchability.

    This is diagnostic only: it does not run, modify, or optimise any trades.
    """
    spot = spot_with_signals.copy()
    spot.index = pd.to_datetime(spot.index)
    if getattr(spot.index, "tz", None) is not None:
        spot.index = spot.index.tz_convert("Asia/Kolkata").tz_localize(None)
    call = _normalise_option_frame(call_bars)
    put = _normalise_option_frame(put_bars)
    start = pd.Timestamp(start_date).normalize()
    end_exclusive = pd.Timestamp(end_date).normalize() + pd.Timedelta(days=1)
    spot = spot.loc[(spot.index >= start) & (spot.index < end_exclusive)]
    call = call.loc[(call.index >= start) & (call.index < end_exclusive)]
    put = put.loc[(put.index >= start) & (put.index < end_exclusive)]
    months = pd.period_range(start=start.to_period("M"), end=(end_exclusive - pd.Timedelta(days=1)).to_period("M"), freq="M")
    rows = []
    for period in months:
        ms = period.start_time
        me = period.end_time + pd.Timedelta(nanoseconds=1)
        sd = spot.loc[(spot.index >= ms) & (spot.index < me)]
        buy = pd.DatetimeIndex(sd.index[sd.get("v413_buy_retest", pd.Series(False, index=sd.index)).fillna(False).astype(bool)])
        sell = pd.DatetimeIndex(sd.index[sd.get("v413_sell_retest", pd.Series(False, index=sd.index)).fillna(False).astype(bool)])
        cs = _side_month_stats(buy, call, ms, me)
        ps = _side_month_stats(sell, put, ms, me)
        rows.append({
            "month": str(period), "spot_rows_5m": int(len(sd)),
            "spot_trading_dates": int(sd.index.normalize().nunique()),
            "CALL_spot_signals": int(len(buy)), **{f"CALL_{k}": v for k, v in cs.items()},
            "PUT_spot_signals": int(len(sell)), **{f"PUT_{k}": v for k, v in ps.items()},
        })
    return pd.DataFrame(rows)
