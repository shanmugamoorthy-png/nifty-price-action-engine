"""Compact diagnostics for locally saved NIFTY spot and option premium history."""
from __future__ import annotations
import json
from pathlib import Path
import pandas as pd


def _read_option(path: str | Path):
    p = Path(path)
    if not p.exists():
        return None, {"exists": False, "file": p.name}
    df = pd.read_csv(p, low_memory=False)
    if df.empty:
        return df, {"exists": True, "file": p.name, "rows": 0, "empty": True}
    cols = {str(c).lower(): c for c in df.columns}
    if "timestamp" not in cols:
        raise ValueError(f"{p.name}: missing timestamp column")
    ts = pd.to_datetime(df[cols["timestamp"]], errors="coerce")
    if getattr(ts.dt, "tz", None) is not None:
        ts = ts.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    df["_ts"] = ts
    df["_date"] = ts.dt.strftime("%Y-%m-%d")
    strike_col = cols.get("strike")
    if strike_col:
        df["_strike"] = pd.to_numeric(df[strike_col], errors="coerce")
    else:
        df["_strike"] = pd.NA
    valid = df.dropna(subset=["_ts"])
    key_dupes = int(valid.duplicated(subset=["_ts", "_strike"], keep=False).sum()) if strike_col else int(valid.duplicated(subset=["_ts"], keep=False).sum())
    dates = sorted(valid["_date"].dropna().unique().tolist())
    ts_unique = set(valid["_ts"].dropna().tolist())
    info = {
        "exists": True, "file": p.name, "bytes": p.stat().st_size,
        "rows": int(len(df)), "valid_timestamp_rows": int(len(valid)),
        "invalid_timestamp_rows": int(df["_ts"].isna().sum()),
        "duplicate_timestamp_strike_rows": key_dupes,
        "first_timestamp": str(valid["_ts"].min()) if len(valid) else None,
        "last_timestamp": str(valid["_ts"].max()) if len(valid) else None,
        "first_date": dates[0] if dates else None, "last_date": dates[-1] if dates else None,
        "unique_dates": len(dates), "unique_timestamps": len(ts_unique),
        "dates": dates,
        "strike_missing_rows": int(valid["_strike"].isna().sum()) if strike_col else int(len(valid)),
        "unique_strikes": int(valid["_strike"].nunique()) if strike_col else 0,
    }
    return valid, info


def build_diagnostic_report(spot: pd.DataFrame, call_path: str | Path, put_path: str | Path, manifest_path: str | Path | None = None):
    if spot is None or spot.empty:
        raise ValueError("Saved NIFTY Spot history is empty")
    spot_idx = pd.to_datetime(spot.index, errors="coerce")
    if getattr(spot_idx, "tz", None) is not None:
        spot_idx = spot_idx.tz_convert("Asia/Kolkata").tz_localize(None)
    spot_idx = pd.DatetimeIndex(spot_idx.dropna())
    spot_dates = sorted(set(spot_idx.strftime("%Y-%m-%d")))
    spot_ts = set(spot_idx.tolist())
    call, ci = _read_option(call_path)
    put, pi = _read_option(put_path)
    all_option_dates = sorted(set(ci.get("dates", [])) | set(pi.get("dates", [])))
    if all_option_dates:
        expected_spot_dates = [d for d in spot_dates if all_option_dates[0] <= d <= all_option_dates[-1]]
    else:
        expected_spot_dates = []
    for frame, info in ((call, ci), (put, pi)):
        if frame is not None and not frame.empty:
            ots = set(frame["_ts"].dropna().tolist())
            exact = len(ots & spot_ts)
            within5 = sum(1 for t in ots if t in spot_ts or (t - pd.Timedelta(minutes=5)) in spot_ts or (t + pd.Timedelta(minutes=5)) in spot_ts)
            info["unique_option_timestamps_matching_spot_exact"] = exact
            info["exact_timestamp_match_pct"] = round(100 * exact / len(ots), 2) if ots else 0.0
            info["unique_option_timestamps_matching_spot_within_5m"] = within5
            info["within_5m_match_pct"] = round(100 * within5 / len(ots), 2) if ots else 0.0
            info["spot_trading_dates_missing_from_options_within_option_history"] = sorted(set(expected_spot_dates) - set(info["dates"]))
            info["option_dates_not_in_spot"] = sorted(set(info["dates"]) - set(spot_dates))
    manifest = {}
    if manifest_path and Path(manifest_path).exists():
        try:
            manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        except Exception as exc:
            manifest = {"read_error": str(exc)}
    report = {
        "report_version": "V4.17", "purpose": "Read-only local dataset diagnostics; no Dhan API calls and no source CSV modifications.",
        "spot": {
            "rows": int(len(spot)), "first_timestamp": str(spot_idx.min()) if len(spot_idx) else None,
            "last_timestamp": str(spot_idx.max()) if len(spot_idx) else None,
            "trading_dates": len(spot_dates), "first_date": spot_dates[0] if spot_dates else None,
            "last_date": spot_dates[-1] if spot_dates else None,
            "zero_or_negative_volume_rows": int((pd.to_numeric(spot.get("volume", pd.Series(index=spot.index, dtype=float)), errors="coerce") <= 0).sum()) if "volume" in spot.columns else None,
        },
        "CALL": ci, "PUT": pi,
        "call_put_date_intersection": sorted(set(ci.get("dates", [])) & set(pi.get("dates", []))),
        "call_dates_missing_from_put": sorted(set(ci.get("dates", [])) - set(pi.get("dates", []))),
        "put_dates_missing_from_call": sorted(set(pi.get("dates", [])) - set(ci.get("dates", []))),
        "coverage_manifest": manifest,
        "warnings": [
            "This report checks file structure and timestamp coverage; it does not prove every expiry/strike is tradable.",
            "Historical lot size is not inferred. Verify the correct lot size for each historical contract/expiry before calculating rupee P&L.",
            "Option rows at timestamps outside Spot candle times (for example a final 15:30 option candle) are reported, not silently discarded.",
        ],
    }
    return report
