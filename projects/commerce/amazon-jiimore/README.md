# Amazon + Jiimore：代码和数据怎么用

这不是一个脚本抓下来的数据。原电脑里有两套工程，名字分别叫 `成功` 和 `新建文件夹`，采集对象包括 Amazon 商品、Jiimore 点击量、SellerSprite 销量和评论。

两套工程的数据**互相补充**，不能只留下其中一个；但它们也有不少重复，不能直接合并两个文件夹里的所有 CSV。

想做分析的同学，**先用这里整理好的 `data/`，不要先启动爬虫**。想继续采集，先看[两个源码目录的关系](source/README.md)和[原始来源对照](docs/来源版本对照.md)。

## 七张表分别是什么

| 文件 | 原始数据主要来自 | 内容 | 行数 |
| --- | --- | --- | ---: |
| `data/amazon/products.csv` | `新建文件夹` | Amazon 商品基本信息 | 2,006 |
| `data/amazon/tags.csv` | `新建文件夹` | 用户提及的商品标签 | 8,533 |
| `data/jiimore/asin.csv` | 两边完全相同 | ASIN 清单及商品链接 | 1,999 |
| `data/jiimore/weekly_clicks.csv` | `成功` | 按日期记录的周点击量 | 117,049 |
| `data/sellersprite/monthly_sales.csv` | `成功` | 月销量、销售额、平均单价 | 66,871 |
| `data/sellersprite/monthly_reviews.csv` | `成功` | 月度评分增长与留评率 | 178,785 |
| `data/sellersprite/keepa_history.csv` | `新建文件夹` | 历史评分、价格等趋势 | 25,824 |

商品主表有 **1,999 个有效 ASIN**，但另有 7 行没有 ASIN；Keepa 历史表只涉及 **18 个 ASIN**。四张主要分析表（商品、周点击、月销量、月评论）共同覆盖 **1,998 个 ASIN**。这些数字是历史数据，不是网站当前数据。

`新建文件夹` 中另外保存的 907 行月销量、1,653 行月评论，**全部存在于 `成功` 的大表中**，没有必要再追加一遍。

## 现在最方便的入口

新增了一个**不联网**的数据检查工具，不需要登录 Amazon、Jiimore 或 SellerSprite，也不需要先安装浏览器。

在总仓库根目录执行：

~~~bash
# 先确认大 CSV 下载完整
git lfs pull
python tools/verify_release.py

# 看七张表各有多少行、多少 ASIN、缺了哪些值、有哪些重复键
python projects/commerce/amazon-jiimore/tools/review_data.py audit

# 按 ASIN + 月份合并销量与评论（输出保存到仓库外）
python projects/commerce/amazon-jiimore/tools/review_data.py merge-monthly --output ../amazon_monthly.csv

# 导出某个真实 ASIN 在七张表中的相关记录，仍保留为七个文件
python projects/commerce/amazon-jiimore/tools/review_data.py export-asin --asin B09XMYFTB7 --outdir ../amazon_B09XMYFTB7
~~~

合并后会同时产生 `amazon_monthly.csv` 和 `amazon_monthly_conflicts.csv`。如果相同 ASIN、月份有不同的销量或评论值，程序**不会擅自选其中一条**：它在合并表中留空并标注 `冲突待核对`，把候选值写进冲突明细供人检查。

输出文件若已存在，程序默认拒绝覆盖；确实要覆盖自己生成的文件，需要额外传 `--overwrite`。工具禁止将输出写回正式的 `data/` 目录。

**别把周点击量直接拼到月销量表。** 一张表是周粒度，另外两张是月粒度。如果要按月分析点击量，先明确采用求和还是月内均值，再单独计算。

## 整理时发现的真实问题

- 月销量有 **6,134 行**缺月销量，另有 **104 行**没有 ASIN。
- 同一 `ASIN + 月份` 里，月销量存在 **28 个有不同值的冲突键**；月评论存在 **1 个**。
- 月评论的 `留评率` 缺了 **130,399 行**，缺失不能填成零。
- 商品主表的 `amazon global shipping` 整列为空；目前的发布数据里**没有补采成功的运费字段**。
- Keepa 的历史日期范围与周点击、月销量不同，不能直接按行号拼。

更完整的核对数字、字段和合并规则在[数据质量记录](docs/数据质量检查_2026-10-17.md)。

## 2026-10-17 的联网小样本测试

已经用仓库里的真实 ASIN 测过当前网站，结果见[完整测试记录](docs/联网实测_2026-10-17.md)：

| 当前功能 | 测试结果 |
| --- | --- |
| Amazon 商品标题、价格、评分 | **2/4 真实商品成功**，另 2 个返回 HTTP 500 |
| SellerSprite 月评论 | **2/2 成功**，月度数据到 2026-09 |
| SellerSprite Keepa 历史趋势 | **2/2 成功**，时间点到 2026-10-17 |
| SellerSprite 月销量 | **0/2**，返回 HTTP 200 但数据是空的 |
| Jiimore 周点击 | **0/1**，未登录返回 401 |

可以用新工具在自己机器上重试：

~~~bash
python -m pip install requests beautifulsoup4
python projects/commerce/amazon-jiimore/tools/live_probe.py --asin B005G9E98O
~~~

这个命令只查一个 ASIN，打印实际字段与结果状态，**不会写入历史 CSV**。如果只想看 Amazon 的当前商品信息，可以加 `--site amazon`。注意：美国站网页可能返回 JPY 等非美元价格，不能把它当作 USD 使用。

**这次成功的是小样本的公开页面或接口读取，不代表历史的批量爬虫已经恢复。** 月销量和 Jiimore 周点击仍需继续处理登录权限或接口变化。

## 如果想重新采集网站

历史脚本在 `source/current/` 和 `source/legacy/`，目录名容易让人误会：前者是从 `成功/` 复制来的，后者是从 `新建文件夹/` 复制来的，**不是已经验证的新旧版本排名**。

要重新抓商品，先看 `source/current/amazon2.py`；要追踪 ASIN/周点击的老流程，看 `source/current/jiimore1.py`；月销量和评论脚本见 `source/current/sellersprite.py`。这些只是**适合先检查的历史入口**，不代表今天已经完成登录和联网采集验收。

除了离线整理，这次也对当前网站做了少量真实请求；**但没有直接启动旧的批量爬虫**。旧脚本有本机绝对路径、浏览器依赖、账号环境和网页变化风险。部分旧代码还会自动尝试识别验证码，联网测试没有使用这些功能。不要复制历史 Cookie 或密码，需要登录时在自己的电脑操作，并先小样本运行。

## 以后加新数据

尽量分别保存原始商品表、周点击、月销量、月评论。记录采集日期、脚本版本、数据来源；相同 ASIN/月出现冲突时保留原始候选记录。大 CSV 使用 Git LFS，修改发布数据后更新 `metadata/data-manifest.json` 再运行 `python tools/verify_release.py`。
