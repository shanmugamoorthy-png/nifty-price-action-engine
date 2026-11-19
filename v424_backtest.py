"""V4.24 continuous chronological, weekly NIFTY options profitability engine.

Signals are generated from the full warmed-up Spot history, then trades are
executed in chronological trading-day order using the production V4.18 option
premium engine. Positions are intraday only, so day boundaries are explicit.
"""
from __future__ import annotations
import pandas as pd
from options_backtest import run_option_buying_backtest
from lot_schedule import validate_lot_schedule


def _normalise(frame: pd.DataFrame) -> pd.DataFrame:
    x = frame.copy()
    x.index = pd.to_datetime(x.index)
    if getattr(x.index, "tz", None) is not None:
        x.index = x.index.tz_convert("Asia/Kolkata").tz_localize(None)
    x.columns = [str(c).lower().strip() for c in x.columns]
    return x.sort_index()


def _trade_metrics(trades: pd.DataFrame) -> dict:
    if trades.empty:
        return {"Trades": 0, "Wins": 0, "Losses": 0, "Win rate %": 0.0,
                "Gross P&L ₹": 0.0, "Slippage ₹": 0.0, "Brokerage ₹": 0.0,
                "Net P&L ₹": 0.0, "Profit factor": 0.0, "Max drawdown ₹": 0.0,
                "Max consecutive losses": 0}
    t = trades.sort_values(["exit_time", "entry_time"]).copy()
    net = pd.to_numeric(t.net_pnl_inr, errors="coerce").fillna(0.0)
    wins = net[net > 0]
    losses = net[net < 0]
    equity = net.cumsum()
    drawdown = equity - equity.cummax().clip(lower=0)
    streak = best = 0
    for value in net:
        streak = streak + 1 if value < 0 else 0
        best = max(best, streak)
    loss_total = abs(float(losses.sum()))
    pf = float(wins.sum()) / loss_total if loss_total else ("∞" if wins.sum() > 0 else 0.0)
    return {"Trades": int(len(t)), "Wins": int((net > 0).sum()), "Losses": int((net < 0).sum()),
            "Win rate %": round(float((net > 0).mean() * 100), 2),
            "Gross P&L ₹": round(float(t.gross_pnl_inr.sum()), 2),
            "Slippage ₹": round(float(t.slippage_inr.sum()), 2),
            "Brokerage ₹": round(float(t.brokerage_inr.sum()), 2),
            "Net P&L ₹": round(float(net.sum()), 2), "Profit factor": round(pf, 3) if isinstance(pf, float) else pf,
            "Max drawdown ₹": round(float(drawdown.min()), 2), "Max consecutive losses": int(best)}


def _monthly(trades: pd.DataFrame, side: str) -> pd.DataFrame:
    cols = ["month", "side", "trades", "wins", "losses", "gross_pnl_inr", "slippage_inr", "brokerage_inr", "net_pnl_inr"]
    if trades.empty:
        return pd.DataFrame(columns=cols)
    x = trades.copy()
    x["month"] = pd.to_datetime(x.exit_time).dt.to_period("M").astype(str)
    x["win"] = pd.to_numeric(x.net_pnl_inr, errors="coerce").fillna(0) > 0
    out = x.groupby("month", as_index=False).agg(trades=("net_pnl_inr", "size"), wins=("win", "sum"),
        gross_pnl_inr=("gross_pnl_inr", "sum"), slippage_inr=("slippage_inr", "sum"),
        brokerage_inr=("brokerage_inr", "sum"), net_pnl_inr=("net_pnl_inr", "sum"))
    out["losses"] = out.trades - out.wins
    out["side"] = side
    return out[cols]


def run_v424_backtest(spot_signals: pd.DataFrame, option_bars: pd.DataFrame, option_type: str,
                      schedule: pd.DataFrame, start_date, end_date, lots=1,
                      premium_stop=20.0, premium_target=40.0,
                      slippage_per_side=0.50, brokerage_round_trip=40.0,
                      square_off_time="15:15"):
    """Run the unchanged entry/exit engine in chronological day order.

    `spot_signals` must contain v413_buy_retest/v413_sell_retest and OHLC.
    `option_bars` must be the selected WEEK / expiry-sequence / 5m data with
    actual strike and offset columns. One lot size is applied per date range.
    """
    side = str(option_type).upper()
    if side not in {"CALL", "PUT"}:
        raise ValueError("Side must be CALL or PUT")
    if int(lots) < 1:
        raise ValueError("Lots must be at least one")
    start = pd.Timestamp(start_date).date()
    end = pd.Timestamp(end_date).date()
    if start > end:
        raise ValueError("Start date must be on or before end date")
    spot = _normalise(spot_signals)
    opts = _normalise(option_bars)
    required_spot = {"open", "high", "low", "close", "v413_buy_retest", "v413_sell_retest"}
    required_opt = {"open", "high", "low", "close", "strike", "spot"}
    if missing := required_spot - set(spot.columns):
        raise ValueError(f"Spot signal data missing columns: {sorted(missing)}")
    if missing := required_opt - set(opts.columns):
        raise ValueError(f"Option data missing columns: {sorted(missing)}")
    schedule = validate_lot_schedule(schedule, start, end)
    spot = spot.loc[(spot.index.date >= start) & (spot.index.date <= end)].copy()
    opts = opts.loc[(opts.index.date >= start) & (opts.index.date <= end)].copy()
    if spot.empty:
        raise ValueError("No Spot candles in the selected period")
    if opts.empty:
        raise ValueError(f"No {side} weekly option candles in the selected period")
    sig_col = "v413_buy_retest" if side == "CALL" else "v413_sell_retest"
    setup_col = "buy_setup" if side == "CALL" else "sell_setup"
    spot[setup_col] = spot[sig_col].fillna(False).astype(bool)
    spot["buy_setup" if side == "PUT" else "sell_setup"] = False
    all_trades = []
    spot_days = set(spot.index.date)
    option_days = set(opts.index.date)
    missing_option_days = sorted(spot_days - option_days)
    if missing_option_days:
        preview = ", ".join(str(d) for d in missing_option_days[:8])
        suffix = " ..." if len(missing_option_days) > 8 else ""
        raise ValueError(f"Option history is missing {len(missing_option_days)} Spot trading day(s): {preview}{suffix}. Do not interpret a partial-coverage run as a full backtest.")
    trading_days = sorted(spot_days)
    for day in trading_days:
        schedule_hit = schedule.loc[(schedule.start_date <= day) & (schedule.end_date >= day)]
        if schedule_hit.empty:
            raise ValueError(f"No historical lot size configured for {day}")
        lot_size = int(schedule_hit.iloc[0].lot_size)
        quantity = int(lots) * lot_size
        sd = spot.loc[spot.index.date == day]
        od = opts.loc[opts.index.date == day]
        trades, _ = run_option_buying_backtest(
            sd, od, side, premium_stop, premium_target, quantity=quantity,
            slippage_per_side=slippage_per_side, brokerage_round_trip=brokerage_round_trip,
            max_trades_per_day=1, square_off_time=square_off_time)
        if not trades.empty:
            trades = trades.copy()
            trades["option_side"] = side
            trades["trade_date"] = str(day)
            trades["lot_size"] = lot_size
            trades["lots"] = int(lots)
            trades["quantity_units"] = quantity
            all_trades.append(trades)
    result = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    if not result.empty:
        result = result.sort_values(["entry_time", "exit_time"]).reset_index(drop=True)
    metrics = _trade_metrics(result)
    monthly = _monthly(result, side)
    return result, metrics, monthly
