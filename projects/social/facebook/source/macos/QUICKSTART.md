# Facebook：Mac 安装和检查

这份说明适用于 Apple Silicon（M1～M4）。先说一个版本差异：**旧项目有 CONFIGURE.command、CHECK.command、START.command 三个启动脚本，但当前总仓库没有收录。** 所以旧版“一键安装”不能直接照做。下面先写当前仓库里确实存在的入口，再保留后面 Android、网络和 Frida 的手动配置资料。

## 1. 先准备本机环境

1. 安装 Python 3.12/3.13 和 [Android Studio](https://developer.android.com/studio)。Android SDK 需要 platform-tools、emulator、command-line tools、Android 35 和 **Google APIs ARM64** 镜像。M 系列 Mac 不要选 x86_64。
2. 进入 `projects/social/facebook/source/`。先安装 Python 依赖，检查基础配置：

   ```bash
   bash setup.sh --install-only
   bash setup.sh --check
   ```

3. 按下面的「ARM64 Android」「采集证书与 Frida」章节配置自己的 AVD，安装合法获得的 Facebook APK，并在模拟器内**人工登录**。
4. 确认设备、证书和 Frida 都正常后，运行 `bash setup.sh` 启动本地服务，再做一条简单帖子的小样本测试。

**`setup.sh` 不负责一键安装和配置所有 Android 组件。** 旧工程里自动创建 AVD、安装证书和启动 Frida 的步骤，不能直接套用到这份发布副本。真机全链路尚未重新验收。

如果要追查原工程当年的配置过程，可以看 [历史 Mac 部署记录](CODEX_DEPLOYMENT_RUNBOOK.md)；其中的 .command 脚本命令在当前仓库里不可执行。

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

原工程的自动配置方案曾由 `facebook_setup.py` 使用 root ADB 启动兼容的 server；当前仓库没有收录对应的 .command 一键脚本。若是手动执行上述安装步骤，使用 `-D` 后台启动并读取 PID；不要运行无参数的前台命令占住终端：

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

运行 `bash setup.sh` 并确认服务启动后，在另一个终端按配置端口检查本地 API：

```sh
API_PORT="$(.venv/bin/python -c 'import facebook_local; print(facebook_local.configuration()["port"])')"
curl -fsS "http://127.0.0.1:$API_PORT/health"
curl -fsS "http://127.0.0.1:$API_PORT/v1/capabilities"
```

预期 `service=ready`、`mode=primary-standby-serial`；`capture_pcap_supported=true` 表示 Mac 已接入 PCAP 实现，设备 root/tcpdump 前置仍需验证。还需要跑 `bash setup.sh --check`、真实小帖任务，并在模拟器重启后再次验证，才能说这台 Mac 的环境已经准备好。请选已确认名单非空的公开小帖作为成功样本，要求 `completion_scope=terminal`、`has_next_page=false`、严格目标/来源匹配、完整性允许完成，且 CSV 至少有一条带 `user_id` 的数据行；仅首屏用户或 HTTP 200 不算终点。真实零反应或身份受限的空名单是另一类边界，应按 [API 示例](../examples/README.md) 核对 empty lists 证据，不能冒充非空样本的验收。请保留退出码和脱敏诊断；不分享 Cookie、CA 私钥或模拟器磁盘。

Windows 已验证的 Facebook 版本、APK 来源边界和证书经验见 [稳定基线](../STABLE_BASELINE.md)；x86_64 APK 不能直接作为 Mac ARM64 基线。已有 Codex 的配置、账号与工具接手见 [Codex 指南](../CODEX_PROJECT_GUIDE.md)。

正常停止先取消任务，等 job/batch 完成并清理传输进程，再在运行 `bash setup.sh` 的终端按 Ctrl+C。升级前备份三个本机 JSON 配置到仓库外私有目录，确认没有未完成任务后再 fast-forward 更新代码；不要删除 AVD、`.setup-cache` 或运行证据。更新后运行 `bash setup.sh --install-only` 和 `bash setup.sh --check`，重新检查 Android、证书及 Frida，再做小样本验证。

旧 `facebook_macos.py` 是独立历史启动/模拟工具，使用它的配置不会自动改变新控制台的根目录配置。新部署请按本指南准备设备并通过 `bash setup.sh` 启动本地服务；旧 START.command 未提供。
