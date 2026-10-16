# 已验证环境与迁移基线（2026-09-17）

本文记录当前 Windows 工作环境的实测值，供换机时对照。它不是强制升级清单。部署命令继续使用 [Windows 部署](WINDOWS_DEPLOYMENT.md)、[Mac 快速开始](macos/QUICKSTART.md) 和 [Mac Codex Runbook](macos/CODEX_DEPLOYMENT_RUNBOOK.md)。

## 自动核对到的本地基线

| 项目 | 当前证据 / 迁移要求 |
| --- | --- |
| Facebook | `com.facebook.katana`，`555.0.0.49.59`，versionCode `470015241`；安装包 ABI 为 x86_64，minSDK 28 / targetSDK 36 |
| APK | `pm path` 返回一个 `base.apk`；设备上 SHA-256 为 `93e8c9e0e8dc533f858437739fb5bfc81c501bfae22dc14ac24d48665e19871b` |
| 安装记录 | 首次/最后安装均为设备报告的 `2026-08-13 03:50:14`；未因此重装或降级 |
| Android | `emulator-5554`，AVD `fb-x86_64-api35`，Android 15 / API 35，Google APIs x86_64、支持 root / writable system |
| ADB | `1.0.41`，Platform-Tools `37.0.0-14910828`；这是本机观测值，不要求接收方为迁移而升级 |
| Python 依赖 | 本机 Python `3.13.12`；支持 3.12/3.13。`mitmproxy==12.2.3`、`frida==17.17.0`、`frida-tools==14.10.4`、`requests==2.32.5` |
| Android Frida | `/data/local/tmp/fb-fs17` 报告 `17.17.0`，必须与主机 frida 匹配；Mac AVD 使用 Android ARM64 ELF，不使用 macOS 可执行文件 |
| 证书 | 本机 `~/.mitmproxy/mitmproxy-ca-cert.pem` 公开 CA 和私钥文件存在；runner 负责 Android system / Conscrypt 安装与验证 |
| 网络 | 当前磁盘配置为 `network.mode=direct`；历史 Windows 上游及设备普通代理为 `127.0.0.1:7897`，任务后恢复记录也显示该值。配置、已启动进程和设备现场分开核对；任务采集通过 ADB reverse 连接 mitmproxy，新配置默认 direct |
| 服务 | 本机 API `127.0.0.1:8765`、UI `8766`；新机以 `facebook.local.json` 实际端口为准，避免使用模拟器的 `5555` |
| 调度 | 单 active worker 串行，备用 worker 仅故障接管；任务结束要验证进程、代理恢复、lease 释放 |

证据来源为本机配置、依赖、ADB 包信息/文件哈希、Frida 版本和既有真实任务产物；没有上传配置、账户、APK、CA 私钥或原始抓包。

## APK 来源与处理边界

历史本地研究记录包含 `patched-libs` 和 SSL pinning 研究文件，但没有找到能证明当前 APK 原始下载来源、签名或补丁全过程的完整清单。因此不能宣称它是某一下载站原包，也不能根据同一 versionName 推断字节或签名一致。上述哈希只标识当前设备上的 x86_64 base.apk；Mac ARM64 包不会有同一哈希。

已经工作的模拟器保持原包。新机由接收者提供有权使用、ABI 匹配的 APK；记录来源 URL、取得日期、版本、包名、SHA-256，以及可用时的 `apksigner verify --print-certs` 签名摘要。`apksigner` 属于 Android Build-Tools。不要为了部署先卸载现有 Facebook；签名冲突或降级错误应先停止并保留已有账号环境。

只读检查与可选本机备份（在 `facebook_local.py shell` 配置过的终端中）：

```text
adb -s emulator-5554 shell dumpsys package com.facebook.katana
adb -s emulator-5554 shell pm path com.facebook.katana
adb -s emulator-5554 shell sha256sum <上一条返回的APK路径，去掉package:>
adb -s emulator-5554 pull <APK路径> <本机私有备份目录>/base.apk
```

