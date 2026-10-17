# Facebook：Mac 旧适配器说明

`facebook_macos.py` 和 `start_facebook_workers.sh` 是早期的 Mac 设备/传输适配层，不是现在推荐的完整本地 API 入口。新同学请先看 [Mac 安装说明](QUICKSTART.md)。

当前总仓库有 `setup.sh`，但没有原工程提到的 `CONFIGURE.command`、`CHECK.command`、`START.command`。旧“一键部署”的命令不能照搬。需要自己准备 Android ARM64、证书、Frida 和账号，具体看快速入门页。

下面内容保留了旧适配器的行为和配置，主要用来查历史问题。不要把它的配置和新的本地 API 配置混用。

## 统一入口 Shares / PCAP（2026-10-04 增量）

根目录统一 API/runner 已接入 Shares 独立采集、Reaction→Shares 串行组合，以及可选 PCAP；本页下面的旧 transport 适配器仍不是这些功能的入口。

- Shares 只点击已验证目标的安全分享计数入口，不点击 Share 发布按钮；要求新鲜来源查询与终页证据，保留账号可见范围和总数差额。组合模式验证 Reaction 后重新绑定同一目标；UI 歧义、查询未触发、来源不符或开放尾页均失败保留证据。
- PCAP 使用 **Android 设备内**已有的 `/system/bin/tcpdump`，由指定 serial 的 Mac 原生 adb 控制。须已有 `su 0`，不执行 Mac sudo，不使用 Windows exe/pktmon，不更换 APK/镜像/Frida 或自动重启 ADB。文件按本次 run_id 隔离，停止前确认 PID/命令身份，停止后拉取并校验 PCAP 头和 SHA-256。失败保留远程/本地证据及清理保护。
- Apple Silicon 要使用对应 ARM64 Android 镜像/APK/Frida；这些运行时前置与本次代码支持是两回事。原生 Mac/真实 Facebook 验收尚未在本轮执行，不能以本地模拟、CI 或 capability=true 宣称实机通过。

## 当前能力边界

已实现：

- 校验已配置 Mac AVD 的 worker id、serial、AVD 名和端口互不冲突；
- 为 Apple Silicon (`arm64-v8a`) 和 Intel (`x86_64`) 提供环境诊断；
- 启动指定 AVD，等待 Android boot 完成；
- 旧适配器恢复 `7897` 和各自 capture port 的 `adb reverse`；
- 旧适配器固定设置 Android 全局代理 `127.0.0.1:7897`；新加坡直连请按 QUICKSTART.md 设置 `network.mode: direct`，不要直接运行本页旧 transport 命令；
- 检查 `com.facebook.katana` 是否安装；
- 在 Windows 上只生成结构化命令，不执行 Mac 或 Android 命令。

旧适配器未实现：它不会移植 Windows 的 `run_facebook_capture.ps1`，其 CLI 会明确输出
`deep_capture_runner: not-implemented-for-macos`。这只描述旧适配器，不描述统一 API；
新入口及其实际证据边界以 [CODEX_DEPLOYMENT_RUNBOOK.md](CODEX_DEPLOYMENT_RUNBOOK.md) 为准。

## 1. Windows 上模拟检查

在项目根目录执行：

```powershell
python macos/facebook_macos.py doctor --simulate
python macos/facebook_macos.py simulate --worker all
python -m unittest tests.test_facebook_macos
```

`executed` 必须为 `false`。这只验证配置、架构识别和命令/端口隔离，不会操作
当前 Windows 模拟器。

## 2. Mac 环境准备

旧适配器仅需要 Python 3（统一项目建议 3.12+）和 Android command-line tools；新控制台的完整前置、依赖和人工登录步骤请回到 [QUICKSTART.md](QUICKSTART.md)。从 ZIP 解压后先恢复 shell 执行权限：

```sh
export ANDROID_SDK_ROOT="$HOME/Library/Android/sdk"
export PATH="$ANDROID_SDK_ROOT/emulator:$ANDROID_SDK_ROOT/platform-tools:$PATH"
chmod +x setup.sh macos/start_facebook_workers.sh
python3 macos/facebook_macos.py doctor
```

Apple Silicon 安装 ARM 镜像：

```sh
sdkmanager "platform-tools" "emulator" \
  "platforms;android-35" "system-images;android-35;google_apis;arm64-v8a"
```

Intel Mac 安装 x86_64 镜像：

```sh
sdkmanager "platform-tools" "emulator" \
  "platforms;android-35" "system-images;android-35;google_apis;x86_64"
```

不要在 Apple Silicon 上创建 x86_64 AVD 作为正式运行方案。Rosetta 不能代替
Android Emulator 的原生虚拟化兼容性验证。

## 3. 创建两个互相隔离的 AVD

先按主机架构设置镜像标识。以下以 Apple Silicon 为例：

```sh
IMAGE="system-images;android-35;google_apis;arm64-v8a"
printf 'no\n' | avdmanager create avd -n fb-macos-api35-worker-01 -k "$IMAGE"
printf 'no\n' | avdmanager create avd -n fb-macos-api35-worker-02 -k "$IMAGE"
cp macos/facebook_workers.macos.example.json macos/facebook_workers.macos.json
```

两台 AVD 分别固定使用 `emulator-5554`/`emulator-5556` 和
`18081`/`18082` capture port。`worker-03` 默认禁用。

配置只描述运行槽位，不保存账号信息。首次启动后应分别在两台模拟器中手工安装
Facebook 并手工登录；不要复制 Windows AVD 或另一台 AVD 的数据目录。

## 4. 启动与 transport

```sh
./macos/start_facebook_workers.sh start --worker worker-01
./macos/start_facebook_workers.sh start --worker worker-02
./macos/start_facebook_workers.sh transport --worker all
```

这一旧 transport 适配器固定使用 `7897`，不读取根目录的 `network.mode`。新部署请按 [QUICKSTART.md](QUICKSTART.md) 手动准备设备，并从根目录运行 `bash setup.sh`；旧自动配置脚本未收录。旧适配器的 capture port 是每个 worker
独占的预留端口，供后续 Mac 深抓 runner 使用。`native_port` 当前只做全局隔离校验，
本适配层不会监听该端口。

## 5. 真实 Mac 验收

以下结果都成功后，才能称为 Mac 启动/transport 已实机通过：

```sh
python3 macos/facebook_macos.py doctor
adb devices
adb -s emulator-5554 shell getprop sys.boot_completed
adb -s emulator-5556 shell getprop sys.boot_completed
adb -s emulator-5554 reverse --list
adb -s emulator-5556 reverse --list
adb -s emulator-5554 shell settings get global http_proxy
adb -s emulator-5556 shell settings get global http_proxy
adb -s emulator-5554 shell pm path com.facebook.katana
adb -s emulator-5556 shell pm path com.facebook.katana
```

还必须另行实测 macOS Hypervisor、Keychain/CA 权限、Frida server 架构、mitmproxy
证书信任、Facebook 登录以及一次真实的严格目标抓取。Windows 模拟、GitHub macOS CI
和 `executed: false` 命令计划无法证明这些项目；当前也不能据此声称完整抓取链路已经
支持 macOS。只有日期化 M4 证据同时满足 runbook 的 host/device/CA/Frida/终点 CSV
清单后，才能更新验收结论。
