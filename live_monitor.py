import pandas as pd
from datetime import datetime, timedelta
from dhan_api import DhanClient
from price_action import add_price_action_signals
from telegram_alert import send_telegram, format_alert

def fetch_recent(client):
    end = datetime.now().date()
    start = end - timedelta(days=7)
    return client.intraday(start, end, interval=5)

def check_latest_signal(client, stop_points=30, target_points=60):
    df = fetch_recent(client)
    if df.empty:
        return None, "No recent candles returned."
    # Drop the still-forming candle conservatively: only process bars whose
    # timestamp + 5 minutes is no later than current IST time.
    now = pd.Timestamp.now(tz="Asia/Kolkata").tz_localize(None)
    df = df[df.index + pd.Timedelta(minutes=5) <= now]
    if df.empty:
        return None, "Waiting for a completed 5-minute candle."
    x = add_price_action_signals(df)
    row = x.iloc[-1]
    sig = int(row["signal"])
    if sig == 0:
        return None, f"No new setup. Latest completed candle: {x.index[-1]}"
    close = float(row["close"])
    sl = close - sig * stop_points
    target = close + sig * target_points
    flags = [k for k in ["bos_up","bos_down","orb_up","orb_down","pd_break_up","pd_break_down","retest_up","retest_down","false_up","false_down"] if bool(row.get(k, False))]
    reason = ", ".join(flags) or "Combined price-action setup"
    msg = format_alert(sig, x.index[-1], close, close, sl, target, reason)
    ok, status = send_telegram(msg)
    return {"time": str(x.index[-1]), "signal": sig, "close": close, "message": msg, "sent": ok}, status
