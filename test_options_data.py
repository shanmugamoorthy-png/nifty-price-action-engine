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
