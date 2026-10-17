# 猎聘

这里有一份比较完整的源数据，也有整理好的最终表：

- `data/full-source.csv`：**49,056 条**，31 列，`record_id` 不重复。
- `data/final.csv`：**46,607 条**，只留职位、JD、薪资和三级分类。

源表里有 **2,449 条 JD 为空**。两个表的数量刚好相差 2,449，但这只能说明数量对得上，**不能据此断言 46,607 条都已经逐条配对确认**。

爬虫代码在 [`../../market-platform/drv_crawler/liepin/`](../../market-platform/drv_crawler/liepin/README.md)，包括分类、翻页、请求解析、断点和浏览器登录相关逻辑。

要做清洗或去重，尽量从 `full-source.csv` 出发，先保留 `record_id` 与原始链接；不要只拿最终六字段的文案判断两条记录是不是同一个岗位。
