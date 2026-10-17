# 前程无忧职业分类采集器

前程无忧这部分既能单独运行，也能和总工程配合使用。如果你是从课题组总仓库打开的，当前工作目录应先切到 `projects/market-platform/`，再按照下面相对路径安装依赖和运行。这里的职业分类数量是 **2026-07-22 当时**测到的，不代表网站今天仍有完全相同的分类。
这是独立的前程无忧适配器，不依赖项目根目录的统一 runner。它从当前前程无忧页面读取官方三级“工作职能”树，再按每个三级职业限量采集岗位。

2026-07-22 的实测分类为 26 个一级分类、130 条二级路径、1246 条三级路径，对应 1049 个唯一三级查询代码。相同职业可能同时出现在多个分类路径中，程序只请求一次，再把岗位关联回所有官方路径。

## 参考项目核实

- `Multi-threadedCrawler` 的前程无忧模块提供了 2025-10 页面字段和分页思路，但没有职业树全覆盖。
- `Auto-JobHunter` 的 2026-04 版本使用 Playwright 保存登录态并监听岗位接口，但仍按关键词和指定页采集。
- 本适配器没有复制上述项目代码。当前实现依据实际页面运行时重新实现：使用页面自己的签名请求客户端触发 `api/job/search-pc`，所以不需要在源码中保存 `sign`、`user-token`、Cookie 或 UUID。

## 安装

在项目根目录执行：

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r drv_crawler\job51\requirements.txt
.venv\Scripts\python -m playwright install chromium
```

当前根项目已经安装 Playwright 时不需要重复安装。

## 检查和登录

```powershell
python -m drv_crawler.job51 doctor --config drv_crawler\job51\config.example.json
python -m drv_crawler.job51 login --config drv_crawler\job51\config.example.json
```

匿名搜索是否可用取决于当前会话和站点限制。遇到登录墙、WAF 或人工验证时，运行 `login`，在专用浏览器中手动完成登录/验证。保存前会依次校验页面 `isLogin=true`、非空 `accountId`、原生岗位搜索接口返回可解析 JSON，并在新的浏览器 context 中重放候选状态再次验证登录和搜索；任一步仍返回 WAF/HTML 都不会覆盖旧状态。登录态只写入 `.state/storage_state.json`，该目录已被忽略，不会写入源码或示例配置。

正式采集使用配置中的独立可见 Edge 持久 profile，并把已被 `search-pc` 有效 JSON 证明可用的当前会话另存为 `.state/runtime_session_state.json`。每次启动都优先把运行态合并进 profile，文件损坏时回退到严格登录态；这样首次启动在状态注入前中断也能在下次恢复，同时继续保留 profile 自身的浏览器状态。持久 profile 启动失败会显式停止，不会静默回退临时无头 context。运行态与登录态路径必须不同，运行态可能是匿名会话，永远不会覆盖或冒充经双重验证保存的登录态。HTTP 200 + HTML、结构异常、登录墙或写盘失败都不会生成新的运行态文件，写入采用临时文件替换，失败时保留上一个可用文件。

`city_code="000000"` 表示全国。前程无忧页面首次打开时会把地址栏和界面改成 IP 所在城市，但采集器在每次岗位查询前重新写入 `jobArea=000000`，并只接受实际响应 URL 中城市代码仍为 `000000` 的结果；其他城市代码同样执行这一校验。

## 刷新官方分类

```powershell
python -m drv_crawler.job51 categories `
  --config drv_crawler\job51\config.example.json `
  --output drv_crawler\job51\data\categories.json
```

分类优先从当前页面动态读取。运行采集时若实时分类组件临时失败，程序会使用最近一次本地快照并明确记录日志。

## 采集

先做 10 个唯一职业代码的有界样本：

```powershell
python -m drv_crawler.job51 run `
  --config drv_crawler\job51\config.example.json `
  --max-codes 10 `
  --per-category-limit 3
```

