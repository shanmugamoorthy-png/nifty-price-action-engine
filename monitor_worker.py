"""Always-on completed-candle Telegram monitor. No orders are placed."""
import os, json, time, logging
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd
from dhan_api import DhanClient
from price_action import add_price_action_signals
from telegram_alert import send_telegram

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
STATE = Path(os.getenv("STATE_FILE", "data/live_state.json"))
STOP = float(os.getenv("STOP_POINTS", "30"))
TARGET = float(os.getenv("TARGET_POINTS", "60"))
MODE = os.getenv("LIVE_MODE", "Intraday").strip().title()
POLL = int(os.getenv("POLL_SECONDS", "30"))

def load_state():
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        return json.loads(STATE.read_text())
    except Exception:
        return {"last_bar": None, "position": None}

def save_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, default=str))
    tmp.replace(STATE)

def run_once(client):
    end = datetime.now().date()
    df = client.intraday(end - timedelta(days=7), end, interval=5)
    if df.empty:
        logging.info("No recent candles")
        return
    now = pd.Timestamp.now(tz="Asia/Kolkata").tz_localize(None)
    df = df[df.index + pd.Timedelta(minutes=5) <= now]
    if df.empty:
        return
    x = add_price_action_signals(df)
    row = x.iloc[-1]
    if (now - x.index[-1]) > pd.Timedelta(minutes=12):
        logging.info("Latest completed candle is stale; waiting for next market candle")
        return
    bar_time = str(x.index[-1])
    state = load_state()
    if state.get("last_bar") == bar_time:
        return
    state["last_bar"] = bar_time
    close = float(row.close)
    pos = state.get("position")
    if pos:
        side = int(pos["side"])
        exit_reason = None
        if side == 1 and float(row.low) <= float(pos["sl"]): exit_reason = "Stop loss"
        elif side == -1 and float(row.high) >= float(pos["sl"]): exit_reason = "Stop loss"
        elif side == 1 and float(row.high) >= float(pos["target"]): exit_reason = "Target"
        elif side == -1 and float(row.low) <= float(pos["target"]): exit_reason = "Target"
        elif MODE == "Intraday" and x.index[-1].strftime("%H:%M") >= "15:20": exit_reason = "Intraday square-off"
        elif int(row.signal) == -side: exit_reason = "Opposite price-action signal"
        if exit_reason:
            pnl = (close - float(pos["entry"])) * side
            ok, detail = send_telegram(f"NIFTY PURE PRICE ACTION — EXIT\nSide: {'BUY' if side==1 else 'SELL'}\nTime: {bar_time}\nExit reference: {close:.2f}\nReason: {exit_reason}\nApprox index points: {pnl:.2f}")
            logging.info("Exit alert sent=%s detail=%s", ok, detail)
            state["position"] = None
            pos = None
    sig = int(row.signal)
    if pos is None and sig:
        entry = close
        sl = entry - sig * STOP
        target = entry + sig * TARGET
        flags = [k for k in ["bos_up","bos_down","orb_up","orb_down","pd_break_up","pd_break_down","retest_up","retest_down","false_up","false_down"] if bool(row.get(k, False))]
        side_text = "BUY" if sig == 1 else "SELL"
        message = (f"NIFTY PURE PRICE ACTION — {side_text}\nTime: {bar_time}\nClose/entry reference: {entry:.2f}\n"
                   f"Stop loss: {sl:.2f}\nTarget: {target:.2f}\nSetup: {', '.join(flags)}\n"
                   "Index points only; no automatic order placed.")
        ok, info = send_telegram(message)
        logging.info("Entry alert sent=%s info=%s", ok, info)
        state["position"] = {"side": sig, "entry": entry, "sl": sl, "target": target, "time": bar_time}
    save_state(state)

def main():
    token = os.getenv("DHAN_ACCESS_TOKEN", "")
    if not token:
        raise RuntimeError("DHAN_ACCESS_TOKEN is required")
    client = DhanClient(token, os.getenv("DHAN_CLIENT_ID", ""))
    logging.info("NIFTY monitor started: mode=%s poll=%ss", MODE, POLL)
    while True:
        try:
            run_once(client)
        except Exception:
            logging.exception("Monitor iteration failed")
        time.sleep(POLL)

if __name__ == "__main__":
    main()
