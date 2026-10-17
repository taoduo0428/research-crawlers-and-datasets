# Facebook 本地采集工具

这套工具通过 Android 模拟器打开指定帖子，在本机管理任务，读取**当前登录账号可见**的 Reaction 和分享信息。它不会替你注册或登录账号，也不能保证每个帖子都能看到完整名单。

第一次使用先按电脑系统准备环境：

- Windows：先读 [Windows 安装说明](WINDOWS_DEPLOYMENT.md)，仓库提供 `START.cmd`。
- Mac（Apple Silicon）：读 [Mac 安装说明](macos/QUICKSTART.md)。这份仓库有 `setup.sh`，但**没有**旧文档提到的 `CONFIGURE.command`、`CHECK.command`、`START.command`。Mac 的模拟器、证书和真实抓取需要自行验证。

想写程序提交任务，看 [API 说明](API_USAGE.md) 或 [调用例子](examples/README.md)。主要文件是 `facebook_capture_api.py`（本地接口）、`facebook_android_capture.py`（设备采集）和 `analyze_facebook_capture.py`（分析结果）。

[环境参考](STABLE_BASELINE.md)和[旧测试记录](VALIDATION_2026-09-16.md)是当时的情况，不等于你现在这台机器已经验收通过。`runs/`、`api-jobs/`、`api-batches/` 属于本地运行记录，不要把它们当作发布源码上传。

## 平台入口

| 平台 | 推荐入口 | 运行边界 |
| --- | --- | --- |
| Windows 10/11 x64 | [Windows 部署与运行](WINDOWS_DEPLOYMENT.md)，然后 `START.cmd` | Android 35 Google APIs x86_64；主备串行，不是并行租户 |
| Apple Silicon Mac | [Mac 安装说明](macos/QUICKSTART.md)，先用 `bash setup.sh --install-only` 准备本地环境 | Android ARM64、证书和登录仍需手动验收；旧 .command 文件未收录 |

Windows 的逐项命令、Frida/CA/Helper 准备、停止和故障处理见 [WINDOWS_DEPLOYMENT.md](WINDOWS_DEPLOYMENT.md)。macOS 的 Codex 执行清单见 [macos/CODEX_DEPLOYMENT_RUNBOOK.md](macos/CODEX_DEPLOYMENT_RUNBOOK.md)；`macos/README.md` 中的旧适配器仅用于兼容性诊断。

## 目录速览

`facebook_local.py`/`facebook_setup.py` 负责本机配置、依赖和环境诊断；`facebook_capture_api.py` 与 `facebook_api_client.py` 提供本地 API；`facebook_android_capture.py` 是 Android 采集执行器；`web/` 和 `web_v2/` 是页面；`macos/` 保存 Apple Silicon 配置与诊断层。`api-jobs/`、`api-batches/`、`runs/` 是本机运行状态和证据目录，不属于发布源码。

## 系统架构

网页或 Python 客户端把 URL 提交给本地 HTTP API；JobManager 持久化任务并通过 capture lease 保证串行执行。Windows 调用 PowerShell runner，macOS 调用 Python Android runner；执行器负责 ADB 导航、采集代理、分页和清理。解析器随后校验目标、来源及分页终点，生成单帖 CSV；批次汇总以 `job_id` 为帖子边界，同一个用户出现在不同任务时分别保留。Worker 接管必须先确认旧进程停止及传输状态恢复。

## 下载后如何开始

Windows 10/11 x64 使用现有 PowerShell 采集链路；Apple Silicon Mac 使用独立 Python Android 执行器，仍待 M4 实机验收。建议 Python 3.12 或 3.13、16 GB 内存（双模拟器建议 24 GB）。**Mac 先读 [安装说明](macos/QUICKSTART.md)；下面的 START.cmd 和 x86_64 操作只适用于 Windows。**

