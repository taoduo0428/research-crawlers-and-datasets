# 课题组爬虫源码与数据总仓库

> **先看这里，再进入具体项目 README。** 本仓库整理了 12 个研究目标，代码、研究数据和历史试验材料按用途分开放置；各网站的详细安装、运行、字段与数据局限写在对应子项目 README 中。
>
> **当前 GitHub 状态：Public（2026-10-17 核实）。** 仓库虽已公开，但过去的 Git 提交仍包含修复前的 Keep 数据，B站字幕、豆瓣影评等第三方内容的公开再分发授权也未确认。最新版本进行了部分脱敏，但**尚未完成旧历史清理**。公开不代表所有数据均已获得再许可权。参见 [全仓库审计报告](docs/REPOSITORY_AUDIT_2026-10-17.md)。

## 一、仓库的研究范围与组织原则

| 研究类别 | 对应网站 / 项目 | 组织方式 |
| --- | --- | --- |
| 外包（4 个网站） | 一品威客、猪八戒、实现网、程序员客栈 | **统一采集引擎** + 四个网站各自的数据及说明 |
| 招聘（3 个网站） | BOSS直聘、前程无忧（51job）、猎聘 | 与外包共用一套引擎，保留各网站独立代码模块、清洗表与源数据层 |
| 社交 / 视频（4 个项目） | B站、Facebook、Keep、豆瓣 | 各自独立；Keep 明确区分第一阶段抓包和第二阶段整理 |
| 电商（1 个整合项目） | Amazon + Jiimore（含 SellerSprite 数据） | **一个联合课题**，以 ASIN 关联不同来源及其时间序列 |

这里的「12 个目标」按网站/整合研究项目计数，**`projects/market-platform/` 是外包和招聘共享的代码工程，不是第 13 个研究网站**。

### 快速导航：从这里打开每个项目

| 项目 | 项目说明 | 现在能找到什么 |
| --- | --- | --- |
| [公共爬虫引擎](projects/market-platform/README.md) | `projects/market-platform/` | 四个外包适配器、BOSS / 51job / 猎聘代码、命令行、测试、数据处理 |
| [一品威客](projects/outsourcing/epwk/README.md) | `projects/outsourcing/epwk/` | 历史数据、人工审核主表、A/B 高质量子集及价格子集 |
| [猪八戒](projects/outsourcing/zbj/README.md) | `projects/outsourcing/zbj/` | 列表层外包任务表及完整度说明 |
| [实现网](projects/outsourcing/shixian/README.md) | `projects/outsourcing/shixian/` | 外包任务 / 全职岗位混合样本及类型说明 |
| [程序员客栈](projects/outsourcing/proginn/README.md) | `projects/outsourcing/proginn/` | 采集适配器的入口说明；暂无核实过的正式数据表 |
| [BOSS直聘](projects/recruitment/boss/README.md) | `projects/recruitment/boss/` | 六字段最终结果与历史岗位 ID 归档 |
| [前程无忧](projects/recruitment/job51/README.md) | `projects/recruitment/job51/` | 六字段最终结果；完整原始层尚未归档 |
| [猎聘](projects/recruitment/liepin/README.md) | `projects/recruitment/liepin/` | 六字段最终结果与 31 列完整源层 |
| [B站](projects/social/bilibili/README.md) | `projects/social/bilibili/` | 元数据、字幕、封面脚本、历史批次及补充表 |
| [Facebook](projects/social/facebook/README.md) | `projects/social/facebook/` | Android 采集/API/分析工具及脱敏反应、分享关系表 |
| [Keep](projects/social/keep/README.md) | `projects/social/keep/` | 第一阶段的最小化匿名 JSON、第二阶段可复现 CSV、历史清洗脚本 |
| [豆瓣](projects/social/douban/README.md) | `projects/social/douban/` | Scrapy 工程、课程旧版代码及去身份电影/影评样本 |
| [Amazon + Jiimore](projects/commerce/amazon-jiimore/README.md) | `projects/commerce/amazon-jiimore/` | Amazon 商品表、Jiimore 周点击、SellerSprite 月销量/评论及历史趋势 |