确认样本后覆盖全部职业：

```powershell
python -m drv_crawler.job51 run --config drv_crawler\job51\config.example.json
```

每个唯一三级职业最多采集 50 条、且只让有明确非零数字薪资的岗位占用配额：

```powershell
python -m drv_crawler.job51 run `
  --config drv_crawler\job51\config.example.json `
  --per-category-limit 50 `
  --numeric-salary-only
```

中断后执行相同命令即可续跑。SQLite 检查点会跳过已完成分类，并以 `record_id` 跨页去重。普通模式每个职业默认最多取 3 条、每次最多检查 2 页；页面客户端每页固定 20 条，配置的页数不足理论配额时会自动提高到理论下限。`numeric_salary_only=true` 时为避免面议、空薪资、重复项或职业码不匹配导致理论 3 页仍不足，单次分类页预算提高到有界上限 20 页，并在每个翻页请求之间执行同样的 3-5 秒随机等待。采集到配额、API `totalcount` 耗尽或本次页预算结束即停止；页预算结束但配额未满时保存为可续跑状态，下次从尚未请求的页继续，不会跳页。

数值薪资模式只输出至少一个薪资上下界为非零数字的岗位；每条入选记录仍保留 `price_raw_joined`、`budget_raw`、`price_evidence[].raw_text` 和原始 API item。若整个职业页耗尽仍不足 50 条，则按实际数量结束，不用面议或空薪资补足。

请求间隔默认为 3-5 秒，每处理 20 个职业会冷却 60 秒。若接口返回 HTTP 200 但正文实际为阿里云 WAF HTML，程序按全局限流处理：同一浏览器会话不重复请求，立即停止当前批次，把当前职业放回待处理队列末尾且不消耗分类失败次数，并把 1800 秒冷却截止写入 SQLite。短冷却曾再次触发 WAF，因此正式配置采用 30 分钟静默窗口。冷却期内重复运行会在启动浏览器前直接返回；到期后相同命令自动从其他待处理职业续跑，避免单个持续受限代码阻塞整个队列，也不会尝试绕过验证。

默认产物：

- `output/jobs.jsonl`：岗位数据。字段与根工程 `CrawlRecord` 对齐，使用 `title_raw`、`price_evidence`、`category_level_1_raw` 等字段；完整 API item、数值薪资上下限和城市等扩展值放在 `metadata`，同时保留全部 `category_paths`。
- `output/coverage.json`：1246 条官方路径的处理状态，以及按唯一三级职业代码统计的 `quota_counts`。`coverage_rate` 只表示类别已经处理，`quota_met_rate` 才表示达到每类配额；`exhausted_below_quota`、`page_limit_below_quota`、`empty` 和 `failed` 分开统计。
- `.state/crawl.sqlite3`：断点和去重索引。
- `.state/categories.json`：最近一次官方分类快照。
- `.state/runtime_session_state.json`：至少一次岗位搜索 JSON 校验成功后的可恢复浏览器运行态。

查看进度：

```powershell
python -m drv_crawler.job51 status `
  --state-db-path drv_crawler\job51\.state\crawl.sqlite3
```

## Python 集成接口

```python
from drv_crawler.job51 import CrawlConfig, run_job51

summary = run_job51(
    CrawlConfig(
        city_code="000000",
        per_category_limit=3,
        max_codes=10,
    )
)
print(summary.to_dict())
```

统一工程可直接导入 `CrawlConfig`、`Job51Crawler` 或 `run_job51`。调用结果为 `RunSummary`，数据交换面是 JSONL 和覆盖报告，不要求共享 runner 知道 Playwright 或 SQLite 的内部实现。

## 验证

```powershell
python -m unittest discover -s drv_crawler\job51\tests -v
python -m compileall -q drv_crawler\job51
```

页面组件名、接口字段或签名链发生变化时，解析会显式失败，不会把结构变化误判为“该分类没有岗位”。
