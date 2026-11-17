"""Dhan rolling expired-options data access for NIFTY option-buying research.

Uses Dhan's /charts/rollingoption endpoint. Dhan documents rolling expired
options history for up to five years, in requests of at most 30 calendar days.
"""
from datetime import date, timedelta
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
        return pd.DataFrame(columns=["open", "high", "low", "close", "strike", "spot"])
    ts = pd.to_datetime(series["timestamp"][:n], unit="s", utc=True, errors="coerce")
    ts = ts.tz_convert("Asia/Kolkata").tz_localize(None)
    frame = pd.DataFrame({
        "timestamp": ts,
        "open": series["open"][:n], "high": series["high"][:n],
        "low": series["low"][:n], "close": series["close"][:n],
        "strike": series.get("strike", [float("nan")] * n)[:n],
        "spot": series.get("spot", [float("nan")] * n)[:n],
        "iv": series.get("iv", [float("nan")] * n)[:n],
        "volume": series.get("volume", [0] * n)[:n],
        "oi": series.get("oi", [0] * n)[:n],
    })
    frame = frame.dropna(subset=["timestamp"]).set_index("timestamp").sort_index()
    for col in ("open", "high", "low", "close", "strike", "spot", "iv", "volume", "oi"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=["open", "high", "low", "close"])
    return frame.loc[~frame.index.duplicated(keep="last")]


def download_rolling_option(client, start: date, end_exclusive: date, option_type: str,
                            expiry_flag="WEEK", expiry_code=1, strike="ATM", interval=5,
                            progress=None):
    """Download rolling near-expiry NIFTY option candles in <=30-day chunks."""
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
    chunks, cursor, batch = [], start, 0
    total = ((end_exclusive - start).days + 29) // 30
    while cursor < end_exclusive:
        chunk_end = min(cursor + timedelta(days=30), end_exclusive)
        payload = {
            "securityId": "13", "exchangeSegment": "NSE_FNO", "instrument": "OPTIDX",
            "expiryFlag": expiry_flag, "expiryCode": int(expiry_code),
            "strike": strike, "drvOptionType": option_type,
            "requiredData": ["open", "high", "low", "close", "strike", "spot", "iv", "volume"],
            "fromDate": cursor.isoformat(), "toDate": chunk_end.isoformat(),
            "interval": int(interval),
        }
        body = client._post("/charts/rollingoption", payload)
        part = _extract_series(body, option_type)
        if not part.empty:
            chunks.append(part)
        batch += 1
        if progress:
            progress(batch / max(total, 1), f"{option_type}: downloaded {batch}/{total} chunks ({cursor} to {chunk_end})")
        cursor = chunk_end
    if not chunks:
        raise DhanAPIError("Dhan returned no rolling option candles. Check API entitlement, token, expiry settings and date range.")
    out = pd.concat(chunks).sort_index()
    out = out.loc[~out.index.duplicated(keep="last")]
    out = out.between_time("09:15", "15:30")
    return out
