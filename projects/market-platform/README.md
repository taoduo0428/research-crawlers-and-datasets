
# 外包 / 招聘公共采集引擎

这是原桌面 `外包+岗位爬虫` 的**代码工作区**。四个外包适配器与 BOSS、前程无忧和猎聘共用 `drv_crawler`，因此仅保留一份代码，不重复复制多个可能分叉的版本。

## 安装
- Python 版本按原 `pyproject.toml` / `requirements.txt`。
- Windows：`py -m venv .venv`，激活后 `pip install -r requirements.txt`。
- 查看命令：`python run.py --help` 或 `python crawler.py --help`。
- 测试（不发起抓取）：`python -m pytest -q tests`。
- 站点选择、采集窗口和停止条件以 `crawler.config.yaml` 与当前 CLI 帮助为准，不能仅按示例参数直接对网站执行批量抓取。
- 自己准备网络、浏览器和合法账号；本仓库不提供登录态、IP 池密钥或本机 Cookie。相关配置仅在本机填写，禁止提交到 Git。

## 数据与使用边界
- 外包任务：`../outsourcing/`；招聘岗位：`../recruitment/`。
- 采集器含网页解析、分页、详情抓取、时间窗口判断、错误记录、断点/恢复、归档和数据后处理，部分历史入口需要具体平台状态。
- `tools/` 是数据清洗、恢复与最终交付工具；要复用请从 `projects/market-platform` 作为当前工作目录执行。
- 不包含本地历史浏览器用户目录（约数 GB）、.env、真实登录状态、完整包捕获和本机运行缓存。
- 第三方子组件保留其原始版权说明，研究使用前仍应阅读对应许可和平台规则。