也可以按业务进入 [外包总说明](projects/outsourcing/README.md) 或 [招聘总说明](projects/recruitment/README.md)。

## 二、完整目录地图：每个文件夹负责什么

```text
research-crawlers-and-datasets/
├── README.md                       # 本页：总导航、目录解释、使用顺序
├── .gitattributes                  # Git LFS 文件规则，决定大数据的存储方式
├── .gitignore                      # 排除本地环境、缓存、密钥和临时输出
├── projects/                       # 具体研究项目：按业务类别 -> 网站整理
│   ├── market-platform/            # 外包4站 + 招聘3站共用的可运行代码工程
│   │   ├── drv_crawler/            # 核心 Python 包；站点适配器、网络与调度
│   │   │   ├── boss_cdp/           # BOSS Chrome/CDP 子工程、脚本、测试
│   │   │   ├── job51/              # 前程无忧职业分类、采集器、断点
│   │   │   └── liepin/             # 猎聘采集、解析、登录、错误与状态处理
│   │   ├── tools/                  # 数据清洗、合并、补采、检查及交付脚本
│   │   │   └── liepin_edge_extension/ # 猎聘 Edge 浏览器扩展组件
│   │   ├── tests/                  # 适配器、时间/价格口径、故障与导出测试
│   │   ├── docs/                   # 项目原始说明与采集复盘
│   │   ├── run.py                  # 推荐的共用采集命令行入口
│   │   ├── crawler.py              # 历史底层网络/抓取工具入口
│   │   ├── crawler.config.yaml     # 采集参数示例/运行配置
│   │   ├── pyproject.toml          # 依赖和 Python 包信息
│   │   └── requirements.txt        # 主要依赖清单
│   ├── outsourcing/                # 外包4站：每站独立说明 + 已有数据
│   │   ├── epwk/data/              # 一品威客多个阶段和质量等级的数据
│   │   ├── zbj/data/               # 猪八戒列表任务数据
│   │   ├── shixian/data/           # 实现网任务/职位数据
│   │   └── proginn/                # 程序员客栈说明；暂无确认的正式数据
│   ├── recruitment/                # 招聘3站：最终结果与已保留的源层
│   │   ├── boss/data/              # 最终六字段表与 ID 归档
│   │   ├── job51/data/             # 前程无忧最终六字段表
│   │   └── liepin/data/            # 猎聘最终表与完整源层
│   ├── social/                     # 视频、社交社区类独立项目
│   │   ├── bilibili/               # B站字幕、视频元数据和多版本源码
│   │   ├── facebook/               # Facebook Android/API 采集工程和脱敏数据
│   │   ├── keep/                   # Keep 第一阶段匿名摘要、第二阶段结构化表
│   │   └── douban/                 # 豆瓣 Scrapy 工程和电影/影评数据
│   └── commerce/
│       └── amazon-jiimore/         # Amazon + Jiimore + SellerSprite 的联合研究
├── docs/                           # 全仓库级使用、数据和安全文档
├── metadata/                       # 文件来源、SHA-256、大小与构建结果清单
├── tools/                          # 总仓库级验证、Keep 转换/安全发布工具
└── tests/                          # 总仓库级测试（目前重点覆盖 Keep 复现）
```

上面为**逻辑目录图**：为便于阅读，只展示主要子目录和关键入口，不代表仓库只有这些文件。完整具体文件请直接点开对应项目，或查阅 `metadata/data-manifest.json` 和 `metadata/source-manifest.json`。

### 1. 七站共用采集引擎：`projects/market-platform/`

**这是源代码所在的主要工程。** 外包网站文件夹里的 `data/` 不需要重复放一份爬虫；招聘网站也使用本工程及其对应专用模块。

