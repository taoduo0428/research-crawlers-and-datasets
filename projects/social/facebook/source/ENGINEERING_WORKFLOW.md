# Facebook 工程维护框架

这页是给以后维护代码的人用的。一个改动最好按「说清问题—改代码—跑测试—检查真实环境—记录结果」这个顺序做。下面那些 Spec Kit、Constitution、Review 等名词，只是当时团队用的工程协作工具，不是采集功能本身。
本项目使用 GitHub Spec Kit 作为后续变更的上层生命周期框架。当前工作区包含历史研发现场和未提交修改；本文件描述以后任务如何工作，不代表这些现有修改已经被整理或验收。

## 框架入口

- Constitution：`.specify/memory/constitution.md`
- Spec Kit 配置、模板和脚本：`.specify/`
- Codex Spec Kit skills：`.agents/skills/speckit-*`。
- 项目 AI/工程规则：`.agents/rules/context_and_standards.md`、`.agents/rules/spec-driven-workflow.md`、`AGENTS.md`
- 现状/部署/测试资料：`STABLE_BASELINE.md`、`API_USAGE.md`、`WINDOWS_DEPLOYMENT.md`、`macos/CODEX_DEPLOYMENT_RUNBOOK.md`、`tests/README.md`

## 标准闭环

`理解现状 → Specify → Plan → Tasks → Implement → Verify → Review → Converge`

Spec Kit 管理规格和任务，不会自动证明功能完成。Codex 每次任务必须在交付中分开报告：代码变更、确定性测试、本机合约、真实平台验收、未验证项和回滚路径。

## 验收分层

1. 静态：Python/PowerShell/Node 语法、依赖和敏感文件检查。
2. 确定性：`tests/` 中受影响 unittest 和完整可行套件。
3. CI：Windows/macOS workflow 实际执行结果。
4. 本机合约：API/UI/worker/lease/传输恢复等隔离验证。
5. 真实平台：Windows、真实 macOS、Android/Facebook 任务；没有对应证据不得宣称通过。

## 推荐维护入口

从项目根目录运行 `python tools/maintenance_gate.py --json`。它执行本地静态检查和 unittest；测试可启动自己的隔离假服务，不启动正式采集器或操作真实设备。退出码 0 = 所列门禁全部通过，1 = 失败，2 = 存在缺失工具或显式跳过（包括 --skip-tests）。报告只保留输出尾部，output_truncated 会标记截断；完整调查请保存原始测试输出。发布路径检查不是文件内容或 Git 历史秘密扫描；真实平台验收仍须单独执行并单独报告。

## 完整检查证据（私有、项目外）

需要可追溯的完整输出时，显式选择项目外、从未存在的运行目录：

```powershell
.venv\Scripts\python.exe -B tools/maintenance_gate.py --json --log-dir C:/Users/lenovo/security-audit-skill/facebook/checks/run-unique
```

`run-unique` 必须换成新的运行名称，既有目录会被拒绝，不会覆盖历史证据。
每条命令的完整 stdout/stderr 按原始字节保存；JSON 摘要仍限制为尾部并标记截断，
同时返回文件路径。`report.json` 在临时文件完成写入、flush/fsync 和关闭后，以排他硬链接原子发布；文件系统不支持时会失败，不降级为直接写最终文件。未启动命令的空日志不标记 full_output_retained，command_started 单独表达启动状态。日志创建/读取/报告保存失败
会使检查失败；直接子进程超时会退出并保留已经输出的内容，不是 PASS。

这些日志可能包含本机路径、源码错误和测试输出；只保留在本机私有证据目录，不提交、
不加入 release-files.txt、不自动上传或删除。目录的 mode=0700 不是 Windows ACL 验证，
不得据此宣称只有本人可读取；共享机器需按已有权限策略另行确认。
不指定 `--log-dir` 时仍可运行，但只有尾部摘要，不能关闭完整原始输出验收要求。
测试可启动隔离假服务；本入口不是安全审计沙箱，也不保证未知子孙进程的超时清理。

## 现有系统不可回归的事实

正确性/完整性优先；保持五类 URL、API contract、单 active worker 串行和主备接管；source/target/completeness/terminal/visibility 校验不可绕过；空用户数组不能单独代表 empty list；不以缩短稳定等待伪造速度；保护登录态、AVD、ADB、Frida、证书、代理、lease、运行产物和用户数据。
