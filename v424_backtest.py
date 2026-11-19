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
    # Group once instead of repeatedly scanning the full multi-year frames for
    # every session. This substantially reduces CPU work on small Render instances.
    spot_by_day = {day: frame for day, frame in spot.groupby(spot.index.date, sort=True)}
    opts_by_day = {day: frame for day, frame in opts.groupby(opts.index.date, sort=True)}
    trading_days = sorted(spot_days)
    for day in trading_days:
        schedule_hit = schedule.loc[(schedule.start_date <= day) & (schedule.end_date >= day)]
        if schedule_hit.empty:
            raise ValueError(f"No historical lot size configured for {day}")
        lot_size = int(schedule_hit.iloc[0].lot_size)
        quantity = int(lots) * lot_size
        sd = spot_by_day.get(day)
        od = opts_by_day.get(day)
        if sd is None or od is None:
            raise ValueError(f"Missing grouped candles for {day}; refusing a partial backtest")
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



def run_v424_csv_resumable(spot_signals: pd.DataFrame, option_csv: str, option_type: str,
                           schedule: pd.DataFrame, start_date, end_date, data_dir: str,
                           lots=1, premium_stop=20.0, premium_target=40.0,
                           slippage_per_side=0.50, brokerage_round_trip=40.0,
                           chunksize=6000, progress_callback=None):
    """Low-memory, resumable V4.24 runner for one option side.

    Reads one side's CSV in bounded chunks, retains at most a partial trading
    day plus one chunk, processes complete sessions, and checkpoints each day
    under data_dir. The option CSV is never loaded in full.
    """
    import os, json, hashlib
    from pathlib import Path
    side = str(option_type).upper()
    if side not in {"CALL", "PUT"}:
        raise ValueError("Side must be CALL or PUT")
    start, end = pd.Timestamp(start_date).date(), pd.Timestamp(end_date).date()
    if start > end:
        raise ValueError("Start date must be on or before end date")
    schedule = validate_lot_schedule(schedule, start, end)
    spot = _normalise(spot_signals)
    sig_col = "v413_buy_retest" if side == "CALL" else "v413_sell_retest"
    setup_col = "buy_setup" if side == "CALL" else "sell_setup"
    if sig_col not in spot.columns:
        raise ValueError(f"Spot signal data missing {sig_col}")
    spot = spot.loc[(spot.index.date >= start) & (spot.index.date <= end)].copy()
    spot[setup_col] = spot[sig_col].fillna(False).astype(bool)
    other = "sell_setup" if side == "CALL" else "buy_setup"
    spot[other] = False
    if spot.empty:
        raise ValueError("No Spot candles in the selected period")
    days = sorted(set(spot.index.date))
    key_payload = f"V4.24.2|{side}|{os.path.abspath(option_csv)}|{start}|{end}|{lots}|{premium_stop}|{premium_target}|{slippage_per_side}|{brokerage_round_trip}|{schedule.to_csv(index=False)}|{os.path.getsize(option_csv)}|{os.path.getmtime(option_csv)}"
    key = hashlib.sha256(key_payload.encode()).hexdigest()[:16]
    root = Path(data_dir) / "v424_lowmem" / key
    root.mkdir(parents=True, exist_ok=True)
    state_path, trades_path = root / "state.json", root / "trades.csv"
    state = json.loads(state_path.read_text()) if state_path.exists() else {"completed": []}
    completed = set(state.get("completed", []))
    pending_days = [d for d in days if str(d) not in completed]
    if not pending_days:
        trades = pd.read_csv(trades_path, parse_dates=["entry_time", "exit_time", "signal_time"]) if trades_path.exists() else pd.DataFrame()
        if not trades.empty:
            trades = trades.sort_values(["entry_time", "exit_time"]).reset_index(drop=True)
        return trades, _trade_metrics(trades), _monthly(trades, side), len(days), len(days)

    # The source is timestamp sorted. We hold the last date from each chunk so
    # that a trading day split across a chunk boundary is not processed early.
    pending = pd.DataFrame()
    processed_now = 0
    found_days = set()
    required = ["timestamp", "open", "high", "low", "close", "strike", "spot", "offset"]
    header = pd.read_csv(option_csv, nrows=0).columns.tolist()
    usecols = [c for c in required if c in header]
    if not {"timestamp", "open", "high", "low", "close", "strike", "spot"}.issubset(set(usecols)):
        raise ValueError("Option CSV is missing required columns (timestamp, OHLC, strike, spot)")

    def process_day(day, day_frame):
        nonlocal processed_now
        if day < start or day > end:
            return
        found_days.add(day)
        if str(day) in completed:
            return
        sd = spot.loc[spot.index.date == day]
        if sd.empty:
            return
        hit = schedule.loc[(schedule.start_date <= day) & (schedule.end_date >= day)]
        if hit.empty:
            raise ValueError(f"No historical lot size configured for {day}")
        lot_size = int(hit.iloc[0].lot_size)
        od = day_frame.copy()
        od["timestamp"] = pd.to_datetime(od["timestamp"])
        od = od.set_index("timestamp").sort_index()
        trades, _ = run_option_buying_backtest(sd, od, side, premium_stop, premium_target,
            quantity=int(lots) * lot_size, slippage_per_side=slippage_per_side,
            brokerage_round_trip=brokerage_round_trip, max_trades_per_day=1,
            square_off_time="15:15")
        if not trades.empty:
            trades = trades.copy()
            trades["option_side"] = side
            trades["trade_date"] = str(day)
            trades["lot_size"] = lot_size
            trades["lots"] = int(lots)
            trades["quantity_units"] = int(lots) * lot_size
            trades.to_csv(trades_path, mode="a", header=not trades_path.exists(), index=False)
        completed.add(str(day))
        state_path.write_text(json.dumps({"completed": sorted(completed)}))
        processed_now += 1
        if progress_callback:
            progress_callback(len(completed), len(days), side, day)

    reader = pd.read_csv(option_csv, usecols=usecols, chunksize=chunksize)
    for chunk in reader:
        chunk["timestamp"] = pd.to_datetime(chunk["timestamp"], errors="coerce")
        chunk = chunk.dropna(subset=["timestamp"])
        chunk = chunk.loc[(chunk.timestamp.dt.date >= start) & (chunk.timestamp.dt.date <= end)]
        if chunk.empty:
            continue
        if not pending.empty:
            chunk = pd.concat([pending, chunk], ignore_index=True)
        chunk = chunk.sort_values("timestamp")
        chunk["_day"] = chunk.timestamp.dt.date
        last_day = chunk["_day"].iloc[-1]
        ready = chunk.loc[chunk._day != last_day]
        for day, frame in ready.groupby("_day", sort=True):
            process_day(day, frame.drop(columns=["_day"]))
        pending = chunk.loc[chunk._day == last_day].drop(columns=["_day"]).copy()
        del chunk, ready
    if not pending.empty:
        pending["_day"] = pd.to_datetime(pending.timestamp).dt.date
        for day, frame in pending.groupby("_day", sort=True):
            process_day(day, frame.drop(columns=["_day"]))

    # Validate coverage for every Spot trading session, not just the days
    # reached before a disconnect. A missing day must never produce a partial result.
    missing = sorted(set(days) - found_days)
    if missing:
        preview = ", ".join(str(d) for d in missing[:8])
        raise ValueError(f"{side} option history is missing {len(missing)} Spot trading day(s): {preview}. Full-period P&L is not available.")
    if completed != set(map(str, days)):
        state_path.write_text(json.dumps({"completed": sorted(completed)}))
        raise RuntimeError(f"{side} backtest paused after {len(completed)}/{len(days)} sessions. Press Run again to resume; completed sessions are saved.")
    trades = pd.read_csv(trades_path, parse_dates=["entry_time", "exit_time", "signal_time"]) if trades_path.exists() else pd.DataFrame()
    if not trades.empty:
        trades = trades.sort_values(["entry_time", "exit_time"]).reset_index(drop=True)
    return trades, _trade_metrics(trades), _monthly(trades, side), len(completed), len(days)
