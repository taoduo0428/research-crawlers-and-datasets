from __future__ import annotations

import unittest

from tools.build_recruitment_detail_results import compact_row


class RecruitmentDetailResultsTests(unittest.TestCase):
    def test_boss_does_not_fall_back_to_list_page_labels(self) -> None:
        row = {
            "site_name": "boss",
            "title_raw": "Java工程师",
            "category_path_raw": "互联网/AI > 后端开发 > Java",
            "description_raw": "3-5年；本科；Java",
            "budget_raw": "20-30K",
        }
        self.assertEqual("", compact_row(row, None)["岗位要求"])

    def test_detail_enrichment_wins_for_boss_and_liepin(self) -> None:
        for site_name in ("boss", "liepin"):
            with self.subTest(site_name=site_name):
                row = {"site_name": site_name, "title_raw": "测试岗位"}
                result = compact_row(row, {"jd": "岗位职责：负责真实业务开发。"})
                self.assertEqual("岗位职责：负责真实业务开发。", result["岗位要求"])

    def test_51job_keeps_verified_api_description(self) -> None:
        row = {
            "site_name": "51job",
            "title_raw": "自动化工程师",
            "description_raw": "工作职责：维护自动化设备。",
        }
        self.assertEqual("工作职责：维护自动化设备。", compact_row(row, None)["岗位要求"])


if __name__ == "__main__":
    unittest.main()
