# 仓库整体离线验收与发布修复记录（2026-10-17）

> 范围：`research-crawlers-and-datasets` 的当前本地 Git 工作树与 GitHub `main`、已归档数据、源码与发布工具、可直接在 Windows 上执行的离线测试、当前跟踪文件隐私形态、Markdown 相对链接。
> **这不是跨站点真实登录 / Android 实机 / macOS 实机 / 全历史隐私与版权审核报告。**

## 一、执行环境与发布基线

- 仓库：`https://github.com/taoduo0428/research-crawlers-and-datasets`；开始检查时本地和 `origin/main` 一致，均为 `c293f37dfce595de02e8f688df2dab9a09fa8461`，工作树无修改。
- 环境：Windows、Python 3.13、pytest 9.1.1、Node 24.18。Amazon 当前源码测试使用隔离虚拟环境中的 `DrissionPage==4.1.1.4`；**未在本次验收中调用外部采集网站或启动账号已登录的 Edge/Android 模拟器。**
- 历史完整性校验：`python tools/verify_release.py` 验证**251 个数据文件和 396 个有来源校验信息的源码文件**，无缺失、不一致、重复路径。该 `metadata/source-manifest.json` 不覆盖后来新添加的所有说明或辅助文件，需结合下面的 Git 跟踪清单检查。
- Amazon 发布版 `source/data/ASIN.txt` 有 2,049 个唯一 ASIN，包含历史 1,999 个并增加 50 个；不代表额外 50 个 ASIN 的跨站数据已采齐。

## 二、可运行的离线测试

| 测试范围 | 执行命令（在对应工作目录运行） | 完成结果 |
| --- | --- | --- |
| 外包/招聘主引擎 | `cd projects/market-platform; python -m pytest -q tests` | **337 passed**，另有 62 个子测试通过 |
| BOSS CDP / 前程无忧 / 猎聘子模块 | `cd projects/market-platform; python -m pytest -q drv_crawler/boss_cdp/tests drv_crawler/job51/tests drv_crawler/liepin/tests` | **209 passed**，另有 12 个子测试通过 |
| Facebook 本地采集/API/UI | `cd projects/social/facebook/source; python -B -m unittest discover -s tests -t .` | **781 tests；779 passed、2 skipped** |
| Amazon 当前源码 | `python -m unittest discover -s projects/commerce/amazon-jiimore/source/tests -p test_*.py`（隔离环境解释器） | **42 passed** |
| Amazon 旧历史数据工具 | `python -m unittest discover -s projects/commerce/amazon-jiimore/tests -p test_*.py` | **12 passed** |
| B 站字幕/视频数据 | `python -m unittest discover -s projects/social/bilibili/tests -p test_*.py` | **8 passed** |
| Keep 公开数据重建 + Git 发布哈希跨平台守卫 | `python -m unittest discover -s tests -p test_*.py` | **7 passed**（其中 4 个 Keep、3 个源码哈希） |

**累计测试案例 1,396 项：1,394 通过，2 项跳过。** 另外 pytest 报告的 74 个子测试通过不重复计入主案例数。所有测试主要使用本地文件、模拟响应或 loopback API，不应推广为所有网站现网通过。

静态检查：`python -m compileall -q projects tools tests` 退出码 0；`node --check` 对 Facebook `web/app.js`、`web_v2/app.js` 和新增 `examples/capture_fetch.mjs` 均成功。Keep 归档代码 `projects/social/keep/source/phase2/测试.py` 会发出旧 Windows 路径的 `SyntaxWarning: invalid escape sequence`；这是一个未更新的历史样例，语法警告不代表它的业务代码可运行。

## 三、本轮发现并修复的发布缺陷

1. **BOSS 许可证丢失。** 主引擎的独立发布测试要求 `drv_crawler/boss_cdp/LICENSE`，但旧仓库未收录。已核对原作者上游仓库 `https://github.com/eatmoreduck/boss-zhipin-scraper/blob/master/LICENSE`，恢复原作者 `Copyright (c) 2026 eatmoreduck` 的 MIT 许可证，非自行新授予许可。
2. **BOSS README 版本未标识。** 已与脚本、`pyproject.toml`、`SKILL.md` 的 `v2.2.1` 对齐；外包/招聘总 README 增加“独立运行不依赖 Codex”说明。
3. **Facebook Node API 示例缺失。** 恢复 `examples/capture_fetch.mjs`，只接受本地 loopback API，使用 Node 22+ 原生 `fetch`、不发送登录凭据、不依赖 npm 安装；成功、空结果、失败、取消、未知状态、超时、非法输入的 **7 项契约测试通过**。
4. **Facebook CI 引用两个已不存在的旧测试模块。** 已移除无效引用，保留现有有效测试目标。Mac 旧式 `CONFIGURE.command` / `START.command` / `CHECK.command` **仍有意不随仓库发布**；将其专属测试改成验证当前公开可用的 `setup.sh` 回退路径，并非伪造旧 Mac 一键安装功能。
5. **Windows PowerShell 5.1 文本编码。** `facebook_transport.ps1`、`run_facebook_capture.ps1`、`open_facebook_post_readonly.ps1` 增加 UTF-8 BOM，**原正文的全部字节不变**；该平台编码回归测试通过。
6. **Facebook 发布文件清单失配。** `release-files.txt` 旧列出 5 个未提供的文件，导致 `python tools/export_source.py` 失败。已按真正发布的文件纠正为 **184 项、184 项存在**，成功生成 184 文件源码 ZIP（打包产物只保留于本地忽略目录）；`tests/test_layout.py` 新增“清单文件必须真实存在”的守卫。
7. **单元测试产生临时状态。** `.gitignore` 增加前程无忧测试输出 `.state/`，避免误上传。
8. **校验清单与离线审计工具。** 增加 `tools/audit_repository_publication.py`，扫描**当前 Git 跟踪文件**的敏感文件名、几类高置信度密钥格式以及 Markdown 相对链接。对应源码校验清单同步更新，但保留历史来源 `source_sha256`。
9. **Windows 与 Git/Linux 换行不一致。** 发现旧 `published_sha256` 对 Windows 工作区字节计算，Git 实际将 **387/396** 份源码签入为 LF，原校验器在 Linux/macOS 上会把正常克隆误报损坏。现将 `metadata/source-manifest.json` 全部 **396 条发布哈希**重算为**Git staged blob 原始字节**（其中 9 份与工作区原字节完全一致，387 份仅因 Git 换行规则有差异）。修改 `tools/verify_release.py`：对源代码只接受**严格字节一致**或**仅 CRLF→LF** 等价；带 NUL 的文本/二进制维持严格字节比较，**251 份历史数据始终不允许换行归一化**。新 `tests/test_verify_release.py` 的 3 项测试确认跨平台等价可通过、源码实质改动仍会失败。独立读取 Git index 的 396 个发布对象后均与新哈希精确匹配，Windows 本地验收也为 0 错误。

