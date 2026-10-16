
# Facebook：Android 抓包、反应用户及分享采集

## 代码入口与安装
`source/` 来源于原项目 `release-files.txt` 白名单（而非整份个人浏览器/模拟器目录）。
Windows 先读 `source/WINDOWS_DEPLOYMENT.md`；Mac 先读 `source/macos/QUICKSTART.md` 和实际设备验收要求。安装 `source/requirements.txt`，由 `source/START.cmd` / `source/START.command` 启动；页面和 `/v1/jobs` 本地 API 负责提交、查询、取消、CSV 导出。需要 Android 模拟器、合法取得的 APK、人工登录、代理和合适网络，**本仓库不含这些敏感组件**。
`source/facebook_capture_api.py` + `source/facebook_android_capture.py` 为调度与 Android 采集链；`source/analyze_facebook_capture.py` 解析历史记录。

## 发布数据（去身份）
`data/sanitized_reactions.csv`、`data/sanitized_shares.csv` 使用每次导出随机密钥作不可逆假名，保留“帖子-用户”二元关系而不保留用户真实 ID、姓名、用户名、URL、帖子正文或任何 Cookie。来自历史多次试验并已去重，不代表一次生产全量采集。
源日志历史 670 个任务：210 completed、233 failed、226 cancelled、1 cancelling。状态 `completed` 也必须结合 target match / terminal / integrity 评估。
## 保密限制
个人登录态、HTTP MITM 完整流、证书、APK、raw JSONL、模拟器磁盘、个人资料及本机 `runs` **未上传**。如有获批的本地审计需求，由授权成员在隔离环境读取本机原件；不能放进 GitHub。组内也不能将假名数据与外部身份列表连接尝试重新识别。
