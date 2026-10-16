> 历史文件：保留原始工程叙述以供回溯。所述旧输出目录和某些 BOSS 采集结论已过时；请以 [当前引擎说明](../README.md) 和 [2026-10-17 审计报告](../../../docs/REPOSITORY_AUDIT_2026-10-17.md) 为准。

# DRV 外包与岗位价格样本爬虫

该项目面向 DRV（Daily Result Value）任务价格表和招聘薪资样本。外包部分覆盖一品威客、猪八戒、实现网和程序员客栈；招聘部分覆盖 BOSS 直聘、前程无忧和猎聘。原始标题与价格原文始终保留，列表价、详情价、平台估价、雇主正文价格及日/月/年薪不会相互覆盖。

这是独立 Python 工程，不依赖 Codex、在线 AI 服务或当前开发会话。安装完成后，采集、匿名浏览器执行、断点记录和数据导出都由本目录中的 Python 代码完成。

## 已实现的站点策略

| 站点 | 采集方式 | 难度 | 当前边界 |
| --- | --- | ---: | --- |
| 一品威客 | `requests` 解析 SSR HTML | 2/5 | 标题、预算、状态公开；完整需求正文可能需正常登录 |
| 猪八戒 | SSR HTML + Nuxt 嵌入状态解析 | 4/5 | 容易触发 `verify.zbj.com`/429；出现滑块时停止并人工接管 |
| 实现网 | `requests` 解析全职/兼职 SSR HTML | 1/5 | 平台估价、预算、详情价和正文价格分栏保存 |
| 程序员客栈 | `requests` 解析 Python/Java 人才分类 | 2/5 | 是人才日/月服务报价，不是具体项目预算 |
| BOSS 直聘 | 专用 Chrome + CDP + 页面内 XHR | 5/5 | 官方 851 个三级职业代码；每类间隔 12-20 秒，限制状态原子冷却后续跑 |
| 前程无忧 | Playwright 复用页面原生签名请求 | 4/5 | 官方 1049 个唯一三级职能代码；WAF HTML 显式识别并保留待处理分类 |
| 猎聘 | `requests` 解析官方 `/career/` 职业页 | 2/5 | 官方 1013 个三级职业页；匿名优先，验证时人工登录 |

## 目录

```text
drv_crawler/
  boss_cdp/       # BOSS 实测可用的 MIT Chrome/CDP 后端
  job51/          # 前程无忧官方职业树、签名 API、SQLite 断点
  liepin/         # 猎聘官方 career 目录、静态岗位卡、SQLite 断点
  recruitment.py # 招聘三站统一调度、快照与登录入口
  boss_archive.py  # 历史快照解析工具（默认不参与 BOSS 运行）
  browser.py       # BOSS Playwright、登录态复用、抓包与 DOM 解析
  cli.py           # doctor/sample/collect/auth/validate
  core.py          # 代理、robots、超时、限速、有界重试、原子写入
  exporters.py     # JSONL/CSV/XLSX/质量报告
  models.py        # 统一数据结构
  runner.py        # 站点调度、检查点与导出
  sites.py         # 四个 SSR 站点适配器
tests/              # 解析、去重、robots 和 429 失败场景测试
docs/               # 调研、站点评估和第三方方案复核
outputs/
  最终结果/          # 当前三站交付：任务/岗位、价格及必要说明
  完整数据/          # 三站完整原始结果，用于追溯和重新打包
crawler.py          # 底层 scout/fetch/cookies/网络侦察工具
run.py              # 业务爬虫入口
crawler.config.yaml # 唯一运行配置
```

## 安装

要求 Python 3.11 或更高版本。Windows PowerShell 一键安装：

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

