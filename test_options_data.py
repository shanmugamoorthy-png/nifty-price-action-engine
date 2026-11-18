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

def test_fetches_offset_series_needed_for_fixed_strike_tracking():
    class MovingSpotClient(FakeClient):
        def _post(self, endpoint, payload):
            self.payloads.append((endpoint, payload.copy()))
            return {"data": {"ce": {
                "timestamp": [1792215900, 1792216200], "open": [100, 101],
                "high": [101, 102], "low": [99, 100], "close": [100, 101],
                "strike": [26000, 26100], "spot": [26000, 26100],
                "iv": [12.5, 12.5], "volume": [10, 10], "oi": [20, 20]
            }}}
    client=MovingSpotClient()
    df=download_rolling_option(client,date(2026,11,1),date(2026,11,2),"CALL",interval=5,request_delay=0)
    labels=[p["strike"] for _,p in client.payloads]
    assert labels == ["ATM","ATM-2","ATM-1","ATM+1","ATM+2"]
    assert set(df["offset"].astype(int)) == {-2,-1,0,1,2}
    assert all(p["fromDate"]==p["toDate"]=="2026-11-01" for _,p in client.payloads)

def test_unsupported_wide_atm_day_is_skipped_and_later_days_continue():
    class WideThenNormalClient(FakeClient):
        def _post(self, endpoint, payload):
            self.payloads.append((endpoint, payload.copy()))
            day = payload["fromDate"]
            if day == "2026-11-01":
                # 11 strike steps between first and last ATM values: beyond ±10.
                strikes = [26000, 26550]
                spots = [26000, 26550]
                timestamps = [1793523900, 1793524200]
                prices = [100, 101]
            else:
                strikes = [26000]
                spots = [26000]
                timestamps = [1793610300]
                prices = [100]
            return {"data": {"ce": {
                "timestamp": timestamps, "open": prices, "high": [x+1 for x in prices],
                "low": [x-1 for x in prices], "close": prices,
                "strike": strikes, "spot": spots, "iv": [12.5]*len(prices),
                "volume": [10]*len(prices), "oi": [20]*len(prices)
            }}}
    client = WideThenNormalClient()
    df = download_rolling_option(client, date(2026, 11, 1), date(2026, 11, 3),
                                 "CALL", interval=5, request_delay=0)
    assert not df.empty
    assert all(ts.date() != date(2026, 11, 1) for ts in df.index)
    assert any(ts.date() == date(2026, 11, 2) for ts in df.index)
    assert df.attrs["skipped_dates"] == [{
        "date": "2026-11-01",
        "reason": "intraday ATM moved 11 strikes; Dhan near-expiry range is ±10"
    }]
    # The unsupported day is only probed with ATM; no partial offset series is fetched.
    assert [payload["strike"] for _, payload in client.payloads if payload["fromDate"] == "2026-11-01"] == ["ATM"]

def test_monthly_batch_merge_preserves_other_months_and_all_strikes(tmp_path):
    from options_data import merge_saved_option_csv

    path = tmp_path / "options.csv"
    old = pd.DataFrame(
        {
            "open": [10.0, 20.0], "high": [11.0, 21.0], "low": [9.0, 19.0],
            "close": [10.5, 20.5], "strike": [26000.0, 26050.0],
            "spot": [26010.0, 26010.0], "offset": [0, 1],
        },
        index=pd.to_datetime(["2025-11-03 09:15", "2025-11-03 09:15"]),
    )
    old.index.name = "timestamp"
    old.to_csv(path, index_label="timestamp")

    new = pd.DataFrame(
        {
            "open": [12.0, 30.0], "high": [13.0, 31.0], "low": [11.0, 29.0],
            "close": [12.5, 30.5], "strike": [26000.0, 26100.0],
            "spot": [26020.0, 26020.0], "offset": [0, 2],
        },
        index=pd.to_datetime(["2025-11-03 09:15", "2025-12-01 09:15"]),
    )
    new.index.name = "timestamp"

    merged = merge_saved_option_csv(path, new)
    # Old strike 26050 remains, overlapping strike 26000 is refreshed, and
    # the new December strike is appended.
    assert len(merged) == 3
    assert set(merged["strike"].astype(int)) == {26000, 26050, 26100}
    row = merged[(merged.index == pd.Timestamp("2025-11-03 09:15")) & (merged["strike"] == 26000)]
    assert float(row.iloc[0]["close"]) == 12.5
    reread = pd.read_csv(path, parse_dates=["timestamp"])
    assert len(reread) == 3


def test_skipped_date_manifest_merge_preserves_outside_batch_and_refreshes_inside():
    from options_data import merge_skipped_dates
    from datetime import date

    old = [
        {"date": "2025-10-30", "reason": "old gap"},
        {"date": "2025-11-05", "reason": "old reason"},
    ]
    fresh = [{"date": "2025-11-05", "reason": "updated reason"}]
    merged = merge_skipped_dates(old, fresh, date(2025, 11, 1), date(2025, 11, 30))
    assert merged == [
        {"date": "2025-10-30", "reason": "old gap"},
        {"date": "2025-11-05", "reason": "updated reason"},
    ]
