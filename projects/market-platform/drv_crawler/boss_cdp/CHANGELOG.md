# Changelog

## 未发布

### 新增
- 新增 BOSS 官方三级职业分类采样：`--all-positions` 覆盖全部唯一职业代码，`--position` 可指定代码，`--per-position-limit` 控制每类样本量，`--max-positions` 支持不改变覆盖分母的分批 smoke；输出网站原始三级分类、全部别名路径、原子断点与覆盖报告，同一职业代码跨路径只请求一次，职业采样默认关闭详情页。
- 新增 `--numeric-price-only`：仅保留薪资原文含非零数字 `[1-9]` 的职位，统一覆盖逐页抓取、`--input`、`--merge` 和最终输出，报告 `filtered_out_count`，且不会为补足数量扩大 `--pages`。
- 详情补采支持已有 JSON/JSONL 列表输入：复用单个 CDP 详情标签页，按 `job_id` 等稳定身份续跑；JSONL 成功记录和独立状态断点逐条 `fsync`，有界等待、重试并明确记录登录失效、验证页和解析失败。

### 修复
- 职业采样和正式登录预检改为逐页导航官方搜索页，并通过有界 CDP Network 事件队列读取页面自身的 `joblist.json` 响应；不再停留在首个职业页后注入跨职业 XHR，也不再在登录预检中先发一条上下文不匹配的搜索请求。职业批次复用一个前台标签页，保持 URL、Referer、Cookie、visibility 与 `document.hasFocus()` 一致；断点 schema 和输出字段不变。
- `--per-position-limit 50` 现在会在去重和数字薪资过滤后继续低频翻页，直到 quota、`hasMore=false` 或 10 页硬上限；跨页优先按加密职位 ID 去重。覆盖报告新增 quota 汇总，区分达标、站点耗尽但不足、空类别、页预算不足和失败，避免把“职业处理完成”误写成“50 条达标”；现有 per-1 断点 scope 不变。
- 职业采样的登录探测若先触发站点限制，现在会把全局冷却原子写入已有断点；首次运行则写入不含空目录的 `awaiting_catalog` 引导断点，冷却后可继续加载官方目录。职业采样默认输出改为稳定文件名以支持首次断点恢复；独立 `--check` 仍不写采集状态。
- 职业采样断点将站点限制和未耗尽预算的分类错误记为 `deferred`，续跑时排在未尝试职业之后；普通分类错误连续 3 次后才转为终态 `failed` 且不阻塞其余职业。覆盖报告的 pending/deferred/failed 统计改为互斥，并兼容已有 schema 1 断点。
- 职业分类采样默认改为每类间隔 12-20 秒；遇到 `code: 37` 或 HTTP 403/429 时立即停止，原子记录冷却期限，冷却期内续跑不再发请求，并优先遵从 `Retry-After`。
- 登录/职业采样限制识别补充 `code: 37`；未知状态码会按访问频繁、安全校验、滑块验证等提示文字识别为限制状态，避免误记为登录失败或自然空类别（同步上游 `d150a2c`）。
- 新增 `--stop-chrome` 命令：抓取/分析完成后关闭 BOSS 专用 CDP Chrome（按 user-data-dir 精准匹配隔离 profile，不碰主 Chrome）；抓取命令新增 `--close-chrome` 选项，正常结束后自动收尾（默认关闭，异常退出不触发以保留登录态）。复用已有 `stop_cdp_chrome` 的安全匹配逻辑，补齐进程关闭/收尾链路的单元测试。（#26）
- 城市码表外置为 `data/city_codes.json`（全量 300+ 城市，覆盖一二三四五线），新增 `--list-cities [关键词]` 命令查看支持的城市；`resolve_city` 查询链改为「本地静态码表 → 运行时拉 BOSS 接口 → 原样兜底」。城市码表打进 wheel，`pip install` 用户也可用。（#24）

