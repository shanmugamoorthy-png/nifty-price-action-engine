import pandas as pd
from price_action import add_price_action_signals
from backtest import run_backtest

# Each setup is tested as a standalone, mutually exclusive signal stream.
# Long and short variants are separate portfolios to prevent overlapping labels
# from being counted as independent trades.
SETUPS = [
    ("Bullish CHoCH", "choch_up"),
    ("Bearish CHoCH", "choch_down"),
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
    """
    Run each price-action event as an isolated strategy.
    Returns a summary DataFrame and a dict of detailed trade DataFrames.
    Signals are generated once, then filtered to one event/direction at a time.
    """
    signals = add_price_action_signals(df)
    summaries = []
    details = {}
    for label, column in SETUPS:
        for mode in modes:
            _, trades, metrics = run_backtest(
                signals,
                mode=mode,
                signal_filter=column,
                signals_precomputed=True,
                **kwargs,
            )
            key = f"{mode} | {label}"
            details[key] = trades
            summaries.append({
                "Mode": mode,
                "Setup": label,
                "Signal column": column,
                "Trades": metrics.get("Trades", 0),
                "Win rate %": metrics.get("Win rate %", 0.0),
                "Gross points": metrics.get("Gross points", 0.0),
                "Net points after slippage": metrics.get("Net points after slippage", 0.0),
                "Profit factor": metrics.get("Profit factor", 0.0),
                "Max drawdown points": metrics.get("Max drawdown points", 0.0),
                "Average net points/trade": metrics.get("Average net points/trade", 0.0),
                "Brokerage estimate ₹": metrics.get("Brokerage estimate ₹", 0.0),
            })
    summary = pd.DataFrame(summaries)
    return summary, details

def setup_signal_counts(df):
    signals = add_price_action_signals(df)
    rows = []
    for label, column in SETUPS:
        rows.append({"Setup": label, "Signal candles": int(signals[column].fillna(False).sum())})
    return pd.DataFrame(rows)
