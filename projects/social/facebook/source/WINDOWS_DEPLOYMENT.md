# Facebook：Windows 安装说明

第一次在 Windows 上使用，按下面顺序来：**安装 Python → 配好 Android → 人工登录 Facebook → 启动本地 API**。别跳过模拟器和登录步骤，只把网页打开并不代表采集功能已经准备好。

仓库没有提供 Facebook APK、账号、Cookie、证书私钥或模拟器磁盘，这些必须在自己的电脑上准备。以前测试过的环境记在 [稳定基线](STABLE_BASELINE.md)，供对照，不要求你先删掉现有环境。

[2026-09-16 测试记录](VALIDATION_2026-09-16.md)是历史记录，不能代替你自己的设备验收。

## 1. 获取代码与 Python

下载 GitHub 仓库 ZIP（或克隆后只保留本项目目录）到可写路径，例如 `C:\facebook-reaction-capture`。不要把整个历史工作目录、`runs/`、`api-jobs/`、`api-batches/` 或本机配置上传到 GitHub。

安装 Python 3.12 或 3.13 x64，并启用 PATH。代码下限是 Python 3.12；当前 CI/本机验收覆盖 3.12/3.13。随后在项目根目录执行：

```powershell
Set-Location C:\facebook-reaction-capture
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -InstallOnly
```

`setup.ps1` 会创建 `.venv`、按 `requirements.txt` 安装固定版本并运行 `pip check`，不会覆盖已有 `facebook.local.json` 或 `facebook_workers.json`。当前固定依赖是 `mitmproxy==12.2.3`、`frida==17.17.0`、`frida-tools==14.10.4` 和 `requests==2.32.5`。`-InstallOnly` 只做虚拟环境、依赖和本机配置初始化，不启动 API、UI 或模拟器。依赖安装失败时修复网络或 pip 配置后重试。

## 2. SDK、AVD 与本机配置

在 Android Studio SDK Manager 安装（仅安装 SDK 不会替代 PATH/配置检查）：

- Android SDK Platform-Tools、Android Emulator、Command-line Tools (latest)
- Android SDK Platform 35
- Android 35 Google APIs **x86_64** system image（需支持 `adb root`；不要使用 Google Play 镜像）

创建一个专用 AVD，例如 `fb-x86_64-api35`，建议 1080x1920、4 CPU、3072 MB。不要覆盖其他账号的 AVD。首次运行 `START.cmd` 会生成 `facebook.local.json` 和 `facebook_workers.json`；检查并按本机路径修改 `sdk_root`、`avd_home`、`serial`、端口和 `network`。在首次启动前，`sdk_root` 下必须实际存在 `platform-tools\adb.exe` 和 `emulator\emulator.exe`；也可以设置 `ANDROID_SDK_ROOT`/`ANDROID_HOME`，但配置值优先。直接访问 Facebook 时使用：

```json
"network": {"mode": "direct", "upstream_url": ""}
```

需要上游 HTTP 代理时才配置 `mode: upstream` 和自己的 `upstream_url`（例如 `http://127.0.0.1:7897`）；工具不会自动启用 TUN，也不会修改系统代理。

## 3. Facebook、Frida、CA 与可选 Helper

使用自己合法取得、与 AVD ABI 匹配的 Facebook APK。启动 AVD 后手动安装并登录：

```powershell
adb -s emulator-5554 install C:\path\to\facebook.apk
# 拆分 APK 使用同一版本的完整 x86_64 集合；文件名以实际发行包为准：
adb -s emulator-5554 install-multiple C:\path\to\base.apk C:\path\to\split_config.x86_64.apk
```

不要把 `arm64-v8a` split、不同版本的 split 或桌面浏览器登录状态混入 x86_64 AVD。安装前可用 `apkanalyzer manifest application-id <apk>` 确认每个 APK 都是 `com.facebook.katana`。

主机 Python 客户端和 Android `frida-server` 必须都是 17.17.0，并且 server 是 Android x86_64 ELF。先确认版本，再从官方 Frida Releases 下载并校验文件；不要使用 Windows/macOS/ARM64 server：

```powershell
.\.venv\Scripts\frida.exe --version
adb -s emulator-5554 root
adb -s emulator-5554 push C:\path\to\frida-server-17.17.0-android-x86_64 /data/local/tmp/fb-fs17
adb -s emulator-5554 shell chmod 755 /data/local/tmp/fb-fs17
adb -s emulator-5554 shell /data/local/tmp/fb-fs17 --version
adb -s emulator-5554 shell pidof fb-fs17
```

