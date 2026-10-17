# BOSS直聘

`data/` 里有两份东西，千万别混：

- `final.csv`：整理后的 **39,594 条岗位**，只有职位、JD、薪资和三级分类。
- `source_id_inventory.csv`：从历史采集的 7 个 worker 断点中整理出的 **40,747 个岗位 ID**。

第二张表主要是帮我们找回来源线索，不是完整岗位原始表，它的 JD 字段也是空的。两份文件不能直接按行号拼起来。

代码在 [`../../market-platform/`](../../market-platform/README.md)，涉及 `drv_crawler/recruitment.py`、`drv_crawler/boss_cdp/` 以及一批以 `boss_` 开头的工具。旧版抓取记录曾提到 40,750 条源记录，与当前 ID 归档口径略有差异；以后复查要分别说明。

如果要补详情，先确认原始 URL、账号环境和重复请求限制，别把没有采到 JD 的记录假设成有完整正文。
