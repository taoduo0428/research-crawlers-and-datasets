# Amazon · Jiimore · SellerSprite｜采集程序与研究数据

本项目围绕 **Amazon ASIN** 组织数据：Jiimore 获取类目商品 ASIN，Amazon 采集商品和评论，SellerSprite 查询销量与历史趋势。本次已将原仓库旧的 `source/current`、`source/legacy` 等多套采集代码替换为本地整理并测试过的 **`Amazon/` 当前代码**；此前归档的 **七张历史研究表和分析工具保留**，避免误删科研成果。

> 区分两个数据层：`source/data/` 是新版程序的运行输入/输出位置；`data/` 是原仓库七张历史整理表，**不是本次新版重新采集的结果**。当前 `source/data/ASIN.txt` 已核对 **2,049 个唯一 ASIN**：包含旧历史列表的 1,999 个，另多 50 个；新增 ASIN 不意味着 Amazon/SellerSprite 已完整采集。

## 从哪里开始

| 想做什么 | 入口 | 要登录吗 |
| --- | --- | --- |
| Jiimore 按类目获取 ASIN | [`source/jiimore.py`](source/README.md#jiimore-获取-asin) | 需要 Jiimore 授权账户 |
| Amazon 商品、真实评论、Customer say | [`source/amazon.py`](source/README.md#amazon-商品及评论) | Edge 采集窗口，必要时人工登录 |
| SellerSprite 月销量、留评率、Keepa | [`source/sellersprite.py`](source/README.md#sellersprite-月度与历史指标) | 需单独授权的本地 Cookie |
| 运行新版离线单元测试 | [源码测试说明](source/tests/README.md) | 否 |
| 阅读已发布的七张历史研究表 | [历史数据说明](data/README.md) | 否 |
| 历史数据审计和冲突处理 | [分析工具说明](tools/README.md) | 否 |
| 查看历史测试和本次替换记录 | [文档目录](docs/README.md) | 否 |

## 目录结构

```text
projects/commerce/amazon-jiimore/
├── README.md                           项目总说明（本页）
├── source/                             ★ 当前整理版采集代码
│   ├── README.md                       安装、命令、配置、输出说明
│   ├── amazon.py                       Amazon 商品和评论，使用 Edge
│   ├── amazon_extract.py               商品/评论 HTML 提取
│   ├── jiimore.py                      类目 → 商品 ASIN
│   ├── sellersprite.py                 月销量、评论增长、Keepa
│   ├── project_config.py               本地配置加载器
│   ├── config.example.json             无密码配置模板
│   ├── sellersprite_cookies.example.json 无 Cookie 模板
│   ├── requirements.txt
│   ├── data/                            ASIN.txt 及本地运行输出
│   └── tests/                           当前五组单元测试
├── data/                                ★ 旧仓库七张历史研究表，原样保留
├── tools/                               旧历史数据分析及公开接口探针
├── tests/                               历史数据分析工具测试
└── docs/                                历史记录、版本发布说明
```

**发布安全措施：**原本地文件夹包含真实登录配置、浏览器个人资料和采集输出，这次只发布程序、测试、无凭据配置示例及已存在的 ASIN 输入。未复制 `config.local.json`、`sellersprite_cookies.json`、`.edge-amazon-profile/`、旧运行结果、真实评论者原文/原 HTML。测试需要的评论 HTML 改为合成数据。用户本机原文件未被修改。

## 新版快速开始（Windows / Edge）

以下从仓库根目录开始：

```powershell
cd projects/commerce/amazon-jiimore/source
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 帮助命令不会启动采集
.\.venv\Scripts\python.exe jiimore.py --help
.\.venv\Scripts\python.exe amazon.py --help
.\.venv\Scripts\python.exe sellersprite.py --help

# 离线测试，不调用外部网站
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
```

采集前自行在 `source/` 内从 `config.example.json` 创建 `config.local.json`，从 `sellersprite_cookies.example.json` 创建 `sellersprite_cookies.json`。密码/Cookie 只能保存在本地，不要提交 Git。SellerSprite **Edge 插件的登录状态不等于独立 Python API 已授权**。

### Jiimore 获取 ASIN

```powershell
python jiimore.py --keyword Makeup --max-pages 1
```

该脚本调用老版 `basCategory/list` 和 `nicheAsin/page`，按 **categoryId** 分页得到 ASIN，写入 `source/data/jiimore/jiimore.csv`，并去重补充到 `source/data/ASIN.txt`。**当前整理版的周点击量和日期字段为空**，没有实现旧版本的商品周点击历史采集，不能误读为空数据就是零。具体细分市场的 `nicheId` 不等于这里的 categoryId。

### Amazon 商品及评论

```powershell
python amazon.py --asin B09XMYFTB7 --max-review-pages 1
python amazon.py --asin-file data/ASIN.txt --limit 2
python amazon.py --asin B09XMYFTB7 --reviews-only
```

采用 Edge + DrissionPage，本地独立浏览器资料和调试端口在 [source/README](source/README.md) 解释。程序收集商品字段、Customer say 和真实评论；遇到登录/网页验证暂停人工处理，不自动绕过。输出在 `source/data/amazon/`，成功与失败状态保留在 `last_run.json`。默认不指定 ASIN 时只运行两个测试商品，不会突然启动全部商品。

### SellerSprite 月度与历史指标

```powershell
python sellersprite.py --asin B09XMYFTB7
python sellersprite.py --limit 2
```

分别保存月销量/销售额/均价、月度评分增长及留评率、Keepa 每日价格/评分趋势到 `source/data/sellersprite/`。月销量接口若返回空响应、需升级或未授权，**不视为成功取得数值**。

三套程序是独立入口：**运行 Jiimore 不会自动启动 Amazon 或 SellerSprite**。先少量验收，再根据 ASIN 清单运行其他两站。完整配置与选项参见 [当前源码说明](source/README.md)。

## 原仓库七张历史表（保留，不被新版覆盖）

| 文件（相对于 `data/`） | 历史行数 | 内容 |
| --- | ---: | --- |
| `amazon/products.csv` | 2,006 | Amazon 商品 |
| `amazon/tags.csv` | 8,533 | Customers say 标签 |
| `jiimore/asin.csv` | 1,999 | ASIN 和商品链接 |
| `jiimore/weekly_clicks.csv` | 117,049 | 历史周点击 |
| `sellersprite/monthly_sales.csv` | 66,871 | 历史销量 |
| `sellersprite/monthly_reviews.csv` | 178,785 | 历史评论增长 |
| `sellersprite/keepa_history.csv` | 25,824 | 历史趋势 |

**这些数字来自旧归档审计，不表示新版已经重爬上述数量。** 已发现部分缺 ASIN、缺销量、留评率缺值、相同 ASIN+月份冲突；Keepa 只覆盖部分商品。见 [数据质量检查](docs/数据质量检查_2026-10-17.md) 和 [七表说明](data/README.md)。

从仓库根目录只读审计：

```powershell
git lfs pull
python tools/verify_release.py
python projects/commerce/amazon-jiimore/tools/check_release.py
python projects/commerce/amazon-jiimore/tools/review_data.py audit
python projects/commerce/amazon-jiimore/tools/review_data.py merge-monthly --output ../amazon_monthly.csv
```

原分析工具保留冲突，不擅自选择不同值覆盖历史行。

## 验收与适用范围

用户已在自己的 Windows + Python 3.11 + Microsoft Edge 环境整理和测试当前 `Amazon/`。发布阶段另在隔离 Python 3.13 环境补齐 `DrissionPage==4.1.1.4`，**42/42 项离线单元测试通过**。仓库内的离线用例只能验证解析、配置和保存逻辑；**不应将它们称为 Jiimore 当前接口登录、SellerSprite 月销量或 Amazon 全量抓取已经通过验收**。

原仓库的 [2026-10-17 联网小样本记录](docs/联网实测_2026-10-17.md) 是**独立无登录探针的历史检查**，不等于本次新 `source/` 实际运行结果。当前源码缺少商品级历史周点击收集；月销量需账户/API 权限；不要混用当前程序 `source/data/` 与旧研究数据 `data/` 的口径。

## 分级文档

- [当前采集源码详细说明](source/README.md)
- [当前程序的离线测试](source/tests/README.md)
- [采集输出与 ASIN 输入](source/data/README.md)
- [历史七张研究表](data/README.md)
- [历史数据分析工具](tools/README.md)
- [历史分析测试](tests/README.md)
- [历史报告、来源对照及此次替换记录](docs/README.md)
- [仓库总 README](../../../README.md)
