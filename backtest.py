import pandas as pd
import numpy as np
from price_action import add_price_action_signals

def run_backtest(df, mode="Both", timeframe="5 minutes", stop_points=30.0, target_points=60.0,
                 brokerage_per_trade=20.0, slippage_points=1.0, square_off="15:20"):
    """
    One position at a time, next candle open execution, fixed-point stop/target.
    Backtest is on NIFTY index points (not option premium/P&L).
    """
    x = add_price_action_signals(df).dropna(subset=["open","high","low","close"]).copy()
    trades = []
    position = None
    day_last = {}
    for d, g in x.groupby(x.index.date):
        day_last[d] = g.index[-1]
    for i in range(1, len(x)):
        ts = x.index[i]
        row = x.iloc[i]
        prev = x.iloc[i-1]
        # Intraday positions are closed before the cash-market close.
        intraday_exit = (mode == "Intraday" and ts.strftime("%H:%M") >= square_off)
        if position:
            exit_price, reason = None, None
            if position["side"] == 1:
                if row["low"] <= position["sl"]:
                    exit_price, reason = position["sl"], "Stop loss"
                elif row["high"] >= position["target"]:
                    exit_price, reason = position["target"], "Target"
            else:
                if row["high"] >= position["sl"]:
                    exit_price, reason = position["sl"], "Stop loss"
                elif row["low"] <= position["target"]:
                    exit_price, reason = position["target"], "Target"
            if exit_price is None and intraday_exit:
                exit_price, reason = row["close"], "Intraday square-off"
            if exit_price is None and i == len(x)-1:
                exit_price, reason = row["close"], "End of data"
            if exit_price is not None:
                exit_price += slippage_points * (-position["side"])
                gross = (exit_price - position["entry"]) * position["side"]
                trades.append({**position, "exit_time": ts, "exit": exit_price, "reason": reason,
                               "gross_points": gross, "net_points": gross - brokerage_per_trade})
                position = None
        if position is None and int(prev["signal"]) != 0:
            side = int(prev["signal"])
            if mode == "Intraday" or mode == "Both":
                # Do not open new intraday trade after 15:00.
                if ts.strftime("%H:%M") >= "15:00":
                    continue
            entry = float(row["open"]) + slippage_points * side
            position = {"side": side, "entry_time": ts, "entry": entry,
                        "sl": entry - side * stop_points, "target": entry + side * target_points}
    trades_df = pd.DataFrame(trades)
    if trades_df.empty:
        metrics = {"Trades": 0, "Win rate": 0.0, "Net points": 0.0, "Profit factor": 0.0, "Max drawdown": 0.0}
        return x, trades_df, metrics
    wins = trades_df[trades_df.net_points > 0].net_points
    losses = trades_df[trades_df.net_points < 0].net_points
    equity = trades_df.net_points.cumsum()
    dd = equity - equity.cummax()
    pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else float("inf")
    metrics = {
        "Trades": int(len(trades_df)),
        "Win rate": round((trades_df.net_points > 0).mean()*100, 2),
        "Net points": round(trades_df.net_points.sum(), 2),
        "Profit factor": round(pf, 2) if np.isfinite(pf) else "∞",
        "Max drawdown": round(dd.min(), 2),
        "Average points/trade": round(trades_df.net_points.mean(), 2),
    }
    return x, trades_df, metrics