`run_facebook_capture.ps1` 在需要 SSL unpin 时会用 root ADB 以 daemon 方式启动 `/data/local/tmp/fb-fs17`，并等待 `pidof fb-fs17`。不要直接运行不带参数的 server 命令：它会占用前台窗口；若需手动启动，使用 `adb -s emulator-5554 shell su 0 /data/local/tmp/fb-fs17 -D` 后再次执行 `pidof`。若设备不支持 `adb root`，不要绕过检查或把该设备写成已验收。

采集证书使用本机 mitmproxy 初始化的公开证书；私钥只能留在 `%USERPROFILE%\.mitmproxy`。需要构建仓库 UI Helper 时才安装 JDK、`keytool`、`javac` 和 Build-Tools 35.0.0，并使用 `android-ui-dump-helper\build-helper.ps1`；默认 Windows 采集链路可使用既有 ADB/UI 路径，Helper 缺失不应被伪装成整机验收通过。

首次安装 CA 时，先用 `.\.venv\Scripts\mitmdump.exe --listen-host 127.0.0.1 --listen-port 18089` 初始化本机证书，确认启动后 Ctrl+C 结束这个初始化进程。安装 OpenSSL 并确认 `Get-Command openssl` 可找到它。任务空闲时，用 `.\.venv\Scripts\python.exe facebook_local.py shell` 打开已加载项目 SDK/AVD 配置的 PowerShell，再执行下面的步骤；serial、AVD 和端口取本机 `facebook_workers.json`，下列数值是首次安装示例：

```powershell
. .\facebook_transport.ps1
. .\facebook_certificate.ps1
.\start_facebook_stable.ps1 -Serial emulator-5554 -AvdName fb-x86_64-api35 -CapturePort 18081 -NativePort 18766 -WritableSystem -SkipFacebookUiCheck
adb -s emulator-5554 root
Ensure-FacebookMitmCertificate -Serial emulator-5554
```