脚本只在项目目录创建 `.venv`，使用标准 `venv + pip` 安装依赖，并自动执行 `doctor`。也可以手动安装：

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe run.py doctor
```

当前工作目录已有 `.venv-standalone` 时，也可以把下文命令中的 `.venv` 直接替换为 `.venv-standalone`。

默认 `crawler.config.yaml` 使用本机 Edge（`browser_channel: msedge`），无需下载额外浏览器。如果机器没有 Edge，可安装 Playwright Chromium，并把配置中的 `browser_channel` 设为空：

```powershell
powershell -ExecutionPolicy Bypass -File .\setup.ps1 -InstallChromium
```

## 运行

环境自检：

```powershell
.\.venv\Scripts\python.exe run.py doctor
```

四个公开 SSR 站点分别采集最多 50 条：

```powershell
.\.venv\Scripts\python.exe run.py sample --site epwk --limit 50
.\.venv\Scripts\python.exe run.py sample --site zbj --limit 50 --no-details
.\.venv\Scripts\python.exe run.py sample --site shixian --limit 50
.\.venv\Scripts\python.exe run.py sample --site proginn --limit 50
```

招聘三站先各跑 2 个职业、每类 2 条的真实小样本：

```powershell
.\.venv\Scripts\python.exe run.py jobs --site all --per-category 2 --max-categories 2
```

确认登录态和样本字段后，覆盖所有尚未完成的官方三级职业。不要传 `--max-categories`，此时才会进入 full probe 自动续跑模式：

```powershell
.\.venv\Scripts\python.exe run.py jobs --site all --per-category 1
```

目录探测结束后，再为三站建立独立的每类 50 条数字薪资 scope：

```powershell
.\.venv\Scripts\python.exe run.py jobs --site all --per-category 50 --numeric-salary-only
```

`--numeric-salary-only` 只保留薪资原文含非零数字的岗位，面议、空薪资和重复岗位不占 50 条配额。该模式与普通模式使用不同断点；达到 50、站点自然耗尽或有界页数用尽才停止单个职业。覆盖报告分别标记 `quota_met`、`exhausted_below_quota`、`empty`、`page_limit_below_quota` 和 `failed`，因此真实岗位不足 50 的类别会保留现有数据并如实说明，不会补造。

三站按职业代码或职业页串行采集，重复运行会从 `.crawler-state/recruitment/` 继续；`--max-categories` 只限制本批次，不缩小覆盖率分母，其单位分别是 BOSS 唯一三级职业码、前程无忧唯一搜索码和猎聘三级职业路径。省略该参数时，BOSS 和前程无忧会在持久冷却到期后自动继续，普通延后类别也会按失败预算轮转；登录失效和无冷却的异常退出不会快速重试。每次运行会在 `outputs/jobs-<timestamp>/` 保存三站统一 JSONL、CSV、XLSX、覆盖报告和运行报告。报告中的 `records` 是当前 scope 累计快照，`records_added_this_run` 才是本批新增岗位数；职业覆盖率和 50 条配额达标率是两个不同指标。

`sample` 硬限制每站不超过 50 条。带教确认数据口径后，才执行大于 50 条的采集：

```powershell
.\.venv\Scripts\python.exe run.py collect --site epwk --limit 500 --approved
```

如只保留含明确非零数字价格的记录，在 `sample` 或 `collect` 命令中加入 `--numeric-price-only`：

```powershell
.\.venv\Scripts\python.exe run.py sample --site zbj --limit 50 --no-details --numeric-price-only
.\.venv\Scripts\python.exe run.py collect --site epwk --limit 500 --approved --numeric-price-only
```

该参数检查一条记录已采集到的全部 `price_evidence[]`：只要任一 `raw_text` 含数字 `1` 到 `9`（正则 `[1-9]`）就保留。因此预算、服务报价和岗位工资都可以纳入筛选口径，但三者的业务含义并不相同；`0元`、`面议`、`待服务商报价`、`可议价` 等不含非零数字的记录会被排除。被排除的记录不计入 `--limit`，程序会在配置允许的最大页数内继续翻页补足；页数耗尽后仍不足目标数量时，结果状态为 `partial`。一品威客同时启用发布时间与数字价格过滤时，会先用列表价格保护有限的详情请求配额，因此列表为“面议/待报价”、数字仅出现在详情页的任务可能不纳入结果。不加该参数时保持原有采集口径和历史数据说明不变。

如需按岗位或任务发布时间过滤，`sample` 和 `collect` 支持互斥参数 `--recent-months N` 与 `--published-since YYYY-MM-DD`，并可用 `--as-of YYYY-MM-DD` 固定相对时间的参考日，避免跨午夜重跑导致截止日漂移。该功能目前仅支持一品威客、猪八戒和实现网。`sample` 只用于小样本检查；`collect` 加时间参数后会自动扫描完整时间窗口：

```powershell
.\.venv\Scripts\python.exe run.py auth --site epwk
.\.venv\Scripts\python.exe run.py sample --site epwk --limit 10 --recent-months 3
.\.venv\Scripts\python.exe run.py collect --site epwk --limit 10000 --approved --recent-months 3 --as-of 2026-07-21
.\.venv\Scripts\python.exe run.py collect --site zbj --limit 10000 --approved --no-details --recent-months 3
.\.venv\Scripts\python.exe run.py collect --site shixian --limit 10000 --approved --recent-months 3 --as-of 2026-07-21
```

一品威客请求使用 `curl_cffi` 的 Chrome impersonation transport，并复用 `.crawler-state/epwk-storage-state.json` 中的站点登录态。登录态失效或 WAF 拦截时，先运行 `auth --site epwk`，在打开的 Edge 中完成登录和页面验证后再执行采集。

详情静态 HTML 无法解析发布时间时，程序才启用有界 Playwright 回退；渲染次数、导航时间、路由请求数和响应大小均有限制。回退失败或达到上限时发布时间保持未知并记录 failure，严格三个月模式不会把该任务误纳入结果，也不会报告窗口完整。

相对月份以运行当天的 Asia/Shanghai 日期为 `as_of`，按自然月计算并将截止日包含在内；`as_of` 和实际 `cutoff` 会写入报告。完整窗口模式会按站点选择可验证的来源耗尽条件，保留截止日当天及之后的全部记录，不会因普通 `limit` 或采样页数提前结束。只有已验证为全局完整且跨页严格倒序的来源才允许用整页旧任务提前停止；过滤默认采用严格口径，发布时间缺失或只能确定模糊区间的记录会被排除，也不能作为窗口完整的证据。

实现网完整窗口会分别扫描全职 `daily_wage_mode` 和兼职 `project_mode` 到各自自然末页。使用 `--recent-months 3` 时，仅实现网页面上的精确标签“3个月前”按站点边界假设纳入；记录仍保留 `publication_time.status: uncertain`，并写入 `boundary_assumption: site_three_month_label`。“N年多前”和“接近N年前”按保守年度区间判定，不伪造精确日期；仍无法解析的原文最多汇总 20 种到运行说明，并使 `window_complete: false`。

默认启用详情抓取，完整窗口会在 `max_publication_detail_requests_per_site` 上限内抓取所有入选记录的详情，而不是只抓前 10 条。达到详情上限、详情链接缺失或详情请求失败都会降级为 `partial`，不能继续报告完整成功。

页数、记录数和详情请求仍有独立的高位安全上限；如果在证明各来源完成前触发上限、验证墙或网络错误，报告会写入 `window_complete: false` 并标记 `partial/failed`，不会声称三个月窗口完整。一品威客完整窗口必须允许详情抓取，因此仍不能与 `--no-details` 同用；主索引中的窗口候选必须逐条请求详情并核对，不能只凭 sitemap `lastmod` 生成记录。已有 `outputs/` 历史结果不会自动重新筛选。

一品威客的普通 `sample` 和非完整窗口列表采集仍使用时间排序参数：第一页为 `https://task.epwk.com/?o=7`，后续页为 `page{page}.html?o=7`。但 2026-07-22 的真实跨页检查发现，旧日期任务页之后仍可能再次出现近期任务，说明 `o=7` 不是跨页全局严格发布时间倒序，项目因此默认设置 `publication_sorted_desc: false`。启用 `publication_sitemap.primary_source: true` 的完整窗口不再扫描该列表，而是只处理配置的 sitemap inventory。若项目本地存在 `.crawler-state/epwk-storage-state.json`，请求前只导入 `epwk.com` 域内且未过期的 Cookie；加载失败会记录 `session_load` 并继续匿名请求，不会输出 Cookie 或 token。

