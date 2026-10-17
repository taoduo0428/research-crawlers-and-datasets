"""Offline tests for the safe, low-volume current-site probes."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import live_probe as live  # noqa: E402


class Response:
    def __init__(self, code, body):
        self.status_code = code
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def iter_content(self, chunk_size=65536):
        yield self.body


class Session:
    def __init__(self, code=200, body=b"{}"):
        self.code, self.body = code, body
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return Response(self.code, self.body)


class LiveSmokeTests(unittest.TestCase):
    def test_amazon_real_field_structure(self):
        html = b'''
        <html><span id="productTitle">A sample product</span>
        <div id="corePrice_feature_div"><span class="a-offscreen">JPY1,476</span></div>
        <div id="acrPopover" title="4.7 out of 5 stars"></div>
        <span id="acrCustomerReviewText">12,500 ratings</span></html>
        '''
        res = live.scrape_amazon(html)
        self.assertTrue(res["looks_like_product_page"])
        self.assertEqual(res["price"], "JPY1,476")
        self.assertEqual(res["fields_found"], 4)

    def test_seller_review_trend_requires_nonempty_data(self):
        raw = json.dumps({"data": {
            "daltaMonth": ["202607", "202608", "202609"],
            "dalta": [9, None, 7], "rate": [0.1, None, 0.3],
        }}).encode()
        res = live.review_summary(raw)
        self.assertTrue(res["usable"])
        self.assertEqual(res["last_month"], "202609")
        self.assertEqual(res["last_month_with_review_delta"], "202609")
        self.assertEqual(res["nonempty_monthly_deltas"], 2)
        self.assertFalse(live.monthly_summary(b"{}")["usable"])

    def test_keepa_real_structure(self):
        raw = json.dumps({"data": {"keepa": {
            "times": ["2026-10-16 14:00", "2026-10-17 14:00"],
            "keepa": {"price": [1.2, 1.1], "rating": [4.8, 4.9]},
            "currency": "USD",
        }}}).encode()
        data = live.keepa_summary(raw)
        self.assertTrue(data["usable"])
        self.assertEqual(data["last_timestamp"], "2026-10-17 14:00")
        self.assertEqual(data["currency_label"], "USD")
        self.assertEqual(data["series_nonempty"]["price"], 2)
        self.assertEqual(data["series_last_nonempty_timestamp"]["price"],
                         "2026-10-17 14:00")
        self.assertFalse(live.keepa_summary(b"{}")["usable"])

    def test_site_status_is_distinguished_from_real_success(self):
        unauth = Session(code=401)
        result = live.single_probe(unauth, "jiimore_weekly", "B005G9E98O")
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "login_required")
        self.assertFalse(unauth.calls[0][2]["allow_redirects"])
        bad = Session(code=200, body=b"{}")
        result = live.single_probe(bad, "sellersprite_sales", "B005G9E98O")
        self.assertFalse(result["success"])
        self.assertTrue(result["returned_empty_json"])
        self.assertEqual(result["status"], "no_usable_data")

    def test_does_not_call_arbitrary_hosts(self):
        session = Session()
        with self.assertRaises(ValueError):
            live.fetch(session, "GET", "http://www.amazon.com/dp/B005G9E98O")
        with self.assertRaises(ValueError):
            live.fetch(session, "GET", "https://evil.example/test")
        self.assertFalse(session.calls)

    def test_fetch_clears_platform_issued_cookies_before_each_request(self):
        class CookieJar:
            def __init__(self):
                self.cleared = 0
            def clear(self):
                self.cleared += 1

        client = Session(code=200, body=b"{}")
        client.cookies = CookieJar()
        status, body = live.fetch(
            client, "GET", "https://www.amazon.com/dp/B005G9E98O"
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, b"{}")
        self.assertEqual(client.cookies.cleared, 1)

    def test_report_cannot_be_saved_inside_repository(self):
        path = live.REPOSITORY / "do-not-create-live-report.json"
        with patch.object(sys, "argv", [
            "live_probe.py", "--output", str(path)
        ]), self.assertRaises(SystemExit) as exc:
            live.main()
        self.assertEqual(exc.exception.code, 2)
        self.assertFalse(path.exists())

    def test_validates_small_sample_and_avoids_credentials(self):
        with self.assertRaises(ValueError):
            live.run(["NOT_ASIN"], ["amazon"])
        with self.assertRaises(ValueError):
            live.run(["B005G9E98O"] * 4, ["amazon"])
        with self.assertRaises(ValueError):
            live.run(["B005G9E98O"], ["amazon"], delay=0)
        self.assertLessEqual(len(live.SOURCES), 6)


if __name__ == "__main__":
    unittest.main()
