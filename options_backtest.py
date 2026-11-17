"""Premium-based NIFTY long-option backtest; intraday only by design."""
import numpy as np
import pandas as pd


def _metrics(trades, quantity, brokerage):
    if trades.empty:
        return {"Trades": 0, "Win rate %": 0.0, "Gross P&L ₹": 0.0,
                "Net P&L after costs ₹": 0.0, "Profit factor": 0.0,
                "Max drawdown ₹": 0.0, "Brokerage ₹": 0.0}
    x = trades.sort_values("exit_time").copy()
    x["gross_pnl_inr"] = (x["raw_exit"] - x["raw_entry"]) * quantity
    x["slippage_inr"] = (x["slippage_points"] * quantity)
    x["net_pnl_inr"] = x["gross_pnl_inr"] - x["slippage_inr"] - brokerage
    wins = x.loc[x.net_pnl_inr > 0, "net_pnl_inr"].sum()
    losses = abs(x.loc[x.net_pnl_inr < 0, "net_pnl_inr"].sum())
    eq = x.net_pnl_inr.cumsum()
    dd = eq - eq.cummax()
    return {
        "Trades": int(len(x)),
        "Win rate %": round(float((x.net_pnl_inr > 0).mean() * 100), 2),
        "Gross P&L ₹": round(float(x.gross_pnl_inr.sum()), 2),
        "Net P&L after costs ₹": round(float(x.net_pnl_inr.sum()), 2),
        "Profit factor": round(float(wins / losses), 3) if losses else ("∞" if wins else 0.0),
        "Max drawdown ₹": round(float(dd.min()), 2),
        "Brokerage ₹": round(float(brokerage * len(x)), 2),
    }, x


