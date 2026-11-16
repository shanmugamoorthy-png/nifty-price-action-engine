import requests
import pandas as pd
from datetime import date, timedelta
from config import DHAN_BASE_URL, SECURITY_ID, EXCHANGE_SEGMENT, INSTRUMENT

class DhanAPIError(RuntimeError):
    pass

class DhanClient:
    def __init__(self, access_token: str, client_id: str = ""):
        if not access_token:
            raise DhanAPIError("Missing DHAN_ACCESS_TOKEN.")
        self.session = requests.Session()
        self.session.headers.update({
            "access-token": access_token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        if client_id:
            self.session.headers["client-id"] = client_id

    def _post(self, endpoint, payload):
        try:
            r = self.session.post(f"{DHAN_BASE_URL}{endpoint}", json=payload, timeout=45)
        except requests.RequestException as e:
            raise DhanAPIError(f"Dhan network error: {e}") from e
        if r.status_code != 200:
            raise DhanAPIError(f"Dhan HTTP {r.status_code}: {r.text[:500]}")
        try:
            body = r.json()
        except ValueError as e:
            raise DhanAPIError(f"Dhan returned non-JSON response: {r.text[:300]}") from e
        if isinstance(body, dict) and body.get("status") == "failure":
            raise DhanAPIError(str(body.get("remarks") or body))
        return body

    @staticmethod
    def _to_frame(body):
        data = body.get("data", body) if isinstance(body, dict) else body
        if not isinstance(data, dict):
            raise DhanAPIError(f"Unexpected candle response: {str(body)[:300]}")
        # Dhan v2 chart response contains parallel OHLCV arrays and epoch timestamps.
        keys = ("timestamp", "open", "high", "low", "close")
        missing = [k for k in keys if k not in data]
        if missing:
            raise DhanAPIError(f"Missing fields in Dhan candle response: {missing}; keys={list(data.keys())}")
        n = min(len(data[k]) for k in keys)
        if n == 0:
            return pd.DataFrame(columns=["open","high","low","close","volume"])
        df = pd.DataFrame({k: data[k][:n] for k in keys})
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
        if "volume" in data:
            df["volume"] = data["volume"][:n]
        else:
            df["volume"] = 0
        df = df.set_index("timestamp").sort_index()
        for c in ["open","high","low","close","volume"]:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        return df.dropna(subset=["open","high","low","close"]).loc[lambda x: ~x.index.duplicated(keep="last")]

    def intraday(self, start: date, end: date, interval=5):
        if interval not in (1,5,15,25,60):
            raise ValueError("Dhan interval must be 1, 5, 15, 25 or 60.")
        payload = {
            "securityId": str(SECURITY_ID),
            "exchangeSegment": EXCHANGE_SEGMENT,
            "instrument": INSTRUMENT,
            "interval": str(interval),
            "oi": False,
            "fromDate": f"{start:%Y-%m-%d} 09:00:00",
            "toDate": f"{end:%Y-%m-%d} 15:45:00",
        }
        return self._to_frame(self._post("/charts/intraday", payload))

    def download_five_years(self, interval=5, progress=None):
        # Dhan documents a maximum 90-day window per intraday request.
        # Use 85-day windows to stay safely below the maximum.
        end = date.today()
        start = end - timedelta(days=365*5 + 5)
        chunks = []
        cursor = start
        total = ((end-start).days // 85) + 1
        done = 0
        while cursor < end:
            chunk_end = min(cursor + timedelta(days=85), end)
            df = self.intraday(cursor, chunk_end, interval)
            if not df.empty:
                chunks.append(df)
            done += 1
            if progress:
                progress(min(done/total, 1.0), f"Downloaded batch {done}/{total}: {cursor} to {chunk_end}")
            cursor = chunk_end + timedelta(days=1)
        if not chunks:
            raise DhanAPIError("No historical candles returned. Verify Dhan Data API entitlement and instrument parameters.")
        out = pd.concat(chunks).sort_index()
        out = out.loc[~out.index.duplicated(keep="last")]
        out = out.between_time("09:15", "15:30")
        return out