1. 安装 [Python 3.12 或 3.13](https://www.python.org/downloads/)，勾选 Add python.exe to PATH；安装 [Android Studio / SDK](https://developer.android.com/studio)。代码下限是 Python 3.12，3.12/3.13 是当前验收版本。
2. 解压仓库到自己的可写目录，双击 **START.cmd**（或直接双击 **START_API_AND_EMULATOR.cmd**）。首次自动创建 `.venv`、安装 `requirements.txt`、生成本机配置，成功后在窗口打印 `http://127.0.0.1:8766/`，请手动打开该地址；API/UI 后台窗口隐藏，Android 模拟器窗口保持可见。安装失败会保留提示，修复网络后重试即可。
3. 按下节准备 Android 环境并手动登录 Facebook。页面能打开不代表模拟器已准备好。双击 **CHECK.cmd** 查看 Python、SDK、AVD、代理和端口检查结果。
4. 在页面中每行输入一个 URL，点击“开始抓取”。任务会进入单执行器串行队列；其它启用 Worker 只是主备候选。选择帖子行查看流程、分页、人数和产物；“停止批次”可取消任务。

原 Mac 项目曾使用 CONFIGURE.command / START.command，但这几个脚本**没有包含在当前总仓库里**。目前可用 `bash setup.sh --install-only` 初始化依赖，`bash setup.sh --check` 做基础自检；Android ARM64、Frida、证书和账号登录仍按 [Mac 说明](macos/QUICKSTART.md) 手动准备。确认环境就绪后执行 `bash setup.sh` 启动本地服务。

连续提交多个 URL 时，同一 Worker 会复用已经启动的模拟器，Windows 优先附加已有 Facebook 主进程；进程不存在时才启动，故障恢复仍可能重启应用。每条任务独立建立采集代理、输出文件和目标校验，避免上一帖数据混入。项目导航指定 Facebook 包名，直接进入 Facebook；Mac 配置入口还设置 Android 的 Facebook 链接打开偏好。分页节流、限流冷却和完整性检查仍然保留。

v2 页面和 `/v1/jobs`、`/v1/batches` API 默认执行全量 Reaction 分页，持续到现有 `terminal` 终点逻辑；缺省或 `null` 的 `reaction_scroll_limit` 不会设置有限上限。仅当请求显式提供 `1..300` 的整数时，任务才按 bounded sample 运行并标记为非全量结果。

Windows 手动排错时可给 `run_facebook_capture.ps1` 加 `-ColdStartFacebook`（别名 `-ColdStart`）恢复原先冷启动行为。日常页面批量抓取使用默认热启动，无需设置此参数。

Windows 用 `START.cmd` 启动、`STOP.cmd` 停止。停止前先取消正在运行的任务，等清理结束。Mac 用 `bash setup.sh` 启动本地服务，停止时在该终端按 Ctrl+C。只安装依赖时，Windows 用 `powershell -ExecutionPolicy Bypass -File .\setup.ps1 -InstallOnly`，Mac 用 `bash setup.sh --install-only`。

## 本机配置

首次启动生成 `facebook.local.json` 和 `facebook_workers.json`，这两个文件不进入 Git。

```json
{
  "sdk_root": "",
  "avd_home": "",
  "port": 8765,
  "open_browser": true,
  "network": {"mode": "direct", "upstream_url": ""}
}
```

空路径由 `ANDROID_SDK_ROOT`/`ANDROID_HOME`、Android Studio 默认位置和 `ANDROID_AVD_HOME` 解析；也可改成接收者自己的绝对路径。Windows 的旧配置若没有 `network` 字段会保留原 7897 上游模式，新配置建议显式选择 `direct` 或填写无凭据的 `upstream_url`。配置只影响本项目及其子进程，不修改系统 PATH 或代理/TUN 设置。

首次生成的 Worker 使用 capture `18081`，避开常见的 `8081` 服务冲突。已有 Worker 配置完全保留。每台的 `worker_id`、`serial`、`avd_name`、capture/native 端口必须唯一。API 端口不要和 Worker 端口重复。

## 首台模拟器配置（首次需要完成）

仓库不附带 Android SDK、Facebook APK、账号、证书私钥或模拟器磁盘。以下步骤在你自己的 Windows 环境执行；Facebook 版本和页面布局变化可能需要重新验收。

1. 在 SDK Manager 安装 **Android SDK Platform-Tools、Android Emulator、Android SDK Command-line Tools (latest)、Android SDK Platform 35、Android 35 Google APIs x86_64 系统镜像**。选择可 `adb root` 的 Google APIs 镜像，不能用禁止 root 的 Google Play 镜像。按安装器提示接受 SDK 许可。只有需要构建可选 UI Helper 时才需要额外安装 **Build-Tools 35.0.0**。
2. 在 Device Manager 创建名为 `fb-x86_64-api35` 的 AVD，建议 1080×1920、4 CPU、3072 MB RAM；名称需和 Worker 配置一致。只创建新的 AVD，勿覆盖其他账号的磁盘。
3. 新加坡等可直接访问 Facebook 的环境保留 `network.mode: direct` 即可。需要上游 HTTP 代理时，在 `facebook.local.json` 中设置 `network.mode: upstream` 和自己的 `upstream_url`，例如 `http://127.0.0.1:7897`。工具不会自动启用 TUN。
4. 安装合法取得、与 x86_64 AVD ABI 一致的 Facebook APK 并人工登录。可使用 SDK 的 `adb -s emulator-5554 install 路径.apk`，拆分 APK 用同一版本的完整 x86_64 `install-multiple` 集合；不要混用 arm64 split。账号验证码和设备确认在模拟器内手工完成。
5. 本机 `.venv\Scripts\frida.exe --version` 与 Android 的 frida-server 必须版本相同。本依赖清单使用 17.17.0；从 [Frida Releases](https://github.com/frida/frida/releases) 取得对应 Android x86_64 ELF，解压后用 `adb root`、`adb push` 放到 `/data/local/tmp/fb-fs17` 并 `chmod 755`。采集 runner 会在需要 SSL unpin 时以 daemon 方式启动并检查 `pidof fb-fs17`；不要直接运行无参数 server 占住前台窗口。既有账号环境应先核对版本，勿覆盖正在运行的工具。
6. 如需构建项目的可选 UI Helper：安装 JDK 和 **Build-Tools 35.0.0** 并设置 JAVA_HOME，使 `javac` 和 `keytool` 可用。执行下面的命令生成本机 debug 签名并构建 APK（已有 keystore 不要重新生成）。构建只编译仓库自带的 Java 源码；默认 Mac runner 使用 ADB XML，不依赖 Helper。

```powershell
# 仅在该文件不存在时执行 keytool；不是 Facebook 的签名密钥。
keytool -genkeypair -keystore "$env:USERPROFILE\.android\debug.keystore" -alias androiddebugkey -storepass android -keypass android -keyalg RSA -validity 10000 -dname "CN=Android Debug,O=Android,C=US"
powershell -ExecutionPolicy Bypass -File .\android-ui-dump-helper\build-helper.ps1 -SdkRoot "<your Android SDK root>"
```

7. 初始化自己本机的 mitmproxy CA：执行 `.venv\Scripts\mitmdump.exe --listen-host 127.0.0.1 --listen-port 18089`，确认启动后 Ctrl+C，公开证书位于 `%USERPROFILE%\.mitmproxy`。安装 OpenSSL（Git for Windows 通常附带），保证 `openssl` 可用。不要复制其他人的 CA 私钥；任务使用前还须由证书脚本把公开证书安装并验证到 Android system/Conscrypt store。
8. 从配置启动当前 SDK/AVD 的 PowerShell 环境，安装并验证本机公开 CA；UI Helper 仅在选择该可选路径时构建和安装。可先用 `.venv\Scripts\python.exe facebook_local.py shell` 进入已加载配置的终端，再执行：

```powershell
. .\facebook_transport.ps1
. .\facebook_certificate.ps1
# AVD 必须使用 writable system；已有 AVD 正常关闭后再执行该启动脚本。
.\start_facebook_stable.ps1 -Serial emulator-5554 -AvdName fb-x86_64-api35 -CapturePort 18081 -NativePort 18766 -WritableSystem -SkipFacebookUiCheck
adb -s emulator-5554 root
Ensure-FacebookMitmCertificate -Serial emulator-5554
# 只有已构建可选 Helper 时才执行；默认链路可用 ADB/UI XML。
Ensure-FacebookUiDumpHelper -Serial emulator-5554 -BasePath $PWD.Path
```

Android 15 的系统与 Conscrypt CA 挂载由证书脚本验证，重启后采集启动器会恢复。当前端勾选 PCAP 时，还要求模拟器可执行 `tcpdump`；如果没有安装 tcpdump，先取消 PCAP，CSV、解析报告、MITM 与进度仍可用。

9. 先用一个自己可访问的小帖子做低频验证：

```powershell
.\probe_facebook_reaction_health.ps1 -Serial emulator-5554 -PostUrl "https://www.facebook.com/作者/posts/数字帖子ID" -PostId "数字帖子ID" -ProxyPort 18081 -EnableFridaSslUnpin
```

实际参数以 `Get-Help .\probe_facebook_reaction_health.ps1 -Full` 为准。Probe 需要 `initial-ready`、准确的来源帖子和真实记录。随后在页面提交一条小任务，到达终止页、严格目标匹配并生成 CSV 才算采集链路完成。

## 增加独立 Worker

首台已就绪后，在 `.venv\Scripts\python.exe facebook_local.py shell` 打开的环境中执行 `add_facebook_worker.cmd`，详见 [FACEBOOK_WORKER_BOOTSTRAP.md](FACEBOOK_WORKER_BOOTSTRAP.md)。新增工具从在线源设备复制程序文件，创建独立数据目录；新账号由使用者手动登录，Probe 验收后才启用。每个帖子固定在一个 Worker，不在限流后换账号继续同一任务。

## URL 与完成状态

标准数字帖子、`share/p`、`share/r`、`story.php` 有历史完整任务；普通 `pfbid`、规范 `pfbid` 变化、群组多图、群组 permalink、Reel 和原始 Watch URL 均已有真实 Android `initial-ready` 证据。`videos`、`share/v` 等仍只有解析、代码测试或较弱历史证据。各类 Probe 只证明目标、入口和首屏成功，逐类全量仍需到达分页终点，详见 [FACEBOOK_LINK_SUPPORT.md](FACEBOOK_LINK_SUPPORT.md)。

页面接受 URL 并不保证名单可枚举。已删除、当前账号无权查看、只有媒体身份或页面不展示名单的情况会失败或返回不可枚举信息。完成必须满足目标匹配、来源一致、到达分页终点和完整性校验。`terminal` 表示当前账号可见列表到终点。限流结果保留为部分结果，不等同于完整成功。

## 文件、维护与排错

- 任务记录：`api-jobs/`；批次记录：`api-batches/`；结果：`runs/`；新增模拟器状态：`worker-bootstrap/`。从页面查看具体目录和下载 CSV。原始记录可能含会话信息，仅留在本机。
- 服务不可达：检查启动窗口；8765 被其他服务占用时修改本机配置端口。已有服务仅在识别到本项目 health 时复用。
- 模拟器 offline：检查 SDK/AVD 配置及 Android 启动状态；不要把离线误判为 Facebook 限流。
- capture 端口被占用：先识别占用者，修改对应 Worker 端口；不要杀掉未知服务。
- 1675004：平台限流，保留部分数据并冷却；不要反复提交。冷却按 Worker 记录。
- API 重启后历史任务可加载；排队任务、以及确认旧采集进程已停止的运行中任务可以重新排队，保留此前 attempt/checkpoint。旧进程仍存在或身份无法确认时拒绝接管。重新排队会重验目标并去重，不表示从旧分页游标继续，详见 [API 恢复语义](API_USAGE.md#取消和重启恢复)。
- 官方默认页面是 `web/`，独立 v2 页面可通过 `start_ui_v2.ps1` 启动在 8766，但需要先配置并启动主 API。
- 当前完整静态/单元门禁以 [`.github/workflows/check.yml`](.github/workflows/check.yml) 的 Windows/macOS 矩阵为准；它包含依赖检查、平台合约、API/取消/导航/产物/队列测试、UI 合约、Node 语法和源码导出。下载后的接收者还必须按本平台完成设备、人工登录、CA/Frida 和真实终点 CSV 验收；CI 通过或历史 release-gate 产物都不能替代实机证据。

## 本地测试

测试文件集中在 `tests/`；独立 PowerShell XML 夹具在 `tests/manual/`。完整命令与目录约定见 [测试说明](tests/README.md)。在项目根目录运行 `python -m unittest discover -s tests -t .` 可发现全套 Python 测试，原来的单模块命令改用 `tests.test_xxx`。

先按平台安装依赖，在项目根目录运行基础回归：

```powershell
# Windows
.venv\Scripts\python.exe -m unittest tests.test_facebook_capture_api tests.test_facebook_cancel_transport tests.test_analyze_facebook_capture tests.test_facebook_capture_orchestrator
.venv\Scripts\python.exe -m pip check
```

```bash
# macOS
.venv/bin/python -m unittest tests.test_facebook_capture_api tests.test_analyze_facebook_capture tests.test_facebook_android_capture tests.test_facebook_mac_device tests.test_facebook_macos
.venv/bin/python -m pip check
```

安装 Node.js 后，两端都可运行 `node --check web/app.js` 和 `node --check web_v2/app.js`。以上是基础集合；发布时还须执行 [CI 工作流](.github/workflows/check.yml) 对应平台的全部命令。测试使用隔离夹具，不代表已通过当前账号的真实分页验收。真实任务、取消后恢复和 CSV 验证步骤见平台部署文档及 [验证记录](VALIDATION_2026-09-16.md)。

## GitHub 发布准备

本地工作目录混有个人文档与采集证据。执行 `python tools/export_source.py`，根据 `release-files.txt` **明确文件清单**生成 `dist/facebook-source-时间.zip`，只包含可交付源码、模板和说明。解压到新目录验证后，再把审查过的清单文件提交到现有仓库的工作分支；不要上传整个历史工作目录。导出器拒绝软链接、不存在的文件和不合法路径，同时输出 SHA-256 清单以便核对。

项目仓库：[taoduo0428/facebook-reaction-capture](https://github.com/taoduo0428/facebook-reaction-capture)。当前为私有仓库，受邀协作者可克隆或通过 Code → Download ZIP 下载。公开发布前由仓库所有者选择许可证与可见性；当前未附带开源许可证。