def run_option_buying_backtest(spot_signals, option_bars, option_type, premium_stop,
                               premium_target, quantity=1, slippage_per_side=0.5,
                               brokerage_round_trip=20.0, strike_offset=0,
                               square_off_time="15:15"):
    """Map spot signals to same-day option contract candles and buy next-bar open.

    Only intraday positions are supported. The rolling API's ATM strike can move
    over time; each trade is therefore pinned to the actual strike returned at
    entry and only candles with that same strike are used until exit. Positions
    are forcibly closed on the same trading day to avoid rolling-expiry identity
    ambiguity in Dhan's rolling series.
    """
    if option_type not in ("CALL", "PUT"):
        raise ValueError("option_type must be CALL or PUT")
    if premium_stop <= 0 or premium_target <= 0 or quantity <= 0:
        raise ValueError("Premium stop, target and quantity must be positive.")
    if slippage_per_side < 0 or brokerage_round_trip < 0:
        raise ValueError("Costs cannot be negative.")
    col = "buy_setup" if option_type == "CALL" else "sell_setup"
    if col not in spot_signals:
        raise ValueError(f"Spot signal column missing: {col}")
    x = option_bars.copy().sort_index()
    x = x.loc[~x.index.duplicated(keep="last")]
    if x.empty:
        return pd.DataFrame(), _metrics(pd.DataFrame(), quantity, brokerage_round_trip)[0]
    # Select ATM or requested offset from ATM by strike distance using the actual
    # rolling ATM strike series. strike_offset=0 is ATM; positive/negative shifts
    # are translated to one standard NIFTY strike step per offset.
    if "strike" not in x or x["strike"].isna().all():
        raise ValueError("Dhan response did not include actual strike values; refusing an ambiguous contract backtest.")
    spot = spot_signals[[col]].copy()
    spot[col] = spot[col].fillna(False).astype(bool)
    spot = spot.loc[spot[col]]
    if spot.empty:
        return pd.DataFrame(), _metrics(pd.DataFrame(), quantity, brokerage_round_trip)[0]
    # For offset strikes, use the option API's rolling ATM series then map the
    # offset to an absolute strike. NIFTY strike interval is configured at 50.
    x = x.copy()
    x["atm_strike"] = (x["spot"] / 50.0).round() * 50.0
    x["wanted_strike"] = x["atm_strike"] + float(strike_offset) * 50.0
    # Dhan returns the actual strike for the requested ATM-relative series. A
    # nonzero offset requires fetching that offset series, not relabelling ATM.
    # The UI currently exposes ATM only to prevent false strike substitution.
    x = x.dropna(subset=["strike", "open", "high", "low", "close"])
    x["session"] = x.index.date
    spot = spot.loc[spot.index.isin(x.index)]
    signal_times = set(spot.index)
    trades = []
    position = None
    idx = x.index
    for i in range(1, len(x)):
        ts = idx[i]
        row = x.iloc[i]
        prev_ts = idx[i-1]
        prev = x.iloc[i-1]
        if position:
            # Never carry a rolling option contract overnight.
            if ts.date() != position["entry_time"].date():
                trades.append(_close(position, prev_ts, float(prev.close), "Intraday expiry/session square-off",
                                     quantity, slippage_per_side, brokerage_round_trip))
                position = None
            else:
                # Ignore bars after contract strike changes: close on previous
                # same-contract candle rather than silently switching strikes.
                if float(row.strike) != float(position["strike"]):
                    trades.append(_close(position, prev_ts, float(prev.close), "Rolling strike changed; close prior contract",
                                         quantity, slippage_per_side, brokerage_round_trip))
                    position = None
                else:
                    op, hi, lo = float(row.open), float(row.high), float(row.low)
                    sl, target = position["sl"], position["target"]
                    exit_price, reason = None, None
                    if op <= sl:
                        exit_price, reason = op, "Premium stop gap"
                    elif op >= target:
                        exit_price, reason = target, "Premium target gap"
                    elif lo <= sl and hi >= target:
                        exit_price, reason = sl, "Premium stop (both touched; conservative)"
                    elif lo <= sl:
                        exit_price, reason = sl, "Premium stop"
                    elif hi >= target:
                        exit_price, reason = target, "Premium target"
                    elif ts.strftime("%H:%M") >= square_off_time:
                        exit_price, reason = float(row.close), "Intraday square-off"
                    if exit_price is not None:
                        trades.append(_close(position, ts, exit_price, reason, quantity,
                                             slippage_per_side, brokerage_round_trip))
                        position = None
        # Signal on prior completed NIFTY candle; buy next option candle open.
        if position is None and prev_ts in signal_times and ts.date() == prev_ts.date():
            if ts.strftime("%H:%M") >= "15:00":
                continue
            raw_entry = float(row.open)
            entry = raw_entry + slippage_per_side
            strike = float(row.strike)
            position = {
                "option_type": option_type, "direction": f"BUY {option_type}",
                "signal_time": prev_ts, "entry_time": ts, "raw_entry": raw_entry,
                "entry": entry, "strike": strike, "sl": entry - premium_stop,
                "target": entry + premium_target,
                "spot_at_signal": float(spot_signals.loc[prev_ts, "close"]),
                "signal_reason": str(spot_signals.loc[prev_ts].get("signal_reason", "")),
            }
    if position:
        last = x.iloc[-1]
        trades.append(_close(position, idx[-1], float(last.close), "End of available data", quantity,
                             slippage_per_side, brokerage_round_trip))
    trades_df = pd.DataFrame(trades)
    metrics, trades_df = _metrics(trades_df, quantity, brokerage_round_trip)
    return trades_df, metrics


def _close(position, ts, raw_exit, reason, quantity, slippage, brokerage):
    exit_fill = float(raw_exit) - slippage
    gross_points = float(raw_exit) - float(position["raw_entry"])
    slip_points = 2.0 * slippage
    return {
        **position, "exit_time": ts, "raw_exit": float(raw_exit), "exit": exit_fill,
        "reason": reason, "gross_points": gross_points,
        "slippage_points": slip_points,
        "gross_pnl_inr": gross_points * quantity,
        "slippage_inr": slip_points * quantity,
        "brokerage_inr": float(brokerage),
        "net_pnl_inr": gross_points * quantity - slip_points * quantity - brokerage,
    }