主索引先完整加载并去重配置的 sitemap，再用详情精确核对 `validation_sample_size` 个唯一任务；默认必须完成 10 个 `lastmod == 详情发布日期` 的匹配才继续。验证通过后，只枚举 sitemap 中截止日当天及之后的 URL，但每条仍必须顺序抓取详情：标题优先取 `div.task_user_info h1`，再回退到面包屑末项或旧标题节点；价格取详情预算节点；发布时间以详情进度中的“发布需求”为首选，严格任务编号标题日期为回退。标题、价格或详情日期任一缺失，以及详情日期与 `lastmod` 不一致时，都记录 failure、跳过该任务并使 sitemap 来源不完整，绝不使用占位值或直接拿 `lastmod` 回填。验证样本会缓存复用，详情处理每 50 条写一次进度 checkpoint，并受 5000 次详情和 10000 条记录硬上限约束。

sitemap 的网络、XML、响应大小、条目上限、验证或详情失败会保留 failure；主索引模式不会再退回不可靠的列表扫描。实测 12 个 sitemap 分片之外仍存在至少两条近期任务，因此 sitemap 不是一品威客的全站任务全集：即使所有 sitemap 候选都成功处理，也只会写入 `source_coverage.sitemap.complete: true`，整体仍为 `window_complete: false`，并以 `sitemap_source_exhausted_not_global` 停止；失败或触发上限则使用对应的不完整停止原因。

