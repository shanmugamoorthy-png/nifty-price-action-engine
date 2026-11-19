"""Historical lot-size schedule helpers for V4.18."""
from __future__ import annotations
import pandas as pd
from options_backtest import run_option_buying_backtest


def validate_lot_schedule(schedule, start_date, end_date):
    required = {"start_date", "end_date", "lot_size"}
    if not required.issubset(schedule.columns):
        raise ValueError("Lot schedule needs start_date, end_date and lot_size columns.")
    rows = schedule.copy()
    rows["start_date"] = pd.to_datetime(rows["start_date"], errors="coerce").dt.date
    rows["end_date"] = pd.to_datetime(rows["end_date"], errors="coerce").dt.date
    rows["lot_size"] = pd.to_numeric(rows["lot_size"], errors="coerce")
    if rows[["start_date", "end_date", "lot_size"]].isna().any().any():
        raise ValueError("Complete every lot schedule row with valid dates and a lot size.")
    if (rows["lot_size"] <= 0).any() or (rows["lot_size"] % 1 != 0).any():
        raise ValueError("Lot sizes must be positive whole numbers.")
    rows = rows.sort_values("start_date").reset_index(drop=True)
    if (rows["start_date"] > rows["end_date"]).any():
        raise ValueError("A lot schedule start date cannot be after its end date.")
    for i in range(1, len(rows)):
        if rows.loc[i, "start_date"] <= rows.loc[i-1, "end_date"]:
            raise ValueError("Lot schedule date ranges overlap. Correct the schedule before running.")
    cursor = start_date
    for row in rows.itertuples(index=False):
        if row.end_date < start_date or row.start_date > end_date:
            continue
        if row.start_date > cursor:
            raise ValueError(f"Lot-size schedule has a gap beginning {cursor}.")
        cursor = max(cursor, row.end_date + pd.Timedelta(days=1))
    if cursor <= end_date:
        raise ValueError(f"Lot-size schedule does not cover the full test period after {cursor}.")
    return rows


def run_scheduled_backtest(spot_signals, option_bars, option_type, premium_stop, premium_target,
                           schedule, start_date, end_date, lots=1, slippage_per_side=0.5,
                           brokerage_round_trip=40.0, max_trades_per_day=1):
    """Run each trading date with the manually verified effective lot size."""
    schedule = validate_lot_schedule(schedule, start_date, end_date)
    all_trades, daily_rows = [], []
    ob = option_bars.copy()
    ob.index = pd.to_datetime(ob.index)
    ss = spot_signals.copy()
    ss.index = pd.to_datetime(ss.index)
    for day in sorted(set(ob.index.date)):
        if day < start_date or day > end_date:
            continue
        hit = schedule.loc[(schedule.start_date <= day) & (schedule.end_date >= day)]
        if hit.empty:
            continue
        lot_size = int(hit.iloc[0].lot_size)
        qty = int(lots) * lot_size
        od = ob.loc[ob.index.date == day]
        sd = ss.loc[ss.index.date == day]
        trades, metrics = run_option_buying_backtest(
            sd, od, option_type, premium_stop, premium_target, quantity=qty,
            slippage_per_side=slippage_per_side, brokerage_round_trip=brokerage_round_trip,
            max_trades_per_day=max_trades_per_day)
        if not trades.empty:
            trades = trades.copy()
            trades["option_side"] = option_type
            trades["lot_size"] = lot_size
            trades["lots"] = int(lots)
            trades["quantity_units"] = qty
            all_trades.append(trades)
    combined = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    return combined
