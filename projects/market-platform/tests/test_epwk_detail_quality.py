import unittest

from bs4 import BeautifulSoup

from drv_crawler.core import ProjectConfig
from drv_crawler.exporters import flatten_record
from drv_crawler.models import CrawlRecord, SiteResult
from drv_crawler.sites import EpwkAdapter


class _Client:
    def load_storage_state(self, *_: object, **__: object) -> int:
        return 0


class EpwkDetailQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = ProjectConfig("crawler.config.yaml")
        cls.adapter = EpwkAdapter(cls.config, _Client(), lambda *_: None)

    @staticmethod
    def _record() -> CrawlRecord:
        return CrawlRecord(
            site_name="epwk",
            content_category="outsourcing_task",
            title_raw="测试任务",
            list_url="https://task.epwk.com/?o=7",
            detail_url="https://task.epwk.com/1234567/",
            page_no=1,
            list_position=1,
        )

    @staticmethod
    def _html(description: str, mode: str = "招标", status: str = "圆满完成") -> BeautifulSoup:
        return BeautifulSoup(
            f"""
            <div class="task_user_info">
              <h1>测试任务</h1>
              <span class="username">公开雇主</span>
            </div>
            <div class="task-user-info-action">
              <span class="dib_vm">任务编号：1234567</span>
            </div>
            <div class="cct-bread-crumb">
              <a href="https://task.epwk.com/">首页</a>
              <a href="/cat/a">软件开发</a><a href="/cat/b">网站</a>
              <a href="/cat/c">定制</a><a href="/1234567/">测试任务</a>
            </div>
            <div class="task-detail-content">
              <h1>测试任务</h1>
              <p>{description}</p>
              <p>登录查看完整需求 收藏任务 返回顶部</p>
            </div>
            <div class="contxt"><div class="catList"><span class="money">5000元</span></div></div>
            <div class="task_user_info">
              <span class="mode">{mode}</span><span class="state">{status}</span>
            </div>
            <div class="task-progress-item"><span class="step_off">
              <b>发布需求</b><font>2026.07.18</font>
            </span></div>
            <div>截止时间：2026-08-08 投标人数：12</div>
            """,
            "html.parser",
        )

    def test_description_cleaning_and_quality_pass_at_80(self) -> None:
        record = self._record()
        description = "需求正文" + "交付网站后台和管理端，包含登录、订单、部署及源代码交付。" * 3
        self.adapter._populate_detail(SiteResult(site_name="epwk", requested_limit=1), record,
                                      detail_soup=self._html(description))

        self.assertGreaterEqual(record.metadata["description_length"], 80)
        self.assertNotIn("登录查看完整需求", record.description_raw or "")
        self.assertEqual("招标", record.job_type_raw)
        self.assertEqual("圆满完成", record.status_raw)
        self.assertTrue(record.metadata["hard_filter_pass"])
        self.assertTrue(record.metadata["main_sample"])
        self.assertEqual("1234567", record.metadata["task_id"])
        self.assertEqual("2026.07.18", record.published_at_raw)
        self.assertEqual("2026-08-08", record.metadata["deadline_raw"])
        self.assertEqual("12", record.metadata["bid_count_raw"])

    def test_description_length_is_informational_not_a_main_sample_filter(self) -> None:
        record = self._record()
        self.adapter._apply_epwk_detail_fields(record, self._html("需求" * 21 + "预算5000元需"))
        self.assertEqual(50, record.metadata["description_length"])
        self.assertTrue(record.metadata["main_sample"])
        self.assertFalse(record.metadata["needs_ai_review"])
        self.assertEqual([], record.metadata["exclude_reasons"])
        self.assertEqual("gray", record.metadata["quality_tier"])

        exact = self._record()
        self.adapter._apply_epwk_detail_fields(exact, self._html("需求" * 36 + "预算5000元需"))
        self.assertEqual(80, exact.metadata["description_length"])
        self.assertTrue(exact.metadata["main_sample"])
        self.assertEqual("standard", exact.metadata["quality_tier"])

    def test_trade_modes_and_completed_status_are_read_verbatim(self) -> None:
        for mode in ("招标", "雇佣", "直接雇佣"):
            with self.subTest(mode=mode):
                record = self._record()
                self.adapter._apply_epwk_detail_fields(
                    record, self._html("明确需求" * 30, mode=mode)
                )
                self.assertEqual(mode, record.job_type_raw)
                self.assertEqual("圆满完成", record.status_raw)
        missing = self._record()
        self.adapter._apply_epwk_detail_fields(
            missing, BeautifulSoup("<div class='task-detail-content'>正文</div>", "html.parser")
        )
        self.assertIsNone(missing.job_type_raw)
        self.assertIsNone(missing.status_raw)
        self.assertFalse(missing.metadata["has_winner"])

    def test_observed_non_completed_statuses_are_retained(self) -> None:
        for status in ("投稿中", "选稿中", "公示中", "已到期关闭"):
            with self.subTest(status=status):
                record = self._record()
                self.adapter._apply_epwk_detail_fields(
                    record, self._html("明确需求" * 30, status=status)
                )
                self.assertEqual(status, record.status_raw)
                self.assertFalse(record.metadata["is_completed"])

    def test_completion_is_preferred_metadata_not_a_main_sample_requirement(self) -> None:
        record = self._record()
        self.adapter._apply_epwk_detail_fields(
            record,
            self._html("明确需求" * 30, mode="招标", status="工作中"),
        )
        self.assertFalse(record.metadata["is_completed"])
        self.assertTrue(record.metadata["hard_filter_pass"])
        self.assertTrue(record.metadata["main_sample"])
        self.assertNotIn("status_not_completed", record.metadata["exclude_reasons"])

    def test_main_sample_only_requires_observed_zhaobiao_or_guyong_mode(self) -> None:
        for mode in ("招标", "雇佣"):
            with self.subTest(mode=mode):
                record = self._record()
                soup = BeautifulSoup(
                    f'<div class="task-detail-content">短描述</div><div>{mode}</div>',
                    "html.parser",
                )
                self.adapter._apply_epwk_detail_fields(record, soup)
                self.assertEqual(mode, record.job_type_raw)
                self.assertEqual(3, record.metadata["description_length"])
                self.assertFalse(record.has_numeric_price)
                self.assertTrue(record.metadata["hard_filter_pass"])
                self.assertTrue(record.metadata["main_sample"])
                self.assertEqual([], record.metadata["exclude_reasons"])

    def test_real_completed_dom_exposes_winner_and_budget(self) -> None:
        record = self._record()
        soup = BeautifulSoup(
            '''
            <div class="contxt">
              <div class="desccnt">需要完成网站设计、后台开发、部署及全部源代码交付。</div>
              <div class="catList"><span class="money">￥16074</span></div>
            </div>
            <div class="rbottom"><div class="info"><div class="dead"><span>任务圆满完成</span></div></div></div>
            <div class="statcnt borderR"><div class="finished"><div class="centrr"><div class="zbgy">
              <div class="zhongbiao"><div class="item">
                <div class="leff"><div class="zb">中标</div></div>
                <div class="rightt"><a><h4>极星网络</h4></a></div>
              </div></div>
            </div></div></div></div>
            ''',
            "html.parser",
        )
        self.adapter._populate_detail(
            SiteResult(site_name="epwk", requested_limit=1), record, detail_soup=soup
        )
        self.assertEqual("圆满完成", record.status_raw)
        self.assertEqual("￥16074", record.budget_raw)
        self.assertTrue(record.metadata["has_winner"])
        self.assertEqual(1, record.metadata["winner_count"])
        self.assertEqual(["极星网络"], record.metadata["winner_names"])

    def test_workflow_text_does_not_fabricate_winner(self) -> None:
        record = self._record()
        soup = self._html("明确需求" * 30, status="选标中")
        soup.append(BeautifulSoup('<div class="window-tip">下一步选择中标服务商</div>', "html.parser"))
        self.adapter._apply_epwk_detail_fields(record, soup)
        self.assertFalse(record.metadata["has_winner"])
        self.assertIsNone(record.metadata["winner_count"])
        self.assertIsNone(record.metadata["winner_names"])

    def test_description_price_context_is_preserved_as_evidence(self) -> None:
        record = self._record()
        self.adapter._apply_epwk_detail_fields(
            record,
            self._html("项目总价38000，一期19000，尾款19000，每张100元。"),
        )
        mentions = record.metadata["description_price_mentions"]
        self.assertTrue(any("项目总价38000" in value for value in mentions))
        self.assertTrue(any(item.price_source == "description_text" for item in record.price_evidence))

    def test_nuxt_task_data_is_preferred_for_new_detail_pages(self) -> None:
        record = self._record()
        soup = BeautifulSoup(
            '''
            <div class="contxt"><div class="desccnt"><div class="nodesc">登录查看详细需求描述</div></div></div>
            <script>
            window.__NUXT__=(function(){return {data:[{taskData:{
              task_id:1234567, task_title:"SSR任务", model_name:"雇佣",
              task_desc:"<p>交付后台、接口和源代码，支持部署验收。</p>",
              cashStr:"￥38000", signup_num:18, username:"公开雇主",
              province:"福建省", city:"厦门市",
              process_desc:{desc:"圆满完成",extDesc:"圆满完成"},
              indusG:{indus_name:"开发"},indusP:{indus_name:"软件开发"},indusC:{indus_name:"网站"}
            }}]}}})()
            </script>
            ''',
            "html.parser",
        )
        self.adapter._apply_epwk_detail_fields(record, soup)
        self.assertEqual("1234567", record.metadata["task_id"])
        self.assertEqual("雇佣", record.job_type_raw)
        self.assertEqual("圆满完成", record.status_raw)
        self.assertIn("交付后台", record.description_raw or "")
        self.assertEqual("18", record.metadata["bid_count_raw"])
        self.assertEqual("公开雇主", record.publisher_name_raw)
        self.assertEqual("￥38000", record.budget_raw)
        self.assertEqual("开发", record.category_level_1_raw)
        self.assertEqual("软件开发", record.category_level_2_raw)
        self.assertEqual("网站", record.category_level_3_raw)
        self.assertEqual("开发 / 软件开发 / 网站", record.category_path_raw)
        self.assertTrue(record.has_numeric_price)

    def test_missing_nuxt_categories_remain_null(self) -> None:
        record = self._record()
        soup = BeautifulSoup(
            '''<script>window.__NUXT__={taskData:{
              task_id:1234567, task_title:"SSR任务", model_name:"招标",
              task_desc:"需求正文", indusG:{indus_name:"开发"},
              indusP:null, indusC:undefined
            }}</script>''',
            "html.parser",
        )
        self.adapter._apply_epwk_detail_fields(record, soup)
        self.assertEqual("开发", record.category_level_1_raw)
        self.assertIsNone(record.category_level_2_raw)
        self.assertIsNone(record.category_level_3_raw)
        self.assertEqual("开发", record.category_path_raw)

    def test_nuxt_positional_aliases_resolve_literal_categories_and_location(self) -> None:
        record = self._record()
        soup = BeautifulSoup(
            '''<script>
            window.__NUXT__=(function(a,b,c){return {data:[{taskData:{
              task_id:1234567, task_desc:"需求正文", model_name:"招标",
              province:a, city:"厦门市",
              indusG:{indus_name:"开发"}, indusP:{indus_name:b}, indusC:{indus_name:c}
            }}]}}})("福建省","软件开发,跨平台（新版）","网站");
            </script>''',
            "html.parser",
        )
        self.adapter._apply_epwk_detail_fields(record, soup)
        self.assertEqual("福建省 厦门市", record.location_raw)
        self.assertEqual("开发", record.category_level_1_raw)
        self.assertEqual("软件开发,跨平台（新版）", record.category_level_2_raw)
        self.assertEqual("网站", record.category_level_3_raw)

    def test_nuxt_detail_metadata_and_publication_date_are_conservative(self) -> None:
        record = self._record()
        soup = BeautifulSoup(
            '''<script>window.__NUXT__={taskData:{
              task_id:1234567, task_title:"SSR详情标题", task_desc:"真实需求正文",
              model_name:"雇佣", publish_time:"2026-08-19", create_time:"2026-08-18 10:20:00",
              start_time:"2026-08-19", end_time:"2026-08-30",
              bounty_dist_desc:"按阶段支付"
            }}</script>''',
            "html.parser",
        )
        self.adapter._apply_epwk_detail_fields(record, soup)
        self.assertEqual("SSR详情标题", record.detail_title_raw)
        self.assertEqual("2026-08-19", record.published_at_raw)
        self.assertEqual("detail_nuxt", record.metadata["publication_date_source"])
        self.assertEqual("2026-08-18 10:20:00", record.metadata["create_time_raw"])
        self.assertEqual("2026-08-19", record.metadata["start_time_raw"])
        self.assertEqual("按阶段支付", record.metadata["budget_distribution_raw"])
        self.assertEqual("2026-08-30", record.metadata["deadline_raw"])

    def test_existing_detail_publication_evidence_wins_over_nuxt(self) -> None:
        record = self._record()
        record.published_at_raw = "2026.08.17"
        record.metadata["publication_date_source"] = "detail_progress"
        soup = BeautifulSoup(
            '''<script>window.__NUXT__={taskData:{
              task_id:1234567, task_desc:"真实需求正文", publish_time:"2026-08-19"
            }}</script>''',
            "html.parser",
        )
        self.adapter._apply_epwk_detail_fields(record, soup)
        self.assertEqual("2026.08.17", record.published_at_raw)
        self.assertEqual("detail_progress", record.metadata["publication_date_source"])

    def test_nuxt_complex_alias_argument_is_not_persisted(self) -> None:
        record = self._record()
        soup = BeautifulSoup(
            '''<script>
            window.__NUXT__=(function(a){return {data:[{taskData:{
              task_id:1234567, task_desc:"需求正文", indusP:{indus_name:a}
            }}]}}})(someExpression());
            </script>''',
            "html.parser",
        )
        self.adapter._apply_epwk_detail_fields(record, soup)
        self.assertIsNone(record.category_level_2_raw)

    def test_nuxt_ext_descriptions_are_cleaned_merged_and_deduplicated(self) -> None:
        record = self._record()
        soup = BeautifulSoup(
            '''<script>window.__NUXT__={taskData:{
              task_id:1234567, task_desc:"<p>主需求：交付品牌命名方案。</p>",
              ext_desc:[
                {ext_desc:"补充需求：名称需要适合海产品干货。",date:"2026-08-01"},
                {ext_desc:"补充需求：名称需要适合海产品干货。",date:"2026-08-02"},
                {ext_desc:"<p>还需要提供三个可注册域名候选。</p>",date:"2026-08-03"}
              ]
            }}</script>''',
            "html.parser",
        )
        self.adapter._apply_epwk_detail_fields(record, soup)
        self.assertEqual(
            ["补充需求：名称需要适合海产品干货。", "还需要提供三个可注册域名候选。"],
            record.metadata["description_supplements"],
        )
        self.assertEqual(1, (record.description_raw or "").count("补充需求：名称需要适合海产品干货。"))
        self.assertIn("还需要提供三个可注册域名候选", record.description_raw or "")

    def test_nuxt_bid_user_only_counts_explicit_winners(self) -> None:
        record = self._record()
        soup = BeautifulSoup(
            '''<script>window.__NUXT__={taskData:{
              task_id:1234567, task_desc:"需求正文",
              bid_user:{bid:[
                {username:"报名者",shop_name:"普通店铺",work_status_name:"报名"},
                {username:"winner-user",shop_name:"公开中标店铺",work_status_name:"中标",
                 nested:{title:"推荐服务不应成为中标者"}},
                {username:"第二中标者",shop_name:aliasName,work_status_name:"中标"}
              ],service:[{shop_name:"推荐店铺",work_status_name:"中标"}]}
            }}</script>''',
            "html.parser",
        )
        self.adapter._apply_epwk_detail_fields(record, soup)
        self.assertTrue(record.metadata["has_winner"])
        self.assertEqual(2, record.metadata["winner_count"])
        self.assertEqual(["公开中标店铺", "第二中标者"], record.metadata["winner_names"])

    def test_nuxt_aliases_do_not_become_description_or_winner_names(self) -> None:
        record = self._record()
        soup = BeautifulSoup(
            '''<script>window.__NUXT__={taskData:{
              task_id:1234567, task_desc:descAlias, ext_desc:extAlias,
              bid_user:{bid:[{username:userAlias,shop_name:shopAlias,
                              work_status_name:"中标"}]}
            }}</script>''',
            "html.parser",
        )
        self.adapter._apply_epwk_detail_fields(record, soup)
        self.assertIsNone(record.description_raw)
        self.assertEqual([], record.metadata["description_supplements"])
        self.assertTrue(record.metadata["has_winner"])
        self.assertEqual(1, record.metadata["winner_count"])
        self.assertIsNone(record.metadata["winner_names"])

    def test_login_prompt_is_missing_description(self) -> None:
        record = self._record()
        self.adapter._apply_epwk_detail_fields(
            record,
            BeautifulSoup(
                '<div class="desccnt"><div class="nodesc">登录查看详细需求描述</div></div>',
                "html.parser",
            ),
        )
        self.assertIsNone(record.description_raw)
        self.assertEqual(0, record.metadata["description_length"])
        self.assertNotIn("description_missing", record.metadata["exclude_reasons"])

    def test_real_low_quality_phrase_is_preserved_for_ai_review(self) -> None:
        record = self._record()
        soup = BeautifulSoup(
            '<div class="task-detail-content">具体详谈 登录查看完整需求描述</div>',
            "html.parser",
        )
        self.adapter._apply_epwk_detail_fields(record, soup)
        self.assertEqual("具体详谈", record.description_raw)
        self.assertEqual(4, record.metadata["description_length"])

    def test_missing_rendered_description_keeps_static_description(self) -> None:
        record = self._record()
        record.description_raw = "静态页面已解析的真实需求"
        soup = BeautifulSoup(
            '<div class="desccnt"><div class="nodesc">登录查看详细需求描述</div></div>',
            "html.parser",
        )
        self.adapter._apply_epwk_detail_fields(record, soup)
        self.assertEqual("静态页面已解析的真实需求", record.description_raw)
        self.assertEqual(len(record.description_raw), record.metadata["description_length"])

    def test_epwk_report_contains_quality_distributions(self) -> None:
        first = self._record()
        self.adapter._populate_detail(
            SiteResult(site_name="epwk", requested_limit=1),
            first,
            detail_soup=self._html("明确需求" * 50, mode="招标", status="圆满完成"),
        )
        second = self._record()
        second.detail_url = "https://task.epwk.com/7654321/"
        self.adapter._apply_epwk_detail_fields(
            second, self._html("短" * 10, mode="直接雇佣", status="工作中")
        )
        result = SiteResult(site_name="epwk", requested_limit=2, records=[first, second])
        stats = result.report_dict()["quality_stats"]
        self.assertEqual(2, stats["description_success_count"])
        self.assertEqual(1, stats["description_length_distribution"]["gte_150"])
        self.assertEqual(1, stats["description_length_distribution"]["lt_40"])
        self.assertEqual(1, stats["completed_count"])
        self.assertEqual(1, stats["main_sample_count"])
        self.assertEqual(1, stats["trade_modes"]["直接雇佣"])

    def test_epwk_detail_metadata_is_flattened_for_csv_and_xlsx(self) -> None:
        record = self._record()
        self.adapter._apply_epwk_detail_fields(
            record, self._html("明确需求" * 30)
        )
        row = flatten_record(record)
        self.assertEqual("1234567", row["task_id"])
        self.assertEqual("2026-08-08", row["deadline_raw"])
        self.assertEqual("12", row["bid_count_raw"])


if __name__ == "__main__":
    unittest.main()
