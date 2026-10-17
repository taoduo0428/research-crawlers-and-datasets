# 课题组爬虫与数据

以前的项目分散在好几个文件夹里。这里把代码、采集结果和使用说明按网站整理到一起，方便大家找文件、查数据，也方便后来的人继续做。

**仓库目前是 Public。** 代码和数据虽已放上 GitHub，但旧 Git 历史里仍有修复前的 Keep 数据，部分字幕、影评等材料也没有核实公开转载权限。需要对外使用或二次传播时，先看[安全说明](docs/SECURITY_AND_LIMITATIONS.md)，不要以为能下载就能随意转载。

## 先找自己的项目

| 项目 | 进去看什么 |
| --- | --- |
| [一品威客](projects/outsourcing/epwk/README.md) | 外包任务、人工审核表、价格分析子集 |
| [猪八戒](projects/outsourcing/zbj/README.md) | 外包任务列表数据 |
| [实现网](projects/outsourcing/shixian/README.md) | 外包任务和少量全职岗位 |
| [程序员客栈](projects/outsourcing/proginn/README.md) | 采集代码说明，暂无核实的正式数据 |
| [BOSS直聘](projects/recruitment/boss/README.md) | 清洗后的招聘数据、岗位 ID 归档 |
| [前程无忧](projects/recruitment/job51/README.md) | 清洗后的招聘数据 |
| [猎聘](projects/recruitment/liepin/README.md) | 招聘清洗表和更完整的源数据 |
| [B站](projects/social/bilibili/README.md) | 视频信息、字幕、封面、几个版本的采集脚本 |
| [Facebook](projects/social/facebook/README.md) | Android 采集程序、本地 API、脱敏后的关系数据 |
| [Keep](projects/social/keep/README.md) | 第一阶段抓包结果的匿名摘要、第二阶段整理后的三张表 |
| [豆瓣](projects/social/douban/README.md) | 电影与影评 Scrapy 项目 |
| [Amazon / Jiimore / SellerSprite](projects/commerce/amazon-jiimore/README.md) | 更新为本地整理过的 Amazon 采集代码（`source/`）；保留七张历史数据表与离线审计工具 |

外包四站与招聘三站共用一套代码，放在[`projects/market-platform/`](projects/market-platform/README.md)。它不是另一个网站。Amazon、Jiimore、SellerSprite 也算同一个研究项目，数据通过 ASIN 关联。

## 每个文件夹是干什么的

```text
research-crawlers-and-datasets/
├── projects/                         各个研究项目
│   ├── market-platform/              外包、招聘共用的爬虫代码
│   │   ├── drv_crawler/              爬虫核心代码
│   │   │   ├── boss_cdp/             BOSS 专用 Chrome/CDP 子工程
│   │   │   ├── job51/                前程无忧采集器
│   │   │   └── liepin/               猎聘采集器
│   │   ├── tools/                    补采、合并、清洗、导出脚本
│   │   ├── tests/                    单元测试和页面样例
│   │   └── run.py                    统一入口，先运行 --help
│   ├── outsourcing/                  四个外包网站的数据和说明
│   │   ├── epwk/                     一品威客
│   │   ├── zbj/                      猪八戒
│   │   ├── shixian/                  实现网
│   │   └── proginn/                  程序员客栈
│   ├── recruitment/                  三个招聘网站的数据和说明
│   │   ├── boss/
│   │   ├── job51/
│   │   └── liepin/
│   ├── social/                       B站、Facebook、Keep、豆瓣
│   │   ├── bilibili/
│   │   ├── facebook/
│   │   ├── keep/
│   │   └── douban/
│   └── commerce/
│       └── amazon-jiimore/           Amazon / Jiimore / SellerSprite：新源码与历史数据
├── docs/                             给成员看的数据、使用和安全说明
├── metadata/                         数据及源码的文件清单、SHA-256
├── tools/                            全仓库校验与 Keep 数据整理工具
├── tests/                            全仓库层面的离线测试
├── .gitattributes                    Git LFS 大文件规则
└── .gitignore                        哪些本地文件不要提交
```

### 代码、数据和文档怎么看

