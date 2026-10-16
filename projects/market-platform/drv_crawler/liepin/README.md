# 猎聘职业分类岗位采集器

该子工程独立采集猎聘岗位，不依赖根项目的共享 runner。当前主链路来自猎聘原站：从首页解析官方三级职业目录，再按 `/career/<slug>/pnN/` 分页访问每个三级目录。实测列表页最多约 40 条岗位；程序会在分类内连续翻页，直到达到配额、确认自然耗尽或触发页数上限。

2026-07-22 最新实测目录规模为 14 个一级类别、99 个二级类别、1013 个三级类别。官网目录数量会随职业上下线小幅变化，程序不硬编码该总数；目录会在运行时重新校验，少于 500 条时视为页面结构或风控异常，不会把残缺目录写成完整结果。

## 安装

在项目根目录使用现有虚拟环境：

```powershell
python -m pip install -r drv_crawler\liepin\requirements.txt
```

只有需要人工登录时才需要安装 Chromium：

```powershell
python -m playwright install chromium
```

## 运行

先做两个类别的真实小样本：

```powershell
python -m drv_crawler.liepin crawl --max-categories 2 --per-category 2 --delay-min 0 --delay-max 0
```

正式覆盖所有尚未完成的三级职业，每类最多保留 50 条有明确非零数字薪资的岗位：

```powershell
python -m drv_crawler.liepin crawl --per-category 50 --numeric-salary-only --max-pages-per-category 20
```

重复执行同一命令会从 SQLite 检查点继续。仅刷新官方目录：

```powershell
python -m drv_crawler.liepin taxonomy --refresh-taxonomy
```

查看断点状态：

```powershell
python -m drv_crawler.liepin status
```

默认自动探测 `http://127.0.0.1:7897`，不可用时回退直连。强制使用代理：

```powershell
python -m drv_crawler.liepin crawl --proxy-policy required --proxy-url http://127.0.0.1:7897
```

## 登录态

职业页匿名状态通常可读。遇到登录墙或验证页时，启动有界人工登录窗口：

```powershell
python -m drv_crawler.liepin login --wait-seconds 300
python -m drv_crawler.liepin crawl --cookie-file .crawler-state\liepin\storage_state.json
```

也可以设置 `LIEPIN_COOKIE_FILE` 指向 Cookie JSON 或 Playwright storage-state。短期调试可设置 `LIEPIN_COOKIE`，但不要把真实 Cookie 写进代码、README 或提交记录。

## 输出与断点

默认目录为 `.crawler-state/liepin/`：

- `checkpoint.sqlite3`：类别状态和岗位去重索引；每个类别原子提交。
- `jobs.jsonl`：统一字段岗位结果，每次运行结束时原子导出。
- `coverage.json`：每个三级类别的检查点状态、页数、过滤数量和配额结果。
- `storage_state.json`：可选登录态，已被忽略。

同一岗位可能同时属于多个官方职业页。去重键使用“三级职业 URL + 猎聘 jobId”，因此同一类别内不会重复，同时保留跨类别的原站分类关系；`metadata.source_job_id` 可用于后续计算全局唯一岗位数。

`coverage.json` 的 `quota_outcome` 明确区分：

- `quota_met`：已收集到目标数量。
- `exhausted_below_quota`：官网分页已明确结束，但有效岗位不足目标数量。
- `empty`：第一页是官网明确空态。
- `page_limit_below_quota`：达到配置页数上限时官网仍有下一页，当前结果是有界部分结果。
- `failed`：验证墙、登录墙、异常 HTML、跨职业链接或重复分页导致失败。

`--numeric-salary-only` 是显式的数据口径。启用后，空薪资、面议和仅含零金额的岗位不会写入结果，也不会占用 50 条配额。旧 SQLite 断点没有该字段时按关闭状态兼容；不能在已有数据的同一输出目录中切换该口径，应使用新的输出目录。提高同一口径下的 `--per-category` 时，旧的低配额完成分类会自动重新进入队列；已确认自然耗尽的分类不会重复请求。

## 集成接口

```python
from pathlib import Path

from drv_crawler.liepin import LiepinConfig, LiepinCrawler

config = LiepinConfig(
    output_dir=Path(".crawler-state/liepin"),
    per_category=50,
    numeric_salary_only=True,
    max_pages_per_category=20,
)
with LiepinCrawler(config) as crawler:
    summary = crawler.run()
print(summary.output_path)
```

`jobs.jsonl` 已对齐根项目的主要字段：`site_name`、`content_category`、`title_raw`、`price_evidence`、三级分类、详情链接和 `record_id`。分类来源固定为 `liepin_home_career_taxonomy`，不会把岗位标题误当分类。
