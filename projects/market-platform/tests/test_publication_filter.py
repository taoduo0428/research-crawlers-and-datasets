from __future__ import annotations

import unittest
from datetime import date

try:
    from drv_crawler.publication import PublicationFilter
except ModuleNotFoundError:
    PublicationFilter = None  # type: ignore[assignment,misc]


class PublicationFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assertIsNotNone(
            PublicationFilter,
            "drv_crawler.publication.PublicationFilter is not implemented",
        )
        self.window = PublicationFilter(  # type: ignore[operator]
            recent_months=3,
            as_of=date(2026, 7, 21),
        )

    def test_resolves_calendar_month_cutoff(self) -> None:
        self.assertEqual(date(2026, 4, 21), self.window.cutoff)
        self.assertEqual("2026-04-21", self.window.describe()["cutoff"])

    def test_absolute_dates_use_an_inclusive_lower_bound(self) -> None:
        cases = {
            "2026-04-20": "too_old",
            "2026-04-21发布": "include",
            "2026.07.14": "include",
            "2026/07/21": "include",
            "2026-07-22": "future",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(expected, self.window.decide(raw).status)

    def test_relative_months_preserve_boundary_uncertainty(self) -> None:
        cases = {
            "1个月前": "include",
            "2个月前": "include",
            "3个月前": "uncertain",
            "大约3个月前": "uncertain",
            "4个月前": "too_old",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(expected, self.window.decide(raw).status)

    def test_relative_year_variants_are_conservative(self) -> None:
        cases = {
            '\u0038\u5e74\u591a\u524d\u53d1\u5e03': 'too_old',
            '\u63a5\u8fd1\u0039\u5e74\u524d\u53d1\u5e03': 'too_old',
            '\u63a5\u8fd1\u0031\u5e74\u524d': 'uncertain',
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(expected, self.window.decide(raw).status)

    def test_relative_days_and_unknown_values_are_strict(self) -> None:
        cases = {
            "刚刚": "include",
            "昨天": "include",
            "91天前": "include",
            "92天前": "too_old",
            "": "unknown",
            "招聘者刚刚活跃": "unknown",
            None: "unknown",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(expected, self.window.decide(raw).status)

    def test_subday_relative_times_are_strict_across_midnight(self) -> None:
        today_only = PublicationFilter(  # type: ignore[operator]
            published_since="2026-07-21",
            as_of=date(2026, 7, 21),
        )
        for raw in ("刚刚", "1分钟前", "1439分钟前", "1小时前", "23小时前"):
            with self.subTest(raw=raw):
                decision = today_only.decide(raw)
                self.assertEqual("uncertain", decision.status)
                self.assertEqual(date(2026, 7, 20), decision.interval_start)
                self.assertEqual(date(2026, 7, 21), decision.interval_end)
        for raw in ("1440分钟前", "24小时前"):
            with self.subTest(raw=raw):
                self.assertEqual("too_old", today_only.decide(raw).status)
        self.assertEqual("include", today_only.decide("今天").status)

    def test_month_end_and_leap_year_cutoffs_are_clamped(self) -> None:
        january = PublicationFilter(  # type: ignore[operator]
            recent_months=3,
            as_of=date(2026, 5, 31),
        )
        leap_year = PublicationFilter(  # type: ignore[operator]
            recent_months=12,
            as_of=date(2024, 2, 29),
        )
        self.assertEqual(date(2026, 2, 28), january.cutoff)
        self.assertEqual(date(2023, 2, 28), leap_year.cutoff)

    def test_malformed_relative_counts_become_unknown(self) -> None:
        for raw in ("0个月前", "999999999天前", "99999个月前", "99999年前"):
            with self.subTest(raw=raw):
                self.assertEqual("unknown", self.window.decide(raw).status)


if __name__ == "__main__":
    unittest.main()
