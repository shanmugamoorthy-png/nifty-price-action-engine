import json
import tempfile
import unittest
from pathlib import Path
import pandas as pd
from data_diagnostics import build_diagnostic_report

class DiagnosticsTests(unittest.TestCase):
    def test_report_counts_and_timestamp_matching(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            idx = pd.to_datetime(["2026-01-02 09:15", "2026-01-02 09:20", "2026-01-05 09:15"])
            spot = pd.DataFrame({"open": [1,2,3], "high": [2,3,4], "low": [0,1,2], "close": [1,2,3], "volume": [10,0,20]}, index=idx)
            option = pd.DataFrame({"timestamp": ["2026-01-02 09:15", "2026-01-02 09:20", "2026-01-05 09:20"], "strike": [25000,25050,25000], "open": [10,11,12]})
            cp, pp, mp = td/"call.csv", td/"put.csv", td/"manifest.json"
            option.to_csv(cp, index=False); option.to_csv(pp, index=False)
            mp.write_text(json.dumps({"CALL": [], "PUT": []}))
            report = build_diagnostic_report(spot, cp, pp, mp)
            self.assertEqual(report["CALL"]["rows"], 3)
            self.assertEqual(report["CALL"]["unique_dates"], 2)
            self.assertEqual(report["CALL"]["exact_timestamp_match_pct"], 66.67)
            self.assertEqual(report["spot"]["zero_or_negative_volume_rows"], 1)
            self.assertEqual(report["call_dates_missing_from_put"], [])

if __name__ == "__main__":
    unittest.main()