如果返回多个 split APK，逐个记录/备份并使用 `adb install-multiple`；单文件使用 `adb install`。安装、替换的具体命令见平台文档；`-r` 仅用于已经确认同签名的替换。不得把 APK、备份或登录状态放入源码提交。当前代码使用匹配 Frida 和公开 CA 处理 HTTPS，不要求重复历史手工 APK 补丁流程。

## 首次、自动与日常步骤

| 阶段 | 自动部分 | 人工只需做什么 |
| --- | --- | --- |
| 首次 Windows | `setup.ps1 -InstallOnly` 创建 venv、装固定依赖、生成缺失配置；按部署文档验证 SDK/AVD、CA、Frida | 准备合法 APK、首次 Facebook 登录/验证码；按实际系统安全提示授权 |
| 首次 Mac | `CONFIGURE.command` 配合配置检查 SDK、ARM64 AVD、Frida、公开 CA；详见 Runbook | 取得 ARM64 APK、首次登录；真实 Mac 的安全/信任提示和 GUI 结果 |
| 日常 Windows | `START.cmd` 启动 API/UI/模拟器；`CHECK.cmd` 诊断，`STOP.cmd` 正常停止 | 查看打印的 UI 地址；仅会话过期时重新登录 |
| 日常 Mac | 同一模拟器会话运行 `START.command`；主机/模拟器重启后再运行 `CONFIGURE.command` 恢复运行时挂载 | 保持 API 终端打开；仅按实际提示处理授权 |
| 每次任务 | 目标身份、来源、分页终点、限流、清理和产物校验 | 仅服务报告需登录/验证或目标权限不明时检查 GUI |

SDK 的 `platform-tools` / `emulator` 必须可发现；优先设置本机 JSON 的 `sdk_root`、`avd_home`，或使用平台文档中的 ANDROID_SDK_ROOT/PATH。`.venv/.../python facebook_local.py shell` 可进入按本机配置加载路径的终端。

公开 CA 在各机独立生成，不复制 CA 私钥。Windows 使用 `Ensure-FacebookMitmCertificate`，Mac 使用 `macos/prepare_device.py --install-ca` 或 CONFIGURE。Android 用户证书安装不等于 system/Conscrypt 信任已生效；脚本输出和真实解密请求共同作为证据。宿主机浏览器不走抓包代理时无需额外导入系统信任。

Node.js 不是抓取服务器、Python 客户端或日常 UI 的运行依赖；只有 Node fetch 示例和 JavaScript 检查需要 Node（示例采用 22+）。JDK/Build-Tools 仅在构建可选 UI Helper 或使用签名检查工具时需要。

## 已有真实验收与限制

五类入口是普通数字 posts、share/p、pfbid posts、群组 posts/permalink、Reel/share/r。示例地址与结果解释见 [examples](examples/README.md)。用户此前反馈五类均成功；本轮历史自动记录中有普通帖子 240、群组 27、pfbid 425、Reel 2 个用户的成功样本。不同任务日期/账号会改变结果，不保证示例永久公开。

2026-09-17 冷启动恢复后的最后任务 `fb_89a636efe59a48a7` 一次尝试完成 Reel 2 个用户，目标与来源严格匹配、到达终点，随后 worker idle、lease available、代理恢复。完整过程及失败记录见 [验证记录](VALIDATION_2026-09-16.md)。本次文档整理不重复提交这些真实 URL。

页面早就绪探测未获得稳定的净收益，Windows `EnableAdaptiveTargetWait` 保持显式试验开关，普通 API 默认不开启，Mac 执行器不受该开关影响。保留默认 URL/时间戳等待和严格目标校验；不能把它宣传为已实现固定比例提速。

真实 Apple Silicon Mac 的 Facebook 登录、证书运行时信任和完整抓取仍需接收方实机确认；macOS CI 只能证明源码/合约检查。不能用 `/health` 返回 200 代替任务完成与下载验证。常见 ADB、CA、上游代理、端口、query-not-triggered 故障按平台文档处理，不通过放宽校验把失败变成成功。
