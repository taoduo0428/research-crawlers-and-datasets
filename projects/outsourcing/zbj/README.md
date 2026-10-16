
# 猪八戒（zbj）
源码：`../../market-platform/drv_crawler/sites.py` 的 `ZbjAdapter`。
数据：`data/records.csv`，7,082 条、record_id 无重复。`run-report` 报告 341 个列表页、`window_complete=true`，但 `details_fetched=0`；描述中位数 46 字，不能把列表摘要宣称为完整详情。只有约 1,668 条包含非零数字的预算或价格字段；另一些是平台估价或非数字报价。按本项目需求评估前先做价格类型、单位和真实性筛选。
