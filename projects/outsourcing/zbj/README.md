# 猪八戒

猪八戒的数据在 `data/records.csv`，一共 **7,082 条任务**，`record_id` 没有重复。

这批采集跑过 341 个列表页。虽然当时的报告写着采集窗口完成，但**没有进一步抓完整详情页**（`details_fetched=0`）。所以 `description_raw` 多数只是列表摘要，文字比较短，别把它直接当成完整需求书。

报价也要小心。已发布表的 `budget_raw` 为空，其他字段里能找到部分价格或平台估价，但不能当作全部任务的真实成交金额。打算用来训练价格模型的话，先区分价格来源、单位和是否有明确数字。

代码在 [共用爬虫工程](../../market-platform/README.md) 的 `drv_crawler/sites.py`，适配器名是 `ZbjAdapter`。
