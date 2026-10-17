# Amazon + Jiimore：把商品和历史指标连起来

这是**一个项目**。Amazon 提供商品信息，Jiimore 提供部分搜索/点击指标，SellerSprite 提供月销量、评论和历史趋势。几套数据之间主要靠 `ASIN` 连接。

## 文件怎么找

| 文件 | 内容 | 当前规模 |
| --- | --- | ---: |
| `data/amazon/products.csv` | Amazon 商品基本信息 | 2,006 行，1,999 个有效唯一 ASIN |
| `data/amazon/tags.csv` | 商品标签 | 8,533 行 |
| `data/jiimore/asin.csv` | Jiimore 商品 ASIN 清单 | 按文件内容为准 |
| `data/jiimore/weekly_clicks.csv` | 周点击量 | 117,049 行 |
| `data/sellersprite/monthly_sales.csv` | 月销量 | 66,871 行 |
| `data/sellersprite/monthly_reviews.csv` | 月评论指标 | 178,785 行 |
| `data/sellersprite/keepa_history.csv` | Keepa 风格历史曲线（取自 SellerSprite） | 25,824 行，18 个 ASIN |

四个主表共同覆盖 **1,998 个 ASIN**。做关联时不要只按商品号拼月数据，月销量和评论还要加上 `月份`；点击量按 `日期` 处理。

有些字段缺值：商品表的 `amazon global shipping` 全空；部分月份没有销量或留评率。月度数据里还有少量相同 ASIN、月份但值不同的记录，需要回查来源，不能随手去重。

## 代码在哪

- `source/current/`：整理时选用的商品、Jiimore、SellerSprite 脚本；建议先看 `jiimore1.py` 和 `sellersprite.py`。
- `source/legacy/`：早期版本，留着查处理方式。
- `source/misc/`：商品检查和其他实验脚本。
- `source/supplement/get-asin/`：补充获取 ASIN 的脚本。

这些脚本的依赖不完全一样，常见有 `requests`、`pandas`、`beautifulsoup4`、`DrissionPage`、`openpyxl`，有些旧版还需要 `selenium` 等。没有经过验证的统一安装清单；确定要跑哪个脚本，再按它的导入项准备环境。

账号也得用自己的。旧代码曾出现硬编码手机号、密码和 Cookie 的问题；不要拿历史认证信息继续用。需要时在本机设置 `JIIMORE_PHONE`、`JIIMORE_PASSWORD`、`SELLERSPRITE_COOKIE` 等环境变量，不要提交到 Git。
