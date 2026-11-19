"""V4.23 resumable monthly per-signal rejection diagnostics."""
from __future__ import annotations
import hashlib, json, os, tempfile
from pathlib import Path
import pandas as pd
from monthly_reconciliation import _read_month, _path
from monthly_reconciliation import load_progress
from rejection_diagnostics import build_v423_signal_ledger
from price_action import add_price_action_signals, add_breakout_retest_signals


def v423_key(start, end, stop, target, call_path, put_path, spot_path):
    def sig(p):
        try:
            st=os.stat(p); return [str(Path(p).resolve()), st.st_size, st.st_mtime_ns]
        except OSError: return [str(p), None]
    raw=[str(start),str(end),float(stop),float(target),sig(call_path),sig(put_path),sig(spot_path),"V4.13-60-70-12"]
    return hashlib.sha256(json.dumps(raw).encode()).hexdigest()[:20]


def run_v423_month(data_dir, key, period, spot_path, call_path, put_path, requested_start, requested_end, stop, target):
    p=pd.Period(period,freq="M")
    start=max(p.start_time,pd.Timestamp(requested_start).normalize())
    end=min((p+1).start_time,pd.Timestamp(requested_end).normalize()+pd.Timedelta(days=1))
    warmup=start-pd.Timedelta(days=45)
    spot=_read_month(spot_path,warmup,end)
    if spot.empty: raise ValueError(f"No Spot candles for {p}")
    spot=add_price_action_signals(spot)
    spot=add_breakout_retest_signals(spot,body_min=0.60,close_edge=0.70,max_wait_bars=12)
    spot=spot.loc[(spot.index>=start)&(spot.index<end)]
    call=_read_month(call_path,start,end); put=_read_month(put_path,start,end)
    if call.empty or put.empty: raise ValueError(f"CALL/PUT option history missing for {p}")
    lc,sc=build_v423_signal_ledger(spot,call,"CALL",start.date(),(end-pd.Timedelta(days=1)).date(),stop,target)
    lp,sp=build_v423_signal_ledger(spot,put,"PUT",start.date(),(end-pd.Timedelta(days=1)).date(),stop,target)
    ledger=pd.concat([lc,lp],ignore_index=True)
    summary=pd.concat([sc,sp],ignore_index=True)
    progress_path=Path(data_dir)/f"v423_progress_{key}.json"
    progress={}
    if progress_path.exists():
        with progress_path.open(encoding="utf-8") as f: progress=json.load(f)
    progress[str(p)]={"ledger":ledger.astype(object).where(pd.notna(ledger),None).to_dict("records"),
                      "summary":summary.astype(object).where(pd.notna(summary),None).to_dict("records")}
    progress_path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix=".v423_",suffix=".tmp",dir=progress_path.parent)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            json.dump(progress,f,allow_nan=False,default=str); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,progress_path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
    return ledger,summary


def load_v423_progress(data_dir, key):
    path=Path(data_dir)/f"v423_progress_{key}.json"
    if not path.exists(): return {}
    with path.open(encoding="utf-8") as f: value=json.load(f)
    if not isinstance(value,dict): raise ValueError("Invalid V4.23 progress file")
    return value
