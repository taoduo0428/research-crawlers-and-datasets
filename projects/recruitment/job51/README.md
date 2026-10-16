
# 前程无忧 / 51job
项目代码 `../../market-platform/drv_crawler/job51/` 包括分类枚举、分页、断点 SQLite、导出与测试。
`data/final.csv`：27,552 条清洗结果，106 行薪资为空。
现存 `job51/output/jobs.jsonl` 只有少量历史试跑记录；本机 `.state` SQLite 也仅有少量测试/烟测记录，不是 27,552 条完整原始层，故不作为正式原始数据上传。保留六字段表，并在未来采集时先写入包含 record_id、URL、公司、JD 的原始层。
