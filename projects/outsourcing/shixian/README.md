# 实现网

这里只有一张 `data/records.csv`，共 **53 条**。样本不大，而且混了两种业务：

- 46 条 `outsourcing_task`：外包需求；
- 7 条 `full_time_job`：全职岗位。

做外包价格分析时先筛出前 46 条，别把全职薪资一起算进去。

爬虫代码在[共用工程](../../market-platform/README.md) 的 `drv_crawler/sites.py`，适配器是 `ShixianAdapter`。描述字段比猪八戒的列表摘要更长一些，但仍建议结合具体详情核对价格与发布时间。