| 路径 / 文件 | 作用 |
| --- | --- |
| `run.py`、`drv_crawler/cli.py` | 对外统一命令：环境自检、少量样本、按范围采集、招聘任务、登录流程与结果校验 |
| `drv_crawler/sites.py` | 一品威客 `EpwkAdapter`、猪八戒 `ZbjAdapter`、实现网 `ShixianAdapter`、程序员客栈 `ProginnAdapter` 的核心站点适配器 |
| `drv_crawler/recruitment.py` | 三个招聘网站的共用采集与任务协调逻辑 |
| `drv_crawler/boss_cdp/` | BOSS 的 Chrome/CDP 方案、职业数据和相关测试 |
| `drv_crawler/job51/` | 前程无忧的职业分类、请求/解析、断点和自身 CLI |
| `drv_crawler/liepin/` | 猎聘页面解析、HTTP、登录、模型、错误及状态恢复 |
| `drv_crawler/core.py`、`browser.py` | HTTP/浏览器相关基础运行能力；部分功能依赖网站与浏览器环境 |
| `drv_crawler/models.py`、`exporters.py` | 统一字段模型和 JSONL/CSV/XLSX 等导出 |
| `drv_crawler/publication.py`、`runner.py` | 发布时间判断、站点执行流程与停止条件 |
| `tools/` | 数据合并、补采、质量分析、清洗与交付打包；不等同于仓库根目录的 `tools/` |
| `tests/`、`tests/fixtures/` | 离线测试和测试 HTML 样例 |
| `docs/` | 采集过程复盘、旧版 README、数据口径；旧说明以当前项目 README 为准 |

在 `projects/market-platform/` 下运行 `python run.py --help` 可查看当前支持的命令。**运行真实采集前**先配置合法账号、网络/浏览器环境、限速及采集范围；仓库不提供可直接复用的登录状态。`crawler.py` 是另一套历史底层工具，不应误当成 `run.py` 的同义入口。

### 2. 四个外包网站：`projects/outsourcing/`

这里**主要放按网站拆开的交付数据和说明**，真正共用的爬虫逻辑在 `../market-platform/`。

| 子文件夹 | 主要文件或用途 | 阅读和使用提醒 |
| --- | --- | --- |
| `epwk/` | `data/legacy-2691.csv`、`reviewed-1997.csv`、`high-quality-ab-1200.csv`、`price-high-confidence-956.csv` 等 | 旧数据、人工审核主集和过滤子集有重叠，**不能累加成总体数据量** |
| `zbj/` | `data/records.csv`（7,082 条） | 主要来自列表层；正文和价格口径有局限 |
| `shixian/` | `data/records.csv`（53 条） | 46 条外包任务、7 条全职岗位，统计时分开 |
| `proginn/` | `README.md` | 已保留适配器说明，但**没有确认过的正式交付数据** |

各网站的 `README.md` 会指出对应的 `sites.py` 适配器名称，以及字段和覆盖范围。

### 3. 三个招聘网站：`projects/recruitment/`

| 子文件夹 | 文件 | 用途及限制 |
| --- | --- | --- |
| `boss/data/` | `final.csv`、`source_id_inventory.csv` | 39,594 条最终结果；ID 归档用于找回记录线索，**不是包含完整 JD 的原始表** |
| `job51/data/` | `final.csv` | 27,552 条最终结果；完整原始采集层尚未归档 |
| `liepin/data/` | `final.csv`、`full-source.csv` | 46,607 条最终表，49,056 条源层记录；源层保留更多字段供溯源 |

三个 `final.csv` 均为 `职位、JD、薪资、一级分类、二级分类、三级分类` 六字段交付。最终六字段并不包含原始岗位 ID/URL/公司；**文本完全相同的两行也不一定是同一职位**。源表与最终表不要简单按行号关联。

### 4. 社交与视频：`projects/social/`

