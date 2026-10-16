# Facebook 采集项目当前状态（2026-09-09）

本文是当前代码、真实运行产物和历史记录之间的校准基准。旧交接、研究和计划文档保留当时事实；结论冲突时，以本文和对应 `runs/` 原始产物为准。

## 状态词

- `initial-ready`：严格目标、Reaction 入口和首屏查询成功，只证明当前账号可见且入口可用。
- `terminal-complete`：严格目标一致，分页到当前账号可见列表终点，并通过完整性检查。
- `partial/open-tail`：已经得到用户，但没有到终点；不能宣称全量完成。
- “代码/测试支持”：只证明解析和控制逻辑，不能代替真实 Facebook、账号、网络和页面布局验收。

## 真实 URL 类型证据

| 类型 | 当前证据 | 结论 |
| --- | --- | --- |
| 数字 `/posts/<id>` | `runs/20260908-221817`，31 个首屏用户 | `initial-ready` |
| `share/p` -> `story.php` | `runs/20260908-222003`，26 个首屏用户 | `initial-ready` |
| `share/r` -> Reel | `runs/20260908-223036`，2 个首屏用户 | `initial-ready` |
| 页面名 + `pfbid`（ATCTPE） | `runs/20260908-231139`，35 个首屏用户 | `initial-ready` |
| 页面名 + `pfbid`（IsmailiGoldenLight） | `runs/20260909-150431`，稳定 post `122106880826437311`，31 个首屏用户 | `initial-ready`；已验证规范 `pfbid` 变化后的严格绑定 |
| 群组 `/posts/` 多图布局 | `runs/20260908-231627`，29 个首屏用户 | `initial-ready` |
| 群组 `/permalink/` | `runs/20260908-231833`，38 个首屏用户 | `initial-ready` |
| `/reel/<id>/` | `runs/20260908-232309`，20 个首屏用户 | `initial-ready` |
| `/watch/?v=<id>` | `runs/20260908-234531`，先规范化到同一 Reel，20 个首屏用户 | `initial-ready` |
| 历史完整分页 | `runs/reaction-full-smoke-20260902-120331`，3539 个唯一用户、143 个查询记录、终点为真、0 限流 | `terminal-complete` |

上述 9 月 8 日和 9 月 9 日 Probe 均未请求第 2 页，`terminal_page_seen=false`。它们补齐的是移动端入口定位和首屏查询证据，不是每类 URL 的全量分页验收。`videos`、`share/v` 等仍只有解析/代码或较弱历史证据，提交时必须逐条通过目标校验；私密、删除、无权访问或不展示用户名单的帖子仍不可枚举。

## 当前实现

- 本地页面接受多条 Facebook URL，按 Worker 排队；同一 Worker 复用已启动的模拟器和 Facebook 主进程，任务之间仍独立创建代理、输出和严格目标边界。
- Windows 使用既有 PowerShell 深度采集链路；默认热启动，故障恢复或显式 `-ColdStartFacebook` 才冷启动 App。
- 新安装可选择 `direct` 或显式 `upstream`。直连不要求 7897；旧 Windows 配置没有 `network` 字段时保留原上游模式。
- `START.cmd`/`CHECK.cmd` 面向 Windows；`CONFIGURE.command`/`START.command`/`CHECK.command` 面向 Apple Silicon Mac。
- 结果必须按目标匹配、来源一致、终点、限流和完整性共同判断。抓到若干用户、HTTP 200 或页面能打开都不等于完成。

## macOS 与 M4 边界

仓库已包含 Apple Silicon ARM64 配置入口、Android 35 Google APIs 镜像准备、专用 AVD、Frida 校验、CA/Helper 准备、Facebook App 链接偏好、直连/上游配置、检查和启动入口。CI 和 Windows 模拟可以验证静态契约与命令生成。

截至 2026-09-09，尚未在接收者真实 M4 上完成 Android 虚拟化、APK、人工登录、系统/Conscrypt CA、Frida、真实 Reaction 首屏和终点分页的整套验收。因此不能写成“Mac 已完整支持”。验收顺序以 `macos/QUICKSTART.md` 为准，并需在重启模拟器后再次执行 `CHECK.command` 和一个小帖任务。

## 文档差异处理

- 8 月交接、研究状态和 `PROJECT_MEMORY.md` 是历史快照，其中“当前限制”“尚未定位”等描述只代表记录日期。
- 9 月 2 日 PPT 母稿的 3539 人完整分页证据仍有效；新增 URL 类型 Probe 不能替换该完整分页证据。
- 实习工作梳理的工作量和历史测试数字按原日期保留，不能当成今天的测试结果。
- `2026-09-08-easy-setup.md` 的实现项现已落地；真实 M4 验收仍未完成。
- `2026-09-08-portable-capture.md` 的代码、启动器、网络和文档工作已落地；真实 M4 全链路仍待验收。
- worker-03 的人工登录、Probe 和启用仍是人工环境任务，不能自动勾成完成。
- 9 月 3 日 share-link 修复计划保留原计划勾选，当前实现和 Probe 证据应以发布测试与本状态表判断。
- `docs/archive/` 与 `archive/` 是归档证据，不改写。

## 验证边界

发布回归测试集合是 `.github/workflows/check.yml` 中列出的 10 个模块。历史 `unittest discover` 还会发现依赖旧本机 release fixture 或固定页面哈希的测试；它们不属于下载仓库后的分发验收。每次发布还应执行 JavaScript 语法检查、源码白名单导出和 `git diff --check`。
