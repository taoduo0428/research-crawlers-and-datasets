# Spec Kit 与 Codex 工程协作规则

适用于 `C:\Users\lenovo\Desktop\facebook`。本文件补充现有 `AGENTS.md`，不替代其中的安全、兼容、单 Worker、平台和数据保护要求。

## 生命周期

所有需要修改代码、配置、测试、启动器、文档 contract 或性能行为的任务，按以下顺序执行：

1. **理解现状**：读取 `AGENTS.md`、`STABLE_BASELINE.md`、相关入口/依赖/测试；检查 Git 状态、未提交修改、进程/端口/设备和配置所有权。
2. **Specify**：只描述本次变化，不重新定义整个 Facebook 系统；写清用户目标、边界、兼容性和验收条件。
3. **Plan**：列模块、数据流、失败/取消/恢复、Windows/macOS 对应面、风险和回滚。
4. **Tasks**：拆成可验证任务；每项绑定源码、测试、文档或运行验收，不把“跑 CI”当成唯一验收。
5. **Implement**：小步、原子、可逆；不得覆盖无关未提交工作，不得修改登录态、模拟器、ADB、Frida、证书、代理或运行数据，除非任务明确授权。
6. **Verify**：先跑受影响的 unittest/静态检查，再按需要跑 CI、本机合约和真实 Windows/macOS/Android/Facebook 验收；报告实际退出码和限制。
7. **Review**：独立检查 diff、API contract、URL 五类兼容、source/target/completeness/terminal 保护、清理/lease 和跨平台配对。
8. **Converge**：根据实际证据追加未完成任务；不把未知、跳过或历史结果改写成通过。

## 工具职责

- **Spec Kit**：维护 Constitution、feature `spec.md`、`plan.md`、`tasks.md` 和流程状态；不代替运行验收。
- **system-architect**：系统边界、异步任务、worker/设备/子进程/lease、稳定性和性能诊断；性能结论必须有同条件证据。
- **windows-macos-dev-guard**：Windows/macOS 路径、shell、原生工具、权限、配置、CI 和文档配对检查。
- **security-audit**：以真实信任边界和 source evidence 做安全审核；不把缺少防御纵深直接报成漏洞。
- **代码/differential review**：实施后独立查看当前 diff、回归风险和范围漂移；未完成独立复核时不得宣称全部通过。
- **unittest/CI**：确定性回归与源码/契约检查；不能替代真实 Android/Facebook 验收。
- **静态工具**：按实际变更选择 Python 编译/导入、PowerShell AST、Node syntax、依赖检查、敏感文件/泄漏检查和 Git diff 检查；不为“看起来完整”扩大工具数量。

## 任务类型门禁

### 普通 Bug
必须有：复现或明确静态证据、最小修复、对应 regression test、受影响模块回归、回滚说明。不得借机调整等待时间或抓取算法。

### 性能优化
必须先建立基线，区分排队、设备准备、网络等待、UI 等待、本地处理和输出写入；同条件比较吞吐、延迟、完整性、失败率、CPU/内存/IO。没有证据时只做测量，不改稳定等待。

### 大功能
必须先完整 Specify/Plan/Tasks，明确 API/schema、五类 URL、单 Worker/主备语义、平台配对、数据迁移、验收矩阵和回滚；未完成这些门禁不得 Implement。

### 安全/审计
默认 source-first、最小本地夹具、无生产/真实账号/外部服务；发现必须区分 confirmed、needs-validation、rejected。独立验证、部署边界和真实运行证据不能省略。

## 禁止事项

不得使用 Spec Kit `--force` 覆盖当前工作区；不得自动重建 `AGENTS.md`、现有 `.agents/rules`、Codex 全局配置或已安装 Skills。不得把整个项目重新 Spec；不得未经授权 commit、push、发布、清理用户数据或重启正在运行的服务。
