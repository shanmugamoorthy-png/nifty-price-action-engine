import requests
import pandas as pd
from datetime import date, timedelta
from config import DHAN_BASE_URL, SECURITY_ID, EXCHANGE_SEGMENT, INSTRUMENT

class DhanAPIError(RuntimeError):
    pass

class DhanClient:
    def __init__(self, access_token: str, client_id: str = ""):
        if not access_token:
            raise DhanAPIError("DHAN_ACCESS_TOKEN is missing.")
        self.session = requests.Session()
        self.session.headers.update({
            "access-token": access_token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        # Dhan chart endpoints authenticate with access-token. client-id is retained
        # for consistency with other Dhan endpoints but is not required here.
        self.client_id = client_id

    def _post(self, endpoint, payload):
        try:
            response = self.session.post(f"{DHAN_BASE_URL}{endpoint}", json=payload, timeout=60)
        except requests.RequestException as exc:
            raise DhanAPIError(f"Dhan network error: {exc}") from exc
        if response.status_code != 200:
            raise DhanAPIError(f"Dhan HTTP {response.status_code}: {response.text[:700]}")
        try:
            body = response.json()
        except ValueError as exc:
            raise DhanAPIError(f"Dhan returned invalid JSON: {response.text[:300]}") from exc
        if isinstance(body, dict) and str(body.get("status", "")).lower() == "failure":
            raise DhanAPIError(str(body.get("remarks") or body))
        return body

    @staticmethod
    def _parse_candles(body):
        data = body.get("data", body) if isinstance(body, dict) else body
        if not isinstance(data, dict):
            raise DhanAPIError(f"Unexpected candle response: {str(body)[:300]}")
        required = ["timestamp", "open", "high", "low", "close"]
        missing = [key for key in required if key not in data]
        if missing:
            raise DhanAPIError(f"Missing candle fields {missing}; received keys {list(data.keys())}")
        n = min(len(data[key]) for key in required)
        if n == 0:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        raw_ts = data["timestamp"][:n]
        ts = pd.to_datetime(raw_ts, unit="s", utc=True, errors="coerce")
        if ts.isna().all():
            ts = pd.to_datetime(raw_ts, errors="coerce")
            if getattr(ts, "tz", None) is not None:
                ts = ts.tz_convert("Asia/Kolkata").tz_localize(None)
        else:
            ts = ts.tz_convert("Asia/Kolkata").tz_localize(None)
        frame = pd.DataFrame({
            "timestamp": ts,
            "open": data["open"][:n],
            "high": data["high"][:n],
            "low": data["low"][:n],
            "close": data["close"][:n],
            "volume": data.get("volume", [0] * n)[:n],
        })
        frame = frame.dropna(subset=["timestamp"]).set_index("timestamp").sort_index()
        for col in ["open", "high", "low", "close", "volume"]:
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        return frame.dropna(subset=["open", "high", "low", "close"]).loc[
            lambda f: ~f.index.duplicated(keep="last")
        ]

    def intraday(self, start: date, end_exclusive: date, interval=5):
        if interval not in (1, 5, 15, 25, 60):
            raise ValueError("Dhan interval must be 1, 5, 15, 25 or 60.")
        payload = {
            "securityId": str(SECURITY_ID),
            "exchangeSegment": EXCHANGE_SEGMENT,
            "instrument": INSTRUMENT,
            "interval": str(interval),
            "oi": False,
            "fromDate": f"{start:%Y-%m-%d} 09:00:00",
            "toDate": f"{end_exclusive:%Y-%m-%d} 15:45:00",
        }
        return self._parse_candles(self._post("/charts/intraday", payload))

    def download_five_years(self, interval=5, progress=None):
        # Dhan allows a maximum 90-calendar-day window per intraday request.
        end_exclusive = date.today() + timedelta(days=1)
        cursor = end_exclusive - timedelta(days=365 * 5 + 2)
        chunks, batch = [], 0
        total = ((end_exclusive - cursor).days + 84) // 85
        while cursor < end_exclusive:
            chunk_end_exclusive = min(cursor + timedelta(days=85), end_exclusive)
            part = self.intraday(cursor, chunk_end_exclusive, interval)
            if not part.empty:
                chunks.append(part)
            batch += 1
            if progress:
                progress(min(batch / max(total, 1), 1.0),
                         f"Batch {batch}/{total}: {cursor} to {chunk_end_exclusive} (end exclusive)")
            cursor = chunk_end_exclusive
        if not chunks:
            raise DhanAPIError(
                "Dhan returned no candles. Check API access, token, instrument parameters and data entitlement."
            )
        result = pd.concat(chunks).sort_index()
        result = result.loc[~result.index.duplicated(keep="last")]
        result = result.between_time("09:15", "15:30")
        return result
