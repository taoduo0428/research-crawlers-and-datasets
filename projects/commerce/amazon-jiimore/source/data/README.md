# 当前采集器的数据目录

本目录属于当前 `source/` 中的三份采集脚本，**不是**项目根目录的 [七张历史数据表](../../data/README.md)。

## 已公开的输入

- `ASIN.txt`：整理版本次发布的输入清单，已核对 **2,049 个不重复 ASIN**。它完整包含旧归档的 1,999 个 ASIN，另外有 **50 个新于历史清单的 ASIN**（来源时间与具体筛选市场需结合采集日志确认）。这只是已保存输入，**不表示新版本次重新爬到了 2,049 件商品的完整资料**。

## 运行时生成（默认不提交 Git）

| 路径 | 生成它的程序 | 内容 |
| --- | --- | --- |
| `jiimore/jiimore.csv` | `../jiimore.py` | ASIN、上架时间、Amazon 链接；当前周点击与日期字段为空 |
| `amazon/goods_infos.csv` | `../amazon.py` | 标题、评分、价格、Customer say |
| `amazon/reviews.csv` | `../amazon.py` | 评论用户名、标题、正文，可能包含第三方个人信息和受版权保护的文本 |
| `amazon/customers_say_tags.csv` | `../amazon.py` | Customers say 标签（页面提供时） |
| `amazon/last_run.json` | `../amazon.py` | 本轮中断、数据保存情况 |
| `sellersprite/monthly_sales.csv` | `../sellersprite.py` | 月销量和销售额等 |
| `sellersprite/reviews.csv` | `../sellersprite.py` | 月度评分增长和留评率 |
| `sellersprite/keepa.csv` | `../sellersprite.py` | 每日价格与评分历史 |

源文件夹里用户测试形成的上述**真实 CSV、JSON 和原始页面不复制到公开仓库**，原因是其中可能有评论用户信息、第三方内容或订阅数据。需要公开经授权的研究结果时应先完成隐私/转载权限评估，并另行放到 `../data/`，同步对应清单与来源说明。

当前程序不存在自动联动三站的单一总入口，应按 Jiimore → ASIN → Amazon → SellerSprite 依次运行。输入清单更新会去重保留已有 ASIN；生成文件不应与历史研究表直接拼接。