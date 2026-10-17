from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sellersprite


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class SellerSpriteTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.old_data_dir = sellersprite.DATA_DIR
        sellersprite.DATA_DIR = Path(self.tempdir.name) / 'sellersprite'
        self.cookies = {'rank-login-user': 'fixture-cookie'}

    def tearDown(self):
        sellersprite.DATA_DIR = self.old_data_dir
        self.tempdir.cleanup()

    def _read(self, name):
        path = sellersprite.DATA_DIR / name
        with path.open('r', encoding='utf-8-sig', newline='') as handle:
            return list(csv.DictReader(handle))

    def test_import_and_help_do_not_load_cookie(self):
        with patch.object(sellersprite, 'load_cookies') as loader:
            with self.assertRaises(SystemExit) as caught:
                sellersprite.main(['--help'])
        self.assertEqual(0, caught.exception.code)
        loader.assert_not_called()

    def test_three_metrics_write_expected_rows_and_use_config(self):
        asin = 'B09XMYFTB7'
        monthly = FakeResponse(200, {'data': {'chartData': {
            '2026-01': {'totalUnits': 10, 'totalAmount': 100, 'averagePrice': 10},
        }}})
        reviews = FakeResponse(200, {'data': {
            'dalta': [3], 'daltaMonth': ['202601'], 'rate': [0.25],
        }})
        keepa = FakeResponse(200, {'data': {'keepa': {
            'times': ['2026-01-01 10:00', '2026-01-01 12:00', '2026-01-02 08:00'],
            'keepa': {
                'rating': [4.1, 4.2, 4.3],
                'reviews': [100, 101, 102],
                'price': [9.0, 9.5, 10.0],
            },
        }}})
        with patch.object(sellersprite, 'load_cookies', return_value=self.cookies), \
             patch.object(sellersprite, 'load_config', return_value={'proxy': 'http://proxy.test:8080', 'request_timeout': 7, 'jiimore': {}}), \
             patch.object(sellersprite, 'get_proxies', return_value={'http': 'http://proxy.test:8080', 'https': 'http://proxy.test:8080'}), \
             patch.object(sellersprite.requests, 'post', side_effect=[monthly, reviews]) as post, \
             patch.object(sellersprite.requests, 'get', return_value=keepa) as get:
            self.assertEqual('success', sellersprite.get_monthly_sales(asin)['status'])
            self.assertEqual('success', sellersprite.get_reviews(asin)['status'])
            self.assertEqual('success', sellersprite.get_keepa(asin)['status'])

        self.assertEqual(sellersprite.MONTHLY_FIELDS, ['ASIN', '\u6708\u4efd', '\u6708\u9500\u91cf', '\u6708\u9500\u552e\u989d', '\u5e73\u5747\u5355\u4ef7'])
        self.assertEqual(sellersprite.REVIEW_FIELDS, ['ASIN', '\u6708\u4efd', '\u8bc4\u5206\u6570\u589e\u957f\u503c', '\u7559\u8bc4\u7387'])
        self.assertEqual(sellersprite.KEEPA_FIELDS, ['ASIN', '\u65e5\u671f', '\u8bc4\u5206', '\u8bc4\u5206\u6570', '\u4ef7\u683c'])
        self.assertEqual(self._read('monthly_sales.csv')[0]['\u6708\u9500\u91cf'], '10')
        self.assertEqual(self._read('reviews.csv')[0]['\u7559\u8bc4\u7387'], '0.25')
        keepa_rows = self._read('keepa.csv')
        self.assertEqual(len(keepa_rows), 2)
        self.assertEqual(keepa_rows[0]['\u8bc4\u5206'], '4.2')
        self.assertEqual(keepa_rows[0]['\u65e5\u671f'], '2026-01-01')
        for call in post.call_args_list + get.call_args_list:
            self.assertEqual(7.0, call.kwargs['timeout'])
            self.assertEqual('http://proxy.test:8080', call.kwargs['proxies']['https'])
            self.assertEqual(self.cookies, call.kwargs['cookies'])

    def test_empty_and_success_false_do_not_write_placeholder(self):
        asin = 'B00FLYWNYQ'
        empty = FakeResponse(200, {'data': {'chartData': {}}})
        failed = FakeResponse(200, {'success': False, 'data': {}})
        with patch.object(sellersprite, 'load_cookies', return_value=self.cookies), \
             patch.object(sellersprite, 'load_config', return_value={'proxy': '', 'request_timeout': 3, 'jiimore': {}}), \
             patch.object(sellersprite, 'get_proxies', return_value={}), \
             patch.object(sellersprite.requests, 'post', side_effect=[empty, failed]) as post:
            self.assertEqual('empty', sellersprite.get_monthly_sales(asin)['status'])
            self.assertFalse((sellersprite.DATA_DIR / 'monthly_sales.csv').exists())
            with self.assertRaises(sellersprite.ResponseError):
                sellersprite.get_monthly_sales(asin)
        self.assertEqual(2, post.call_count)
        self.assertFalse((sellersprite.DATA_DIR / 'monthly_sales.csv').exists())

    def test_auth_failure_is_not_retried_and_stops_remaining_asins(self):
        asin_file = Path(self.tempdir.name) / 'ASIN.txt'
        asin_file.write_text('B00FLYWNYQ\nB09XMYFTB7\n', encoding='utf-8')
        with patch.object(sellersprite, 'load_cookies', return_value=self.cookies), \
             patch.object(sellersprite, 'load_config', return_value={'proxy': '', 'request_timeout': 3, 'jiimore': {}}), \
             patch.object(sellersprite, 'get_proxies', return_value={}), \
             patch.object(sellersprite.requests, 'post', return_value=FakeResponse(401, {})) as post:
            code = sellersprite.main(['--asin-file', str(asin_file)])
        self.assertEqual(2, code)
        self.assertEqual(1, post.call_count)

    def test_limit_and_skip_avoid_unneeded_requests(self):
        asin_file = Path(self.tempdir.name) / 'ASIN.txt'
        asin_file.write_text('A1\nA2\n', encoding='utf-8')
        calls = []
        def success(asin):
            calls.append(asin)
            return {'asin': asin, 'status': 'success', 'rows': 1}
        with patch.object(sellersprite, 'get_monthly_sales', side_effect=success), \
             patch.object(sellersprite, 'get_reviews', side_effect=success), \
             patch.object(sellersprite, 'get_keepa', side_effect=success):
            self.assertEqual(0, sellersprite.main(['--asin-file', str(asin_file), '--limit', '1']))
        self.assertEqual(['A1', 'A1', 'A1'], calls)

        sellersprite.DATA_DIR.mkdir(parents=True, exist_ok=True)
        with (sellersprite.DATA_DIR / 'reviews.csv').open('w', encoding='utf-8', newline='') as handle:
            handle.write('ASIN,\u6708\u4efd,\u8bc4\u5206\u6570\u589e\u957f\u503c,\u7559\u8bc4\u7387\nA1,2026-01,1,0.1\n')
        with patch.object(sellersprite, 'load_cookies') as loader, \
             patch.object(sellersprite.requests, 'post') as post:
            result = sellersprite.get_reviews('A1')
        self.assertEqual('skipped', result['status'])
        loader.assert_not_called()
        post.assert_not_called()

    def test_missing_cookie_has_actionable_error_without_value_echo(self):
        missing = Path(self.tempdir.name) / 'missing-cookies.json'
        with self.assertRaises(sellersprite.CookieError) as caught:
            sellersprite.load_cookies(missing)
        self.assertIn('Cookie', str(caught.exception))
        self.assertNotIn('fixture-cookie', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