### 修复
- 登录探测改为区分可用、未登录、限制、空结果和响应异常；每轮仅请求一次并采用有上限的退避等待，`code: 31` 等明确限制会立即停止。探测请求现已纳入全局请求预算，CLI 不再把风控或异常统一提示为未登录。（#31）
- 登录检查、列表/详情抓取和 smoke test 的临时标签页统一在后台创建，仅人工登录页置前，避免自动流程抢占前台焦点（#28）
- 详情页 JD 改为只提取“职位描述”区，并在登录墙、导航页或过短正文出现时拒绝写入，不再把整页 `body`、招聘者信息、公司介绍和推荐职位当作 JD
- 同步 BOSS 当前 `city.json` / `condition.json` 映射，修正城市码以及薪资、经验、学历筛选枚举漂移，并在内置城市表未命中时自动加载 BOSS `cityGroup.json` 支持更多城市中文名
- `scrape_details` 最终保存改用 `os.path.dirname(path) or "."`，`--detail-output` 传不带目录的裸文件名时不再抛 `FileNotFoundError`（与循环内及其它写文件处保持一致）
- 修正城市码：天津 `101030100`、沈阳 `101070100`（原均误用 `101060100`）
- `require_runtime_dependencies` 缺失依赖时同时提示 uv 和 pip 安装方式
- `--merge` 现在会合并旧详情并落盘到 `--detail-output`（之前只合并列表，详情丢失）
- API URL filter 改用 `urlencode`（原字符串拼接，filter 值含特殊字符会出错）

### 变更
- 平台支持声明改为 macOS + Linux（Windows 代码分支保留但未经实测，不再声称支持，避免过度承诺）
- `pyproject.toml` 删除空的 `[csv]` extra（csv 是标准库）
- SKILL.md 脚本路径解析改用 Python `os.path.realpath`（macOS 自带 `readlink` 无 `-f`）

### 新增
- `scripts/job_summary.py` 抓取后摘要脚本：读取已有 JSON，输出岗位聚合摘要和求职材料优化提示词
- `boss-summary` 命令行入口，便于打包安装后直接运行摘要脚本
- 抓取后摘要测试：覆盖 JSON 加载、聚合维度、提示词输出和项目边界
- 版本号一致性测试：校验脚本、pyproject.toml、SKILL.md、README.md 四处版本同步
- CONTRIBUTING.md 贡献指南

## v2.0.0 (2026-06)

### 新功能
- `--check` 环境检查（CDP 连通性、依赖、登录态）
- `--setup-chrome` 一键启动 Chrome CDP（持久隔离 profile）
- `--copy-login-state` 手动导入主 Chrome 的 Local State + Cookie 相关文件到隔离 profile
- `--reset-chrome-profile` 重建 BOSS 专用 Chrome profile
- `--setup-chrome` 默认等待 BOSS 登录完成，并确认接口返回明文薪资
- `--no-wait-login` / `--login-timeout` 控制 setup 登录等待
- 默认抓取结果保存到 `~/.boss-zhipin-scraper/job-result`
- 未传 `--city` 时默认搜索上海
- `--format csv` 同时导出列表 CSV 和详情 CSV
- `--merge` 合并多次抓取结果（去重）
- `--cdp-port` 自定义 CDP 端口（默认 9222）
- `--smoke-test` 用真实 Chrome/CDP 跑一次搜索 API smoke test，不写结果文件
- `--allow-dom-fallback` 显式允许 API 失败时降级 DOM 提取
- `--version` 查看版本号
- 登录态检测：未登录时给出明确提示
- 分析报告技术词动态提取（不再硬编码）
- 进度显示：`[2/3 页, 45/90 条]`

### 改进
- CDP WebSocket 消息过滤 + 超时重试（不再无限卡死）
- 详情页写入去重（中断重跑不重复）
- 请求频率保护（最多 10 页，全局 500 次上限）
- 清除所有 bare except，改为具体异常类型
- API 路径提取为常量，方便维护
- DOM fallback 标记为 deprecated
- DOM fallback 默认关闭，避免把字体反爬后的薪资写进结果
- API 错误行不再被当成职位数据处理
- 详情输出保留 `job_id`、`job_link` 和 `salary_source`
- 详情页访问会带上列表 API 返回的 `securityId` / `lid` 上下文
- `--input ... --analysis --no-detail` 会从 `--detail-output`、同目录同时间戳详情文件、默认结果目录最新详情文件中加载详情
- 登录态检测改为多关键词、多城市 probe，但仍要求接口返回明文薪资
- Linux / Windows 平台支持（Chrome 路径 + 隔离 profile）
- pyproject.toml 版本锁定依赖

### 安全
- 默认不软链接、不复制主 Chrome profile；首次启动也不自动导入主 Chrome 登录态，避免影响 Gmail/GitHub 等主浏览器登录态
- API URL 可配置（`API_JOB_LIST_PATH` 常量）

## v1.0.0 (2026-06)

### 初始版本
- Chrome CDP 抓取 BOSS直聘职位列表
- API 明文薪资（绕过字体反爬）
- 详情页 JD 抓取 + 技能标签提取
- 增量写入（异常退出不丢数据）
- 分析报告（薪资分布、经验要求、简历建议）
- 多维筛选（规模、融资、薪资、经验、学历、行业）