| 项目 / 子目录 | 具体作用 |
| --- | --- |
| `bilibili/source/main/` | 主批次的 B站元数据/API、检查、视频封面脚本及 Notebook |
| `bilibili/source/supplement/` | 后续补充的字幕、视频信息和封面脚本 |
| `bilibili/data/第一次字幕/` | 第一轮字幕表和去重合并表 |
| `bilibili/data/supplement/`、其他 `data/*.csv` | 补充采集、键盘/手环主题和视频基本信息；多个版本存在重叠 |
| `facebook/source/` | 真实代码工程：Android 抓取、接口服务、进度、结果分析、部署说明 |
| `facebook/source/web/`、`web_v2/` | 历史和新版浏览器操作界面（仅前端资源） |
| `facebook/source/macos/` | macOS 设备/worker 部署脚本与使用说明 |
| `facebook/source/tests/`、`specs/`、`examples/` | 回归测试、设计/验收材料和接口调用示例 |
| `facebook/source/android-ui-dump-helper/` | Android UI 结构收集辅助组件 |
| `facebook/data/` | `sanitized_reactions.csv`、`sanitized_shares.csv`，仅包含匿名帖子—行为者关系 |
| `keep/data/phase1_anonymized/profiles/` | **第一阶段共享版**：101 份最小化匿名用户资料 JSON，不是原始完整抓包 |
| `keep/data/phase1_anonymized/activities/` | **第一阶段共享版**：105 份最小化匿名动态 JSON，保留必要关联结构 |
| `keep/data/phase2/` | **第二阶段结果**：`profiles.csv`、`posts.csv`、`comments.csv`，分别为 100 / 1,050 / 1,692 行 |
| `keep/source/phase2/` | 历史清洗/转换脚本，包含旧环境路径；**正式复现请使用仓库根目录的 `tools/normalize_keep.py`** |
| `douban/source/fcrs2/` | 主要 Scrapy 工程，含 `scrapy.cfg`、爬虫、Items、Pipelines、Settings |
| `douban/source/course-legacy/` | 早期教学/试验 Scrapy 残存代码，非主要运行入口 |
| `douban/data/` | 去身份电影/影评数据和早期课程影评文本，两份样本不能直接相加 |

Keep 第一阶段的**真实抓包原件不在 GitHub**，第二阶段可以直接从共享的最小化 JSON 离线重建。Facebook 的登录态、APK、原始 MITM 数据、证书和本机运行记录也没有发布。B站及豆瓣的文本属于第三方内容，数据访问和进一步传播应分别审核。

### 5. 一个电商整合项目：`projects/commerce/amazon-jiimore/`

**Amazon、Jiimore、SellerSprite 是一个联合商品研究，不拆成三个独立仓库。**

| 子文件夹 | 作用 |
| --- | --- |
| `source/current/` | 当前整理的 Amazon 商品、Jiimore、SellerSprite 相关采集脚本；建议首先阅读 `jiimore1.py`、`sellersprite.py` |
| `source/legacy/` | 较早版本，供算法与采集策略回溯，不保证现在独立运行 |
| `source/misc/` | 商品检查、临时分析和其他历史试验脚本 |
| `source/supplement/get-asin/` | ASIN 补充获取脚本 |
| `data/amazon/products.csv`、`tags.csv` | 商品基本信息与商品标签 |
| `data/jiimore/asin.csv`、`weekly_clicks.csv` | 商品 ASIN 索引与每周点击时间序列 |
| `data/sellersprite/monthly_sales.csv` | 按 ASIN / 月份统计的销量与销售额 |
| `data/sellersprite/monthly_reviews.csv` | 按 ASIN / 月份统计的评论增量与留评率 |
| `data/sellersprite/keepa_history.csv` | 来自 SellerSprite 的 Keepa 风格历史指标；仅覆盖 18 个 ASIN |

分析时先按 `ASIN` 关联商品，再按 `ASIN + 日期/月份` 关联时间序列。销量/评论表已有缺失值和部分联合键冲突；详细口径及覆盖数在 [项目 README](projects/commerce/amazon-jiimore/README.md)。历史脚本依赖并不统一，运行前须配置自己的合法访问凭据、浏览器和 Python 环境。

### 6. 仓库根目录的非项目文件夹

