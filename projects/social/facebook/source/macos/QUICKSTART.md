# M4 Mac 首次安装与验收

本指南的本轮代码/验收记录见 [../VALIDATION_2026-09-16.md](../VALIDATION_2026-09-16.md)；[../CURRENT_STATUS_2026-09-09.md](../CURRENT_STATUS_2026-09-09.md) 是历史快照。本指南描述可执行安装流程，不代表已经在接收者 M4 上完成真实 Facebook 深度抓取验收。

本指南面向 Apple Silicon（M1–M4），在自己的账号下运行。当前 Python 采集执行器正在跨平台验收；Windows 模拟与 GitHub macOS 主机测试不能替代 M4 上的 Android、Facebook 与证书验收。不要把 Windows AVD 复制到 Mac。

若由另一位 Codex 代为配置，请让它从仓库根目录逐项执行 [Codex 部署与验收 Runbook](CODEX_DEPLOYMENT_RUNBOOK.md)，不要只把本页概览当成完成证据。

## 接收方先取这个分支（2026-10-04 交接）

本次可部署源码在 **`codex/facebook-maintenance-validation`** 测试分支；不要直接下载默认分支后误以为包含这些修复。[打开交付分支](https://github.com/taoduo0428/facebook-reaction-capture/tree/codex/facebook-maintenance-validation)。当前交付属于代码/本地测试/Windows与macOS CI已验证，接收方真实Mac/Android/Facebook业务待验收。

首次取得代码时，在准备放项目的目录执行（目标文件夹必须尚不存在）：

```sh
git clone --single-branch --branch codex/facebook-maintenance-validation \
  https://github.com/taoduo0428/facebook-reaction-capture.git facebook-mac-deploy
cd facebook-mac-deploy
git rev-parse HEAD
bash CHECK.command
```

记录提交号。私有仓库需要接收方已有读取权限；不要把密码或Token贴进日志。也可在上述分支页面选择 Code → Download ZIP，再解压到新的可写目录；不要覆盖旧项目。

如果这台Mac已配好基础环境，先按[部署Runbook](CODEX_DEPLOYMENT_RUNBOOK.md)检查现有SDK路径、Python、AVD架构、Facebook登录、CA和Frida，复用它自己的配置和账号。不要复制Windows的`.venv`、AVD、APK、CA私钥或账户文件；也不要先卸载Facebook或重建模拟器。缺少首次配置时再执行下文的 `bash CONFIGURE.command`；已经就绪且同一模拟器会话中用 `bash START.command`。主机/模拟器重启后的证书挂载与Frida恢复按Runbook处理。

接收方请在本机完成并记录：Reaction小帖、Shares单独采集、Reaction+Shares组合、需要时的PCAP，以及正常取消后的进程/代理/lease状态。成功必须核对目标来源、查询触发、分页终点、可见范围及产物；单次成功不代表所有URL/账户都可用。PCAP依赖设备已有`su 0`和`/system/bin/tcpdump`；capability=true只表示代码支持，不代表设备前置已通过。

回传时只需提交号、系统/架构/工具版本、job_id、退出码及脱敏的状态和诊断摘要。账号、Cookie、Token、CA私钥、原始抓包、用户CSV和模拟器数据留在接收方本机。

## 1. 推荐：一键配置

第一次先安装 [Python 3.12 或 3.13](https://www.python.org/downloads/macos/)（官方 universal2 安装包）与 [Android Studio](https://developer.android.com/studio)。代码下限是 Python 3.12，3.12/3.13 是当前验收版本。打开 Android Studio → SDK Manager → SDK Tools，勾选 **Android SDK Command-line Tools (latest)**；JDK 可使用 Android Studio 自带的运行时。随后：

1. 下载仓库 ZIP，解压到自己的可写目录。
2. 把合法取得的 Facebook **Android ARM64 APK** 放入仓库的 `setup-input` 文件夹（没有就创建；拆分包的全部 APK 一起放入）。已有 Facebook 安装会保留。
3. 若 ZIP/文件系统没有保留执行权限，先在仓库根目录运行 `chmod +x CONFIGURE.command CHECK.command START.command setup.sh macos/start_facebook_workers.sh`；也可以始终用 `bash CONFIGURE.command`。
4. 双击 **CONFIGURE.command**。第一次按官方提示阅读并接受 Android SDK 许可；等待镜像下载与模拟器启动。
5. 在模拟器中手动登录 Facebook、处理验证码。配置入口完成后自动打开本地页面，在页面中每行粘贴一个帖子 URL。

默认配置适合新加坡直连，无需填写代理、端口、Frida 版本或模拟器名称。工具会初始化 `facebook.local.json`、`facebook_workers.json` 和 `facebook.setup.local.json`；已有文件不覆盖。虚拟环境固定安装 `mitmproxy==12.2.3`、`frida==17.17.0`、`frida-tools==14.10.4` 和 `requests==2.32.5`，依赖或 SDK 缺失时配置会停止并保留现有设备。

下载的 `.command` 如果无法双击，在终端进入仓库目录执行一次：

```sh
bash CONFIGURE.command
```

CONFIGURE 会校验/安装缺失的核心 SDK 组件、只创建缺失的 ARM64 AVD、校验官方 Frida 下载、安装公开采集证书、以 daemon 方式启动匹配的 Frida 服务，并设置“Facebook 链接用 Facebook 打开”。它不会自动安装或构建可选 UI Helper；需要 Helper 时另行安装 Build-Tools 35.0.0 并执行构建脚本。失败时按提示修复后再次运行，已有设备和账号不重建。它不自动登录，不保证当前账号能看到每个帖子的用户名单。

同一次模拟器运行期间，后续双击 **START.command** 即可。**Mac 或模拟器重启后重新双击 CONFIGURE.command**，恢复运行时证书挂载与 Frida 服务。终端需保持打开。下面第 3、4 节是高级手动配置参考；一键配置完成后无需重复执行。

特殊情况才需编辑 `facebook.setup.local.json`：`facebook_apks` 可填写 APK 的完整路径数组；无法访问 GitHub 时，将自己校验过的 Android ARM64 Frida server 完整路径填入 `frida_server`，并设置 `download_frida: false`。不要把本机配置、APK、下载缓存或证书私钥交给其他人；分享 GitHub 仓库即可。

## 2. 网络和端口

新加坡直连 Facebook 的电脑使用 `facebook.local.json` 中的默认配置：

```json
"network": {"mode": "direct", "upstream_url": ""}
```

无需 Clash、小火箭或 7897 端口。mitmproxy 是本机采集代理，直接连互联网，不是翻墙代理。如果确需上游 HTTP 代理，改为 `{"mode":"upstream","upstream_url":"http://127.0.0.1:你的端口"}`；暂不支持嵌入用户名密码。

`port` 是网页/API 的本机监听端口；每个 Worker 的 `capture_port` 是流量采集端口，`native_port` 为独占预留端口，`serial` 中的偶数是模拟器控制端口。首次生成配置会避开已占用的 API/capture/native 端口；这不是永久端口预约，之后仍需检查冲突。已有配置不自动更改；冲突时修改对应 JSON。均不需要公网端口或路由器转发。

## 3. ARM64 Android

Android Studio SDK Manager 安装 platform-tools、emulator、command-line tools、platforms android-35，以及 **Android 35 Google APIs ARM64-v8a** 镜像（不要用禁止 root 的 Google Play 镜像）。只有构建可选 UI Helper 时才需要 build-tools 35.0.0。M4 不使用 x86_64 镜像。

Device Manager 创建 AVD，名称与根目录 `facebook_workers.json` 中的 `avd_name` 完全一致，默认 `fb-macos-api35-worker-01`。建议 1080×1920、4 核、3GB RAM。配置 `sdk_root` 为自己的 SDK 目录（通常 `~/Library/Android/sdk`，JSON 中可以使用完整路径）。

在项目目录运行 `.venv/bin/python facebook_local.py shell`，让终端加载本机 SDK/AVD 路径。启动模拟器：

```sh
emulator -avd fb-macos-api35-worker-01 -port 5554 -writable-system -no-snapshot
```

安装合法取得的 Android ARM64 Facebook APK，并在模拟器内人工登录。多 APK 包使用 `adb install-multiple`。不要把桌面浏览器登录直接当作模拟器登录。

## 4. 采集证书与 Frida

从 [Frida Releases](https://github.com/frida/frida/releases) 取得与 `.venv/bin/frida --version` 一致的 `frida-server-17.17.0-android-arm64.xz`，解压为 Android ARM64 ELF。项目当前固定 Frida 17.17.0；不要下载 darwin server 或 x86_64 server。配置入口会校验官方 SHA-256。

```sh
adb -s emulator-5554 root
.venv/bin/python macos/prepare_device.py --serial emulator-5554 \
  --frida-server "/完整路径/frida-server-17.17.0-android-arm64" \
  --install-ca
```

`prepare_device.py --frida-server` 只安装并校验 server，不负责启动它；`--install-ca` 只推送公开 CA，不推私钥。流程会校验 Android 15 Conscrypt 和应用 mount namespace。初次初始化 mitmproxy CA 会留在本机 `~/.mitmproxy`。挂载随模拟器重启失效，需要重新执行证书安装并验证。

正常使用 CONFIGURE 时，`facebook_setup.py` 已用 root ADB 按 daemon 模式启动兼容的 server。若是手动执行上述安装步骤，使用 `-D` 后台启动并读取 PID；不要运行无参数的前台命令占住终端：

```sh
adb -s emulator-5554 root
adb -s emulator-5554 shell /data/local/tmp/frida-server --version
adb -s emulator-5554 shell /data/local/tmp/frida-server -D
adb -s emulator-5554 shell pidof frida-server
```

保持 Facebook 已打开且已登录，然后启动页面。当前 Mac 执行器使用 ADB XML 滚动，不依赖 UI Helper 的 native scroll。`prepare_device.py` 还会检查可选 UI Helper；若仅此项失败，记录为非阻断缺项即可，不要把它写成所有前置检查通过。

## 5. 实际验收

先从 [浏览器样本](../FACEBOOK_BROWSER_SAMPLES.md) 选择名单可见的帖子，用接收者自己的账号检查 Reaction 列表。网页可见不代表 Android 一定可见。

在本地页面每行粘贴一个 URL，先单条低频验证。Mac 已接入 Reaction、Shares（独立或顺序组合）、CSV、解析报告、MITM、进度及可选 Android 内 PCAP。PCAP 要求该设备已有 su 0 和 /system/bin/tcpdump；不会安装工具、重启 ADB 或使用 Mac 宿主机 sudo。代码/契约通过与原生 Mac 实机验收分开记录。成功必须同时有严格目标匹配、同一来源帖子、ALL 列表分页终止与完整性校验；没有增长或只显示好友姓名会保留为部分/受限结果。不要在限流后重复提交。

启动 `START.command` 后，在另一个终端按配置端口检查 loopback API：

```sh
API_PORT="$(.venv/bin/python -c 'import facebook_local; print(facebook_local.configuration()["port"])')"
curl -fsS "http://127.0.0.1:$API_PORT/health"
curl -fsS "http://127.0.0.1:$API_PORT/v1/capabilities"
```

预期 `service=ready`、`mode=primary-standby-serial`；`capture_pcap_supported=true` 表示 Mac 已接入 PCAP 实现，设备 root/tcpdump 前置仍需验证。通过 CHECK.command、真实小帖任务和模拟器重启后再验收，才可判断 M4 部署成功。请选已确认名单非空的公开小帖作为成功样本，要求 `completion_scope=terminal`、`has_next_page=false`、严格目标/来源匹配、完整性允许完成，且 CSV 至少有一条带 `user_id` 的数据行；仅首屏用户或 HTTP 200 不算终点。真实零反应或身份受限的空名单是另一类边界，应按 [API 示例](../examples/README.md) 核对 empty lists 证据，不能冒充非空样本的验收。请保留退出码和脱敏诊断；不分享 Cookie、CA 私钥或模拟器磁盘。

Windows 已验证的 Facebook 版本、APK 来源边界和证书经验见 [稳定基线](../STABLE_BASELINE.md)；x86_64 APK 不能直接作为 Mac ARM64 基线。已有 Codex 的配置、账号与工具接手见 [Codex 指南](../CODEX_PROJECT_GUIDE.md)。

正常停止先取消任务并等待 job/batch 到达终态和 transport cleanup，再在 `START.command` 终端按 Ctrl+C。升级前备份三个本机 JSON 配置到仓库外私有目录，确认没有未完成任务后再 fast-forward 更新代码；不要删除 AVD、`.setup-cache` 或运行证据。更新后重新运行 `bash setup.sh --install-only`、`CHECK.command`、`CONFIGURE.command`，再做 bounded sample。

旧 `facebook_macos.py` 是独立历史启动/模拟工具，使用它的配置不会自动改变新控制台的根目录配置。新部署使用本指南和 START.command。
