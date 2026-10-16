# 项目协作指令

- 先读 `STABLE_BASELINE.md`，按任务查 `API_USAGE.md`、`WINDOWS_DEPLOYMENT.md` 或 `macos/CODEX_DEPLOYMENT_RUNBOOK.md`；历史记录不是当前运行证明。
- 修改前检查 Git、相关进程/端口/设备和当前任务。保护未提交修改、登录状态、已有配置、运行产物和其他任务；不要为方便分析重启服务。
- 当前是单 worker 串行捕获，主备接管前必须确认旧进程和传输清理。不要扩大为并行租户。
- 保持五类 URL、API 参数/返回兼容；`query-not-triggered`、目标来源不匹配和未确认的清理仍须失败或保持保护。
- 空用户数组不足以证明 empty lists；必须结合目标匹配、终点和 visibility/completeness。不得把仅好友姓名限制宣称为全量名单。
- Windows 自适应目标等待默认关闭；没有同条件性能与完整性证据，不缩短稳定等待，不改变 Facebook/Frida/SDK/证书版本。
- 优先符号级或定向读取，使用适用的 system-architect / windows-macos-dev-guard；不为整洁重构、不自动开多代理。
- 新可发布文件同时加入 `.gitignore` 的允许项和 `release-files.txt`；不要提交 APK、账户、CA 私钥、本机配置、日志、原始抓包和运行目录。
- 按实际 diff 跑相关测试；本机合约、CI 和真实 Mac/Android 验收分别报告。只读健康检查不能证明业务完成。
- 实际 Codex 配置修改先做时间戳备份和 CODEX-CHANGELOG；仅写项目文档不需要改全局配置。Git 发布按当前用户授权，勿擅自更换分支或强推。

- 修改任务先读取 `.specify/memory/constitution.md`、`.agents/rules/spec-driven-workflow.md` 和 `ENGINEERING_WORKFLOW.md`；Spec Kit 只管理本次增量变更。Verify/Review/Converge 不能以任务勾选或本地 PASS 代替实机验收。
