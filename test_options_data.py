from datetime import date
import pandas as pd
from options_data import download_rolling_option

class FakeClient:
    def __init__(self):
        self.payloads = []
    def _post(self, endpoint, payload):
        self.payloads.append((endpoint, payload))
        return {"data": {"ce": {
            "timestamp": [1792215900], "open": [100], "high": [101],
            "low": [99], "close": [100], "strike": [26000], "spot": [26010]
        }}}

def test_rolling_option_payload_uses_integer_interval_and_required_fields():
    client = FakeClient()
    df = download_rolling_option(client, date(2026, 11, 1), date(2026, 11, 2),
                                 "CALL", interval=5)
    endpoint, payload = client.payloads[0]
    assert endpoint == "/charts/rollingoption"
    assert payload["interval"] == 5
    assert isinstance(payload["interval"], int)
    assert payload["securityId"] == "13"
    assert payload["exchangeSegment"] == "NSE_FNO"
    assert payload["instrument"] == "OPTIDX"
    assert payload["expiryCode"] == 1
    assert payload["expiryFlag"] == "WEEK"
    assert payload["drvOptionType"] == "CALL"
    assert set(["open", "high", "low", "close"]).issubset(payload["requiredData"])
    assert not df.empty

def test_31_calendar_day_range_is_split_into_valid_inclusive_chunks():
    client = FakeClient()
    download_rolling_option(client, date(2026, 10, 17), date(2026, 11, 17),
                            "CALL", interval=5)
    assert len(client.payloads) == 2
    ranges = [(p["fromDate"], p["toDate"]) for _, p in client.payloads]
    assert ranges == [("2026-10-17", "2026-11-15"),
                      ("2026-11-16", "2026-11-16")]
    for _, payload in client.payloads:
        first = date.fromisoformat(payload["fromDate"])
        last = date.fromisoformat(payload["toDate"])
        assert 1 <= (last - first).days + 1 <= 30


def test_exact_30_calendar_days_are_one_valid_request():
    client = FakeClient()
    download_rolling_option(client, date(2026, 10, 17), date(2026, 11, 16),
                            "CALL", interval=5)
    assert len(client.payloads) == 1
    _, payload = client.payloads[0]
    assert payload["fromDate"] == "2026-10-17"
    assert payload["toDate"] == "2026-11-15"
