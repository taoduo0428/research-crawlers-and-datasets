
# 猎聘
源码 `../../market-platform/drv_crawler/liepin/`，包括请求、解析、断点和协调支持。
`data/full-source.csv`：49,056 条完整层（31 列、record_id 唯一）；其中 2,449 条 JD 空白。
`data/final.csv`：46,607 条六字段清洗版，JD 均非空。数量上两者相差 2,449，但单条原文完全一致验证未完成，不据此断言每条都逐一映射成功。
