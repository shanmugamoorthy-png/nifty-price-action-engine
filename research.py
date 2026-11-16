import pandas as pd
from price_action import add_price_action_signals
from backtest import run_backtest

SETUPS = [
    ("Bullish CHoCH + Higher Low", "choch_hl_up"),
    ("V4.3 Baseline — Pullback-High Breakout", "choch_hl_breakout_up"),
    ("V4.4 Variant 1 — Breakout + Retest Hold", "choch_hl_breakout_retest_up"),
    ("V4.4 Benchmark — Strong Breakout 50%", "choch_hl_strong_breakout_up"),
    ("V4.5 Strong Breakout — 40% body", "choch_hl_strong_breakout_40_up"),
    ("V4.5 Strong Breakout — 50% body", "choch_hl_strong_breakout_50_up"),
    ("V4.5 Strong Breakout — 60% body", "choch_hl_strong_breakout_60_up"),
    ("Bullish CHoCH (baseline)", "choch_up"),
    ("Bearish CHoCH (comparison)", "choch_down"),
    ("Bullish BOS", "bos_up"),
    ("Bearish BOS", "bos_down"),
    ("Bullish liquidity sweep", "liquidity_sweep_up"),
    ("Bearish liquidity sweep", "liquidity_sweep_down"),
    ("Bullish opening-range break", "orb_up"),
    ("Bearish opening-range break", "orb_down"),
    ("Previous-day high break", "prev_day_break_up"),
    ("Previous-day low break", "prev_day_break_down"),
]

def run_setup_isolation(df, modes=("Intraday", "Positional"), **kwargs):
    signals = add_price_action_signals(df)
    summaries, details = [], {}
    for label, column in SETUPS:
        for mode in modes:
            _, trades, metrics = run_backtest(
                signals, mode=mode, signal_filter=column,
                signals_precomputed=True, **kwargs
            )
            key = f"{mode} | {label}"
            details[key] = trades
            summaries.append({
                "Mode": mode, "Setup": label, "Signal column": column,
                "Trades": metrics.get("Trades", 0),
                "Win rate %": metrics.get("Win rate %", 0.0),
                "Gross points": metrics.get("Gross points", 0.0),
                "Net points after slippage": metrics.get("Net points after slippage", 0.0),
                "Profit factor": metrics.get("Profit factor", 0.0),
                "Max drawdown points": metrics.get("Max drawdown points", 0.0),
                "Average net points/trade": metrics.get("Average net points/trade", 0.0),
                "Brokerage estimate ₹": metrics.get("Brokerage estimate ₹", 0.0),
            })
    return pd.DataFrame(summaries), details

def run_one_setup(signals, setup_column, mode, **kwargs):
    """Run one precomputed setup stream, keeping intraday/positional separate."""
    return run_backtest(
        signals, mode=mode, signal_filter=setup_column,
        signals_precomputed=True, **kwargs
    )
