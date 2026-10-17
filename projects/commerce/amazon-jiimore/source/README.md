# 原来的两套代码怎么找

这两个子目录来自原电脑的两个独立文件夹，不要只看名字判断哪个“更好”：

- **`current/` ← 原电脑 `亚马逊\成功\`**
- **`legacy/` ← 原电脑 `亚马逊\新建文件夹\`**

`current` 和 `legacy` 是最初合仓时起的名字，**不表示脚本已经联网验收，也不意味着 `legacy` 一定更旧**。原文件保留在这里，方便查当年的采集方法；正常读数据请从[项目首页](../README.md)进入。

## 每种脚本负责什么

| 脚本 | 从哪里开始看 | 作用和实际差别 |
| --- | --- | --- |
| `amazon.py` | `current/`、`legacy/` 都保留 | Amazon 商品信息和标签；`legacy` 多了读取已有 ASIN 的逻辑 |
| `Amazon1.py` | **先确认用途，再运行** | `current` 是并发抓商品信息；`legacy` 则是补 `amazon global shipping` 的另一套代码，二者不是一个程序的小版本更新 |
| `amazon2.py` | `current/amazon2.py` | 商品信息、标签、颜色等采集流程，函数拆分较清楚，适合做后续维护起点 |
| `legacy/amazon2.py` | 只在需要补字段时看 | 主要做 `amazon global shipping` 补采；和 `current/amazon2.py` 目标不同 |
| `jiimore.py` | 任一目录 | Jiimore 数据采集，带多线程与重复记录跳过逻辑；两边源文件原本相同 |
| `jiimore1.py` | 任一目录 | 获取 ASIN 清单与点击数据，历史输出包括 `asin.csv`、`jiimore.csv` |
| `sellersprite.py` | 任一目录 | SellerSprite 月销量、评论、Keepa 历史接口；两边内容原本相同 |
| `sellersprite1.py` | `current/` | 和基础版近似，增加对已有 ASIN 的读取和跳过；两边版本不同 |

`misc/` 里有较早的 Selenium 练习与单页检查脚本，`supplement/get-asin/` 是额外的 ASIN 获取脚本。它们都不是推荐的统一运行入口。

## 新同学从哪里开始

1. 如果只是做数据分析，用 `../tools/review_data.py`，**不必安装 DrissionPage 或 Selenium**。
2. 如果要补 Amazon 商品信息，先阅读 `current/amazon2.py` 的函数与依赖，再准备隔离浏览器。
3. 如果要补 Jiimore 或 SellerSprite 的时间序列，先查原脚本中的数据参数、输出路径和账号读取方式，并在自己机器上做少量请求验证。
4. 不要直接批量运行 `misc/` 里的旧脚本。它们有些顶层代码会在导入时启动浏览器，旧电脑路径也不一定还存在。

Python 源码的语法检查和部分重试逻辑的离线测试已做过。2026-10-17 又补了[联网小样本测试](../docs/联网实测_2026-10-17.md)：Amazon 商品公开页、SellerSprite 的评论与 Keepa 接口已有成功案例；**月销量仍返回空对象，Jiimore 未登录返回 401**。成功的是新写的 `../tools/live_probe.py`，不是上面这些旧脚本已经整体恢复。网页登录、Cookie、验证码和网络环境不同，不应把“以前抓出 CSV”理解为“现在仍能直接重跑”。

本仓库没有重新分发原始账号配置。不要上传 `.env`、浏览器登录 Profile、Cookie 或令牌。