猪八戒需求源使用站点实际的发布时间排序路径 `r4`，并按 `r4p{page}` 对每个需求分类持续翻页到截止日；若跳转到 `verify.zbj.com`，本次站点运行会停止并保留失败记录，不会自动绕过验证。

默认串行、单域并发 1；一品威客使用站点级 0.5 秒下载间隔，其他站点默认 2.5 秒并带抖动。对 429 遵守 `Retry-After`；429 没有 `Retry-After` 时立即停止该域本轮采集。

## 招聘三站登录与单站运行

BOSS 使用 `drv_crawler/boss_cdp` 的 MIT Chrome/CDP 后端。第一次运行先打开隔离 Chrome 并人工登录；之后统一入口会复用该 profile：

```powershell
.\.venv\Scripts\python.exe run.py auth --site boss --wait-seconds 300
.\.venv\Scripts\python.exe run.py jobs --site boss --per-category 2 --max-categories 2
```

前程无忧可匿名搜索，但当前会话也可能在搜索入口收到 HTTP 405，或在 API 收到 HTTP 200 的阿里云 WAF HTML；程序会把两者识别为全局限流，停止本批并将当前职业保留在待处理队列，不会误记为空类别。正式采集使用独立的可见 Edge 持久 profile，并在每次启动时合并最近一次已验证的运行时状态，以便首次初始化和启动中断后恢复；持久 profile 启动失败会显式停止，不会回退临时无头会话。冷却截止持久化在 SQLite，冷却期重跑不会启动浏览器。遇到登录墙或 WAF 时通过人工登录入口刷新状态；只有登录、原生搜索 JSON 和新 context 重放三项都通过才会覆盖旧状态。猎聘职业页通常匿名可读，出现验证页时采用同样的人工登录入口：

```powershell
.\.venv\Scripts\python.exe run.py auth --site job51 --wait-seconds 300
.\.venv\Scripts\python.exe run.py auth --site liepin --wait-seconds 300
.\.venv\Scripts\python.exe run.py jobs --site job51 --per-category 2 --max-categories 10
.\.venv\Scripts\python.exe run.py jobs --site liepin --per-category 2 --max-categories 10
```

Cookie、token、SQLite 断点和浏览器 profile 都位于被忽略的本地状态目录，不写入最终结果。单站参数、分类规模和故障恢复说明分别见 [`boss_cdp/README.md`](../drv_crawler/boss_cdp/README.md)、[`job51/README.md`](../drv_crawler/job51/README.md) 和 [`liepin/README.md`](../drv_crawler/liepin/README.md)。

同一站点的所有持久 scope 共享站点级互斥锁；即使 per1 与 per50 参数不同，第二个 `jobs` 进程也会立即报告 `RecruitmentScopeBusy`，避免共享 CDP、浏览器会话或风控额度。每个 scope 仍保留独立锁，防止断点并发写入。BOSS 分类默认每类间隔 12-20 秒，遇到限制首次冷却 3600 秒，单次子进程默认最多运行 21600 秒；前程无忧默认每类间隔 3-5 秒，每 20 类冷却 60 秒，WAF 软拦截冷却 1800 秒。Full probe 默认最多自动恢复 8 轮、总计 86400 秒；达到边界会以 `full_probe_cycle_limit` 或 `full_probe_runtime_limit` 停止并保留断点。可通过 `crawler.recruitment.auto_resume_full_probe`、`full_probe_max_cycles`、`full_probe_max_runtime_seconds` 调整；BOSS 单次子进程超时另由 `crawler.recruitment.sites.boss.run_timeout_seconds` 控制。

GitHub 候选、许可证和本机实测结论见 第三方爬虫方案（原 THIRD_PARTY_REVIEW.md 未收录）。

## 招聘详情补采与交付结果

前程无忧原始 API 已包含真实岗位描述；BOSS 和猎聘的列表页不含完整 JD。三站基础元数据统一放在 `outputs/招聘--完整数据`，详情 worker 与补采断点放在 `.crawler-state/recruitment-details`，不再混入交付目录。

