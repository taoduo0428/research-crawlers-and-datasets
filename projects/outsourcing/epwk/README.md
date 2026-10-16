
# 一品威客（epwk）

## 源码
`../../market-platform/drv_crawler/sites.py` 中 `EpwkAdapter`；入口 `../../market-platform/run.py`。依赖原始项目配置、合法网络环境和可验证的任务详情。

## 数据
- `data/legacy-2691.csv`：旧版 2,691 条，任务描述为空，适合核对 ID，不适合正文分析。
- `data/reviewed-1997.csv`：新版本 1,997 条人工审核；A 677 / B 523 / C 424 / D 373。
- `data/high-quality-ab-1200.csv`：A/B 子集 1,200 条。
- `data/price-high-confidence-956.csv`：价格建模优先子集 956 条。
- 其他 `data/` 子集：去重候选、核心样本、强价格样本等，均来自同一审核主集，**不能与主表相加**。

## 数据局限与复现
新版有 376 条详情发布日期为空、使用 sitemap_lastmod 候选日期，A/B 子集 229 条、价格优先子集 195 条。未取得发布日期时不得宣称“已确认近三个月发布”。旧版原始 `run-report` 标记 `partial` / `window_complete=false`，不能按全站完全覆盖理解。
数据主键 `record_id`；预算文本可包含区间、标价、估价、协商价，须区分来源与单位。目录下的 CSV 对联系信息和邮箱/手机号做了脱敏，字段与本机原始记录可对齐。
