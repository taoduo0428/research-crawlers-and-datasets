from __future__ import annotations

import unittest

from bs4 import BeautifulSoup

from drv_crawler.categories import (
    CategoryParseError,
    category_from_zbj_list_url,
    parse_epwk_detail_category,
    parse_shixian_detail_category,
    validate_original_category_fields,
)


class OriginalCategoryTests(unittest.TestCase):
    def test_zbj_uses_verified_source_heading_mapping(self) -> None:
        category = category_from_zbj_list_url(
            "https://www.zbj.com/xq/dzlogo/r4p12/"
        )
        self.assertEqual(("广告宣传品设计",), category.levels)
        self.assertEqual("source_list_heading", category.source)

    def test_zbj_rejects_same_path_on_another_host(self) -> None:
        with self.assertRaises(CategoryParseError):
            category_from_zbj_list_url("https://example.test/xq/dzlogo/r4p12/")

    def test_epwk_excludes_current_title_by_url_role_not_text(self) -> None:
        soup = BeautifulSoup(
            """
            <div class="task_user_info"><h1>软件开发</h1></div>
            <div class="task-user-info-action"><span class="dib_vm">任务编号：123</span></div>
            <div class="cct-bread-crumb">
              <a href="https://www.epwk.com/">首页</a>
              <a href="https://task.epwk.com/">所有任务</a>
              <a href="https://task.epwk.com/develop/">开发</a>
              <a href="https://task.epwk.com/ruanjian/">软件开发</a>
              <a href="https://task.epwk.com/qita/">其他软件开发</a>
              <a href="https://task.epwk.com/123/">软件开发</a>
            </div>
            """,
            "html.parser",
        )

        category = parse_epwk_detail_category(
            soup,
            detail_url="https://task.epwk.com/123/",
            expected_titles=("软件开发",),
        )

        self.assertEqual(("开发", "软件开发", "其他软件开发"), category.levels)
        self.assertEqual("开发 > 软件开发 > 其他软件开发", category.path)

    def test_epwk_current_nuxt_markup_uses_breadcrumb_identity(self) -> None:
        soup = BeautifulSoup(
            """
            <div class="contxt"><div class="rctr"><div class="title">当前任务</div></div></div>
            <div class="cct-bread-crumb">
              <a href="https://www.epwk.com">首页</a>
              <a href="https://task.epwk.com">所有任务</a>
              <a href="https://task.epwk.com/develop/">开发</a>
              <a href="https://task.epwk.com/soft/">软件开发</a>
              <a href="https://task.epwk.com/wuxian/">APP开发</a>
              <a href="/1234567/">当前任务</a>
            </div>
            <script>window.__NUXT__={data:[{taskData:{task_id:"1234567"}}]}</script>
            """,
            "html.parser",
        )
        category = parse_epwk_detail_category(
            soup,
            detail_url="https://task.epwk.com/1234567/",
            expected_titles=("当前任务",),
        )
        self.assertEqual(("开发", "软件开发", "APP开发"), category.levels)
        self.assertTrue(category.identity_verified)

    def test_epwk_rejects_title_identity_mismatch(self) -> None:
        soup = BeautifulSoup(
            """
            <div class="task_user_info"><h1>另一条任务</h1></div>
            <div class="task-user-info-action"><span class="dib_vm">任务编号：123</span></div>
            <div class="cct-bread-crumb"></div>
            """,
            "html.parser",
        )
        with self.assertRaises(CategoryParseError):
            parse_epwk_detail_category(
                soup,
                detail_url="https://task.epwk.com/123/",
                expected_titles=("预期任务",),
            )

    def test_epwk_accepts_explicit_indexed_original_category_recovery(self) -> None:
        levels = validate_original_category_fields(
            "epwk",
            {
                "category_level_1_raw": "视频",
                "category_level_2_raw": "影视后期",
                "category_level_3_raw": "剪辑服务",
                "category_path_raw": "视频 > 影视后期 > 剪辑服务",
                "category_source": (
                    "search_indexed_original_category_listing+original_site_taxonomy"
                ),
                "category_source_url": "https://task.epwk.com/ypjj/",
                "category_identity_verified": True,
            },
            detail_url="https://task.epwk.com/1156205/",
        )

        self.assertEqual(("视频", "影视后期", "剪辑服务"), levels)

    def test_shixian_uses_breadcrumb_category_not_matching_title(self) -> None:
        soup = BeautifulSoup(
            """
            <article class="job-show">
              <h1 class="title">支付系统后端开发<span class="type">远程全职</span></h1>
            </article>
            <ol class="breadcrumb jobs">
              <li><a href="/job/hangzhou">杭州</a></li>
              <li><a href="/job/hangzhou/backend">后端工程师</a></li>
            </ol>
            """,
            "html.parser",
        )

        category = parse_shixian_detail_category(
            soup,
            detail_url="https://shixian.com/jobs/1",
            expected_title="支付系统后端开发",
        )

        self.assertEqual(("后端工程师",), category.levels)
        self.assertEqual("https://shixian.com/job/hangzhou/backend", category.source_url)

    def test_shixian_rejects_category_link_on_another_host(self) -> None:
        soup = BeautifulSoup(
            """
            <article class="job-show"><h1 class="title">后端工程师</h1></article>
            <ol class="breadcrumb jobs">
              <li><a href="/job/hangzhou">杭州</a></li>
              <li><a href="https://example.test/job/hangzhou/backend">后端工程师</a></li>
            </ol>
            """,
            "html.parser",
        )
        with self.assertRaises(CategoryParseError):
            parse_shixian_detail_category(
                soup,
                detail_url="https://shixian.com/jobs/1",
                expected_title="后端工程师",
            )


if __name__ == "__main__":
    unittest.main()