| 路径 | 负责什么 | 何时使用 |
| --- | --- | --- |
| `docs/DATA_GUIDE.md` | 字段释义、主键、合并与 Git LFS 数据说明 | 准备统计分析时 |
| `docs/RESEARCH_WORKFLOW.md` | 课题组成员操作与贡献流程 | 第一次参与协作时 |
| `docs/SECURITY_AND_LIMITATIONS.md` | 数据脱敏、使用权限及缺失边界 | 共享资料或准备公开时 |
| `docs/REPOSITORY_AUDIT_2026-10-17.md` | 全项目审计、已修问题与未解决风险 | 评估质量与研究限制时 |
| `docs/optional-ci-workflow-template.yml` | 可选 GitHub Actions 模板，**当前未启用** | 未来由具备权限的维护者设置 CI |
| `metadata/data-manifest.json` | 每个已发布数据文件的路径、记录数（如适用）、字节大小、SHA-256 和脱敏信息 | 验证是否下载完整、定位数据来源 |
| `metadata/source-manifest.json` | 发布代码与来源副本对应关系及校验哈希 | 核对代码版本与发布完整性 |
| `metadata/build-summary.json` | 此次整理/构建时生成的数量摘要 | 回顾打包构建结果 |
| `tools/verify_release.py` | 同时检查**250 份数据**和**407 份来源文件**的路径与哈希 | 克隆并完成 LFS 下载后 |
| `tools/normalize_keep.py` | 使用匿名第一阶段数据确定性重建 Keep 第二阶段 CSV | 复现 Keep 研究数据时 |
| `tools/keep_release.py` | 维护者使用的 Keep 发布白名单/去身份投影代码，不负责联网抓包 | 更新共享数据副本前的专门审查 |
| `tools/build_repository.py` | 从原本机项目目录制作仓库发布副本的维护脚本，含历史来源路径假设 | **仅维护者**使用；课题组成员无需运行 |
| `tests/test_keep_release.py` | 测试 Keep 字段白名单、敏感字段剔除和第二阶段重建一致性 | 修改 Keep 发布/转换代码之后 |
| `.gitattributes` | 标记 Git LFS 对象；Git 页面看到指针不代表数据坏了 | 上传大表、克隆数据时 |
| `.gitignore` | 忽略本地私密信息、环境、缓存和临时文件 | 任何成员提交新文件前 |

> **容易混淆的两个 `tools/`：** 根目录 `tools/` 是整个仓库的数据校验/Keep 复现/发布管理；`projects/market-platform/tools/` 是外包与招聘专属的数据清洗、恢复和补采工具。它们不能互换。

## 三、首次使用：按任务选择正确入口

### A. 只想下载、阅读和分析已经采到的数据

需要 Git、Git LFS 和 Python 3.11+。先取得仓库访问权限，在终端执行：

~~~bash
git clone https://github.com/taoduo0428/research-crawlers-and-datasets.git
cd research-crawlers-and-datasets
git lfs install
git lfs pull
python tools/verify_release.py
~~~

校验通过后应看到数据与来源文件均成功验证，缺失和哈希不一致数量为 0。数据文件较大时不要直接从 GitHub 网页复制 LFS 指针当作 CSV 使用。

然后从本页「快速导航」选择网站，阅读它自己的 README 和 `data/`。**不要假设所有数据都是最新实时结果，也不要把多个清洗子集相加。**

### B. 要运行外包或招聘爬虫

先在共用引擎目录查看帮助和依赖：

~~~bash
cd projects/market-platform
python -m venv .venv
python -m pip install -r requirements.txt
python run.py --help
~~~

安装依赖后，再按 [共用引擎 README](projects/market-platform/README.md) 的要求做本机环境自检、离线测试与小样本验收。爬虫访问真实网站可能依赖浏览器、合法登录、代理配置和网站当前页面结构；**本仓库不保证换一台机器就能直接开展全站采集**。

### C. 复现 Keep 第二阶段数据（不访问网络）

在**仓库根目录**执行，输出放在仓库外以免覆盖原始发布表：

