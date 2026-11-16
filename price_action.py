import numpy as np
import pandas as pd

def _confirmed_pivots(high, low, left=3, right=3):
    n = len(high)
    new_high = np.full(n, np.nan)
    new_low = np.full(n, np.nan)
    for confirm_idx in range(left + right, n):
        pivot_idx = confirm_idx - right
        h_left = high[pivot_idx-left:pivot_idx]
        h_right = high[pivot_idx+1:pivot_idx+right+1]
        l_left = low[pivot_idx-left:pivot_idx]
        l_right = low[pivot_idx+1:pivot_idx+right+1]
        if len(h_left) == left and len(h_right) == right:
            if high[pivot_idx] > max(h_left.max(), h_right.max()):
                new_high[confirm_idx] = high[pivot_idx]
            if low[pivot_idx] < min(l_left.min(), l_right.min()):
                new_low[confirm_idx] = low[pivot_idx]
    return new_high, new_low

def add_price_action_signals(df, pivot_left=3, pivot_right=3, orb_bars=3):
    """
    Causal price-action engine. A pivot is exposed only after pivot_right bars close.
    Signal set: BOS, CHoCH, opening-range break, previous-day break, liquidity sweep.
    No indicators, OI, volume, moving averages or extra breakout-distance filter.
    """
    x = df.copy().sort_index()
    x.columns = [str(c).lower().strip() for c in x.columns]
    required = {"open", "high", "low", "close"}
    if not required.issubset(x.columns):
        raise ValueError(f"OHLC data must contain {sorted(required)}")
    x = x[~x.index.duplicated(keep="last")]
    x = x.dropna(subset=["open", "high", "low", "close"])
    x["session_date"] = pd.Index(x.index.date)

    daily = x.groupby("session_date").agg(day_high=("high", "max"), day_low=("low", "min"))
    daily["prev_day_high"] = daily["day_high"].shift(1)
    daily["prev_day_low"] = daily["day_low"].shift(1)
    x["prev_day_high"] = x["session_date"].map(daily["prev_day_high"])
    x["prev_day_low"] = x["session_date"].map(daily["prev_day_low"])

    # Opening range is first `orb_bars` 5-minute bars from 09:15. Levels are
    # only actionable from 09:30 onward, so no intraday look-ahead is used.
    x["orb_high"] = np.nan
    x["orb_low"] = np.nan
    for day, idx in x.groupby("session_date").groups.items():
        day_frame = x.loc[idx]
        opening = day_frame.between_time("09:15", "09:15", inclusive="both")
        # Select the first N rows of the session rather than relying on exact bar count.
        opening = day_frame.iloc[:orb_bars]
        if len(opening):
            x.loc[idx, "orb_high"] = float(opening["high"].max())
            x.loc[idx, "orb_low"] = float(opening["low"].min())

    high = x["high"].to_numpy(dtype=float)
    low = x["low"].to_numpy(dtype=float)
    close = x["close"].to_numpy(dtype=float)
    n = len(x)
    pivot_h, pivot_l = _confirmed_pivots(high, low, pivot_left, pivot_right)
    x["new_swing_high"] = pivot_h
    x["new_swing_low"] = pivot_l

    last_high = np.nan
    last_low = np.nan
    previous_high = np.nan
    previous_low = np.nan
    trend = 0
    trend_labels, bos_up, bos_down, choch_up, choch_down = [], [], [], [], []
    sweep_up, sweep_down = [], []
    active_high, active_low = [], []
    for i in range(n):
        if np.isfinite(pivot_h[i]):
            previous_high, last_high = last_high, pivot_h[i]
        if np.isfinite(pivot_l[i]):
            previous_low, last_low = last_low, pivot_l[i]
        active_high.append(last_high)
        active_low.append(last_low)
        bu = bd = cu = cd = su = sd = False
        if i > 0:
            prior_close = close[i-1]
            if np.isfinite(last_high) and prior_close <= last_high < close[i]:
                if trend == -1:
                    cu = True
                else:
                    bu = True
                trend = 1
            elif np.isfinite(last_low) and prior_close >= last_low > close[i]:
                if trend == 1:
                    cd = True
                else:
                    bd = True
                trend = -1
            # Liquidity sweep: pierce a confirmed swing and close back inside.
            if np.isfinite(last_high) and high[i] > last_high and close[i] < last_high and close[i] < x["open"].iloc[i]:
                sd = True
            if np.isfinite(last_low) and low[i] < last_low and close[i] > last_low and close[i] > x["open"].iloc[i]:
                su = True
        trend_labels.append(trend)
        bos_up.append(bu); bos_down.append(bd)
        choch_up.append(cu); choch_down.append(cd)
        sweep_up.append(su); sweep_down.append(sd)

    x["swing_high"] = active_high
    x["swing_low"] = active_low
    x["market_structure_trend"] = trend_labels
    x["bos_up"], x["bos_down"] = bos_up, bos_down
    x["choch_up"], x["choch_down"] = choch_up, choch_down
    x["liquidity_sweep_up"], x["liquidity_sweep_down"] = sweep_up, sweep_down

    prev_close = x["close"].shift(1)
    after_orb = x.index.time >= pd.Timestamp("09:30").time()
    x["orb_up"] = after_orb & (prev_close <= x["orb_high"]) & (x["close"] > x["orb_high"])
    x["orb_down"] = after_orb & (prev_close >= x["orb_low"]) & (x["close"] < x["orb_low"])
    x["prev_day_break_up"] = (prev_close <= x["prev_day_high"]) & (x["close"] > x["prev_day_high"])
    x["prev_day_break_down"] = (prev_close >= x["prev_day_low"]) & (x["close"] < x["prev_day_low"])

    long_cols = ["bos_up", "choch_up", "orb_up", "prev_day_break_up", "liquidity_sweep_up"]
    short_cols = ["bos_down", "choch_down", "orb_down", "prev_day_break_down", "liquidity_sweep_down"]
    x["buy_setup"] = x[long_cols].fillna(False).any(axis=1)
    x["sell_setup"] = x[short_cols].fillna(False).any(axis=1)
    x["signal"] = 0
    x.loc[x["buy_setup"] & ~x["sell_setup"], "signal"] = 1
    x.loc[x["sell_setup"] & ~x["buy_setup"], "signal"] = -1
    x["signal_reason"] = ""
    for col in long_cols + short_cols:
        active = x[col].fillna(False)
        x.loc[active, "signal_reason"] = x.loc[active, "signal_reason"].astype(str).apply(
            lambda s: (s + ", " if s else "") + col
        )
    return x
