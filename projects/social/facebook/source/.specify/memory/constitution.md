# Facebook 项目 Constitution

<!--
Sync Impact Report
- Version change: new project constitution -> 1.0.0
- Added: correctness/completeness, compatibility, serial worker safety, evidence gates,
  platform parity, security/data protection, reversible change and lifecycle governance.
- Removed: none.
- Follow-up: feature-specific requirements remain in Spec Kit feature directories; this file
  governs all future changes without re-specifying the existing product.
-->

## Core Principles

### I. 正确性与完整性优先
系统必须优先给出真实、可解释、可追溯的结果，而不是单纯追求速度或更大的数量。任何完成结论必须同时考虑 target/source 匹配、query 是否真正触发、visibility、completeness、terminal pagination、rate-limit/session restrictions 和清理状态。空数组本身不得证明 empty list 或全量完成；仅好友可见名单不得宣称为完整名单。

### II. 兼容性与契约稳定
现有 API 参数、返回字段、错误语义、五类 URL 入口和已验证客户端行为必须保持兼容。改变公共 contract 必须在 Specify 阶段明确列出迁移影响、兼容策略、回滚方式和 regression verification；没有明确授权不得通过“顺便重构”改变接口。

### III. 单 Worker 与运行时安全不可破坏
默认保持单 active worker、串行捕获、主备故障接管和 capture lease 语义，不得擅自扩展为并行租户。任何涉及 worker、ADB、进程、代理、Frida、证书、模拟器、登录态或清理的变更，必须先确认所有权和当前状态，并验证进程身份、传输恢复、lease 释放和失败关闭行为。不得为了测试重启服务、登出 Facebook、重置 AVD 或清理用户数据。

### IV. 证据驱动的变更与验证
每个 Bug 修复必须有可复现输入和对应 regression verification。性能优化必须提供修改前后的同条件基线、负载、样本量、结果完整性和资源代价；缩短稳定等待、减少检查或改变采样边界不得伪装成性能提升。CI、静态检查、HTTP 200 或只读 health 不得替代真实 Android/Facebook 运行验收。

### V. Windows/macOS 配对与边界清晰
平台相关修改必须检查 Windows 与 macOS 对应实现、脚本、配置、CI 和文档；未能在真实平台验证时必须明确标注静态/CI/部分验证，不得宣称完整跨平台支持。共享业务规则应保持一致，平台差异放在窄适配层。

### VI. 安全、隐私与可发布边界
不得提交密码、Cookie、Token、真实抓包、PCAP、APK、CA 私钥、本机配置、运行目录或账户状态。安全审计必须以真实 trust boundary、source evidence 和最小安全验证为依据，不把防御性建议直接升级为漏洞。发布白名单、`.gitignore`、敏感数据隔离和依赖来源必须作为变更验收的一部分。

### VII. 最小、可逆、可审查
修改应围绕一个明确目标，优先小范围、原子、可回滚的变更；保留未提交工作和有效行为，不为整洁重构。每次实施都必须说明影响文件、验证命令、未验证边界和回滚路径。未经授权不得 commit、push、发布、安装系统软件或删除用户数据。

## 工程工作流

所有新功能、Bug 修复、性能优化和安全修复遵循：

`理解现状 → Specify → Plan → Tasks → Implement → Verify → Review → Converge`

- 先读 `AGENTS.md`、`STABLE_BASELINE.md` 和任务相关文档，检查 Git、进程/端口/设备/配置；不把历史证据当作当前验收。
- Spec Kit 只描述新增或变化的工作，不重新 Spec 整个 Facebook 系统。
- Plan 必须列出边界、兼容性、跨平台、风险、回滚和验证；Tasks 必须可独立检查并标明代码/测试/文档范围。
- Implement 前确认未提交修改所有权；Implement 不得绕过 Constitution。
- Verify 分为确定性 unittest/CI、本机合约、真实 Windows、真实 macOS/Android/Facebook 五类证据，分别报告。
- Review 由独立审查路径检查 diff、contract、风险和测试；Converge 只追加剩余任务，不删除未完成证据。

## 治理

本 Constitution 是项目级工程原则，优先于临时便利和未经记录的历史习惯；它不替代 `AGENTS.md` 的具体操作约束，而是与其共同生效。原则修订必须通过一次 Spec Kit Constitution 变更，说明版本、影响、迁移和验证；仅文字澄清递增 PATCH，新增或实质扩展原则递增 MINOR，删除或重新定义硬约束递增 MAJOR。任何功能 Spec、Plan、Tasks、代码评审和发布检查都必须引用适用原则，并在证据不足时标记为未完成而不是推断通过。

**Version**: 1.0.0 | **Ratified**: 2026-10-02 | **Last Amended**: 2026-10-02
