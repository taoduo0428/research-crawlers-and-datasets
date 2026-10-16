
# 亚马逊 + Jiimore：一个多数据源商品研究项目

**一个项目，不将 Amazon、Jiimore 和 SellerSprite 作为彼此独立的三个课题。** 全部通过 `ASIN` 关联。
代码位于 `source/current`、`source/legacy`、`source/supplement`，包括 Amazon 商品详情、Jiimore 品类 ASIN 与周点击量、SellerSprite 月销量/留评率/Keepa 风格趋势。主运行入口推荐先看 `source/current/jiimore1.py` 和 `source/current/sellersprite.py`；有些早期脚本仍是历史实验，不保证每个入口都能独立工作。各脚本依赖并不完全相同：主脚本主要涉及 `requests pandas beautifulsoup4 DrissionPage openpyxl`，部分旧脚本还依赖 `selenium`、`chromedriver_autoinstaller` 和 `ddddocr`。仓库目前没有经过全量验证的统一电商 `requirements.txt`，应在独立虚拟环境按实际脚本导入项安装依赖，不要假定所有旧版本可以直接运行。使用前配置 `JIIMORE_PHONE` / `JIIMORE_PASSWORD` / `SELLERSPRITE_COOKIE` 在本机环境变量，不要写到源码或提交 Git。

## 数据关联
- `data/amazon/products.csv`：2,006 行，其中 1,999 个有效唯一 ASIN；商品标题、价格、评分等。
- `data/amazon/tags.csv`：8,533 行，覆盖 1,342 个 ASIN。
- `data/jiimore/weekly_clicks.csv`：117,049 条、覆盖同样 1,999 个 ASIN（2023-01 至 2024-04）。
- `data/sellersprite/monthly_sales.csv`：66,871 行，覆盖 1,998 个 ASIN。
- `data/sellersprite/monthly_reviews.csv`：178,785 行，覆盖 1,999 个 ASIN。
- `data/sellersprite/keepa_history.csv`：25,824 行，仅覆盖 18 个 ASIN；接口在 SellerSprite 域名，不代表直接调用 Keepa。
四个主要表共同覆盖 1,998 个 ASIN。关联用 `ASIN` + （`日期` / `月份`），切勿只按 ASIN 去重。
已观察月销量 28 条、月评论 1 条“ASIN + 月份”非完全相同冲突。`amazon global shipping` 列现有商品表全部为空；其他时间序列亦有缺失。

## 认证与安全
所有发现的硬编码旧手机号/密码/Cookie 在发布副本中需替换为环境读取，原始文件留在本机；建议立即轮换此前使用过的有效凭据。原 ZIP/HTML 可能含认证会话，未上传。
