import numpy as np
import pandas as pd

def _confirmed_pivots(high, low, left=3, right=3):
    """Return pivot values on the bar where the right-side confirmation completes."""
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
    Causal pure-price-action engine.
    A swing is usable only on its confirmation bar (after pivot_right bars).
    CHoCH + Higher Low:
      1) A bullish CHoCH breaks the latest confirmed swing high while prior trend is bearish.
      2) After that CHoCH, a new confirmed swing low must be strictly above the
         prior confirmed swing low.
      3) The setup signal is emitted only when the higher low is confirmed.
    Thus the strategy does not enter retrospectively at the pivot candle.
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

    x["orb_high"] = np.nan
    x["orb_low"] = np.nan
    for day, idx in x.groupby("session_date").groups.items():
        day_frame = x.loc[idx]
        opening = day_frame.iloc[:orb_bars]
        if len(opening):
            x.loc[idx, "orb_high"] = float(opening["high"].max())
            x.loc[idx, "orb_low"] = float(opening["low"].min())

    high = x["high"].to_numpy(dtype=float)
    low = x["low"].to_numpy(dtype=float)
    close = x["close"].to_numpy(dtype=float)
    open_ = x["open"].to_numpy(dtype=float)
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

    # CHoCH->HL state. The baseline is the latest confirmed swing low that existed
    # at the CHoCH event. A lower/equal subsequent pivot invalidates this attempt.
    waiting_for_hl = False
    choch_reference_low = np.nan
    choch_hl_up = np.zeros(n, dtype=bool)
    choch_hl_reference = np.full(n, np.nan)
    choch_hl_confirmed_low = np.full(n, np.nan)

    for i in range(n):
        prior_confirmed_low = last_low

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

            if np.isfinite(last_high) and high[i] > last_high and close[i] < last_high and close[i] < open_[i]:
                sd = True
            if np.isfinite(last_low) and low[i] < last_low and close[i] > last_low and close[i] > open_[i]:
                su = True

        # Start a new HL sequence only on a confirmed bullish CHoCH.
        if cu:
            waiting_for_hl = np.isfinite(prior_confirmed_low)
            choch_reference_low = prior_confirmed_low if waiting_for_hl else np.nan

        # Only pivots confirmed AFTER the CHoCH can validate the higher low.
        # A pivot confirmed on the same bar as CHoCH is not used.
        if waiting_for_hl and i > 0 and np.isfinite(pivot_l[i]) and not cu:
            if pivot_l[i] > choch_reference_low:
                choch_hl_up[i] = True
                choch_hl_reference[i] = choch_reference_low
                choch_hl_confirmed_low[i] = pivot_l[i]
                waiting_for_hl = False
            else:
                # A lower/equal low invalidates this CHoCH attempt.
                waiting_for_hl = False

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
    x["choch_hl_up"] = choch_hl_up
    x["choch_hl_reference_low"] = choch_hl_reference
    x["choch_hl_confirmed_low"] = choch_hl_confirmed_low

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
    x.loc[x["choch_hl_up"], "signal_reason"] = "choch_hl_up"
    return x
