
# BOSS直聘
采集代码 `../../market-platform` 中的 `boss_*`、`drv_crawler/recruitment.py`、相关测试与工具。
`data/final.csv`：39,594 条清洗岗位。
`data/source_id_inventory.csv`：从本机 7 个不同采集/详情 worker JSONL 断点投影出的**岗位 ID 归档**（不是原始网页，也不等于完整 JD）；用于恢复分类和链接溯源。不得将断点文件重复行相加。
源记录历史统计约 40,750；以本机完整性审计和 manifest 为准。
