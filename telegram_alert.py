import requests
import os

def send_telegram(text):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False, "Telegram token/chat ID not configured."
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=15)
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            return False, str(data)
        return True, "Sent"
    except Exception as e:
        return False, str(e)

def format_alert(signal, timestamp, close, entry, sl, target, reason, event="ENTRY"):
    side = "BUY" if signal == 1 else "SELL"
    return (
        f"NIFTY PURE PRICE ACTION — {event}\n"
        f"Signal: {side}\nTime: {timestamp}\n"
        f"Close: {close:.2f}\nEntry reference: {entry:.2f}\n"
        f"Stop loss: {sl:.2f}\nTarget: {target:.2f}\n"
        f"Setup: {reason}\n"
        "Index-point signal only; no automatic order placed."
    )
