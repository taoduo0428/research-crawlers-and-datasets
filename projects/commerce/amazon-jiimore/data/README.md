# 归档的七张历史研究数据表

这是本项目以前已经整理并公开的**历史数据层**，用于研究分析和可复核的统计。2026-10-17 发布新的采集源码时，**没有覆写或重新生成这些表**。当前采集脚本运行时写在 [`../source/data/`](../source/data/README.md) 中。

| 路径 | 历史行数 | 说明 |
| --- | ---: | --- |
| `amazon/products.csv` | 2,006 | Amazon 商品信息；有少量 ASIN 空行 |
| `amazon/tags.csv` | 8,533 | Customer say 标签 |
| `jiimore/asin.csv` | 1,999 | ASIN 输入与商品链接 |
| `jiimore/weekly_clicks.csv` | 117,049 | 旧版商品级周点击量 |
| `sellersprite/monthly_sales.csv` | 66,871 | 旧版月销量、月销售额、均价 |
| `sellersprite/monthly_reviews.csv` | 178,785 | 旧版评分增长、留评率 |
| `sellersprite/keepa_history.csv` | 25,824 | 旧版历史价格、评分曲线 |

**以上行数是原仓库发布时的历史审计结果。** 它们不是当天现网查询结果；多张表有时间跨度、缺值与覆盖率差异。历史月销量有 28 个 `ASIN+月份` 冲突组、月评论有 1 个，不能随意挑一个值覆盖。Keepa 表只覆盖 18 个 ASIN。历史版本的 Jiimore 周点击结果不能误认为当前 `source/jiimore.py` 已恢复该项功能。

数据体积较大，`*.csv` 使用 Git LFS。克隆后在仓库根目录执行：

```bash
git lfs pull
python tools/verify_release.py
python projects/commerce/amazon-jiimore/tools/review_data.py audit
```

表的字段、空值、重复键与冲突详情请看 [数据质量检查](../docs/数据质量检查_2026-10-17.md)；可审计、按月合并、按 ASIN 导出的工具见 [工具说明](../tools/README.md)。