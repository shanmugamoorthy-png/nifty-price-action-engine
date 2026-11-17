"""Chronological out-of-sample validation and trade diagnostics."""
import pandas as pd
from backtest import run_backtest


def max_consecutive_losses(trades: pd.DataFrame) -> int:
    if trades is None or trades.empty or "net_points" not in trades:
        return 0
    best = current = 0
    ordered = trades.sort_values("exit_time") if "exit_time" in trades else trades
    for value in ordered["net_points"]:
        if float(value) < 0:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return int(best)


def monthly_summary(trades: pd.DataFrame) -> pd.DataFrame:
    cols = ["Month", "Trades", "Wins", "Win rate %", "Net points", "Profit factor", "Brokerage estimate ₹"]
    if trades is None or trades.empty:
        return pd.DataFrame(columns=cols)
    x = trades.copy()
    x["exit_time"] = pd.to_datetime(x["exit_time"])
    x["Month"] = x["exit_time"].dt.to_period("M").astype(str)
    rows = []
    for month, group in x.groupby("Month", sort=True):
        wins = group.loc[group["net_points"] > 0, "net_points"]
        losses = group.loc[group["net_points"] < 0, "net_points"]
        gross_loss = abs(float(losses.sum()))
        pf = float(wins.sum()) / gross_loss if gross_loss else (float("inf") if wins.sum() > 0 else 0.0)
        rows.append({
            "Month": month,
            "Trades": int(len(group)),
            "Wins": int((group["net_points"] > 0).sum()),
            "Win rate %": round(float((group["net_points"] > 0).mean() * 100), 2),
            "Net points": round(float(group["net_points"].sum()), 2),
            "Profit factor": round(pf, 3) if pd.notna(pf) and pf != float("inf") else ("∞" if pf == float("inf") else 0.0),
            "Brokerage estimate ₹": round(float(group.get("brokerage_inr", pd.Series(dtype=float)).sum()), 2),
        })
    return pd.DataFrame(rows, columns=cols)


def enrich_metrics(metrics: dict, trades: pd.DataFrame) -> dict:
    out = dict(metrics)
    out["Max consecutive losses"] = max_consecutive_losses(trades)
    return out


def run_walk_forward(signals: pd.DataFrame, signal_filter: str, train_start, train_end,
                     test_start, test_end, stop_points=30.0, target_points=60.0,
                     brokerage_per_round_trip_inr=20.0, slippage_points_per_side=1.0,
                     mode="Intraday"):
    """Chronological train/test split. No random shuffling; test follows training."""
    train_start, train_end = pd.Timestamp(train_start).date(), pd.Timestamp(train_end).date()
    test_start, test_end = pd.Timestamp(test_start).date(), pd.Timestamp(test_end).date()
    if train_start > train_end or test_start > test_end or test_start <= train_end:
        raise ValueError("Use non-overlapping chronological dates: training must end before test starts.")
    common = dict(
        mode=mode, signal_filter=signal_filter, signals_precomputed=True,
        stop_points=float(stop_points), target_points=float(target_points),
        brokerage_per_round_trip_inr=float(brokerage_per_round_trip_inr),
        slippage_points_per_side=float(slippage_points_per_side),
    )
    train = run_backtest(signals, start_date=train_start, end_date=train_end, **common)
    test = run_backtest(signals, start_date=test_start, end_date=test_end, **common)
    train = (train[0], train[1], enrich_metrics(train[2], train[1]))
    test = (test[0], test[1], enrich_metrics(test[2], test[1]))
    return train, test


def run_target_ratio_comparison(signals: pd.DataFrame, signal_filter: str, start_date, end_date,
                                stop_points=30.0, baseline_target_points=60.0,
                                brokerage_per_round_trip_inr=20.0, slippage_points_per_side=1.0,
                                mode="Intraday"):
    """Compare the configured target with a 2R target on the same OOS window."""
    rows = []
    details = {}
    for label, target in (("Configured target", float(baseline_target_points)),
                          ("1:2 risk/reward", float(stop_points) * 2.0)):
        result = run_backtest(
            signals, mode=mode, signal_filter=signal_filter, signals_precomputed=True,
            start_date=start_date, end_date=end_date, stop_points=float(stop_points),
            target_points=target, brokerage_per_round_trip_inr=float(brokerage_per_round_trip_inr),
            slippage_points_per_side=float(slippage_points_per_side),
        )
        metrics = enrich_metrics(result[2], result[1])
        rows.append({"Target test": label, "Target points": target, **metrics})
        details[label] = result
    return pd.DataFrame(rows), details