## 四、仓库级结构、安全、文档与打包审计

```powershell
# 在仓库根目录执行：
python tools/verify_release.py
python tools/audit_repository_publication.py --strict-links
python projects/commerce/amazon-jiimore/tools/check_release.py
# Facebook 单独测试发布包（在该项目 source 内）：
python tools/export_source.py
```

- `tools/audit_repository_publication.py` 本轮扫描了 **711 个当前 Git 跟踪文件、100 份 Markdown、201 处 Markdown 链接**；缺失的跟踪文件、断开的本地相对链接、命中的高置信度密钥形态和私人配置文件名均为 **0**。该扫描只覆盖少数**高置信度密钥格式**，不等于完整的个人信息、版权、许可证或历史泄漏核验。
- `tools/verify_release.py` 对已有 `metadata` 中有记录的文件逐一检查 SHA-256；**251 个数据文件严格按字节和大小核对，396 个源码文件与 Git blob 进行跨平台换行等价比较**。Windows 已完整通过；还直接对全部 396 个已暂存的 Git blob 进行 SHA-256 核验，均精确匹配。此处验证的是逻辑和实际 Git 对象，**未声称已在 Linux/macOS 实机运行 Python 测试**。历史表的数字真实性、时间范围和采样口径仍以各项目审计为准。
- Amazon 当前版 **42/42**，`check_release.py` 对 2,049 唯一 ASIN、7 张历史表、18 个有意发布的 `source/` 路径做安全范围检查，结果合格；其子项目 README 相对链接检查 **63 处**，无问题。
- Facebook 在其项目根目录 `tools/export_source.py` 打包 184 项成功；ZIP 在 `source/dist/`，不提交 Git。本仓库根 `.github/workflows/` 未部署全项目自动 CI；Facebook 内部存在项目级 `source/.github/workflows/check.yml` 供原工程参考，不代表 GitHub 自动在本仓库执行了所有测试。

## 五、仍不能称作“全部现网验收通过”的事项

- **Amazon / Jiimore / SellerSprite：** 当前 GitHub 版的 42 项离线测试通过，旧的公网探针另有成功样本；但真实账号登录、Jiimore 商品历史周点击、SellerSprite 月销量、Amazon 长批次和 Edge 扩展数据尚无统一的现网端到端成功证据。`source/jiimore.py` 现在按 `categoryId` 获取 ASIN，并没有自动填充商品周点击历史。
- **Facebook：** 781 项自动化用例覆盖本地 API/UI/安全逻辑，**不等于 Android/Frida/真实账号/实际 Reaction/分享名单完整性验收**。当前公开包仍缺 Mac 旧 `.command` 一键部署入口，README 已说明当前可用的 `setup.sh`；Apple Silicon 实机能力需要独立验收。
- **外包、招聘、B站、豆瓣、Keep：** 本次覆盖所列离线测试和语法检查，**没有**逐站登录、全量采集或重新证明历史数据真实完整。豆瓣历史 Scrapy 子工程与 Keep 的旧演示脚本不等于可一键运行的现行采集程序。
- **公开内容合规：** 部分第三方文本、字幕、评论、老仓库 Git/LFS 历史仍有版权、隐私、合规待核对问题。当前 Git 敏感形态扫描**无法排除历史提交的风险**，不应宣传“彻底无隐私泄露”。
- **持续集成：** 根仓库目前没有全面启用跨项目 GitHub Actions；此次验收是本地实测。后续代码一旦修改，应重跑与修改点相关的测试、文件哈希和安全扫描。

## 六、验收结论

**当前仓库的发布文件、可运行的离线测试和本轮可确认的打包契约已修复并通过。** 发布后的剩余工作属于“外部环境/真实业务数据/历史数据合规与真实设备”的独立验收，不能写成通过。本报告不改变其他项目数据，也不删除历史 Git 记录。具体补采方式与风险警告仍以各项目 README 为准。