~~~bash
python tools/normalize_keep.py --input projects/social/keep/data/phase1_anonymized --output ../keep-rebuilt
python -m unittest discover -s tests -p test_keep_release.py
~~~

将 `../keep-rebuilt/` 内三张表与 `projects/social/keep/data/phase2/` 对比，应该字节一致。该命令只运行**第二阶段处理**，不是重新抓包。

### D. 要查看其他独立项目源码

| 项目 | 最先阅读 | 开始之前要知道的事 |
| --- | --- | --- |
| Facebook | [source/README.md](projects/social/facebook/source/README.md)、`source/WINDOWS_DEPLOYMENT.md` 或 `source/macos/QUICKSTART.md` | 真实运行可能需要 Android 设备/模拟器、人工登录和本地组件；先执行已明确为离线的测试 |
| B站 | [B站 README](projects/social/bilibili/README.md)、`source/main/`、`source/supplement/` | 多个历史脚本使用旧 API 和 Windows 绝对路径，不能保证现成可运行 |
| 豆瓣 | [豆瓣 README](projects/social/douban/README.md)、`source/fcrs2/scrapy.cfg` | 单独安装 Scrapy 依赖，先查看可用 Spider；不要直接覆盖现有结果 |
| Amazon + Jiimore | [整合项目 README](projects/commerce/amazon-jiimore/README.md)、`source/current/` | 依赖和环境变量按具体脚本配置；旧版实验脚本不一定能直接使用 |

## 四、数据、源码与成果如何区分

- **`source/` 或 `drv_crawler/`**：Python / JavaScript 等**代码**。可能是主程序、工具或历史实验，存在并不等于已经在现网通过运行验收。
- **`data/`**：实际共享的**研究数据**。`final.csv` 通常是清洗交付层，`full-source.csv` 是保留更多源字段的层，`legacy` / `supplement` / 多个筛选表可能互相重叠。
- **`README.md`**：每个研究项目的方法、如何运行、数据量、主键、已知缺失与局限。请把项目级说明当作分析口径的起点。
- **`metadata/`**：核对「有哪些文件、是否损坏、如何形成发布副本」的证据，不是可用于爬取的配置目录。
- **`tests/`**：离线可验证的测试与固定样例；通过不等于实时网站采集成功。

数据合并时常见主键：外包 `record_id`（连同站点）；招聘应尽可能保留原始岗位 ID/URL；B站字幕按 `CID`；电商按 `ASIN + 时间`；Keep 与 Facebook 的假名键仅在本次发布数据范围内用于关联。

## 五、协作、更新与安全边界

1. **先阅读本页与相关项目 README**，再安装该项目依赖、运行离线验证，最后才考虑访问真实网站。
2. 新增采集记录应保留采集批次、数据时间、来源、字段口径和可复现处理步骤。`final.csv` 与原始层应该分开保存，不要覆盖旧结果。
3. 更新数据要同步维护 `metadata/data-manifest.json` / `metadata/source-manifest.json`，并在提交前运行 `python tools/verify_release.py`；大型 CSV/XLSX 按 `.gitattributes` 使用 Git LFS。
4. 不要向 GitHub 提交真实 Cookie、`.env`、浏览器 Profile、身份明文、原始 MITM/HAR/PCAP、登录证书、APK 或未经授权传播的完整内容。
5. 仓库最新文件已经进行了必要脱敏，**但先前 Git 历史不会自动被清除**；计划将仓库改为 Public 之前，必须完成独立的历史清理和第三方数据授权审查。

### 已知验证情况（2026-10-17）

审计时验证了 **250 份数据、407 份来源文件**的哈希；公共外包/招聘引擎 **193 项**、Facebook **97 项**选定离线测试、Keep **4 项**专门测试通过。它们都是特定范围的测试，**没有证明所有历史脚本均可直接运行，也没有完成所有网站的现网全链路验收**。详见 [完整审计报告](docs/REPOSITORY_AUDIT_2026-10-17.md)。
