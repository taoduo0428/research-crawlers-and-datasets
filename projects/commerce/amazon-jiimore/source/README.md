# 当前 Amazon 采集源码：使用与维护

这里是本次替换后**唯一的当前采集代码**。源码取自本地测试通过的整理版 `Amazon/`，五个 Python 模块逐字节同步；公开发布**不包含**真实登录配置、Edge Profile、运行结果或私人评论原 HTML。

返回[项目总览](../README.md)。

## 环境与安装

- 用户整理时采用 **Windows、Python 3.11、Microsoft Edge**；其他操作系统或 Python 版本没有作为已验证环境。
- `requirements.txt` 锁定了 `DrissionPage`、`requests`、`pandas`、`lxml` 版本；不同解释器环境可能无法直接满足依赖。
- Amazon 需要 Edge 桌面浏览器，Jiimore 与 SellerSprite 分别使用 `requests` 会话，**并不自动继承你日常 Edge 的登录态**。

```powershell
cd projects/commerce/amazon-jiimore/source
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
```

只想读取此前的研究表，请回到 [`../data/`](../data/README.md)，不需要安装浏览器依赖。

## 本地配置与敏感信息

```powershell
Copy-Item config.example.json config.local.json
Copy-Item sellersprite_cookies.example.json sellersprite_cookies.json
```

- `config.local.json` 中的 `jiimore.phone_num`、`jiimore.password` 属于本机 Jiimore 授权登录，`proxy` 默认为 `http://127.0.0.1:7897`；没有代理可设 `""`，`request_timeout` 是请求超时秒数。
- `sellersprite_cookies.json` 存放经授权取得的 SellerSprite 网站登录状态，可包含 `rank-login-user` 等字段。Edge 扩展已登录**不等于**这个独立 API 已得到同样权限。
- 两个本机 JSON、`.edge-amazon-profile/`、`.venv/` 及本地运行输出均已被 `.gitignore` 排除。**不要上传这些文件、粘贴 Cookie 或提交带个人信息的抓包日志。**
- 示例文件只是空模板/占位符，不含真实登录凭据。

## Jiimore 获取 ASIN

`jiimore.py` 调用历史接口 `basCategory/list` → `opportunityExplorer/nicheAsin/page`，按类目名称查 `categoryId`，分页获取商品 ASIN、上架时间、Amazon 链接；有效新 ASIN 会去重追加到 `data/ASIN.txt`，已有列表不覆盖。

```powershell
python jiimore.py --help
python jiimore.py --keyword Makeup --max-pages 1
```

采集结果为 `data/jiimore/jiimore.csv`，字段包括 `ASIN`、`上架时间`、`Amazon_url`、`周点击量`、`日期`。**当前整理版没有采集商品历史周点击数值，周点击量/日期按原实现保持空值。** 历史仓库的 `../data/jiimore/weekly_clicks.csv` 是更早的数据，不能冒充现在这次请求所得。

**类目 categoryId 与具体细分市场 nicheId 不等同。** 例如网页中某个市场产品列表有 73 行，不代表当前 `--keyword` 类目请求也正好返回 73 个 ASIN。网络错误、账号受限或需要登录时应保留已有记录、暂停检查。

## Amazon 商品及评论

`amazon.py` 用 DrissionPage 连接 **Microsoft Edge**，通过 `amazon_extract.py` 分离 HTML 文本解析和实际浏览器访问。浏览器调试端口默认 `9223`，采集 Profile 位于本机 `.edge-amazon-profile/`，当前 Edge 日常窗口未必能被复用；不要复制其他人的浏览器资料目录。

```powershell
python amazon.py --help
python amazon.py                           # 原来的两个默认测试 ASIN
python amazon.py --asin B09XMYFTB7 --max-review-pages 1
python amazon.py --asin-file data/ASIN.txt --limit 2
python amazon.py --asin B09XMYFTB7 --reviews-only
```

- 默认美国站 `amazon.com`、英语；商品信息写入 `data/amazon/goods_infos.csv`，评论标题/正文写入 `data/amazon/reviews.csv`，有 Customers say 标签时写入对应标签表。
- `last_run.json` 记录本轮每个 ASIN 的商品、评论状态和评论行数；CSV 使用原版安全写入和去重逻辑。
- 有登录页、页面挑战或验证码时**停止并等待本人手工操作**；当前代码不依赖自动解验证码，也不保证能自动进入已登录的日常 Edge。
- `--limit` 可小样本运行；没有显式 `--asin`/`--asin-file` 时**不会扫描完整 ASIN.txt**。

## SellerSprite 月度与历史指标

`sellersprite.py` 分别按 ASIN 读取月销量、评分数增长/留评率、Keepa 风格按天历史。每张表独立检查是否已出现相同 ASIN，并把文件保存到 `data/sellersprite/`。

```powershell
python sellersprite.py --help
python sellersprite.py --asin B09XMYFTB7
python sellersprite.py --limit 2
```

- `monthly_sales.csv`：`ASIN`、`月份`、`月销量`、`月销售额`、`平均单价`。
- `reviews.csv`：`ASIN`、`月份`、`评分数增长值`、`留评率`。这是历史统计，**不是评论正文**。
- `keepa.csv`：`ASIN`、`日期`、`评分`、`评分数`、`价格`；如同原脚本，每天取最后一个有效时间点。
- 月销量接口无数据、账号返回 401/403、限流或解析失败均不视为已采集成功；**不可把缺失当作 0**。
- SellerSprite 与 Amazon 浏览器插件不是同一个会话来源；插件界面显示的数值需要独立的授权能力才能导出或复核。

## 数据目录与时间维度

`source/data/ASIN.txt` 是公开的既有输入清单；其他 CSV/JSON 是运行时本地生成，不属于已发布七张历史表。运行前先做好自身数据备份。

数据关联优先以 `ASIN` 为键：月销量/留评率结合 `月份`，Keepa 结合 `日期`；Jiimore 统计中“市场级周点击”不可赋给某个 ASIN。父 ASIN / 子 ASIN 的汇总规则应在分析前单独定义。

详见 [运行数据说明](data/README.md) 和 [历史整理表说明](../data/README.md)。

## 测试与限制

`tests/` 下有 `test_amazon_extract.py`、`test_amazon_runtime.py`、`test_jiimore.py`、`test_project_config.py`、`test_sellersprite.py` 五组测试，均主要使用合成样本、模拟会话或临时目录，不需要真实账号。发布版的评论 HTML fixture 是**人工合成**，不是他人的真实评论内容。具体运行环境与已知限制见 [测试 README](tests/README.md)。

**离线测试通过 ≠ 当前网站实际登录、全部 ASIN、完整历史月销量均已成功采集。** 请先用一件商品验证真实请求，再决定是否扩大范围。