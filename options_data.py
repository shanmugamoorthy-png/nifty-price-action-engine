"""Dhan expired-options downloader with one-calendar-day requests and fixed-strike coverage.

For each trading date the downloader fetches ATM first, determines the day's ATM
range from returned spot values, then fetches every ATM-relative offset needed to
follow an absolute strike through that session. Each API request covers exactly
one calendar date, as instructed by the user's Dhan Support email.
"""
from datetime import date, timedelta
from pathlib import Path
import random
import time
import pandas as pd
from dhan_api import DhanAPIError

STRIKE_STEP = 50
MAX_OFFSET = 10


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
    ts = pd.to_datetime(series["timestamp"][:n], unit="s", utc=True, errors="coerce")
    ts = ts.tz_convert("Asia/Kolkata").tz_localize(None)
    def values(key, default):
        vals = series.get(key)
        return [default] * n if vals is None else list(vals[:n])
    frame = pd.DataFrame({
        "timestamp": ts, "open": series["open"][:n], "high": series["high"][:n],
        "low": series["low"][:n], "close": series["close"][:n],
        "strike": values("strike", float("nan")), "spot": values("spot", float("nan")),
        "iv": values("iv", float("nan")), "volume": values("volume", 0), "oi": values("oi", 0),
    })
    frame = frame.dropna(subset=["timestamp"]).set_index("timestamp").sort_index()
    for col in ("open", "high", "low", "close", "strike", "spot", "iv", "volume", "oi"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=["open", "high", "low", "close", "strike"])
    return frame.loc[~frame.index.duplicated(keep="last")]


def _post_with_retry(client, payload, retries=4):
    for attempt in range(retries + 1):
        try:
            return client._post("/charts/rollingoption", payload)
        except DhanAPIError as exc:
            msg = str(exc)
            transient = any(t in msg for t in ("HTTP 429", "HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504", "network error"))
            if not transient or attempt >= retries:
                raise
            time.sleep(min(2 ** attempt, 12) + random.uniform(0.1, 0.4))


def _offset_label(offset):
    return "ATM" if offset == 0 else (f"ATM+{offset}" if offset > 0 else f"ATM{offset}")


def _load_or_fetch_day(client, day, option_type, offset, expiry_flag, expiry_code, interval,
                       cache_root, request_delay):
    label = _offset_label(offset)
    cache_file = cache_root / f"{day.isoformat()}_{label.replace('+','p').replace('-','m')}.csv" if cache_root else None
    if cache_file and cache_file.exists():
        try:
            cached = pd.read_csv(cache_file, parse_dates=["timestamp"], index_col="timestamp")
            cached.index = pd.DatetimeIndex(cached.index)
            if cached.index.tz is not None:
                cached.index = cached.index.tz_convert("Asia/Kolkata").tz_localize(None)
            cached["offset"] = offset
            return cached
        except Exception:
            pass
    payload = {
        "securityId": "13", "exchangeSegment": "NSE_FNO", "instrument": "OPTIDX",
        "expiryFlag": expiry_flag, "expiryCode": int(expiry_code), "strike": label,
        "drvOptionType": option_type,
        "requiredData": ["open", "high", "low", "close", "volume", "oi", "iv", "strike", "spot"],
        "fromDate": day.isoformat(), "toDate": day.isoformat(), "interval": int(interval),
    }
    if payload["fromDate"] != payload["toDate"]:
        raise AssertionError("Dhan expired-option requests must cover exactly one calendar date.")
    frame = _extract_series(_post_with_retry(client, payload), option_type)
    frame["offset"] = int(offset)
    if cache_file:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        save = frame.copy(); save.index.name = "timestamp"; save.to_csv(cache_file)
    if request_delay > 0:
        time.sleep(request_delay)
    return frame


def download_rolling_option(client, start: date, end_exclusive: date, option_type: str,
                            expiry_flag="WEEK", expiry_code=1, strike="ATM", interval=5,
                            progress=None, cache_dir=None, request_delay=1.05):
    """Return all daily relative-strike candles needed to follow fixed ATM-entry strikes.

    The first request for each day is ATM. Its spot range determines the offsets
    needed to follow any absolute strike selected at ATM during that session.
    The resulting frame can contain repeated timestamps, one row per absolute strike.
    """
    if option_type not in ("CALL", "PUT"):
        raise ValueError("option_type must be CALL or PUT")
    if expiry_flag not in ("WEEK", "MONTH") or expiry_code not in (1, 2, 3):
        raise ValueError("Invalid expiry flag/code")
    if interval not in (1, 5, 15, 25, 60):
        raise ValueError("Unsupported Dhan option interval")
    if strike != "ATM":
        raise ValueError("V4.8 currently selects ATM at entry; choose ATM in the UI")
    if start >= end_exclusive:
        raise ValueError("Start date must be before end date")
    days=[]; d=start
    while d < end_exclusive:
        days.append(d); d += timedelta(days=1)
    cache_root = Path(cache_dir) / f"{expiry_flag.lower()}_{expiry_code}_{interval}m" / option_type.lower() if cache_dir else None
    if cache_root: cache_root.mkdir(parents=True, exist_ok=True)
    all_frames=[]
    for day_no, day in enumerate(days, 1):
        atm = _load_or_fetch_day(client, day, option_type, 0, expiry_flag, expiry_code, interval, cache_root, request_delay)
        if atm.empty:
            if progress: progress(day_no/len(days), f"{option_type}: no candles on {day}; skipped")
            all_frames.append(atm)
            continue
        if "spot" not in atm or atm["spot"].dropna().empty:
            raise DhanAPIError(f"{day}: Dhan did not return spot values; cannot calculate fixed-contract strike coverage")
        # Prefer Dhan's returned absolute ATM strike values over re-rounding spot.
        atm_levels = atm["strike"].dropna()
        if atm_levels.empty:
            raise DhanAPIError(f"{day}: Dhan ATM response did not include absolute strike values")
        min_atm, max_atm = float(atm_levels.min()), float(atm_levels.max())
        span = int(round((max_atm-min_atm)/STRIKE_STEP))
        if span > MAX_OFFSET:
            raise DhanAPIError(f"{day}: intraday ATM moved {span} strikes; exceeds supported near-expiry ±{MAX_OFFSET} range. Narrow date or inspect this session.")
        offsets = range(-span, span+1)
        day_frames=[atm]
        for offset in offsets:
            if offset == 0: continue
            f = _load_or_fetch_day(client, day, option_type, offset, expiry_flag, expiry_code, interval, cache_root, request_delay)
            if not f.empty: day_frames.append(f)
        all_frames.extend(day_frames)
        if progress:
            progress(day_no/len(days), f"{option_type}: {day_no}/{len(days)} days; fetched ATM and {2*span} offset series for {day}")
    nonempty=[f for f in all_frames if f is not None and not f.empty]
    if not nonempty:
        raise DhanAPIError("Dhan returned no rolling option candles. Check token, entitlement, expiry settings and dates.")
    out=pd.concat(nonempty).sort_index()
    out=out.between_time("09:15", "15:30")
    out.index.name="timestamp"
    return out
