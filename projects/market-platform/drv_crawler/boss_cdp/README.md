# BOSS直聘爬虫 · 职位抓取工具 v2.2（Chrome CDP / 明文薪资）

> 🌐 English documentation: [README.en.md](./README.en.md)

![Python](https://img.shields.io/badge/python-3.10+-blue.svg)
![License](https://img.shields.io/badge/license-MIT-green.svg)
![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey.svg)
![Version](https://img.shields.io/badge/version-2.2.1-orange.svg)

一个轻量的 **BOSS直聘爬虫（spider / crawler / scraper）**：通过 Chrome DevTools Protocol 连接本地已登录的 Chrome，复用真实登录态调用 zhipin.com 搜索 API，绕过前端字体反爬，输出含**明文薪资**的职位数据（JSON / CSV），并生成薪资分布、技能词频和求职材料优化提示词。同时作为 Hermes Agent Skill 提供。

> 📌 **一句话介绍**：不用 Selenium/Playwright，直接通过 Chrome DevTools Protocol 连接本地已登录的 Chrome，复用真实登录态调搜索 API，输出含明文薪资的 JSON/CSV，并生成薪资分布、技能词频和求职材料优化提示词。

*原上游封面图未包含在研究发布副本中。*

---

## ⚠️ 免责声明

本项目仅供学习和技术研究参考，旨在探讨 Chrome DevTools Protocol、前端反爬机制与数据采集技术。请勿用于任何违反 [BOSS直聘用户协议](https://www.zhipin.com/about/protocol.html) 或相关法律法规的用途，不得用于商业转售、恶意爬取或对目标网站造成负担的行为。使用本项目所产生的一切后果由使用者自行承担，作者不对任何滥用行为负责。

---

## 🚀 30 秒快速开始

```bash
# 1. 克隆 + 装依赖
git clone https://github.com/eatmoreduck/boss-zhipin-scraper.git
cd boss-zhipin-scraper
pip install -r requirements.txt          # 或 uv sync

# 2. 启动隔离 Chrome 并登录（只需一次，登录态持久保存）
python3 scripts/boss_cdp_raw.py --setup-chrome

# 3. 抓取 + 分析
python3 scripts/boss_cdp_raw.py --keyword "AI Agent" --city 上海 --pages 3 --analysis

# 支持全国城市（含三四五线），例如：
python3 scripts/boss_cdp_raw.py --keyword "前端" --city 赣州 --pages 3
# 查看支持的城市：--list-cities [关键词]
python3 scripts/boss_cdp_raw.py --list-cities 江

# 4. 抓取后生成聚合摘要 + 提示词（默认读取最新结果）
python3 scripts/job_summary.py
```

抓完直接拿到：薪资分布、经验要求、高频技能词、求职材料优化提示词。提示词只基于岗位数据，不读取本地简历文件，也不给岗位算个人匹配分。

## ✨ 特性

- 明文薪资（API 模式，绕过字体反爬）
- JSON / CSV 双格式输出
- 详情页 JD 抓取 + 技能分析
- 抓取后聚合摘要 + 可复制提示词
- 增量写入（异常退出不丢数据）
- 一键环境检查 + 持久隔离 Chrome CDP profile
- 多维筛选（规模、融资、薪资、经验、学历、行业）
- macOS + Linux 支持（Windows 代码分支已预留，未经实测，不保证可用）

<details>
<summary>🔍 为什么不选 Selenium / Playwright 类爬虫？</summary>

- Selenium/Playwright 会启动完整的受控浏览器，体积大、指纹明显，容易触发 BOSS 的风控和验证码。
- 本工具直接连接你已经登录的真实 Chrome（CDP），复用真实指纹和登录态，调用的也是页面内合法的搜索 API，返回的 `salaryDesc` 本就是明文——不需要解析被字体反爬加密的 DOM 薪资。
- 因此比传统 DOM 抓取类爬虫更稳定，也更难被识别为自动化流量。

</details>

## 安装

### 方式 1：克隆到本地再安装（推荐）

由于 `hermes skills install` 的网络请求在某些环境下可能无法直接访问 GitHub，推荐先克隆仓库再本地安装：

```bash
# 1. 克隆仓库
git clone https://github.com/eatmoreduck/boss-zhipin-scraper.git
cd boss-zhipin-scraper

# 2. 复制到 Hermes skills 目录
mkdir -p ~/.hermes/skills/data-science/boss-zhipin-scraper/scripts
cp SKILL.md ~/.hermes/skills/data-science/boss-zhipin-scraper/
cp scripts/boss_cdp_raw.py ~/.hermes/skills/data-science/boss-zhipin-scraper/scripts/
cp scripts/job_summary.py ~/.hermes/skills/data-science/boss-zhipin-scraper/scripts/
```

### 方式 2：curl 一键安装

不需要克隆整个仓库，直接下载必要文件：

```bash
mkdir -p ~/.hermes/skills/data-science/boss-zhipin-scraper/scripts && \
curl -sL https://raw.githubusercontent.com/eatmoreduck/boss-zhipin-scraper/master/SKILL.md \
  -o ~/.hermes/skills/data-science/boss-zhipin-scraper/SKILL.md && \
curl -sL https://raw.githubusercontent.com/eatmoreduck/boss-zhipin-scraper/master/scripts/boss_cdp_raw.py \
  -o ~/.hermes/skills/data-science/boss-zhipin-scraper/scripts/boss_cdp_raw.py && \
curl -sL https://raw.githubusercontent.com/eatmoreduck/boss-zhipin-scraper/master/scripts/job_summary.py \
  -o ~/.hermes/skills/data-science/boss-zhipin-scraper/scripts/job_summary.py
```

### 方式 3：hermes skills install（需网络直连 GitHub）

```bash
hermes skills install https://raw.githubusercontent.com/eatmoreduck/boss-zhipin-scraper/master/SKILL.md --category data-science
```

> 注意：此方式依赖 hermes 进程能直接访问 GitHub，如果遇到超时或连接失败，请使用方式 1 或 2。

### 验证安装

```bash
# 检查文件是否存在
ls ~/.hermes/skills/data-science/boss-zhipin-scraper/SKILL.md
ls ~/.hermes/skills/data-science/boss-zhipin-scraper/scripts/boss_cdp_raw.py
ls ~/.hermes/skills/data-science/boss-zhipin-scraper/scripts/job_summary.py
```

安装后直接在 Hermes 对话中说"帮我搜一下 BOSS直聘 上上海的 AI Agent 岗位"。

## 作为命令行工具使用

不想装成 Skill 也可以直接当 CLI 用：

```bash
# 1. 克隆 + 安装依赖
git clone https://github.com/eatmoreduck/boss-zhipin-scraper.git
cd boss-zhipin-scraper
pip install -r requirements.txt

# 2. 启动 Chrome CDP
python3 scripts/boss_cdp_raw.py --setup-chrome
# 首次使用也不会复制主 Chrome 登录态；请在弹出的 BOSS 专用浏览器中登录 zhipin.com
# setup 会等待登录完成，并确认接口能返回明文薪资

# 3. 检查环境
python3 scripts/boss_cdp_raw.py --check

# 可选：真实浏览器/API smoke test（不写结果文件）
python3 scripts/boss_cdp_raw.py --smoke-test

# 4. 抓取
python3 scripts/boss_cdp_raw.py --keyword "AI Agent" --city 上海 --pages 3 --format csv --analysis

# 只保留薪资原文含明确非零数字的职位
python3 scripts/boss_cdp_raw.py --keyword "AI Agent" --city 上海 --pages 3 --numeric-price-only --no-detail

# 对已有 JSONL 列表补采真实“职位描述”；输出和断点均可重复续跑
python3 scripts/boss_cdp_raw.py --input jobs.jsonl --detail --max-details 500 \
  --detail-output details.jsonl --detail-checkpoint details_checkpoint.jsonl

# 覆盖 BOSS 官方全部三级职业，每类最多 5 条；中断后重复同一命令自动续跑
python3 scripts/boss_cdp_raw.py --all-positions --city 全国 --pages 3 \
  --per-position-limit 5 --numeric-price-only --output boss_jobs.json

# 有界 smoke：本次只跑待采队列前 3 类，重复运行继续后续类别
python3 scripts/boss_cdp_raw.py --all-positions --max-positions 3 --pages 1 \
  --per-position-limit 1 --output boss_smoke.json

# 每个唯一三级职业最多 50 条；达到目标前可继续翻页，硬上限仍为 10 页
python3 scripts/boss_cdp_raw.py --all-positions --pages 1 \
  --per-position-limit 50 --output boss_position_jobs.json

# 5. 抓取后摘要和提示词
python3 scripts/job_summary.py --top 15
```

## 参数

| 参数 | 说明 |
|------|------|
| `--keyword` | 搜索关键词（默认 "AI Agent"） |
| `--city` | 城市（中文或代码，默认上海）。**支持全国城市**（一二三四五线全覆盖，共 300+ 个），运行时自动从 BOSS 同步最新城市码；码表见 [`data/city_codes.json`](data/city_codes.json)，或用 `--list-cities` 查看 |
| `--list-cities [关键词]` | 打印支持的城市列表，可选关键词过滤，如 `--list-cities 江` |
| `--list-positions [关键词]` | 打印 BOSS 官方三级职业代码，可按代码或分类路径过滤 |
| `--pages` | 普通模式为抓取页数；职业采样中单页目标沿用此预算，多页目标（如每类 50 条）可继续到 quota、`hasMore=false` 或 10 页硬上限 |
| `--format` | json / csv；csv 会同时导出列表和详情 CSV |
| `--detail` | 抓取详情页真实“职位描述”（默认开启） |
| `--no-detail` | 不抓取详情页 |
| `--max-details` | 本次最多补采的未完成详情数；默认受单次 500 请求上限约束 |
| `--detail-output` | 详情输出；大批量建议 `.jsonl`，成功一条即追加并 `fsync` |
| `--detail-checkpoint` | 详情成功、失败、登录失效或验证页状态 JSONL；默认由详情输出名派生 |
| `--detail-load-timeout` | 单个详情渲染的有界等待秒数（默认 15） |
| `--detail-retries` | 单个详情失败后的重试次数（默认 1） |
| `--analysis` | 分析报告 |
| `--merge FILE` | 合并已有数据（按 job_id 去重） |
| `--all-positions` | 依次采样官方全部三级职业代码；同一代码的重复分类路径只请求一次 |
| `--position CODE` | 只采样指定三级职业代码，可重复传入 |
| `--per-position-limit` | 每个职业最多保留的职位数（默认 5，最大 100） |
| `--max-positions` | 只限制本次待跑职业数，用于 smoke/分批；不改变覆盖分母，默认不限制 |
| `--category-interval-seconds` | 职业分类之间固定等待秒数（默认 12） |
| `--category-jitter-seconds` | 固定等待之外的随机抖动上限（默认 8，即每类间隔 12-20 秒） |
| `--block-cooldown-seconds` | 遇到站点限制后的首次冷却秒数（默认 3600；重复限制指数退避，最多 14400；`Retry-After` 优先） |
| `--checkpoint` | 职业采样断点路径；默认由 `--output` 派生 |
| `--coverage-output` | 职业覆盖报告路径；默认由 `--output` 派生 |
| `--numeric-price-only` | 只保留薪资原文含 `[1-9]` 的职位；多页职业目标会在 10 页硬上限内继续寻找达标记录 |
| `--allow-dom-fallback` | API 无数据时允许降级 DOM 提取；默认关闭，薪资可能不可信 |
| `--check` | 环境检查（CDP + 依赖 + 登录态） |
| `--smoke-test` | 用真实 Chrome/CDP 跑一次 BOSS 搜索 API smoke test，不写结果文件 |
| `--setup-chrome` | 一键启动 Chrome CDP（持久隔离 profile） |
| `--copy-login-state` | 手动导入主 Chrome 的 Local State + Cookie 相关文件到隔离 profile（默认、首次启动、重复启动都不复制） |
| `--reset-chrome-profile` | 重建 BOSS 专用 Chrome profile，会清除此专用浏览器内的登录态 |
| `--no-wait-login` | `--setup-chrome` 启动后不等待登录完成 |
| `--login-timeout` | `--setup-chrome` 等待登录完成的秒数（默认 300） |
| `--stop-chrome` | 关闭 BOSS 专用 CDP Chrome（按隔离 profile 精准匹配，不碰主 Chrome） |
| `--close-chrome` | 抓取正常结束后自动关闭专用 Chrome（默认不关；异常退出不触发，保留登录态） |
| `--output` | 列表输出路径；普通模式默认时间戳文件，职业采样默认稳定路径 `~/.boss-zhipin-scraper/job-result/boss_position_jobs.json`，便于断点续跑 |
| `--cdp-port` | CDP 端口（默认 9222） |
| `--scale/--salary/--experience/--degree` | 筛选条件 |

## 抓取后摘要与提示词

`scripts/job_summary.py` 只读取已抓取的 `boss_jobs_*.json` 和 `boss_details_*.json`，做简单聚合分析并生成一段可复制提示词。它不读取本地简历文件，不引入 PDF 依赖，也不给个人与岗位做分数判断。

```bash
# 读取默认结果目录下最新的 boss_jobs_*.json，并自动匹配同时间戳或最新详情文件
python3 scripts/job_summary.py

# 指定列表和详情文件
python3 scripts/job_summary.py \
  --input ~/.boss-zhipin-scraper/job-result/boss_jobs_20260625_1200.json \
  --details ~/.boss-zhipin-scraper/job-result/boss_details_20260625_1200.json \
  --top 15

# 只输出提示词
python3 scripts/job_summary.py --prompt-only
```

打包安装后也可以使用入口命令：

```bash
uv run boss-summary --top 15
```

摘要会覆盖这些维度：薪资区间、经验要求、学历要求、地区分布、高频公司、技能标签、JD 高频词。提示词会要求模型基于这些统计去做简历关键词补齐、项目经历改写方向和面试准备清单，但明确要求不要虚构经历。

## 文件结构

```
boss-zhipin-scraper/
├── SKILL.md              # Hermes Skill 定义
├── README.md
├── CHANGELOG.md
├── LICENSE
├── pyproject.toml
├── scripts/
│   ├── boss_cdp_raw.py   # 抓取主脚本
│   └── job_summary.py    # 抓取后摘要 + 提示词
└── requirements.txt
```

## 工作原理

这是一个基于 Chrome CDP 的 BOSS直聘爬虫，核心流程：

1. 通过 Chrome DevTools Protocol (CDP) 连接到已打开的 Chrome
2. 普通关键词模式在页面内调用搜索 API；职业采样与正式登录预检则导航到真实搜索页，通过 CDP Network 读取页面自身发出的职位响应，不重复注入首屏 XHR
3. API 返回明文 `salaryDesc`，绕过前端字体反爬
4. 列表 API 保留 `securityId` / `lid` 等上下文，进入详情页时带上这些参数
5. 每页抓完立即写入文件，按 `job_id` 去重

职业采样模式先从 BOSS 官方 `getCityShowPosition` 接口读取三级职业树，再把职业代码作为搜索页的 `position` 参数逐类导航。每一页的职位数据来自该页面自己的 `joblist.json` 响应，页面 URL、Referer、Cookie 和站内脚本上下文保持一致，不再固定停留在首个职业页面后连续注入跨职业 XHR。官方目录中同一三级职业代码可能出现在多个一级/二级路径下；脚本只请求一次该代码，同时把主路径写入 `category_level_1_raw`、`category_level_2_raw`、`category_level_3_raw`、`category_path_raw`，把全部官方路径保存在 `category_paths_raw` 和 `category_matches`，不会拿职位标题猜分类。每条职业采样记录还直接输出统一工程字段 `site_name`、`content_category`、`title_raw`、`price_evidence`、`list_url`、`detail_url`、`record_id`，并保留原 `title`、`salary`、`job_link` 兼容字段。

每完成一个职业，脚本先原子写入职位数据，再更新断点和覆盖报告。相同命令重复运行会跳过已完成职业；若两次参数不一致则拒绝复用旧断点，避免混入不同口径。`--max-positions N` 仅截取本次尚未完成队列的前 N 类，不进入任务口径，因此可以用同一输出连续分批且覆盖率分母始终是完整目录。默认每类之间等待 12-20 秒；遇到 `code: 31/32/37`、HTTP 403/429 等站点限制时会立即停止且不重试，并把限制时间、尝试次数和冷却截止写入断点。若限制发生在登录探测、尚未取得职业目录时，脚本只生成 `awaiting_catalog` 引导断点，不生成空目录、空输出或覆盖报告；冷却后登录探测恢复，正式采样会加载官方目录并继续。已有完整断点则只原子更新限制字段，保留全部进度。冷却期内重跑不会打开 CDP 或发送请求；独立 `--check` 仅报告状态，永远不写采集断点。服务端返回 `Retry-After` 时优先遵从，否则首次冷却 1 小时，重复限制指数退避至最多 4 小时。限制类别或其他非终态错误会进入 `deferred`，续跑时排在所有未尝试类别之后；普通分类错误跨运行累计 3 次后才转为终态 `failed`，并从后续请求队列移除，不会阻塞其他职业。旧版 schema 1 断点会自动补齐该状态，无需重建进度。默认派生 `*_checkpoint.json` 和 `*_coverage.json`，覆盖报告分别统计互不重叠的 `completed`、`pending`、`deferred`、`failed` 代码和路径，并保留一级分类汇总及每个职业的采样数量。职业采样默认不抓详情，显式加 `--detail` 才会进入详情页。

每类 50 条沿用页面原生分页；若数字薪资过滤或跨页重复导致不足，会继续低频导航，直到 quota、`hasMore=false` 或 10 页硬上限。覆盖报告按 `records_fetched` 和 `stop_reason` 汇总 `quota_met`、`exhausted_below_quota`、`empty`、`page_limit_below_quota`、`failed`。因此“职业处理完成”不等于“50 条达标”，站点耗尽和页预算用完也不会互相混淆。

默认不会使用 DOM 提取列表，因为 DOM 薪资可能受字体反爬影响。只有明确传 `--allow-dom-fallback` 时，API 无数据才会降级 DOM。

传入 `--numeric-price-only` 后，脚本只保留薪资原文至少包含一个非零数字 `[1-9]` 的记录，例如 `20-40K`、`30万-50万元`；`面议`、`待服务商报价`、`可议价`、`0元` 会被过滤。过滤在逐页抓取、`--input` 加载和 `--merge` 合并后都会执行，并在 JSON 的 `filtered_out_count` 和终端汇总中报告数量。过滤项不会触发额外翻页，因此最终数量可能少于页面原始职位数。

详情页只从包含“职位描述”的详情区提取 JD，整页 `body` 仅用于识别登录墙和导航页，不会直接写入结果。若页面出现“登录查看完整内容”，抓取会明确报错并停止，避免把截断正文、招聘者信息、公司介绍和推荐职位当成完整 JD 保存。对已有大批量列表时使用 `--input ...jsonl --detail --detail-output ...jsonl`：脚本复用一个 CDP 详情标签页，按职位身份跳过已成功记录，并在每条成功结果后追加并同步 JSONL；登录失效、验证页和解析失败写入独立断点日志。

`--input ... --analysis --no-detail` 会优先加载 `--detail-output`，其次加载与输入列表同目录、同时间戳的 `boss_details_*.json`，最后查找 `~/.boss-zhipin-scraper/job-result` 下最新详情文件。

## Chrome profile 安全策略

`--setup-chrome` 默认使用持久隔离 profile，不软链接、不复制你的主 Chrome 数据。首次启动和后续重复启动都只是创建或复用这个专用 profile：

- `~/.boss-zhipin-scraper/chrome-profile`

未显式指定 `--output` 或 `--detail-output` 时，抓取结果默认保存到：

- `~/.boss-zhipin-scraper/job-result`

首次使用需要在这个专用 Chrome 中手动登录 BOSS直聘。`--setup-chrome` 会等待登录完成，并用搜索接口确认能拿到明文 `salaryDesc` 后再返回。登录态保存在专用 profile 内，重启机器后仍然保留；重复运行 `--setup-chrome` 不会清空它，也不会影响主 Chrome、Gmail、GitHub 等账号。

登录探测每轮只发送一个搜索请求，并在不同关键词/城市之间轮换，等待间隔会从 3 秒逐步退避到最多 15 秒；这些请求同样计入单次 500 次的全局请求预算。未登录、探测样本为空、接口限制和响应异常会分别提示。遇到已确认的限制状态（例如 `code: 31/32/37`），或未知状态码但提示文字包含账户/环境异常、暂时禁止使用、访问频繁、安全校验、滑块验证等风控特征时，会立即停止探测，不会继续提示重复登录或密集重试。

`code: 32` 且提示“账户存在异常”或“暂时被禁止使用”属于账号侧限制，修改请求参数、重置 profile 或密集重试不能解除。此时保留现有专用 profile，停止自动请求；需要人工查看时可用 `--setup-chrome --no-wait-login` 只打开浏览器，不发送登录探测。按页面提示完成验证，或通过 [BOSS 直聘帮助反馈](https://www.zhipin.com/web/common/protocol/use-help.html) / [官方联系渠道](https://www.zhipin.com/aboutContact) 申诉。网页版恢复正常后先运行一次 `--check`，确认能返回明文薪资，再从原断点续跑。

`--setup-chrome` 的交互式登录页会主动置前。正式登录预检和职业采样也会各激活一个专用搜索标签页，使页面的 visibility 与 `document.hasFocus()` 保持正常浏览器语义；整个职业批次复用同一个标签页，不会逐类新开或反复抢焦点。普通列表和详情临时页仍在后台运行。

如确实需要从主 Chrome 手动导入 BOSS 登录态，可以显式运行：

```bash
python3 scripts/boss_cdp_raw.py --setup-chrome --copy-login-state
```

`--copy-login-state` 每次运行都会覆盖隔离 profile 内对应的 Cookie 相关文件；日常启动不要加这个参数。它只复制 `Local State` 和 `Default/Cookies*`、`Default/Network/Cookies*` 这类 Cookie 数据库相关文件，不复制密码库、历史记录、扩展或完整 profile。需要清空专用浏览器登录态时使用：

```bash
python3 scripts/boss_cdp_raw.py --setup-chrome --reset-chrome-profile
```

### 用完如何收尾

抓取/分析结束后，专用 Chrome 不会自动关闭（默认保留登录态，方便你接着跑下一条抓取）。确认不再使用时，可以手动收尾：

```bash
python3 scripts/boss_cdp_raw.py --stop-chrome
```

`--stop-chrome` 只关闭 scraper 隔离 profile（`--user-data-dir`）对应的 Chrome 进程，**绝不**按端口或进程名去 kill，因此不会误伤你正在用的主 Chrome、Gmail、GitHub 等账号。

如果你希望某次抓取正常结束后就顺手关掉 Chrome，可以加 `--close-chrome`：

```bash
python3 scripts/boss_cdp_raw.py --keyword "AI Agent" --city 上海 --pages 3 --close-chrome
```

`--close-chrome` 默认不开启；且只在抓取走完的**成功路径**上触发，登录失败、异常退出等情况不会关闭 Chrome，登录态得以保留。

## 📌 TODO

- [ ] 详情页抓取补强 Referer 与请求指纹，进一步降低风控触发概率

## License

MIT

## 友情链接

- [LINUX DO](https://linux.do/) — 真诚、友善、充满活力的技术社区，本项目认可并推荐。

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=eatmoreduck/boss-zhipin-scraper&type=Date)](https://star-history.com/#eatmoreduck/boss-zhipin-scraper&Date)
