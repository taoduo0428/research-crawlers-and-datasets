# 当前采集源码的离线测试

测试对象是上一级 `source/` 的代码。对应本地整理工程中五个原始 `test_*.py` 均未修改；真实 Amazon 评论 HTML fixture 在公开版替换为**合成的最小页面**，仅验证 HTML 解析接口，不含真实评论用户或浏览器资料。

| 文件 | 检查范围 |
| --- | --- |
| `test_amazon_extract.py` | 商品价格、评分、HTML 范围、翻译内容、真实评论正文和标签解析 |
| `test_amazon_runtime.py` | 商品与评论的原子写入、ASIN 选择、运行中断保留进度 |
| `test_jiimore.py` | 类目与 API 模拟、重试、ASIN 清单去重和文件更新 |
| `test_project_config.py` | 本机配置默认值、格式验证、禁止泄漏敏感值 |
| `test_sellersprite.py` | 三个趋势指标解析与保存、鉴权失败停止、空数据不伪造成功 |

从 `source/` 目录运行：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
```

**环境说明：**源码指定 Python 3.11 与 `DrissionPage`。仅运行其他 Python 版本而缺少 DrissionPage 时，`test_amazon_runtime.py` 会在导入阶段报 `ModuleNotFoundError`；这是运行环境未完整安装，不等同于其他测试失败，也不代表真实页面已经过验收。应在已安装依赖的解释器中重新执行全套测试。

**仓库发布复测（2026-10-17）：**维护环境为 Windows / Python 3.13，在独立且不提交 Git 的 `source/.venv/` 中安装 `DrissionPage==4.1.1.4`（其余依赖复用本机已安装版本），**42/42 项单元测试通过**。这次测试没有开启 Edge 真实采集，也没有读取本地账户；并非所有 requirements 锁定版本已在 Python 3.13 下逐项确认。用户原项目的主要适配环境仍是 Python 3.11。

单元测试主要使用模拟响应/临时目录，不主动运行全量采集，也不会读取 `config.local.json` 的真实账户。真实联网验收需另外记录时间、ASIN、站点、是否登录及数据条数。