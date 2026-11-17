"""Dhan rolling expired-options downloader with one-calendar-day requests.

Dhan support confirmed to the user that expired-options history permits only
one day per API request. This module therefore iterates day-by-day, caches each
successful day separately, and resumes from cached days after an interruption.
"""
from datetime import date, timedelta
from pathlib import Path
import random
import time
import pandas as pd
from dhan_api import DhanAPIError


def _extract_series(body, option_type):
    if not isinstance(body, dict):
        raise DhanAPIError(f"Unexpected rolling-option response: {str(body)[:300]}")
    if str(body.get("status", "success")).lower() == "failure":
        raise DhanAPIError(str(body.get("remarks") or body))
    data = body.get("data", body)
    if not isinstance(data, dict):
        raise DhanAPIError(f"Unexpected rolling-option data: {str(data)[:300]}")
    keys = ("ce", "CALL", "call") if option_type == "CALL" else ("pe", "PUT", "put")
    series = next((data[k] for k in keys if k in data and isinstance(data[k], dict)), None)
    if series is None and all(k in data for k in ("timestamp", "open", "high", "low", "close")):
        series = data
    if series is None:
        raise DhanAPIError(f"No {option_type} candle series found. Response keys: {list(data.keys())}")
    required = ("timestamp", "open", "high", "low", "close")
    missing = [k for k in required if k not in series]
    if missing:
        raise DhanAPIError(f"Rolling option response missing {missing}; keys={list(series.keys())}")
    n = min(len(series[k]) for k in required)
    if n == 0:
        return pd.DataFrame(columns=["open", "high", "low", "close", "strike", "spot", "iv", "volume", "oi"])
    raw_ts = series["timestamp"][:n]
    ts = pd.to_datetime(raw_ts, unit="s", utc=True, errors="coerce")
    ts = ts.tz_convert("Asia/Kolkata").tz_localize(None)
    def values(key, default):
        vals = series.get(key)
        if vals is None:
            return [default] * n
        return list(vals[:n])
    frame = pd.DataFrame({
        "timestamp": ts,
        "open": series["open"][:n], "high": series["high"][:n],
        "low": series["low"][:n], "close": series["close"][:n],
        "strike": values("strike", float("nan")),
        "spot": values("spot", float("nan")),
        "iv": values("iv", float("nan")),
        "volume": values("volume", 0),
        "oi": values("oi", 0),
    })
    frame = frame.dropna(subset=["timestamp"]).set_index("timestamp").sort_index()
    for col in ("open", "high", "low", "close", "strike", "spot", "iv", "volume", "oi"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=["open", "high", "low", "close"])
    return frame.loc[~frame.index.duplicated(keep="last")]


def _post_with_retry(client, payload, retries=4):
    """Retry only transient rate-limit/server/network failures, not DH-905."""
    for attempt in range(retries + 1):
        try:
            return client._post("/charts/rollingoption", payload)
        except DhanAPIError as exc:
            msg = str(exc)
            transient = any(token in msg for token in ("HTTP 429", "HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504", "network error"))
            if not transient or attempt >= retries:
                raise
            time.sleep(min(2 ** attempt, 12) + random.uniform(0.1, 0.4))


def download_rolling_option(client, start: date, end_exclusive: date, option_type: str,
                            expiry_flag="WEEK", expiry_code=1, strike="ATM", interval=5,
                            progress=None, cache_dir=None, request_delay=1.05):
    """Download one calendar date per request; cache/resume each successful day.

    `end_exclusive` is an internal exclusive boundary. Dhan receives identical
    fromDate and toDate values for each request, exactly one calendar day.
    """
    if option_type not in ("CALL", "PUT"):
        raise ValueError("option_type must be CALL or PUT")
    if expiry_flag not in ("WEEK", "MONTH"):
        raise ValueError("expiry_flag must be WEEK or MONTH")
    if expiry_code not in (1, 2, 3):
        raise ValueError("Expired-option expiry_code must be 1 (near), 2 (next), or 3 (far).")
    if interval not in (1, 5, 15, 25, 60):
        raise ValueError("Unsupported Dhan option interval.")
    if start >= end_exclusive:
        raise ValueError("Start date must be before end date.")

    days = []
    cursor = start
    while cursor < end_exclusive:
        days.append(cursor)
        cursor += timedelta(days=1)
    total = len(days)
    cache_root = Path(cache_dir) if cache_dir else None
    if cache_root:
        cache_root = cache_root / f"{expiry_flag.lower()}_{expiry_code}_{interval}m" / option_type.lower()
        cache_root.mkdir(parents=True, exist_ok=True)

    frames = []
    for idx, day in enumerate(days, start=1):
        cache_file = cache_root / f"{day.isoformat()}.csv" if cache_root else None
        if cache_file and cache_file.exists():
            try:
                cached = pd.read_csv(cache_file, parse_dates=["timestamp"], index_col="timestamp")
                cached.index = pd.DatetimeIndex(cached.index)
                if cached.index.tz is not None:
                    cached.index = cached.index.tz_convert("Asia/Kolkata").tz_localize(None)
                frames.append(cached)
                if progress:
                    progress(idx / total, f"{option_type}: reused saved day {idx}/{total} ({day})")
                continue
            except Exception:
                # A damaged cache file is fetched again and replaced below.
                pass

        payload = {
            "securityId": "13", "exchangeSegment": "NSE_FNO", "instrument": "OPTIDX",
            "expiryFlag": expiry_flag, "expiryCode": int(expiry_code),
            "strike": strike, "drvOptionType": option_type,
            "requiredData": ["open", "high", "low", "close", "volume", "oi", "iv", "strike", "spot"],
            "fromDate": day.isoformat(), "toDate": day.isoformat(),
            "interval": int(interval),
        }
        # Strict guard: Dhan support confirmed one calendar date per request.
        if payload["fromDate"] != payload["toDate"]:
            raise AssertionError("Rolling-option requests must contain exactly one calendar date.")
        body = _post_with_retry(client, payload)
        part = _extract_series(body, option_type)
        if not part.empty:
            frames.append(part)
        # Persist even an empty day so weekends/holidays are not requested again.
        if cache_file:
            save = part.copy()
            save.index.name = "timestamp"
            save.to_csv(cache_file)
        if progress:
            progress(idx / total, f"{option_type}: completed day {idx}/{total} ({day}); saved for resume")
        if idx < total and request_delay > 0:
            time.sleep(request_delay)

    if not frames or not any(not f.empty for f in frames):
        raise DhanAPIError("Dhan returned no rolling option candles for this period. Check token, expired-options entitlement, expiry settings and date range.")
    out = pd.concat(frames).sort_index()
    out = out.loc[~out.index.duplicated(keep="last")]
    out = out.between_time("09:15", "15:30")
    return out