预期返回 `system_installed=true` 和 `conscrypt_runtime_installed=true`。已有模拟器进程不会因传入 `-WritableSystem` 自动重启；若它最初没有启用可写系统且证书安装需要 remount，请先正常停止配置的 AVD，再用上面的启动命令启动，保留原磁盘和登录状态。Helper 的构建与可选安装命令见 [README 的首台模拟器配置](README.md#首台模拟器配置首次需要完成)；公开 CA 安装不依赖 Helper。

## 4. 启动、检查与 API

先运行检查：

```powershell
.\CHECK.cmd
```

日常一键启动：

```powershell
.\START.cmd
```

`START.cmd` 调用 `START_API_AND_EMULATOR.cmd`，读取本机配置，复用已健康的项目 API/模拟器；缺失时启动配置的 AVD，再以隐藏窗口启动 API 和 v2 UI。命令窗口会打印 UI 地址，通常是 `http://127.0.0.1:8766/`；它不会自动打开浏览器，请手动访问。API 地址取 `facebook.local.json` 的 `port`，默认 `http://127.0.0.1:8765/health`。`CHECK.cmd` 只做 doctor 检查，不安装、登录或启动采集。验证：

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
```

预期 `service: ready`，并显示 `mode: primary-standby-serial`。调度器一次只运行一个 active worker；其他启用 worker 是确认设备故障后的 standby，不是并行租户，也不代表已经登录或验收。API 客户端流程、状态、artifact 和取消语义见 [API_USAGE.md](API_USAGE.md)。

## 5. 低风险验收与运行

先用当前账号确实可见 Reaction 名单的小帖子做单条 bounded sample，再逐步扩大范围。页面或 API 返回 HTTP 成功、首屏用户或空名单，都不能单独证明分页终止。全量任务必须看到 `completion_scope=terminal`、目标匹配、完整性通过和 `has_next_page=false`；限流、open-tail 和取消保留其实际状态。

手动 API bounded sample 示例（省略 `reaction_scroll_limit` 才是全量终点语义）：

```powershell
$body = @{ post_url = 'https://www.facebook.com/example/posts/1'; reaction_scroll_limit = 20 } | ConvertTo-Json
$job = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/v1/jobs -ContentType 'application/json' -Body $body
do {
  Start-Sleep 2
  $state = Invoke-RestMethod "http://127.0.0.1:8765/v1/jobs/$($job.job_id)"
} while ($state.status -in @('queued','running','cancelling'))
$state
```

取消后继续读取状态，直到 `cancelled`；仅凭取消接口的 HTTP 200 不代表进程已经停止。`transport_cleanup_state` 为失败或 `transport_cleanup_error` 非空时，capture lease 会继续阻塞新任务；先修复清理并确认 `/health` 的 `capture_lease.state` 可用。

用已知名单可见的小帖做安装验收时，还要检查 job 为 `completed`、`completion_scope=terminal`、报告允许完成、严格目标/来源匹配、最后成功页 `has_next_page=false`，以及 `facebook-reactor-users.csv` 至少有一条带 `user_id` 的数据行。HTTP 200、首屏用户、只有 CSV 表头或页面滚到底部都不能单独证明全量终点。

平台可见性与采集失败分开处理：已到 terminal 的可见名单可能少于页面总数，结果会标记 `complete-terminal-visible-subset`；已验证目标、查询终止且传输和诊断均干净的 friends-only 可见性结果可能返回 `reaction_users: []`。这些结果不表示取得了当前账号不可见的用户，也不能代替上面的非空安装样本。`query-not-triggered`、目标不匹配、解析错误、限流和 open-tail 仍然是失败或部分结果。

## 6. 停止与数据保留

先在页面/API 取消运行中的批次并等待终态，然后执行：

```powershell
.\STOP.cmd
```

STOP 只按 `facebook_workers.json` 识别并停止本项目 API/UI、采集辅助进程、watchdog 和配置中的 AVD；它会在发送 emulator kill 前重新校验 AVD 身份，不停止 ADB server、其他模拟器、系统代理或未知进程。不要使用 `taskkill /IM emulator.exe`、`adb reverse --remove-all` 或删除 AVD 来“清理”。

本机配置和运行证据保留在 `facebook.local.json`、`facebook_workers.json`、`api-jobs/`、`api-batches/`、`runs/`；它们可能含会话或目标数据，只保存在本机。升级前复制配置到仓库外的私有目录，不要 reset/clean 覆盖未提交修改。

## 7. 安全升级

升级前先在页面/API 取消 queued/running 任务，逐个确认终态和 transport cleanup，再运行 `STOP.cmd`。备份 `facebook.local.json`、`facebook_workers.json`（以及存在时的 `facebook.setup.local.json`）到仓库外的私有目录；同时保留 `api-jobs/`、`api-batches/`、`runs/` 和当前登录 AVD。确认 `git status --short --branch` 中没有待保留的源码修改后，再按项目维护者给出的远端提交更新；优先使用 fast-forward，不使用 `git reset`、`git clean` 或删除 AVD 回滚。

更新代码后重新运行 `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup.ps1 -InstallOnly`、`CHECK.cmd`，确认依赖 hash 与 doctor 输出正常，再 `START.cmd`。先做单条低频 bounded sample，随后才重新评估终点 CSV；旧 run 的历史结果不能替代升级后的当前验收。

## 8. 常见故障

| 现象 | 处理 |
| --- | --- |
| `Python 3.12+ required` | 安装 x64 Python 3.12/3.13，确认 `py -3 --version`，再重跑 `START.cmd`。 |
| fresh clone 找不到 Python/ADB/Emulator | 先确认 `py -3 --version`、`where.exe adb`、`where.exe emulator`；在 `facebook.local.json` 设置实际 `sdk_root`/`avd_home`，再运行 `setup.ps1 -InstallOnly` 和 `CHECK.cmd`。不要把某台开发机的绝对路径复制给接收者。 |
| ADB offline/AVD 未找到 | `.\CHECK.cmd`，核对 `sdk_root`、`avd_home`、`serial` 与 Device Manager 的 AVD 名称；不要误判为 Facebook 限流。 |
| API 端口被占用 | 访问配置端口的 `/health`；若不是本项目服务，修改 `facebook.local.json` 的 `port`，不要杀未知进程。 |
| Frida server 前台占住窗口或无 PID | 先 Ctrl+C 结束手动前台进程；确认 `adb root`、`/data/local/tmp/fb-fs17 --version`，再交给 runner 用 `su 0 ... -D`/detached fallback 启动并检查 `pidof`。不要强杀未知 PID。 |
| CA/Conscrypt 校验失败 | 确认本机 `%USERPROFILE%\.mitmproxy\mitmproxy-ca-cert.pem` 是公开证书且 OpenSSL 可用；在任务空闲时用 `Ensure-FacebookMitmCertificate` 重试。不要上传私钥或关闭校验。 |
| 8766 已被占用 | 确认是否为本项目 v2 UI；若是其他服务，停止它或调整 UI 启动参数，保留原服务。 |
| Frida 版本/架构不匹配 | 比较 `.venv\Scripts\frida.exe --version` 与 Android server；重新取得同版本 x86_64 ELF。 |
| 1675004/429 | 停止重复提交，遵守 cooldown，保留部分证据；不要换账号续同一帖子。 |
| visibility-limited 或页面总数多于 CSV | 检查 terminal、目标和完整性字段，保留账号可见范围说明；可见性差异本身不等于限流或失败。 |
| cancel 后 lease uncertain | 查看 job 的 `process_state`、`transport_cleanup_state/error` 和 `/health`；在身份未确认前不要强杀或启动新任务。 |
