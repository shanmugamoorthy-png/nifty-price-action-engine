import os
import requests

def send_telegram(message):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False, "Telegram token/chat ID not configured."
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        return (True, "Sent") if payload.get("ok") else (False, str(payload))
    except Exception as exc:
        return False, str(exc)

def build_signal_message(event, signal, timestamp, close, sl, target, reason, mode="5m"):
    side = "BUY" if signal == 1 else "SELL"
    return (
        f"NIFTY PURE PRICE ACTION — {event}\n"
        f"Direction: {side}\nTime (IST): {timestamp}\n"
        f"Close: {close:.2f}\nStop: {sl:.2f}\nTarget: {target:.2f}\n"
        f"Timeframe: {mode}\nSetup: {reason}\n"
        "Index points only; no order has been placed."
    )
