
import os, time, sqlite3, logging
from datetime import datetime, timedelta
import pandas as pd
from dhan_api import DhanClient
from price_action import add_price_action_signals
from telegram_alert import send_telegram
from config import DATA_DIR

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
DB_PATH = os.path.join(DATA_DIR, "alert_state.sqlite3")
MODES = ("Intraday", "Positional")

def db():
    return sqlite3.connect(DB_PATH)

def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    with db() as con:
        con.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        con.execute("""CREATE TABLE IF NOT EXISTS positions (
            mode TEXT PRIMARY KEY, side INTEGER, entry REAL, sl REAL, target REAL, entry_time TEXT
        )""")

def get_state(key):
    with db() as con:
        row = con.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return row[0] if row else None

def set_state(key, value):
    with db() as con:
        con.execute("INSERT INTO state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, str(value)))

def get_position(mode):
    with db() as con:
        row = con.execute("SELECT side,entry,sl,target,entry_time FROM positions WHERE mode=?", (mode,)).fetchone()
    if not row:
        return None
    return {"side": int(row[0]), "entry": float(row[1]), "sl": float(row[2]),
            "target": float(row[3]), "entry_time": row[4]}

def save_position(mode, pos):
    with db() as con:
        if pos is None:
            con.execute("DELETE FROM positions WHERE mode=?", (mode,))
        else:
            con.execute("""INSERT INTO positions(mode,side,entry,sl,target,entry_time)
                VALUES(?,?,?,?,?,?) ON CONFLICT(mode) DO UPDATE SET
                side=excluded.side,entry=excluded.entry,sl=excluded.sl,target=excluded.target,entry_time=excluded.entry_time""",
                (mode, pos["side"], pos["entry"], pos["sl"], pos["target"], pos["entry_time"]))

def notify(text):
    ok, info = send_telegram(text)
    if not ok:
        logging.error("Telegram send failed: %s", info)
    return ok

def alert_entry(mode, side, ts, entry, sl, target, reason):
    direction = "BUY" if side == 1 else "SELL"
    return notify(
        f"NIFTY PURE PRICE ACTION — {mode.upper()} ENTRY\nDirection: {direction}\n"
        f"Time (IST): {ts}\nEntry: {entry:.2f}\nStop: {sl:.2f}\nTarget: {target:.2f}\n"
        f"Setup: {reason}\nIndex-point paper signal only; no order placed."
    )

def alert_exit(mode, pos, ts, exit_price, reason):
    points = (exit_price - pos["entry"]) * pos["side"]
    direction = "BUY" if pos["side"] == 1 else "SELL"
    return notify(
        f"NIFTY PURE PRICE ACTION — {mode.upper()} EXIT\nPosition: {direction}\n"
        f"Entry: {pos['entry']:.2f}\nExit: {exit_price:.2f}\n"
        f"Approx index points: {points:.2f}\nTime (IST): {ts}\nReason: {reason}\n"
        "Paper tracking only; no broker order placed."
    )

def adverse(price, side, is_entry, slip):
    return float(price + (side if is_entry else -side) * slip)

def process_bar(mode, ts, row, previous_signal, previous_reason, stop, target, slip):
    pos = get_position(mode)
    op, hi, lo, cl = map(float, (row["open"], row["high"], row["low"], row["close"]))

    # Opposite completed-candle signal exits at current candle open.
    if pos and previous_signal == -pos["side"]:
        fill = adverse(op, pos["side"], False, slip)
        alert_exit(mode, pos, ts, fill, "Opposite price-action signal")
        save_position(mode, None)
        pos = None

    # Check an existing position against this candle.
    if pos:
        side = pos["side"]
        exit_raw, reason = None, None
        if side == 1:
            if op <= pos["sl"]: exit_raw, reason = op, "Stop loss gap"
            elif op >= pos["target"]: exit_raw, reason = pos["target"], "Target"
            elif lo <= pos["sl"] and hi >= pos["target"]: exit_raw, reason = pos["sl"], "Stop loss (both touched; conservative)"
            elif lo <= pos["sl"]: exit_raw, reason = pos["sl"], "Stop loss"
            elif hi >= pos["target"]: exit_raw, reason = pos["target"], "Target"
        else:
            if op >= pos["sl"]: exit_raw, reason = op, "Stop loss gap"
            elif op <= pos["target"]: exit_raw, reason = pos["target"], "Target"
            elif hi >= pos["sl"] and lo <= pos["target"]: exit_raw, reason = pos["sl"], "Stop loss (both touched; conservative)"
            elif hi >= pos["sl"]: exit_raw, reason = pos["sl"], "Stop loss"
            elif lo <= pos["target"]: exit_raw, reason = pos["target"], "Target"
        if exit_raw is None and mode == "Intraday" and ts.strftime("%H:%M") >= "15:15":
            exit_raw, reason = cl, "Intraday square-off"
        if exit_raw is not None:
            fill = adverse(exit_raw, side, False, slip)
            alert_exit(mode, pos, ts, fill, reason)
            save_position(mode, None)
            pos = None

    # New signal from previous completed candle enters at this candle open.
    if pos is None and previous_signal in (-1, 1):
        if mode == "Intraday" and ts.strftime("%H:%M") >= "15:00":
            return
        side = int(previous_signal)
        entry = adverse(op, side, True, slip)
        pos = {"side": side, "entry": entry, "sl": entry - side*stop,
               "target": entry + side*target, "entry_time": str(ts)}
        save_position(mode, pos)
        alert_entry(mode, side, ts, entry, pos["sl"], pos["target"], previous_reason)
        # Conservatively test entry candle's remaining OHLC range.
        exit_raw, reason = None, None
        if side == 1:
            if lo <= pos["sl"] and hi >= pos["target"]: exit_raw, reason = pos["sl"], "Stop loss (both touched; conservative)"
            elif lo <= pos["sl"]: exit_raw, reason = pos["sl"], "Stop loss"
            elif hi >= pos["target"]: exit_raw, reason = pos["target"], "Target"
        else:
            if hi >= pos["sl"] and lo <= pos["target"]: exit_raw, reason = pos["sl"], "Stop loss (both touched; conservative)"
            elif hi >= pos["sl"]: exit_raw, reason = pos["sl"], "Stop loss"
            elif lo <= pos["target"]: exit_raw, reason = pos["target"], "Target"
        if exit_raw is not None:
            fill = adverse(exit_raw, side, False, slip)
            alert_exit(mode, pos, ts, fill, reason)
            save_position(mode, None)

def main():
    init_db()
    client = DhanClient(os.getenv("DHAN_ACCESS_TOKEN", ""), os.getenv("DHAN_CLIENT_ID", ""))
    poll = max(30, int(os.getenv("POLL_SECONDS", "60")))
    stop = float(os.getenv("LIVE_STOP_POINTS", "30"))
    target = float(os.getenv("LIVE_TARGET_POINTS", "60"))
    slip = float(os.getenv("LIVE_SLIPPAGE_POINTS", "1"))
    logging.info("Alert worker started; independent Intraday and Positional virtual portfolios.")
    while True:
        try:
            today = datetime.now().date()
            frame = client.intraday(today - timedelta(days=7), today + timedelta(days=1), interval=5)
            if frame.empty:
                time.sleep(poll); continue
            now = pd.Timestamp.now(tz="Asia/Kolkata").tz_localize(None)
            closed = frame[frame.index + pd.Timedelta(minutes=5) <= now]
            if len(closed) < 10:
                time.sleep(poll); continue
            signals = add_price_action_signals(closed)
            last_done = get_state("last_processed_bar")
            if last_done is None:
                # Avoid replaying historical alerts on first worker boot.
                set_state("last_processed_bar", signals.index[-1].isoformat())
                logging.info("Initialised at latest completed candle %s; waiting for next candle.", signals.index[-1])
            else:
                pending = signals[signals.index > pd.Timestamp(last_done)]
                for ts, row in pending.iterrows():
                    loc = signals.index.get_loc(ts)
                    previous = signals.iloc[loc-1] if loc > 0 else None
                    prev_signal = int(previous["signal"]) if previous is not None else 0
                    prev_reason = str(previous.get("signal_reason", "")) if previous is not None else ""
                    for mode in MODES:
                        process_bar(mode, ts, row, prev_signal, prev_reason, stop, target, slip)
                    set_state("last_processed_bar", ts.isoformat())
            time.sleep(poll)
        except Exception:
            logging.exception("Alert worker iteration failed")
            time.sleep(poll)

if __name__ == "__main__":
    main()
