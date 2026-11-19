"""V4.22 incremental diagnostic. Each click handles one month and persists output atomically.

Important: month-isolated trades cannot exactly reproduce a continuous multi-month
backtest because open positions and signals at month boundaries are not carried.
"""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import pandas as pd


def progress_key(start, end, stop, target, call_path, put_path, spot_path):
    def signature(p):
        try:
            s = os.stat(p)
            return [str(Path(p).resolve()), s.st_size, s.st_mtime_ns]
        except OSError:
            return [str(p), None]
    payload = [str(start), str(end), float(stop), float(target), signature(call_path), signature(put_path), signature(spot_path)]
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest()[:20]


def _path(data_dir, key):
    return Path(data_dir) / f"v422_progress_{key}.json"


def load_progress(data_dir, key):
    p = _path(data_dir, key)
    if not p.exists():
        return {}
    with p.open(encoding='utf-8') as f:
        value = json.load(f)
    if not isinstance(value, dict):
        raise ValueError('Invalid saved progress format')
    return value


def reset_progress(data_dir, key):
    _path(data_dir, key).unlink(missing_ok=True)


def _read_month(path, start, end, chunksize=20000):
    parts=[]
    for chunk in pd.read_csv(path, parse_dates=['timestamp'], chunksize=chunksize):
        mask = chunk['timestamp'].between(start, end, inclusive='left')
        if mask.any():
            parts.append(chunk.loc[mask])
    if not parts:
        return pd.DataFrame(index=pd.DatetimeIndex([], name='timestamp'))
    return pd.concat(parts, ignore_index=True).set_index('timestamp').sort_index()


def run_one_month(data_dir, key, period, spot_path, call_path, put_path, requested_start, requested_end, stop, target):
    from price_action import add_price_action_signals, add_breakout_retest_signals
    from trade_reconciliation import build_v421_reconciliation
    period = pd.Period(period, freq='M')
    start = max(period.start_time, pd.Timestamp(requested_start).normalize())
    end = min((period + 1).start_time, pd.Timestamp(requested_end).normalize() + pd.Timedelta(days=1))
    if start >= end:
        raise ValueError('Month outside selected period')
    # Warm up price-action structure on 45 preceding calendar days.
    warmup = start - pd.Timedelta(days=45)
    spot = _read_month(spot_path, warmup, end)
    if spot.empty:
        raise ValueError(f'No saved Spot candles for {period}')
    spot = add_price_action_signals(spot)
    spot = add_breakout_retest_signals(spot, body_min=0.60, close_edge=0.70, max_wait_bars=12)
    spot = spot.loc[(spot.index >= start) & (spot.index < end)].copy()
    call = _read_month(call_path, start, end)
    put = _read_month(put_path, start, end)
    if call.empty or put.empty:
        raise ValueError(f'CALL/PUT data absent for {period}; month not marked complete')
    result = build_v421_reconciliation(spot, call, put, start.date(), (end-pd.Timedelta(days=1)).date(), stop, target)
    progress = load_progress(data_dir, key)
    progress[str(period)] = result.to_dict(orient='records')
    p = _path(data_dir, key)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.v422_', suffix='.tmp', dir=p.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(progress, f, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return result