- **`source/`、`drv_crawler/`**：放采集代码。部分其他项目仍保留 `current/legacy/supplement` 历史分类；**Amazon 项目已于 2026-10-17 用整理版的 `source/` 替换旧多版本源码**，不要按旧目录继续找采集入口。
- **`data/`**：已经采到或整理好的数据。`final.csv` 往往是简化后的交付表，`full-source.csv` 会保留更多原字段，`phase1/phase2` 是处理前后两个阶段。不同版本可能重复，不能直接相加。
- **各项目的 `README.md`**：先看这一份，里面写了文件在哪、怎么用、还有哪些问题没解决。像 `specs/`、`.agents/`、`SKILL.md`、`CHANGELOG.md` 这些是历史研发记录或工具配置，不是新同学的入门教程，没必要从头读。
- **`projects/market-platform/tools/`**：专门处理外包/招聘数据。和仓库根目录的 `tools/` 不是一回事。
- **`metadata/data-manifest.json`**：数据文件清单，记录路径、大小和校验值；`source-manifest.json` 用来核对源码副本。
- **`docs/`**：全仓库的[数据说明](docs/DATA_GUIDE.md)、[成员使用流程](docs/RESEARCH_WORKFLOW.md)、[安全和已知问题](docs/SECURITY_AND_LIMITATIONS.md)、[逐项目检查记录](docs/REPOSITORY_AUDIT_2026-10-17.md)。

## 第一次下载

GitHub 上比较大的 CSV、Excel 通过 Git LFS 存放。克隆完最好把数据完整拉下来，再检查一遍：

```bash
git clone https://github.com/taoduo0428/research-crawlers-and-datasets.git
cd research-crawlers-and-datasets
git lfs install
git lfs pull
python tools/verify_release.py
```

Python 建议使用 3.11 或更新版本；具体爬虫可能另有版本要求。校验脚本会同时检查数据和源码文件。看到 `missing=0`、`mismatched=0` 才说明这次下载完整。

如果只想做数据分析，到相应网站的 `data/` 找 CSV 即可，不用启动爬虫。

## 想自己重新运行怎么办

**外包、招聘**：先进 `projects/market-platform/`，安装这个项目的依赖，再看命令行支持哪些任务。

```bash
# 先进入共用爬虫目录
cd projects/market-platform

# Windows (PowerShell)
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe run.py --help
```

各网站的登录、浏览器、反爬和采集窗口不同。先跑离线测试、小样本，再决定是否做新的采集，不要直接运行大批量任务。

**Keep 第二阶段**：这部分不用联网。回到仓库根目录执行：

```bash
python tools/normalize_keep.py --input projects/social/keep/data/phase1_anonymized --output ../keep-rebuilt
python -m unittest discover -s tests -p test_keep_release.py
```

整理后会得到用户、动态、评论三张 CSV。第一阶段真正的网络抓包程序和未脱敏原件没有放在仓库里。

**Facebook、B站、豆瓣、电商项目**：分别打开对应 README，按项目环境安装。Facebook 有 Windows/macOS 的部署手册；豆瓣需要 Scrapy。**Amazon 已换成本地整理版源码**，启动命令及隐私配置请看 [亚马逊项目 README](projects/commerce/amazon-jiimore/README.md)；其现网接口权限仍需要按账号另行验收。B站的部分代码仍含历史版本。

## 用数据时容易弄错的地方

- 一品威客的 1,997 条人工审核数据和 1,200、956、920 等子集是包含关系，**不是几批独立样本**。
- 实现网有 46 条外包需求和 7 条全职岗位，不能混在一张“外包报价表”里。
- 三个招聘网站的最终表只有六列，缺公司和原始岗位 ID；即使两行文字一样，也不能直接认定同一个岗位。
- B站多份字幕文件有重叠，先按 `CID` 处理。
- Amazon、Jiimore、SellerSprite 按 `ASIN` 联表；月度数据还得加上月份。**Amazon 旧七张研究表在 `projects/commerce/amazon-jiimore/data/`，新版爬虫本地输出在 `source/data/`，不是同一批数据。**
- Keep 的 1,692 行是**示例评论索引**，不是 1,692 条动态；Facebook 的数据也已做匿名处理。

更细的口径写在[数据说明](docs/DATA_GUIDE.md)和各项目 README 中。

## 接着开发或补数据

先留好原始层，再做清洗表；每次采集尽量记下日期、网站、批次、筛选条件。新数据上传前检查个人信息和账号凭据，大文件按 Git LFS 规则提交。改了数据或已纳入清单的源码，记得更新 `metadata/` 的 SHA-256 再运行校验。

这份仓库的历史数据已经上传，并做过文件完整性检查。但**“文件能下载”与“爬虫今天还能跑”是两回事**。目前没有对所有网站重新做完整在线采集验收。Amazon 当前采集代码已于 2026-10-17 更新，说明和未发布的隐私文件范围见[亚马逊项目 README](projects/commerce/amazon-jiimore/README.md)及[替换记录](projects/commerce/amazon-jiimore/docs/发布替换说明_2026-10-17.md)；其他网站继续看各项目 README 和[检查记录](docs/REPOSITORY_AUDIT_2026-10-17.md)。
