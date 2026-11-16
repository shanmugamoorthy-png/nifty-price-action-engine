import pandas as pd
import numpy as np

def add_price_action_signals(df: pd.DataFrame, pivot_left=3, pivot_right=3, orb_minutes=15):
    """
    Causal price-action signals. Pivot levels become usable only after pivot_right
    candles have closed. No indicators, moving averages, RSI, MACD, VWAP or OI.
    """
    x = df.copy().sort_index()
    required = {"open","high","low","close"}
    if not required.issubset(x.columns):
        raise ValueError(f"Data must contain {required}")
    x["date"] = x.index.date
    x["day_high_prev"] = x.groupby("date")["high"].transform("max").groupby(x["date"]).first().shift(1).reindex(x["date"]).to_numpy()
    x["day_low_prev"] = x.groupby("date")["low"].transform("min").groupby(x["date"]).first().shift(1).reindex(x["date"]).to_numpy()

    # Confirmed swing points: a pivot at t is only revealed at t + pivot_right.
    highs = x["high"].to_numpy()
    lows = x["low"].to_numpy()
    ph = np.full(len(x), np.nan)
    pl = np.full(len(x), np.nan)
    for confirm_i in range(pivot_left + pivot_right, len(x)):
        pivot_i = confirm_i - pivot_right
        left = highs[pivot_i-pivot_left:pivot_i]
        right = highs[pivot_i+1:pivot_i+pivot_right+1]
        if len(left) == pivot_left and len(right) == pivot_right and highs[pivot_i] > max(left.max(), right.max()):
            ph[confirm_i] = highs[pivot_i]
        left_l = lows[pivot_i-pivot_left:pivot_i]
        right_l = lows[pivot_i+1:pivot_i+pivot_right+1]
        if len(left_l) == pivot_left and len(right_l) == pivot_right and lows[pivot_i] < min(left_l.min(), right_l.min()):
            pl[confirm_i] = lows[pivot_i]
    x["new_swing_high"] = ph
    x["new_swing_low"] = pl
    x["swing_high"] = pd.Series(ph, index=x.index).ffill()
    x["swing_low"] = pd.Series(pl, index=x.index).ffill()

    # Opening range high/low, computed only after the opening range completes.
    x["orb_high"] = np.nan
    x["orb_low"] = np.nan
    for d, idx in x.groupby("date").groups.items():
        day = x.loc[idx]
        opening = day.between_time("09:15", "09:30", inclusive="left")
        if len(opening):
            x.loc[idx, "orb_high"] = opening["high"].max()
            x.loc[idx, "orb_low"] = opening["low"].min()
    x["orb_high"] = x.groupby("date")["orb_high"].transform("first")
    x["orb_low"] = x.groupby("date")["orb_low"].transform("first")

    prev_close = x["close"].shift(1)
    # Structural breaks and prior-day levels
    x["bos_up"] = (prev_close <= x["swing_high"].shift(1)) & (x["close"] > x["swing_high"].shift(1))
    x["bos_down"] = (prev_close >= x["swing_low"].shift(1)) & (x["close"] < x["swing_low"].shift(1))
    x["orb_up"] = (x.index.time >= pd.Timestamp("09:30").time()) & (prev_close <= x["orb_high"]) & (x["close"] > x["orb_high"])
    x["orb_down"] = (x.index.time >= pd.Timestamp("09:30").time()) & (prev_close >= x["orb_low"]) & (x["close"] < x["orb_low"])
    x["pd_break_up"] = (prev_close <= x["day_high_prev"]) & (x["close"] > x["day_high_prev"])
    x["pd_break_down"] = (prev_close >= x["day_low_prev"]) & (x["close"] < x["day_low_prev"])

    # Rejection/false-break candle: wick pierces prior level, close returns inside.
    x["false_up"] = (x["high"] > x["swing_high"].shift(1)) & (x["close"] < x["swing_high"].shift(1)) & (x["close"] < x["open"])
    x["false_down"] = (x["low"] < x["swing_low"].shift(1)) & (x["close"] > x["swing_low"].shift(1)) & (x["close"] > x["open"])

    # Retest: previous candle broke the level; current candle touches and closes back on breakout side.
    x["retest_up"] = (prev_close > x["swing_high"].shift(2)) & (x["low"] <= x["swing_high"].shift(1)) & (x["close"] > x["swing_high"].shift(1))
    x["retest_down"] = (prev_close < x["swing_low"].shift(2)) & (x["high"] >= x["swing_low"].shift(1)) & (x["close"] < x["swing_low"].shift(1))

    x["buy_setup"] = x[["bos_up","orb_up","pd_break_up","retest_up","false_down"]].fillna(False).any(axis=1)
    x["sell_setup"] = x[["bos_down","orb_down","pd_break_down","retest_down","false_up"]].fillna(False).any(axis=1)
    x["signal"] = 0
    x.loc[x["buy_setup"] & ~x["sell_setup"], "signal"] = 1
    x.loc[x["sell_setup"] & ~x["buy_setup"], "signal"] = -1
    # Avoid repeated same-direction entries without a new opposing signal.
    return x
