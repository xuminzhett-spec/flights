import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import check_prices as cp  # noqa: E402

PASSPORT_CSV = """Passport,Destination,Requirement
CN,CA,visa required
CN,TH,visa free
CN,JP,e-visa
CN,KH,visa on arrival
CN,SG,30
CN,AU,eta
CN,CN,-1
US,CA,visa free
"""
AIRPORTS_CSV = """id,ident,type,name,latitude_deg,longitude_deg,elevation_ft,continent,iso_country,iso_region,municipality,scheduled_service,gps_code,iata_code
1,CYEG,large_airport,Edmonton,0,0,0,NA,CA,CA-AB,Edmonton,yes,CYEG,YEG
2,VTBS,large_airport,Suvarnabhumi,0,0,0,AS,TH,TH-10,Bangkok,yes,VTBS,BKK
3,ZSPD,large_airport,Pudong,0,0,0,AS,CN,CN-31,Shanghai,yes,ZSPD,PVG
"""


def fake_download(url, timeout=90):
    return (PASSPORT_CSV if "passport" in url else AIRPORTS_CSV).encode()


def opt(price, airline="AC", stops=0, bag="unknown"):
    return {"price": price, "airline": airline, "stops": stops, "minutes": 300, "bag": bag}


class TestParsing(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(cp.classify_requirement("visa free")[0], "free")
        self.assertEqual(cp.classify_requirement("30"), ("free", "免签（30天）"))
        self.assertEqual(cp.classify_requirement("visa on arrival")[0], "voa")
        self.assertEqual(cp.classify_requirement("e-visa")[0], "evisa")
        self.assertEqual(cp.classify_requirement("eta")[0], "eta")
        self.assertEqual(cp.classify_requirement("visa required")[0], "required")
        self.assertEqual(cp.classify_requirement("-1")[0], "home")
        self.assertEqual(cp.classify_requirement("???")[0], "unknown")

    def test_visa_map_only_for_passport(self):
        m = cp.build_visa_map(PASSPORT_CSV, "CN")
        self.assertEqual(m["TH"]["code"], "free")
        self.assertEqual(m["CA"]["code"], "required")  # not the US row
        self.assertEqual(len(m), 7)

    def test_bag_status(self):
        self.assertEqual(cp.bag_status({"extensions": ["Carry-on bag included"]}), "unknown")
        self.assertEqual(cp.bag_status({"flights": [{"extensions": ["1st checked bag free"]}]}), "yes")
        self.assertEqual(cp.bag_status({"extensions": ["Checked baggage for a fee"]}), "no")
        self.assertEqual(cp.bag_status({}), "unknown")

    def test_parse_options_sorted_and_skips_no_price(self):
        data = {"best_flights": [{"price": 500, "flights": [{"airline": "AC"}], "total_duration": 100}],
                "other_flights": [{"price": 300, "flights": [{"airline": "WS"}, {"airline": "WS"}]},
                                  {"flights": [{"airline": "X"}]}]}
        out = cp.parse_options(data)
        self.assertEqual([o["price"] for o in out], [300, 500])
        self.assertEqual(out[0]["stops"], 1)


class TestReference(unittest.TestCase):
    def test_ensure_reference_and_visa_for(self):
        ref = cp.ensure_reference({}, {"YEG", "BKK", "ZZZ"}, "CN", fake_download)
        self.assertEqual(ref["airports"], {"YEG": "CA", "BKK": "TH"})
        self.assertEqual(cp.visa_for(ref, "BKK")["code"], "free")
        self.assertEqual(cp.visa_for(ref, "YEG")["code"], "required")
        self.assertEqual(cp.visa_for(ref, "ZZZ")["code"], "unknown")

    def test_download_failure_does_not_raise(self):
        def boom(url, timeout=90):
            raise OSError("offline")
        ref = cp.ensure_reference({}, {"YEG"}, "CN", boom)
        self.assertEqual(cp.visa_for(ref, "YEG")["code"], "unknown")


class TestAlerts(unittest.TestCase):
    def rec(self, latest, visa="free", **meta):
        return {"meta": {"visa": {"code": visa, "text": "x"}, **meta}, "latest": latest, "history": []}

    def test_candidate_filters(self):
        r = self.rec([opt(200, bag="no"), opt(300, bag="yes")])
        self.assertEqual(cp.alert_candidate({}, r), 200)
        self.assertEqual(cp.alert_candidate({"need_checked_bag": True}, r), 300)
        self.assertIsNone(cp.alert_candidate({"visa_ok_only": True}, self.rec([opt(1)], visa="required")))
        self.assertEqual(cp.alert_candidate({"visa_ok_only": True}, self.rec([opt(1)], visa="voa")), 1)

    def test_should_alert(self):
        route = {"alert_below": 250}
        self.assertTrue(cp.should_alert(route, self.rec([]), 200))
        self.assertFalse(cp.should_alert(route, self.rec([]), 260))
        self.assertFalse(cp.should_alert({}, self.rec([]), 100))
        # already alerted at 200 recently -> no repeat at the same price
        recent = self.rec([], last_alert_price=200, last_alert_at=cp.now_iso())
        self.assertFalse(cp.should_alert(route, recent, 200))
        self.assertTrue(cp.should_alert(route, recent, 150))  # cheaper -> alert again
        old = self.rec([], last_alert_price=200, last_alert_at="2020-01-01T00:00:00+00:00")
        self.assertTrue(cp.should_alert(route, old, 200))


class TestRun(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "docs" / "data").mkdir(parents=True)
        patches = {"ROUTES_FILE": self.tmp / "docs" / "routes.json",
                   "PRICES_FILE": self.tmp / "docs" / "data" / "prices.json",
                   "REF_FILE": self.tmp / "docs" / "data" / "reference.json"}
        self.patchers = [mock.patch.object(cp, k, v) for k, v in patches.items()]
        for p in self.patchers:
            p.start()
        self.routes = {"settings": {"currency": "CAD", "daily_search_limit": 2, "passport": "CN"},
                       "routes": [
                           {"id": "a", "from": "yeg", "to": "bkk", "depart": "2026-12-20", "return": "2026-12-28", "alert_below": 900},
                           {"id": "b", "from": "YEG", "to": "PVG", "depart": "2026-12-21", "alert_below": 100},
                           {"id": "c", "from": "YEG", "to": "BKK", "depart": "2026-12-22"},
                           {"id": "old", "from": "YEG", "to": "BKK", "depart": "2020-01-01"},
                           {"id": "off", "from": "YEG", "to": "BKK", "depart": "2026-12-22", "enabled": False}]}
        cp.ROUTES_FILE.write_text(json.dumps(self.routes))
        os.environ["SERPAPI_KEY"] = "k"

    def tearDown(self):
        for p in self.patchers:
            p.stop()

    def test_limit_round_robin_history_and_email(self):
        calls = []

        def fetch(route, settings, key):
            calls.append(route["id"])
            return [opt(800, bag="yes"), opt(950)]

        sent = []
        with mock.patch.object(cp, "send_email", side_effect=lambda body, subj: sent.append((body, subj)) or True):
            self.assertEqual(cp.run(fetch, fake_download, today=date(2026, 11, 1)), 0)
            self.assertEqual(calls, ["a", "b"])           # limit 2, a and b first
            self.assertEqual(len(sent), 1)                # only 'a' is under its 900 target
            self.assertIn("BKK", sent[0][0])
            self.assertIn("免签", sent[0][0])
            self.assertEqual(cp.run(fetch, fake_download, today=date(2026, 11, 2)), 0)
            self.assertEqual(calls[2:], ["c", "a"])       # round robin: never-checked 'c' first
            self.assertEqual(len(sent), 1)                # no repeat alert for same price

        prices = json.loads(cp.PRICES_FILE.read_text())
        self.assertEqual(len(prices["routes"]["a"]["history"]), 2)
        self.assertEqual(prices["routes"]["a"]["history"][0]["min_bag"], 800)
        self.assertEqual(prices["routes"]["a"]["meta"]["visa"]["code"], "free")
        self.assertNotIn("old", prices["routes"])
        self.assertNotIn("off", prices["routes"])

    def test_failure_is_recorded_and_returns_error_when_all_fail(self):
        def fetch(route, settings, key):
            raise RuntimeError("quota exceeded")
        self.assertEqual(cp.run(fetch, fake_download, today=date(2026, 11, 1)), 1)
        prices = json.loads(cp.PRICES_FILE.read_text())
        self.assertIn("quota", prices["routes"]["a"]["meta"]["last_error"])

    def test_deleted_route_history_is_dropped(self):
        cp.PRICES_FILE.write_text(json.dumps({"routes": {"gone": {"meta": {}, "history": [{"min": 1}], "latest": []}}}))
        cp.run(lambda *a: [opt(500)], fake_download, today=date(2026, 11, 1))
        self.assertNotIn("gone", json.loads(cp.PRICES_FILE.read_text())["routes"])


if __name__ == "__main__":
    unittest.main()
