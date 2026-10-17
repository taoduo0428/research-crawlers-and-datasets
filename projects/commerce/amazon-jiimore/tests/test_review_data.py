"""离线检查真实历史 CSV，另用小型样例验证合并时不会丢冲突。"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import review_data as review  # noqa: E402


def csvwrite(path: Path, columns: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(columns)
        writer.writerows(rows)


def fixture(root: Path):
    files = {
        "products": ([ "ASIN", "标题"], [
            ["B09XMYFTB7", "商品甲"], ["B074PTZCNX", "商品乙"], ["", "无法关联的记录"],
        ]),
        "tags": (["ASIN", "标签"], [["B09XMYFTB7", "耐用"]]),
        "asin": (["ASIN"], [["B09XMYFTB7"], ["B074PTZCNX"]]),
        "weekly_clicks": (["ASIN", "日期", "周点击量"], [
            ["B09XMYFTB7", "2023-01-08", "55"], ["B074PTZCNX", "2023-01-08", "4"],
        ]),
        "monthly_sales": (["ASIN", "月份", "月销量", "月销售额", "平均单价"], [
            ["B09XMYFTB7", "2023-01", "100", "200", "2"],
            ["B09XMYFTB7", "2023-01", "120", "240", "2"],  # 真实冲突
            ["B09XMYFTB7", "2023-02", "30", "60", "2"],
            ["B09XMYFTB7", "2023-02", "30", "60", "2"],  # 完全重复
            ["", "2023-01", "400", "800", "2"],
            ["B074PTZCNX", "2023-01", "1", "5", "5"],
        ]),
        "monthly_reviews": (["ASIN", "月份", "评分数增长值", "留评率"], [
            ["B09XMYFTB7", "2023-01", "9", "0.1"],
            ["B09XMYFTB7", "2023-03", "1", ""],
            ["B074PTZCNX", "2023-01", "0", ""],
        ]),
        "keepa_history": (["ASIN", "日期", "价格"], [
            ["B09XMYFTB7", "2023-01-01", "5"],
        ]),
    }
    for name, (cols, rows) in files.items():
        csvwrite(root / review.TABLES[name][0], cols, rows)
    return files


class ReviewAmazonTests(unittest.TestCase):
    def test_monthly_conflict_is_not_silently_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "dataset"
            fixture(base)
            output = Path(tmp) / "monthly.csv"
            conflict_file = Path(tmp) / "conflicts.csv"
            result = review.merge_monthly(base, output, conflict_file)
            self.assertEqual(result["sales_conflict_keys"], 1)
            self.assertEqual(result["review_conflict_keys"], 0)
            self.assertEqual(result["sales_rows_with_invalid_keys"], 1)
            self.assertEqual(result["merged_rows"], 4)
            self.assertEqual(result["conflict_source_rows"], 2)
            with output.open(encoding="utf-8-sig", newline="") as file:
                merged = list(csv.DictReader(file))
            jan = next(r for r in merged if
                       (r["ASIN"], r["月份"]) == ("B09XMYFTB7", "2023-01"))
            self.assertEqual(jan["销量数据状态"], "冲突待核对")
            self.assertEqual(jan["月销量"], "")
            self.assertEqual(jan["评分数增长值"], "9")
            feb = next(r for r in merged if
                       (r["ASIN"], r["月份"]) == ("B09XMYFTB7", "2023-02"))
            self.assertEqual(feb["月销量"], "30")
            with self.assertRaises(FileExistsError):
                review.merge_monthly(base, output, conflict_file)

    def test_audit_reports_missing_and_duplicate_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "dataset"
            fixture(base)
            report = review.audit(base)
            self.assertFalse(report["network_used"])
            self.assertEqual(report["coverage"]["four_main_tables_common_asins"], 2)
            self.assertEqual(report["tables"]["products"]["rejected_key_rows"], 1)
            self.assertEqual(report["tables"]["monthly_sales"]["repeated_key_rows"], 2)
            self.assertEqual(report["tables"]["monthly_sales"]["exact_repeated_rows"], 1)
            self.assertEqual(report["tables"]["monthly_sales"]["conflicting_key_groups"], 1)

    def test_asin_exports_seven_separate_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "dataset"
            fixture(base)
            first = base / "amazon" / "products.csv"
            before = hashlib.sha256(first.read_bytes()).hexdigest()
            output = Path(tmp) / "by_asin"
            result = review.export_asin(base, "b09xmyftb7", output)
            self.assertEqual(len(result["files"]), 7)
            self.assertEqual(result["files"]["monthly_sales"]["rows"], 4)
            self.assertEqual(result["files"]["weekly_clicks"]["rows"], 1)
            self.assertEqual(hashlib.sha256(first.read_bytes()).hexdigest(), before)
            with self.assertRaises(ValueError):
                review.export_asin(base, "../danger", output)
            with self.assertRaises(ValueError):
                review.export_asin(base, "B000000000", Path(tmp) / "unknown")
            with self.assertRaises(ValueError):
                review.export_asin(base, "B09XMYFTB7", base / "output")
            with self.assertRaises(ValueError):
                review.assert_destination(ROOT / "data" / "do-not-create.csv", base)

    def test_real_data_coverage_and_conflicts(self):
        report = review.audit(ROOT / "data")
        self.assertEqual(report["tables"]["products"]["rows"], 2006)
        self.assertEqual(report["tables"]["weekly_clicks"]["rows"], 117049)
        self.assertEqual(report["tables"]["monthly_sales"]["rows"], 66871)
        self.assertEqual(report["tables"]["monthly_reviews"]["rows"], 178785)
        self.assertEqual(report["tables"]["monthly_sales"]["valid_unique_asins"], 1998)
        self.assertEqual(report["tables"]["monthly_sales"]["conflicting_key_groups"], 28)
        self.assertEqual(report["tables"]["monthly_reviews"]["conflicting_key_groups"], 1)
        self.assertEqual(report["coverage"]["four_main_tables_common_asins"], 1998)


if __name__ == "__main__":
    unittest.main()
