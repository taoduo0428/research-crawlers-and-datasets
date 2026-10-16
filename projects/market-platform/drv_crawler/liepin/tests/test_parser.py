from __future__ import annotations

import unittest

from drv_crawler.liepin.models import CareerCategory
from drv_crawler.liepin.parser import (
    career_page_index,
    has_nonzero_numeric_salary,
    is_empty_career_page,
    looks_like_career_page,
    looks_like_challenge,
    parse_detail_description,
    parse_job_cards,
    parse_next_page_url,
    parse_taxonomy,
)
from drv_crawler.liepin.tests.fixtures import (
    EMPTY_JOB_HTML,
    DETAIL_JOB_FALLBACK_HTML,
    DETAIL_JOB_HTML,
    JOB_HTML,
    TAXONOMY_HTML,
    build_job_page,
)


class ParserTests(unittest.TestCase):
    def test_career_page_identity_accepts_percent_encoded_pipe_slug(self) -> None:
        cases = (
            ("shejijl|zhuguan", "shejijl%7Czhuguan"),
            ("youxidong作", "youxidong%E4%BD%9C"),
            ("zhu baoxiaoshou", "zhu%20baoxiaoshou"),
        )
        for raw_slug, encoded_slug in cases:
            with self.subTest(raw_slug=raw_slug):
                self.assertEqual(
                    0,
                    career_page_index(
                        f"https://www.liepin.com/career/{encoded_slug}/pn0/",
                        category_url=f"https://www.liepin.com/career/{raw_slug}/",
                    ),
                )

    def test_parses_official_three_level_taxonomy(self) -> None:
        categories = parse_taxonomy(TAXONOMY_HTML)
        self.assertEqual(2, len(categories))
        self.assertEqual("IT/互联网 > 后端开发 > Java", categories[0].path)
        self.assertEqual("https://www.liepin.com/career/python/", categories[1].url)

    def test_parses_job_card_and_preserves_category_identity(self) -> None:
        category = CareerCategory(
            "IT/互联网",
            "后端开发",
            "Java",
            "https://www.liepin.com/career/java/",
        )
        records = parse_job_cards(JOB_HTML, category, per_category=2)
        self.assertEqual(1, len(records))
        record = records[0]
        self.assertEqual("84299681", record.job_id)
        self.assertEqual("高级Java开发工程师", record.title)
        self.assertEqual("18-29k·14薪", record.salary_raw)
        self.assertEqual("武汉-中北路", record.location)
        self.assertEqual("5-8年", record.experience)
        self.assertEqual("本科", record.education)
        self.assertEqual("互联网", record.company_industry)
        self.assertEqual("1000-2000人", record.company_size)
        unified = record.to_unified_dict()
        self.assertEqual("liepin_home_career_taxonomy", unified["category_source"])
        self.assertEqual(category.path, unified["category_path_raw"])
        self.assertEqual("18-29k·14薪", unified["price_evidence"][0]["raw_text"])

    def test_job_page_metadata_and_numeric_salary_eligibility(self) -> None:
        category = CareerCategory(
            "IT/互联网", "后端开发", "Java", "https://www.liepin.com/career/java/"
        )
        html = build_job_page(
            [(1, "0-0k"), (2, "面议"), (3, ""), (4, "18-29k·14薪")],
            current_page=1,
        )
        records = parse_job_cards(
            html,
            category,
            per_category=10,
            page_no=2,
            list_position_offset=40,
        )
        self.assertEqual([41, 42, 43, 44], [record.list_position for record in records])
        self.assertEqual([2, 2, 2, 2], [record.page_no for record in records])
        self.assertEqual(
            [False, False, False, True],
            [has_nonzero_numeric_salary(record) for record in records],
        )
        self.assertEqual(2, records[-1].to_unified_dict()["page_no"])

    def test_next_page_must_stay_in_same_official_career(self) -> None:
        current_url = "https://www.liepin.com/career/java/pn0/"
        html = build_job_page(
            [(1, "10-20k")], current_page=0, page_links=(0, 1)
        )
        self.assertEqual(
            "https://www.liepin.com/career/java/pn1/",
            parse_next_page_url(
                html,
                current_url=current_url,
                category_url="https://www.liepin.com/career/java/",
            ),
        )

        cross_category = build_job_page(
            [(1, "10-20k")],
            current_page=0,
            page_links=(0, 1),
            next_slug="python",
        )
        with self.assertRaisesRegex(Exception, "career"):
            parse_next_page_url(
                cross_category,
                current_url=current_url,
                category_url="https://www.liepin.com/career/java/",
            )

    def test_next_page_ignores_data_currentpage_target_metadata(self) -> None:
        html = build_job_page(
            [(1, "10-20k")], current_page=99, page_links=(0, 1)
        )
        self.assertEqual(
            "https://www.liepin.com/career/java/pn1/",
            parse_next_page_url(
                html,
                current_url="https://www.liepin.com/career/java/pn0/",
                category_url="https://www.liepin.com/career/java/",
            ),
        )

    def test_empty_career_page_is_recognized(self) -> None:
        self.assertTrue(looks_like_career_page(EMPTY_JOB_HTML))
        self.assertTrue(is_empty_career_page(EMPTY_JOB_HTML))
        self.assertFalse(is_empty_career_page("<div class='job-list-box'></div>"))

    def test_extracts_detail_jd_and_keeps_section_boundaries(self) -> None:
        description = parse_detail_description(DETAIL_JOB_HTML)
        self.assertIsNotNone(description)
        assert description is not None
        self.assertIn("职位职责：", description)
        self.assertIn("职位要求：", description)
        self.assertIn("技能要求：", description)
        self.assertIn("1、负责项目开发；", description)
        self.assertNotIn("其他信息", description)

    def test_detail_parser_falls_back_to_position_introduction_dd(self) -> None:
        self.assertEqual(
            "职位职责：负责测试。职位要求：经验不限。",
            parse_detail_description(DETAIL_JOB_FALLBACK_HTML),
        )
        self.assertIsNone(parse_detail_description(EMPTY_JOB_HTML))

    def test_challenge_detection_is_bounded_to_page_evidence(self) -> None:
        self.assertTrue(
            looks_like_challenge(
                "<html><title>安全验证</title></html>",
                "https://www.liepin.com/career/java/",
            )
        )
        self.assertFalse(
            looks_like_challenge(JOB_HTML, "https://www.liepin.com/career/java/")
        )


    def test_normal_job_security_center_text_is_not_a_challenge(self) -> None:
        html = (
            "<html><title>Industrial Data Engineer</title><body>"
            "<h1>Industrial Data Engineer</h1>"
            "<div>Company: \u56fd\u5bb6\u5de5\u4fe1\u5b89\u5168\u4e2d\u5fc3</div>"
            "<div>Work with the \u8d28\u91cf\u5b89\u5168\u4e2d\u5fc3 team.</div>"
            "</body></html>"
        )
        self.assertFalse(
            looks_like_challenge(html, "https://www.liepin.com/job/123.shtml")
        )

    def test_structured_captcha_is_a_challenge(self) -> None:
        html = '<html><body><div class="geetest_panel"></div></body></html>'
        self.assertTrue(
            looks_like_challenge(html, "https://www.liepin.com/job/123.shtml")
        )


if __name__ == "__main__":
    unittest.main()
