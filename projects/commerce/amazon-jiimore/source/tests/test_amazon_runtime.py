import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


SOURCE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_DIR))
spec = importlib.util.spec_from_file_location('amazon_runtime_under_test', SOURCE_DIR / 'amazon.py')
amazon = importlib.util.module_from_spec(spec)
spec.loader.exec_module(amazon)


class PersistenceTests(unittest.TestCase):
    def test_invalid_product_cannot_replace_existing_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'goods.csv'
            amazon.save_product({'ASIN': 'B00FLYWNYQ', '标题': 'Existing product', '价格': '$79.00'}, path)
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                amazon.save_product({'ASIN': 'B00FLYWNYQ', '标题': '', '价格': ''}, path)
            self.assertEqual(before, path.read_bytes())

    def test_product_upsert_retains_optional_values_and_does_not_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'goods.csv'
            amazon.save_product({'ASIN': 'B00FLYWNYQ', '标题': 'Old', '价格': '$79.00', 'amazon global shipping': '$27.57'}, path)
            amazon.save_product({'ASIN': 'B00FLYWNYQ', '标题': 'Updated', '价格': '$78.00', 'amazon global shipping': ''}, path)
            rows = amazon.read_rows(path)
            self.assertEqual(1, len(rows))
            self.assertEqual('Updated', rows[0]['标题'])
            self.assertEqual('$27.57', rows[0]['amazon global shipping'])

    def test_comment_retries_add_only_new_nonempty_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'reviews.csv'
            first = {'ASIN': 'B00FLYWNYQ', '评论用户名': 'Fixture', '评论简要': 'Title', '评论内容': 'A real body'}
            another = dict(first, 评论内容='Another body')
            blank = dict(first, 评论内容='')
            self.assertEqual(1, amazon.save_reviews([first, first, blank], path))
            self.assertEqual(1, amazon.save_reviews([first, another], path))
            before = path.read_bytes()
            self.assertEqual(0, amazon.save_reviews([first, another], path))
            self.assertEqual(before, path.read_bytes())
            self.assertEqual(2, len(amazon.read_rows(path)))

    def test_replace_failure_preserves_original_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'reviews.csv'
            path.write_bytes(b'original bytes\n')
            with mock.patch.object(amazon.os, 'replace', side_effect=PermissionError('locked')):
                with self.assertRaises(PermissionError):
                    amazon.atomic_csv(path, amazon.REVIEW_FIELDS, [])
            self.assertEqual(b'original bytes\n', path.read_bytes())
            self.assertEqual([path], list(Path(directory).iterdir()))

    def test_existing_product_still_reaches_comment_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            item = {'ASIN': 'B00FLYWNYQ', '标题': 'Existing product', '价格': '$79.00'}
            amazon.save_product(item, data_dir / 'goods_infos.csv')
            with mock.patch.object(amazon, 'DATA_DIR', data_dir), \
                 mock.patch.object(amazon, 'page', mock.Mock()), \
                 mock.patch.object(amazon, 'wait_for_content', return_value='<html/>'), \
                 mock.patch.object(amazon, 'extract_product', return_value=item), \
                 mock.patch.object(amazon, 'extract_customer_tags', return_value=[]), \
                 mock.patch.object(amazon, 'get_reviews', return_value={'status': 'success', 'seen': 2, 'added': 2}) as reviews:
                result = amazon.get_info('B00FLYWNYQ')
            reviews.assert_called_once()
            self.assertEqual('success', result['reviews'])
            self.assertEqual(1, len(amazon.read_rows(data_dir / 'goods_infos.csv')))

    def test_login_interruption_keeps_completed_product_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory)
            item = {'ASIN': 'B00FLYWNYQ', '标题': 'Product', '价格': '$79.00'}
            interrupted = amazon.AccessRequired('login_required')
            interrupted.reviews_seen = 2
            interrupted.reviews_added = 2
            with mock.patch.object(amazon, 'DATA_DIR', data_dir), \
                 mock.patch.object(amazon, 'page', mock.Mock()), \
                 mock.patch.object(amazon, 'wait_for_content', return_value='<html/>'), \
                 mock.patch.object(amazon, 'extract_product', return_value=item), \
                 mock.patch.object(amazon, 'extract_customer_tags', return_value=[]), \
                 mock.patch.object(amazon, 'get_reviews', side_effect=interrupted):
                with self.assertRaises(amazon.AccessRequired) as caught:
                    amazon.get_info('B00FLYWNYQ')
            self.assertEqual('saved', caught.exception.result['product'])
            self.assertEqual(2, caught.exception.result['reviews_added'])
            self.assertEqual(1, len(amazon.read_rows(data_dir / 'goods_infos.csv')))
    def test_select_asins_defaults_and_explicit_deduplication(self):
        self.assertEqual(amazon.DEFAULT_ASINS, amazon.select_asins())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'asins.txt'
            path.write_text('b09xmyftb7\nB00FLYWNYQ,B09XMYFTB7\n', encoding='utf-8')
            selected = amazon.select_asins(['b00flywnyq'], path, limit=3)
        self.assertEqual(['B00FLYWNYQ', 'B09XMYFTB7'], selected)

    def test_explicit_empty_asin_file_does_not_start_browser(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / 'data'
            asin_file = Path(directory) / 'empty.txt'
            asin_file.write_text('', encoding='utf-8')
            with mock.patch.object(amazon, 'DATA_DIR', data_dir), \
                 mock.patch.object(amazon, 'setup_browser') as setup:
                self.assertEqual(0, amazon.main(['--asin-file', str(asin_file)]))
            setup.assert_not_called()
            report = json.loads((data_dir / 'last_run.json').read_text(encoding='utf-8'))
            self.assertEqual([], report['results'])

    def test_cli_asin_and_limit_override_defaults_without_real_browser(self):
        with tempfile.TemporaryDirectory() as directory:
            data_dir = Path(directory) / 'data'
            result = {'ASIN': 'B000TEST01', 'product': 'saved', 'reviews': 'success', 'reviews_seen': 1, 'reviews_added': 1}
            with mock.patch.object(amazon, 'DATA_DIR', data_dir), \
                 mock.patch.object(amazon, 'setup_browser', return_value=object()) as setup, \
                 mock.patch.object(amazon, 'get_info', return_value=result) as get_info:
                self.assertEqual(0, amazon.main(['--asin', 'B000TEST01', '--asin', 'B000TEST02', '--limit', '1']))
            setup.assert_called_once()
            get_info.assert_called_once_with('B000TEST01', amazon.MAX_REVIEW_PAGES, False)

    def test_relative_asin_file_is_resolved_from_amazon_dir_when_cwd_differs(self):
        with tempfile.TemporaryDirectory(dir=amazon.SCRIPT_DIR) as script_temp, \
             tempfile.TemporaryDirectory() as other_cwd:
            input_path = Path(script_temp) / 'asins.txt'
            input_path.write_text('B000TEST01\n', encoding='utf-8')
            relative = str(input_path.relative_to(amazon.SCRIPT_DIR))
            previous = os.getcwd()
            try:
                os.chdir(other_cwd)
                with mock.patch.object(amazon, 'DATA_DIR', Path(other_cwd) / 'data'), \
                     mock.patch.object(amazon, 'setup_browser') as setup:
                    self.assertEqual(0, amazon.main(['--asin-file', relative, '--limit', '0']))
                setup.assert_not_called()
            finally:
                os.chdir(previous)


if __name__ == '__main__':
    unittest.main()