猎聘详情页补采（重复运行自动跳过已完成记录）：

```powershell
py tools\backfill_liepin_details.py
```

BOSS 先启动并登录当前 CDP profile，再按最多 500 条一批重复运行；单个浏览器标签页复用，登录失效或出现验证时保存断点并停止：

```powershell
cd drv_crawler\boss_cdp
py scripts\boss_cdp_raw.py --input "..\..\outputs\招聘--完整数据\BOSS\岗位数据.jsonl" --detail --max-details 500 --detail-output "..\..\.crawler-state\recruitment-details\BOSS\详情补充.jsonl" --detail-checkpoint "..\..\.crawler-state-boss-detail-checkpoint.jsonl" --cdp-port 9224
cd ..\..
```

### 低风险详情续跑（推荐）

当前默认策略：**BOSS 与猎聘各一路、各一个账号、人工登录、不注入 Cookie**，优先降低安全验证触发率，而不是追求吞吐。

**BOSS**（单 worker + headed Chrome profile 登录）：

```powershell
py tools\run_boss_detail_workers.py prepare
# 打开 plan 中的 profile（默认 .crawler-state/boss-detail-parallel/profiles/worker-1），在浏览器内登录 BOSS
py tools\run_boss_detail_workers.py check
py tools\run_boss_detail_workers.py run --smoke-limit 1   # 先 1 条确认登录有效
py tools\run_boss_detail_workers.py run                   # 正式：默认 gap 8-15s，batch 20，batch 后冷却 900s
```

默认不注入 Cookie、不自动轮换账号。登录墙 / 验证 / 连续空 JD 会停机并写入约 1 小时冷却；冷却期内 `run` 会拒绝，除非加 `--ignore-cooldown`。确认风险已解除后也可用 `clear-cooldown`。

可选（提高验证风险，一般不要用）：`--inject-cookies`、`--enable-rotation`、`--workers N`。

**猎聘**（单 headed 持久 profile，默认不注入 Cookie）：

```powershell
# 首次：脚本会打开 Chromium 持久 profile，你在窗口内登录猎聘
py tools\run_liepin_browser_single.py --max-attempts 20
# 之后续跑自动跳过已成功 record；默认详情间隔 20-40s，每 10 条冷却 900s
py tools\run_liepin_browser_single.py --max-attempts 50
```

安全中心 / 连续空 JD / 连续错误会停机并写入冷却。可用 `--clear-cooldown` 或确认后 `--ignore-cooldown`。

多账号 `requests` 并行补采（`run_liepin_detail_workers` / `run_liepin_single_rotating`）易触发安全中心，默认拒绝执行；仅在你明确接受风险时加 `--i-accept-risk`。

历史多账号并发说明（不推荐）：独立 headed Chrome、CDP 9231-9233、record_id 分片仍可用，但需显式提高 workers 并自行承担风控。

不使用登录态时，只运行匿名公开 canary。它使用独立 Chrome profile，不读取账号文件、不注入 Cookie，只保存通过完整 JD 校验的记录；遇登录墙、安全验证或空页面立即停止，不会把截断正文写入结果，也不会污染登录态 worker 的失败预算：

```powershell
py tools\run_boss_public_details.py --limit 3
```

匿名 canary 连续成功后，才可逐批把 `--limit` 提高到最多 50。当前站点若返回登录墙或 `verify/security` 页面，匿名模式会如实返回 blocked，此时不能用空结果或部分正文冒充完整 JD。

如需生成历史四列兼容结果，可运行：

```powershell
py tools\build_recruitment_detail_results.py
```

兼容结果写入 `.crawler-state/recruitment-legacy/招聘详情处理结果`，补采断点位于 `.crawler-state/recruitment-details`，不会增加 `outputs` 中的交付目录。

前程无忧和猎聘的最终整理使用完整元数据合并与六字段清洗脚本。猎聘未取得 JD 的岗位会在完整元数据中保留空值，但不会进入清洗结果：

```powershell
.\.venv-standalone\Scripts\python.exe tools\build_recruitment_clean_dataset.py
```

三站完整元数据位于 `outputs/招聘--完整数据`；用于后续分析的“职位、JD、薪资、一级分类、二级分类、三级分类”位于 `outputs/招聘--最终结果`，其中不包含 BOSS。数据口径和代码入口分别见 [招聘数据说明](招聘数据说明.md) 与 [当前采集器说明](../README.md)（原代码说明未收录），风控与浏览器登录态问题见 [招聘失败复盘](招聘爬取失败原因复盘.md)。

