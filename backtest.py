import numpy as np
import pandas as pd
from price_action import add_price_action_signals

def _adverse_fill(raw_price, side, is_entry, slippage):
    # Long entry pays up / long exit sells lower; short entry sells lower / cover pays up.
    direction = side if is_entry else -side
    return float(raw_price + direction * slippage)

def run_backtest(df, mode="Intraday", stop_points=30.0, target_points=60.0,
                 brokerage_per_round_trip_inr=20.0, slippage_points_per_side=1.0,
                 square_off_time="15:20", start_date=None, end_date=None,
                 exit_on_opposite_signal=True, signal_filter=None, signals_precomputed=False):
    """
    One-position backtest. Signals are known at candle close and filled at next
    candle open. Intraday and positional are separate modes. Brokerage is
    reported in INR and NEVER subtracted from index points.
    """
    if mode not in ("Intraday", "Positional"):
        raise ValueError("mode must be Intraday or Positional")
    if stop_points <= 0 or target_points <= 0:
        raise ValueError("Stop and target must be positive.")
    if slippage_points_per_side < 0 or brokerage_per_round_trip_inr < 0:
        raise ValueError("Costs cannot be negative.")

    x = df.copy() if signals_precomputed else add_price_action_signals(df).copy()
    if signal_filter:
        if signal_filter not in x.columns:
            raise ValueError(f"Unknown setup filter: {signal_filter}")
        active = x[signal_filter].fillna(False).astype(bool)
        x["signal"] = 0
        x.loc[active, "signal"] = 1 if signal_filter.endswith("_up") else -1
        x["signal_reason"] = ""
        x.loc[active, "signal_reason"] = signal_filter
    trades = []
    position = None
    start = pd.Timestamp(start_date).date() if start_date else None
    end = pd.Timestamp(end_date).date() if end_date else None

    def close_position(ts, raw_exit, reason):
        nonlocal position
        side = position["side"]
        exit_fill = _adverse_fill(raw_exit, side, False, slippage_points_per_side)
        gross = (float(raw_exit) - position["raw_entry"]) * side
        net = (exit_fill - position["entry"]) * side
        trades.append({
            **position,
            "exit_time": ts,
            "raw_exit": float(raw_exit),
            "exit": exit_fill,
            "reason": reason,
            "gross_points": gross,
            "slippage_points": gross - net,
            "net_points": net,
            "brokerage_inr": float(brokerage_per_round_trip_inr),
        })
        position = None

    for i in range(1, len(x)):
        ts = x.index[i]
        row = x.iloc[i]
        prior = x.iloc[i-1]
        if end is not None and ts.date() > end:
            if position:
                close_position(x.index[i-1], float(prior["close"]), "End of test window")
            break
        in_test_window = (start is None or ts.date() >= start) and (end is None or ts.date() <= end)
        signal = int(prior["signal"]) if in_test_window else 0

        # An opposite completed-candle signal exits at this candle's open.
        if position and exit_on_opposite_signal and signal == -position["side"]:
            close_position(ts, float(row["open"]), "Opposite price-action signal")

        # Manage any carried position against this candle's OHLC.
        if position:
            side = position["side"]
            op, hi, lo = float(row["open"]), float(row["high"]), float(row["low"])
            sl, target = position["sl"], position["target"]
            exit_price, reason = None, None
            if side == 1:
                if op <= sl:
                    exit_price, reason = op, "Stop loss gap"
                elif op >= target:
                    exit_price, reason = target, "Target"
                elif lo <= sl and hi >= target:
                    exit_price, reason = sl, "Stop loss (both touched; conservative)"
                elif lo <= sl:
                    exit_price, reason = sl, "Stop loss"
                elif hi >= target:
                    exit_price, reason = target, "Target"
            else:
                if op >= sl:
                    exit_price, reason = op, "Stop loss gap"
                elif op <= target:
                    exit_price, reason = target, "Target"
                elif hi >= sl and lo <= target:
                    exit_price, reason = sl, "Stop loss (both touched; conservative)"
                elif hi >= sl:
                    exit_price, reason = sl, "Stop loss"
                elif lo <= target:
                    exit_price, reason = target, "Target"
            # Intraday positions close at the 15:15 candle close (approx 15:20 IST).
            if exit_price is None and mode == "Intraday" and ts.strftime("%H:%M") >= "15:15":
                exit_price, reason = float(row["close"]), "Intraday square-off"
            if exit_price is not None:
                close_position(ts, exit_price, reason)

        # New signal from prior candle is filled at current candle open.
        if position is None and signal != 0 and in_test_window:
            if mode == "Intraday" and ts.strftime("%H:%M") >= "15:00":
                continue
            side = signal
            raw_entry = float(row["open"])
            entry = _adverse_fill(raw_entry, side, True, slippage_points_per_side)
            position = {
                "side": side,
                "direction": "BUY" if side == 1 else "SELL",
                "entry_time": ts,
                "raw_entry": raw_entry,
                "entry": entry,
                "sl": entry - side * stop_points,
                "target": entry + side * target_points,
                "signal_reason": str(prior.get("signal_reason", "")),
            }

    if position:
        last = x.iloc[-1]
        close_position(x.index[-1], float(last["close"]), "End of data")

    trades_df = pd.DataFrame(trades)
    if trades_df.empty:
        return x, trades_df, {
            "Trades": 0, "Win rate %": 0.0, "Gross points": 0.0,
            "Net points after slippage": 0.0, "Profit factor": 0.0,
            "Max drawdown points": 0.0, "Brokerage estimate ₹": 0.0,
        }

    trades_df = trades_df.sort_values("exit_time").reset_index(drop=True)
    wins = trades_df.loc[trades_df["net_points"] > 0, "net_points"]
    losses = trades_df.loc[trades_df["net_points"] < 0, "net_points"]
    equity = trades_df["net_points"].cumsum()
    drawdown = equity - equity.cummax()
    gross_profit = wins.sum()
    gross_loss = abs(losses.sum())
    pf = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)
    metrics = {
        "Trades": int(len(trades_df)),
        "Win rate %": round(float((trades_df["net_points"] > 0).mean() * 100), 2),
        "Gross points": round(float(trades_df["gross_points"].sum()), 2),
        "Net points after slippage": round(float(trades_df["net_points"].sum()), 2),
        "Profit factor": round(float(pf), 3) if np.isfinite(pf) else "∞",
        "Max drawdown points": round(float(drawdown.min()), 2),
        "Average net points/trade": round(float(trades_df["net_points"].mean()), 2),
        "Brokerage estimate ₹": round(float(trades_df["brokerage_inr"].sum()), 2),
    }
    return x, trades_df, metrics

def run_both_modes(df, **kwargs):
    intraday = run_backtest(df, mode="Intraday", **kwargs)
    positional = run_backtest(df, mode="Positional", **kwargs)
    return intraday, positional
