from datetime import date
import pandas as pd
from options_data import download_rolling_option

class FakeClient:
    def __init__(self):
        self.payloads = []
    def _post(self, endpoint, payload):
        self.payloads.append((endpoint, payload.copy()))
        return {"data": {"ce": {
            "timestamp": [1792215900], "open": [100], "high": [101],
            "low": [99], "close": [100], "strike": [26000], "spot": [26010],
            "iv": [12.5], "volume": [10], "oi": [20]
        }}}

def test_payload_is_one_calendar_day_and_interval_is_integer():
    client = FakeClient()
    df = download_rolling_option(client, date(2026, 11, 1), date(2026, 11, 2),
                                 "CALL", interval=5, request_delay=0)
    endpoint, payload = client.payloads[0]
    assert endpoint == "/charts/rollingoption"
    assert payload["interval"] == 5 and isinstance(payload["interval"], int)
    assert payload["fromDate"] == payload["toDate"] == "2026-11-01"
    assert payload["securityId"] == "13"
    assert payload["exchangeSegment"] == "NSE_FNO"
    assert payload["instrument"] == "OPTIDX"
    assert payload["expiryCode"] == 1 and payload["expiryFlag"] == "WEEK"
    assert payload["drvOptionType"] == "CALL"
    assert {"open", "high", "low", "close", "volume", "oi", "iv", "strike", "spot"}.issubset(payload["requiredData"])
    assert not df.empty

def test_31_days_makes_31_separate_daily_requests():
    client = FakeClient()
    download_rolling_option(client, date(2026, 10, 17), date(2026, 11, 17),
                            "CALL", interval=5, request_delay=0)
    assert len(client.payloads) == 31
    requested = [(p["fromDate"], p["toDate"]) for _, p in client.payloads]
    assert requested[0] == ("2026-10-17", "2026-10-17")
    assert requested[-1] == ("2026-11-16", "2026-11-16")
    assert all(a == b for a, b in requested)

def test_daily_cache_resumes_without_re_requesting_saved_days(tmp_path):
    client = FakeClient()
    kwargs = dict(option_type="CALL", interval=5, request_delay=0, cache_dir=tmp_path)
    download_rolling_option(client, date(2026, 11, 1), date(2026, 11, 4), **kwargs)
    assert len(client.payloads) == 3
    # A second run should read the three saved daily files and make no API calls.
    download_rolling_option(client, date(2026, 11, 1), date(2026, 11, 4), **kwargs)
    assert len(client.payloads) == 3