## 输出格式

每次运行新建目录，不覆盖历史样本：

```text
outputs/sample-YYYYMMDD-HHMMSS-mmm/
  run-report.json
  run-report.md
  <site>/
    records.jsonl
    records.csv
    records.xlsx
    report.json
    report.md
  checkpoints/
```

核心字段：

```text
record_id
site_name
content_category
category_level_1_raw
category_level_2_raw
category_level_3_raw
category_path_raw
category_source
category_source_url
category_identity_verified
title_raw
detail_title_raw
price_evidence[]
price_raw_joined
status_raw
job_type_raw
budget_raw
work_period_raw
description_raw
list_url
detail_url
capture_method
captured_at
```

`price_evidence[]` 将每个价格原文与 `price_source` / `evidence_location` / `evidence_url` 绑定。派生解析不会覆盖 `raw_text`。

分类字段保存原站栏目证据：猪八戒来自需求列表栏目，一品威客来自任务详情三级面包屑，实现网来自岗位详情分类面包屑。标题仅用于详情身份核验，不参与分类推断。

本次三站完整原始结果保留在内部目录：

```text
outputs/外包--完整数据/
  猪八戒/     # 三个月任务原始记录与运行报告
  一品威客/   # 详情日期核验结果与未解决清单
  实现网/     # 严格记录、边界记录与来源运行
```

当前供审核和交付的结果位于：

```text
outputs/外包--最终结果/
  说明.md
  猪八戒/任务与价格.xlsx
  猪八戒/分类价格统计.xlsx
  一品威客/任务与价格.xlsx
  一品威客/分类价格统计.xlsx
  实现网/任务与价格.xlsx
  实现网/分类价格统计.xlsx
  实现网/边界说明.md
```

三个 Excel 的字段严格为“任务/岗位、分类一级、分类二级、分类三级、价格”。价格列为 Excel 数值：精确价取原值，区间取中点，上限价取上限，下限价取下限；无实际金额的待报价记录已排除。

三个 `分类价格统计.xlsx` 是独立统计文件，不覆盖明细。猪八戒和实现网按一级分类统计；一品威客按一级、二级、三级分别建立 Sheet，输出任务数量、平均价格和中位价格。

重新生成统计文件：

```powershell
.\.venv-standalone\Scripts\python.exe -B tools\analyze_category_prices.py --results-dir outputs\外包--最终结果
```

## 验证

```powershell
.\.venv\Scripts\python.exe -m compileall -q drv_crawler run.py tests
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m unittest discover -s drv_crawler\job51\tests -v
.\.venv\Scripts\python.exe -m unittest discover -s drv_crawler\liepin\tests -v
.\.venv\Scripts\python.exe -m unittest discover -s drv_crawler\boss_cdp\tests -v
.\.venv\Scripts\python.exe run.py validate outputs\sample-<timestamp>
```

测试包含解析器、原文保留、重跑去重、robots 拒绝、公开 API 显式策略、Range 请求、WARC 解压上限及“429 无 `Retry-After` 立即停止”失败场景。

## 当前交付结果

以 2026-07-21 为参考日，严格三个月截止日为 2026-04-21：

- 猪八戒：1668 条三个月内且有明确数字预算的需求任务；5414 条“待服务商报价”已排除。
- 一品威客：2691 条详情发布日期核验通过的任务；86 个无法核验发布时间的页面未纳入。
- 实现网：53 条岗位，其中 34 条严格三个月内，19 条为页面显示“3个月前”的边界记录。

交付口径、价格处理和边界说明见 [当前外包项目组说明](../../outsourcing/README.md)（原输出说明未收录）。

GitHub 候选仓库、许可证、时效性和未采用原因见 第三方爬虫方案（原 THIRD_PARTY_REVIEW.md 未收录）。

## 可选的底层侦察命令

`crawler.py` 同样是独立 Python 脚本，可用于新页面的低成本探测：

```powershell
.\.venv\Scripts\python.exe crawler.py --config crawler.config.yaml probe --pretty
.\.venv\Scripts\python.exe crawler.py --config crawler.config.yaml scout "https://target.example/list" --dynamic auto --pretty
.\.venv\Scripts\python.exe crawler.py --config crawler.config.yaml fetch "https://target.example/list" --mode static --pretty
```
