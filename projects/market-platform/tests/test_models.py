from __future__ import annotations

import unittest

from drv_crawler.models import CrawlRecord, PriceEvidence, SiteResult, dedupe_records


class ModelTests(unittest.TestCase):
    @staticmethod
    def _record_with_price(raw_text: str) -> CrawlRecord:
        record = CrawlRecord(
            site_name="demo",
            content_category="outsourcing_task",
            title_raw="demo",
            list_url="https://example.test/list",
            detail_url="https://example.test/item",
            page_no=1,
            list_position=1,
        )
        record.add_price(
            PriceEvidence(
                raw_text=raw_text,
                price_type="test",
                price_source="test",
                evidence_url=record.list_url,
            )
        )
        return record

    def test_numeric_price_requires_a_nonzero_digit_in_price_evidence(self) -> None:
        cases = {
            "30万-50万元": True,
            "17-20K": True,
            "待服务商报价": False,
            "面议": False,
            "0元": False,
        }
        for raw_text, expected in cases.items():
            with self.subTest(raw_text=raw_text):
                self.assertEqual(expected, self._record_with_price(raw_text).has_numeric_price)

    def test_site_report_includes_filtered_out_count(self) -> None:
        result = SiteResult(site_name="demo", requested_limit=1, filtered_out_count=3)
        self.assertEqual(3, result.report_dict()["filtered_out_count"])

    def test_complete_window_finish_does_not_truncate_to_item_cap(self) -> None:
        first = self._record_with_price("100元")
        second = self._record_with_price("200元")
        second.record_id = "demo-second"
        result = SiteResult(
            site_name="demo",
            requested_limit=1,
            records=[first, second],
            completion_mode="publication_window",
            window_complete=True,
            stop_reason="cutoff_reached",
        )

        result.finish()

        self.assertEqual(2, len(result.records))
        self.assertEqual("success", result.status)
        self.assertTrue(result.quality()["quality_passed"])

    def test_complete_empty_window_is_successful(self) -> None:
        result = SiteResult(
            site_name="demo",
            requested_limit=50,
            completion_mode="publication_window",
            window_complete=True,
            stop_reason="cutoff_reached",
        )

        result.finish()

        self.assertEqual("success", result.status)
        self.assertTrue(result.quality()["quality_passed"])

    def test_raw_price_is_preserved_and_deduped(self) -> None:
        record = CrawlRecord(
            site_name="demo",
            content_category="outsourcing_task",
            title_raw="  demo   task ",
            list_url="https://example.test/list",
            detail_url="https://example.test/1",
            page_no=1,
            list_position=1,
        )
        evidence = PriceEvidence(
            raw_text=" 30万-50万元 ",
            price_type="budget_range",
            price_source="list_page",
            evidence_url=record.list_url,
        )
        record.add_price(evidence)
        record.add_price(evidence)
        self.assertEqual("demo task", record.title_raw)
        self.assertEqual("30万-50万元", record.price_raw_joined)
        self.assertEqual(1, len(record.price_evidence))
        self.assertTrue(record.core_complete)

    def test_record_id_makes_rerun_deduplication_idempotent(self) -> None:
        values = [
            CrawlRecord(
                site_name="demo",
                content_category="service_listing",
                title_raw="same",
                list_url="https://example.test/list",
                detail_url="https://example.test/item/1",
                page_no=1,
                list_position=index,
            )
            for index in (1, 2)
        ]
        self.assertEqual(1, len(dedupe_records(values)))


if __name__ == "__main__":
    unittest.main()
