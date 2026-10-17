import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

SOURCE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_DIR))
spec = importlib.util.spec_from_file_location('jiimore_under_test', SOURCE_DIR / 'jiimore.py')
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

CONFIG = {'proxy': '', 'request_timeout': 3, 'jiimore': {'phone_num': 'fixture', 'password': 'fixture'}}


class JiimoreTests(unittest.TestCase):
    def test_missing_credentials_stop_before_network(self):
        config = dict(CONFIG, jiimore={'phone_num': '', 'password': ''})
        with mock.patch.object(app, 'load_config', return_value=config), \
             mock.patch.object(app.requests, 'Session') as session:
            with self.assertRaises(app.AccessRequired):
                app.main(max_pages=1)
        session.assert_not_called()

    def test_default_run_creates_output_and_merges_asin_list(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / 'data'
            data_root.mkdir()
            asin_path = data_root / 'ASIN.txt'
            asin_path.write_text('ORIGINAL\n', encoding='utf-8')
            output = data_root / 'jiimore'
            with mock.patch.object(app, 'DATA_ROOT', data_root), \
                 mock.patch.object(app, 'DATA_DIR', output), \
                 mock.patch.object(app, 'load_config', return_value=CONFIG), \
                 mock.patch.object(app.requests, 'Session', return_value=mock.Mock()), \
                 mock.patch.object(app, 'login', side_effect=lambda session, *_: session), \
                 mock.patch.object(app, 'get_category_id', return_value=1), \
                 mock.patch.object(app, 'get_data', return_value=[{'asin': 'B09XMYFTB7', 'launchDate': None}]), \
                 contextlib.redirect_stdout(io.StringIO()):
                count = app.main(max_pages=2)
            frame = app.pd.read_csv(output / 'jiimore.csv').fillna('')
            self.assertEqual(1, count)
            self.assertEqual(1, len(frame))
            self.assertEqual(['ASIN', '上架时间', 'Amazon_url', '周点击量', '日期'], list(frame.columns))
            self.assertEqual('', frame.iloc[0]['上架时间'])
            self.assertEqual('', frame.iloc[0]['周点击量'])
            self.assertEqual('ORIGINAL\nB09XMYFTB7\n', asin_path.read_text(encoding='utf-8'))

    def test_access_limit_is_not_retried(self):
        response = mock.Mock(status_code=429)
        session = mock.Mock()
        session.post.return_value = response
        with mock.patch.object(app, 'load_config', return_value=CONFIG), \
             mock.patch.object(app.time, 'sleep') as sleep:
            with self.assertRaises(app.AccessRequired):
                app.login(session, 'fixture', 'fixture')
        session.post.assert_called_once()
        sleep.assert_not_called()

    def test_legacy_export_flag_also_preserves_existing_asins(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / 'data'
            data_root.mkdir()
            asin_path = data_root / 'ASIN.txt'
            asin_path.write_text('OLD-INPUT\n', encoding='utf-8')
            with mock.patch.object(app, 'DATA_ROOT', data_root), \
                 mock.patch.object(app, 'DATA_DIR', data_root / 'jiimore'), \
                 mock.patch.object(app, 'load_config', return_value=CONFIG), \
                 mock.patch.object(app.requests, 'Session', return_value=mock.Mock()), \
                 mock.patch.object(app, 'login', side_effect=lambda session, *_: session), \
                 mock.patch.object(app, 'get_category_id', return_value=1), \
                 mock.patch.object(app, 'get_data', return_value=[{'asin': 'B09XMYFTB7', 'launchDate': '20220401'}]), \
                 contextlib.redirect_stdout(io.StringIO()):
                app.main(max_pages=1, export_asins=True)
            self.assertEqual('OLD-INPUT\nB09XMYFTB7\n', asin_path.read_text(encoding='utf-8'))

    def test_merge_keeps_original_text_and_order_without_duplicate_additions(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'ASIN.txt'
            before = b'\xef\xbb\xbf b09xmyftb7 \r\n\r\nB00FLYWNYQ'
            path.write_bytes(before)
            added = app.merge_asins(['B09XMYFTB7', ' B00FLYWNYQ ', 'B000NEW001', 'b000new001', '', None], path)
            self.assertEqual(1, added)
            self.assertEqual(before + b'\r\nB000NEW001\r\n', path.read_bytes())

    def test_merge_no_new_values_does_not_rewrite_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'ASIN.txt'
            before = b'B09XMYFTB7\n'
            path.write_bytes(before)
            with mock.patch.object(app.os, 'replace') as replace:
                self.assertEqual(0, app.merge_asins(['B09XMYFTB7', ' b09xmyftb7 '], path))
            replace.assert_not_called()
            self.assertEqual(before, path.read_bytes())

    def test_merge_creates_missing_file_and_parent(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'missing' / 'ASIN.txt'
            self.assertEqual(2, app.merge_asins(['B09XMYFTB7', 'B09XMYFTB7', 'B00FLYWNYQ'], path))
            self.assertEqual('B09XMYFTB7\nB00FLYWNYQ\n', path.read_text(encoding='utf-8'))

    def test_merge_empty_input_creates_empty_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'missing' / 'ASIN.txt'
            self.assertEqual(0, app.merge_asins(['', None, float('nan')], path))
            self.assertEqual(b'', path.read_bytes())

    def test_merge_replace_failure_preserves_old_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'ASIN.txt'
            before = b'B09XMYFTB7\n'
            path.write_bytes(before)
            with mock.patch.object(app.os, 'replace', side_effect=PermissionError('fixture locked')):
                with self.assertRaises(PermissionError):
                    app.merge_asins(['B000NEW001'], path)
            self.assertEqual(before, path.read_bytes())
            self.assertEqual([path], list(path.parent.iterdir()))

    def test_default_run_recreates_list_from_existing_csv_without_new_products(self):
        with tempfile.TemporaryDirectory() as temporary:
            data_root = Path(temporary) / 'data'
            output = data_root / 'jiimore'
            output.mkdir(parents=True)
            app.pd.DataFrame([{'ASIN': 'B09XMYFTB7'}], columns=app.FIELDS).to_csv(output / 'jiimore.csv', index=False)
            with mock.patch.object(app, 'DATA_ROOT', data_root), \
                 mock.patch.object(app, 'DATA_DIR', output), \
                 mock.patch.object(app, 'load_config', return_value=CONFIG), \
                 mock.patch.object(app.requests, 'Session', return_value=mock.Mock()), \
                 mock.patch.object(app, 'login', side_effect=lambda session, *_: session), \
                 mock.patch.object(app, 'get_category_id', return_value=1), \
                 mock.patch.object(app, 'get_data', return_value=[]), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, app.main(max_pages=1))
            self.assertEqual('B09XMYFTB7\n', (data_root / 'ASIN.txt').read_text(encoding='utf-8'))

    def test_connection_retry_uses_imported_time_and_request_timeout(self):
        responses = [app.requests.ConnectionError('fixture connection failure'),
                     mock.Mock(status_code=200), mock.Mock(status_code=200)]
        responses[1].json.return_value = {'cookies': {'sid': 'fixture'}}
        responses[2].json.return_value = {'uname': 'fixture'}
        session = mock.Mock()
        session.post.side_effect = responses
        with mock.patch.object(app, 'load_config', return_value=CONFIG), \
             mock.patch.object(app.time, 'sleep') as sleep, \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertIs(session, app.login(session, 'fixture', 'fixture'))
        self.assertEqual(3, session.post.call_count)
        sleep.assert_called_once()
        self.assertTrue(all(call.kwargs['timeout'] == 3 for call in session.post.call_args_list))

    def test_missing_login_confirmation_stops(self):
        first = mock.Mock(status_code=200)
        first.json.return_value = {'cookies': {'sid': 'fixture'}}
        second = mock.Mock(status_code=200)
        second.json.return_value = {}
        session = mock.Mock()
        session.post.side_effect = [first, second]
        with mock.patch.object(app, 'load_config', return_value=CONFIG):
            with self.assertRaises(app.AccessRequired):
                app.login(session, 'fixture', 'fixture')
        self.assertEqual(2, session.post.call_count)

    def test_invalid_page_budget_stops_before_network(self):
        with mock.patch.object(app.requests, 'Session') as session:
            with self.assertRaises(ValueError):
                app.main(max_pages=0)
        session.assert_not_called()


if __name__ == '__main__':
    unittest.main()
