# macOS Apple Silicon 部署与验收 Runbook

**这是原工程的历史部署记录。** 里面的 `CONFIGURE.command`、`CHECK.command`、`START.command` 没有收入当前总仓库，不能直接复制相关命令来运行。现在接手 Mac 环境请先读 [更新后的安装说明](QUICKSTART.md)；下面保留旧步骤，主要为了查当时的配置和故障处理。


本文给另一个 Codex 直接执行。目标是在 Apple Silicon（M1-M4）Mac 上完成可审计的本地 Facebook Reaction 采集部署，并把“主机就绪”“设备前置就绪”“单链接低压样本”“全量终点完成”分别记录。

新执行器入口是根目录的 CONFIGURE.command、CHECK.command、START.command、facebook_setup.py、facebook_local.py、facebook_capture_api.py 和 facebook_android_capture.py。macos/facebook_macos.py 与 macos/start_facebook_workers.sh 是旧 transport/模拟适配器，不是新控制台入口。

截至仓库当前状态，GitHub macOS CI 和 Windows 模拟只能证明源码契约、命令生成和单元测试；不能证明真实 M4 的 Android 虚拟化、Facebook 登录、系统/Conscrypt CA、Frida、真实帖子或终点分页。当前日期化证据若存在，以 [VALIDATION_2026-09-16.md](../VALIDATION_2026-09-16.md) 为准；只有真实 M4 证据齐全后，才可写“该 M4 已验收”。

## 0. 执行规则

所有命令从仓库根目录执行，先确认路径和仓库身份：

~~~sh
pwd
test -f CONFIGURE.command
test -f CHECK.command
test -f START.command
test -f facebook_setup.py
test -f facebook_capture_api.py
test -f facebook_android_capture.py
test -f macos/prepare_device.py
chmod +x CONFIGURE.command CHECK.command START.command setup.sh macos/start_facebook_workers.sh
git rev-parse --show-toplevel
git status --short --branch
~~~

预期：test 命令无输出，git rev-parse 输出当前仓库根目录。若入口不存在或当前目录不是目标仓库，停止，不要在猜测的父目录继续执行。

本 runbook 只允许改入口执行位、本机配置、setup-input/、.venv 和运行产物，不改业务代码，不把个人配置或运行证据提交到 Git。

## 1. 获取仓库

已有 checkout 时执行上一节。没有 checkout 时，使用仓库所有者授权账号或授权 ZIP：

~~~sh
REPO_URL='https://github.com/taoduo0428/facebook-reaction-capture.git'
DEST="$HOME/facebook-reaction-capture"
test ! -e "$DEST" || { printf 'destination exists: %s\n' "$DEST" >&2; exit 1; }
git clone "$REPO_URL" "$DEST"
cd "$DEST"
git remote -v
git status --short --branch
~~~

预期：clone 完成且工作树干净。私有仓库若返回 Repository not found 或认证失败，停止并让仓库所有者提供授权下载；不要把密码、Token、Cookie 或验证码写入命令、配置或日志。

ZIP 必须解压到自己的可写目录，再执行第 0 节。不要复制 Windows AVD、模拟器数据目录、~/.mitmproxy、浏览器登录状态或他人的 CA 私钥。

## 2. 主机、架构与权限

~~~sh
test "$(uname -s)" = Darwin || { printf 'macOS required\n' >&2; exit 1; }
test "$(uname -m)" = arm64 || { printf 'Apple Silicon arm64 required\n' >&2; exit 1; }
sw_vers -productVersion
uname -m
python3 --version
df -h .
sysctl -n hw.memsize
python3 - <<'PY'
import platform
import sys
if sys.version_info < (3, 12):
    raise SystemExit("Python 3.12+ is required; 3.12 and 3.13 are the tested releases")
if platform.machine().lower() not in {"arm64", "aarch64"}:
    raise SystemExit("Apple Silicon arm64 required")
print("host preflight: PASS")
PY
test -w .
mkdir -p setup-input
test -w setup-input
~~~

预期：系统为 Darwin、架构为 arm64、Python 至少为 3.12，最后为 host preflight: PASS。当前 CI 和项目验收明确覆盖 3.12/3.13；更高版本虽然满足代码下限，但不能据此声称已经过本仓库验收。facebook_setup.py 会在非 Darwin 或非 ARM64 上停止；Intel Mac 不属于这条新配置链路。

单模拟器建议主机至少 16 GB 内存并预留约 25 GB 可用磁盘；双模拟器建议至少 24 GB 内存并额外预留镜像、AVD 和运行证据空间。数字是部署余量，不是代码自动验收项；若 `df` 显示空间紧张，先由使用者清理或扩容，不删除未知 AVD 和历史 run。

不需要宿主机 sudo、关闭 macOS 安全功能、修改系统 TUN/代理或开放公网端口。Android root 只针对可写 Android 15 模拟器，由 adb root 使用。

## 3. Android Studio、SDK 与 JDK

Android Studio SDK Manager 必须在同一 SDK 根目录安装：

- Android SDK Command-line Tools (latest)
- Android SDK Platform-Tools
- Android Emulator
- Android SDK Platform 35
- Android SDK Build-Tools 35.0.0（仅在构建可选 UI Helper 时需要）
- Android 35 Google APIs ARM64-v8a system image

加载标准 Apple Silicon 路径并检查真实工具：

如果 SDK 不在该默认目录，把下面的 ANDROID_SDK_ROOT 改成实际 SDK 根目录，并让 facebook.local.json 的 sdk_root 与它一致。

~~~sh
export ANDROID_SDK_ROOT="$HOME/Library/Android/sdk"
export ANDROID_HOME="$ANDROID_SDK_ROOT"
export ANDROID_AVD_HOME="$HOME/.android/avd"
export PATH="$ANDROID_SDK_ROOT/cmdline-tools/latest/bin:$ANDROID_SDK_ROOT/platform-tools:$ANDROID_SDK_ROOT/emulator:$PATH"

test -x "$ANDROID_SDK_ROOT/platform-tools/adb"
test -x "$ANDROID_SDK_ROOT/emulator/emulator"
test -x "$ANDROID_SDK_ROOT/cmdline-tools/latest/bin/sdkmanager"
test -x "$ANDROID_SDK_ROOT/cmdline-tools/latest/bin/avdmanager"
test -x "$ANDROID_SDK_ROOT/cmdline-tools/latest/bin/apkanalyzer"
adb version
emulator -version
sdkmanager --version
avdmanager --version
~~~

预期：test 无输出，随后打印各工具版本。核心配置不要求 Build-Tools；任一列出的核心工具缺失都停止，修正 SDK 根目录或在 SDK Manager 安装组件。只有要构建可选 UI Helper 时，才额外检查 Build-Tools。

补齐组件时先让使用者阅读并接受官方许可：

~~~sh
sdkmanager --licenses
sdkmanager \
  'platform-tools' \
  'emulator' \
  'platforms;android-35' \
  'system-images;android-35;google_apis;arm64-v8a'
~~~

若需要构建可选 UI Helper，再单独安装：

~~~sh
sdkmanager 'build-tools;35.0.0'
~~~

预期：许可由使用者明确确认，核心组件安装结束后相应文件存在。若许可或网络失败，停止；不要用 x86_64、Google Play 或不能 adb root 的镜像替代。

facebook_setup.py 会优先使用 facebook.local.json 的 sdk_root，否则使用环境变量或 ~/Library/Android/sdk；JDK 优先使用 JAVA_HOME，其次使用 Android Studio 自带 JBR：

~~~sh
java -version
test -x '/Applications/Android Studio.app/Contents/jbr/Contents/Home/bin/java' || test -n "$JAVA_HOME" || command -v java
~~~

预期：java -version 成功。若 JAVA_HOME 指向不存在的 bin/java，修正后停止并重试。

## 4. Python 虚拟环境和依赖

setup.sh --install-only 创建 .venv、安装 requirements.txt，并初始化运行时配置；它不会创建
facebook.setup.local.json。该本机配置会在首次运行 CONFIGURE.command 调用 facebook_setup.py
时由 read_profile() 从示例文件创建：

~~~sh
bash setup.sh --install-only
test -x .venv/bin/python
.venv/bin/python --version
.venv/bin/python -m pip check
.venv/bin/python - <<'PY'
import importlib.metadata as metadata
for name in ("mitmproxy", "frida", "frida-tools"):
    print(f"{name}=={metadata.version(name)}")
PY
~~~

预期：.venv/bin/python 存在，Python 至少 3.12（3.12/3.13 是当前明确验收版本），pip check 输出 No broken requirements found.，版本为 mitmproxy 12.2.3、frida 17.17.0、frida-tools 14.10.4。失败时修复网络/虚拟环境后重跑，不要安装到系统 Python。

该命令不启动 API，也不覆盖已有 facebook.local.json 或 facebook_workers.json。

## 5. 配置字段

首次运行 CONFIGURE.command 时生成的本机配置只属于本机，不能提交。

facebook.setup.local.json 来自 facebook.setup.example.json：

| 字段 | 含义 |
| --- | --- |
| facebook_apks | 字符串数组。非空时使用这些完整路径；为空时读取 setup-input/*.apk。拆分包必须全部提供。 |
| frida_server | 字符串路径。为空且 download_frida=true 时下载与主机 Frida 匹配的 Android ARM64 server；离线时填已核验的未压缩 ELF。 |
| download_frida | 布尔值，是否允许配置入口下载并校验官方 Frida。 |
| install_sdk_packages | 布尔值，缺组件时是否调用 sdkmanager。 |
| configure_app_links | 布尔值，是否设置 Facebook app-link 偏好。 |

facebook.local.json：

| 字段 | 含义 |
| --- | --- |
| sdk_root | SDK 绝对路径或空字符串；空值由环境变量或 ~/Library/Android/sdk 解析。 |
| avd_home | AVD 路径或空字符串；空值由 ANDROID_AVD_HOME 或 ~/.android/avd 解析，不能用 Windows 盘符。 |
| port | 1024-65535 的本地 API 端口，默认 8765。 |
| open_browser | 布尔值，START.command 是否自动打开页面。 |
| network.mode | direct 或 upstream。Mac 直连推荐 direct。 |
| network.upstream_url | upstream 模式下的 http(s)://host:port；无凭据、路径、查询和片段。直连时为空。 |

推荐配置：

~~~json
"network": {"mode": "direct", "upstream_url": ""}
~~~

mitmproxy 是本机采集代理，不是外部翻墙代理。旧 macos/facebook_macos.py 固定用 7897，不要与新 API 混用。

facebook_workers.json 必须是 {"version": 1, "workers": [...]}。字段如下：

| 字段 | 含义 |
| --- | --- |
| worker_id | 唯一 worker 标识。 |
| serial | 唯一 ADB serial，如 emulator-5554；新 runner 要求偶数 console port 5554-5682。 |
| avd_name | 唯一 AVD 名，默认 fb-macos-api35-worker-01。 |
| capture_port | MITM/ADB reverse 端口，如 18081。 |
| native_port | 仅作隔离预留，如 18766；Mac 不启动 native scroll 服务。 |
| enabled | 布尔值，至少一个启用。 |
| cooldown_seconds | 非负整数，默认 900。 |
| frida_ssl_unpin | 可选布尔值；字段省略时为 false，当前发行模板对 worker-01 显式设为 true。决定采集时是否附加 TLS hook。 |

所有 worker 身份和端口必须全局唯一，API 端口也不能冲突。新 API 只读根目录 facebook_workers.json，不读 macos/facebook_workers.macos.json。

~~~sh
.venv/bin/python - <<'PY'
from pathlib import Path
import json
import facebook_local
from facebook_capture_api import load_worker_config
root = Path.cwd()
settings = facebook_local.configuration(root)
workers = load_worker_config(root / "facebook_workers.json")
print("sdk_root:", settings["sdk_root"])
print("avd_home:", settings["avd_home"])
print("api_port:", settings["port"])
print("network_mode:", settings["network"]["mode"])
print("workers:", [(w.worker_id, w.serial, w.avd_name, w.capture_port, w.native_port, w.enabled) for w in workers])
profile_path = root / "facebook.setup.local.json"
if not profile_path.exists():
    profile_path = root / "facebook.setup.example.json"
profile = json.loads(profile_path.read_text(encoding="utf-8-sig"))
print("setup_profile_source:", profile_path.name)
print("setup_profile_types: PASS", {key: type(profile[key]).__name__ for key in profile})
PY
~~~

预期：无 traceback，打印当前 SDK/AVD/API/worker。首次 CONFIGURE 前读取示例配置是正常的；`facebook_setup.py` 会用排他创建方式生成 `facebook.setup.local.json`，已有文件不覆盖。若 JSON 类型、version、身份或端口错误，停止并修正，不删除旧配置来“重置”。

### 给后续命令使用的实际变量

端口或 API 初始化可能自动避开已占用值，所以后续命令不要把示例中的 `emulator-5554`、`18081` 或 `18766` 当成当前值。每打开一个新终端，先在仓库根目录重新执行下面的变量读取；它选择配置中第一个启用的 worker：

~~~sh
SERIAL="$(.venv/bin/python -c 'from facebook_capture_api import load_worker_config; print(next(w.serial for w in load_worker_config() if w.enabled))')"
AVD_NAME="$(.venv/bin/python -c 'from facebook_capture_api import load_worker_config; print(next(w.avd_name for w in load_worker_config() if w.enabled))')"
WORKER_CAPTURE="$(.venv/bin/python -c 'from facebook_capture_api import load_worker_config; print(next(w.capture_port for w in load_worker_config() if w.enabled))')"
WORKER_NATIVE="$(.venv/bin/python -c 'from facebook_capture_api import load_worker_config; print(next(w.native_port for w in load_worker_config() if w.enabled))')"
API_PORT="$(.venv/bin/python -c 'import facebook_local; print(facebook_local.configuration()["port"])')"
API="http://127.0.0.1:$API_PORT"
export SERIAL AVD_NAME WORKER_CAPTURE WORKER_NATIVE API_PORT API
printf 'serial=%s avd=%s capture=%s native=%s api=%s\n' "$SERIAL" "$AVD_NAME" "$WORKER_CAPTURE" "$WORKER_NATIVE" "$API"
~~~

预期：打印配置中的真实值。若需要检查第二个 worker，按同样方式读取它的 `serial`、`avd_name` 和端口；不要凭记忆替换。换终端后变量不会自动保留，必须重新执行本段。

### 单 Worker 与主备 Worker

干净 checkout 的根模板只创建一个 `worker-01`。此时调度仍是串行的，但没有可接管的 standby；不能从 `/health` 的 `mode=primary-standby-serial` 推断第二台设备已经存在。先用单 Worker 完成真实小帖验收，资源充足且确实需要故障接管时，再添加 `worker-02`。

Mac 新入口只读根目录 `facebook_workers.json`。不要把旧适配器的 `macos/facebook_workers.macos.example.json` 复制成根配置，也不要在 Mac 使用当前 `add_facebook_worker.cmd`；该 Windows bootstrap 会生成 x86 AVD 名并调用 PowerShell。新建、尚未登录任何账号的 Mac 部署可在第一次 CONFIGURE 前把根配置明确写成下面结构；已有账号的配置只做字段级合并，先留一份权限为 600 的本机备份：

~~~sh
mkdir -p "$HOME/.facebook-reaction-capture-backups"
chmod 700 "$HOME/.facebook-reaction-capture-backups"
BACKUP="$HOME/.facebook-reaction-capture-backups/facebook_workers.$(date +%Y%m%d-%H%M%S).json"
cp -p facebook_workers.json "$BACKUP"
chmod 600 "$BACKUP"
~~~

~~~json
{
  "version": 1,
  "workers": [
    {
      "worker_id": "worker-01",
      "serial": "emulator-5554",
      "avd_name": "fb-macos-api35-worker-01",
      "capture_port": 18081,
      "native_port": 18766,
      "enabled": true,
      "cooldown_seconds": 900,
      "frida_ssl_unpin": true
    },
    {
      "worker_id": "worker-02",
      "serial": "emulator-5556",
      "avd_name": "fb-macos-api35-worker-02",
      "capture_port": 18082,
      "native_port": 18767,
      "enabled": true,
      "cooldown_seconds": 900,
      "frida_ssl_unpin": true
    }
  ]
}
~~~

写入后先调用 `load_worker_config` 做校验，再运行 CONFIGURE；CONFIGURE 会逐台只创建缺失 AVD。两台都必须分别人工登录并分别验收，不能复制另一台 AVD 的数据目录。双模拟器建议至少 24 GB 内存，并在配置前确认 5554/5555、5556/5557、18081/18082、18766/18767 没有被其他服务占用。端口被占用时选择新的全局唯一值，不停止未知进程。

如果是在已经完成单 Worker 验收后增加 standby，先让 API 的 `queued_jobs` 为 0、页面没有运行中的任务，再在 API 终端按 Ctrl+C 停止服务。备份并编辑根目录 `facebook_workers.json`，把新的 worker 以 `enabled: true` 追加后运行上面的 `load_worker_config` 校验；随后执行 `bash CONFIGURE.command`，让新 AVD、APK、CA 和 Frida 在独立设备上完成配置，再分别人工登录和做 bounded sample。CONFIGURE 结束后启动的是一个全新的 API 进程，它才会读取追加的 worker。

不要在旧 API 仍运行时直接把新 worker 写成 enabled 后只调用 `/v1/workers/reload`：为防止未验收设备被接管，reload 要求新增 worker 先以 disabled 加载，而 CONFIGURE 又只准备 enabled worker。若只是修改已加载 worker 的 cooldown 或 `frida_ssl_unpin`，才可在没有运行任务时调用：

~~~sh
curl -fsS -X POST "$API/v1/workers/reload"
~~~

只有新设备已完成登录、CHECK 和 bounded sample 后，才允许让它保持 enabled 并作为 standby。standby 的账号、CA、Frida 和 AVD 数据必须独立；不要复制 worker-01 的数据目录或登录状态。

## 6. APK、Frida 和 CA

### APK

只使用合法取得的 Facebook Android ARM64 APK：

~~~sh
APK_COUNT=0
for apk in setup-input/*.apk; do
    test -f "$apk" || continue
    APP_ID="$(apkanalyzer manifest application-id "$apk")" || exit 1
    printf '%s: %s\n' "$apk" "$APP_ID"
    test "$APP_ID" = com.facebook.katana || {
        printf 'unexpected application id: %s\n' "$APP_ID" >&2
        exit 1
    }
    APK_COUNT=$((APK_COUNT + 1))
done
test "$APK_COUNT" -gt 0 || { printf 'no APK found\n' >&2; exit 1; }
~~~

预期：至少一个 APK，且每个包名严格为 com.facebook.katana。没有 APK、包名不同或只有 x86 APK 时停止；CONFIGURE 不会下载 Facebook APK。已有 com.facebook.katana 会被保留，不要为换版本删除账号数据。

### Frida

requirements.txt 固定主机 Frida 17.17.0。官方文件名为 frida-server-17.17.0-android-arm64.xz，配置入口会核对固定 SHA-256。不要用 darwin、x86_64 或未解压文件。

~~~sh
.venv/bin/python - <<'PY'
import importlib.metadata
print("host frida:", importlib.metadata.version("frida"))
PY
PREPARE_JSON="/tmp/facebook-prepare-device-$$.json"
set +e
.venv/bin/python macos/prepare_device.py --serial "$SERIAL" --sdk-root "$ANDROID_SDK_ROOT" >"$PREPARE_JSON"
PREPARE_EXIT=$?
set -e
cat "$PREPARE_JSON"
PREPARE_EXIT="$PREPARE_EXIT" .venv/bin/python - "$PREPARE_JSON" <<'PY'
import json
import os
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text())
checks = payload.get("checks") or {}
required = ("device", "frida", "ca")
missing = [name for name in required if not isinstance(checks.get(name), dict)]
failed = [name for name in required if isinstance(checks.get(name), dict) and checks[name].get("ok") is not True]
helper = checks.get("helper")
prepare_exit = int(os.environ["PREPARE_EXIT"])
if missing or failed or not isinstance(helper, dict):
    raise SystemExit(f"required prepare checks failed: missing={missing}, failed={failed}")
if prepare_exit == 0 and not payload.get("ok", False):
    raise SystemExit("prepare_device returned exit 0 but ok=false")
if prepare_exit != 0 and not helper.get("ok"):
    if any(checks[name].get("ok") is not True for name in required):
        raise SystemExit("prepare_device failed a required device/frida/ca check")
    print("prepare_device: optional UI Helper check failed; required checks are OK")
elif prepare_exit != 0:
    raise SystemExit("prepare_device failed without an optional-helper-only failure")
PY
~~~

该命令保留 `prepare_device.py` 的真实退出码，并用 JSON gate 只允许“可选 UI Helper 单项失败”继续；device、Frida 或 CA 任一为 false 时命令会停止。这里的 `frida` 只证明设备上的 ELF 与 Python client 版本匹配，不证明 daemon 已经在运行；`facebook_setup.py` 的 Frida probe 才会检查连接并按需启动 daemon。只有 `checks.helper.ok=false` 且 device/Frida/CA 合格时，新 runner 才可继续，因为它使用受保护的 ADB XML 手势。

`frida_ssl_unpin` 字段省略时的代码默认值是 false；当前根目录发行模板为 `worker-01` 显式写了 true。CONFIGURE 无论该开关如何都会准备并探测 Frida server；该开关只决定采集时是否附加 TLS hook。设为 true 时，API 会传入 --frida-ssl-unpin，runner 要求已登录 Facebook 主进程唯一，并把 attach 日志写入该次 run 的 frida.log。它不 eternalize hook，也不主动杀 Facebook 进程。若当前 Facebook 版本无需 hook，可显式改为 false；启用后若 frida.log 没有 hook installed，停止任务并检查 host/server 版本。

### CA

配置入口使用本机 `~/.mitmproxy/mitmproxy-ca-cert.pem`，只安装公开证书并验证 Android 15 Conscrypt 与 Facebook 进程命名空间；私钥留在本机。不要直接启动一个不带退出钩子的 `mitmdump` 进程，因为它可能一直占用前台终端；让 CONFIGURE 或下面的 `--install-ca` 负责初始化 CA：

~~~sh
.venv/bin/python macos/prepare_device.py \
  --serial "$SERIAL" \
  --sdk-root "$ANDROID_SDK_ROOT" \
  --install-ca
~~~

预期 `ca_install` 和 `ca` 成功。模拟器不是 API 35、不可 root、未使用 `-writable-system` 或命名空间校验失败时停止；不要关闭 verity、随意 remount 或删除系统文件绕过检查。

## 7. CONFIGURE、CHECK、START 顺序

### 7.1 CONFIGURE

确认目标 serial 没有正在运行的采集任务后，人类可在专用终端运行：

~~~sh
bash CONFIGURE.command
~~~

真实顺序是 setup.sh --install-only、facebook_setup.py，然后再次 setup.sh 启动本地 API。预期先出现“准备 worker-01”“配置完成”，随后出现 `Local console: http://127.0.0.1:<port>`。到达 Local console 后服务会在前台持续运行，命令不会自行返回；这不是卡死，也不要再启动第二份 API。只有在 Ctrl+C 正常停止后才会得到最终退出码。它只创建缺失 AVD，已有 AVD 必须验证为 API 35 Google APIs ARM64，错误时不覆盖。

另一个 Codex 执行时，不要给整个 CONFIGURE 设置几分钟的短超时。若工具无法安全保留前台会话，按相同顺序分三步执行；SDK 许可、Facebook 登录、验证码和设备确认仍交给使用者：

~~~sh
bash setup.sh --install-only
.venv/bin/python facebook_setup.py
bash START.command
~~~

前两步必须明确返回 0。第三步应保留为长时运行会话，并从另一个 shell 用 `/health` 验证；不要用 `nohup ... &` 后立刻假定启动成功。

Mac runner 的模拟器、mitmdump、Frida attach 和分析器使用无 stdin 的独立子进程，并把输出写入 `api-jobs/` 或本次 run；正常运行不会为每个链接弹出新的 Terminal 窗口。`START.command` 所在的一个主终端必须保留，用于显示服务状态和 Ctrl+C 收尾。

看到缺 APK、Frida SHA、CA、SDK、AVD identity 或端口错误时停止并修复对应对象，不删除 .setup-cache、AVD 或配置。

配置入口启动 AVD 时使用 -writable-system -no-snapshot -netdelay none -netspeed full，日志为 api-jobs/emulator-<console>-bootstrap.log：

~~~sh
AVD_HOME="$(.venv/bin/python -c 'import facebook_local; print(facebook_local.configuration()["avd_home"])')"
adb -s "$SERIAL" get-state
adb -s "$SERIAL" shell getprop sys.boot_completed
adb -s "$SERIAL" shell getprop ro.build.version.sdk
adb -s "$SERIAL" shell getprop ro.product.cpu.abi
adb -s "$SERIAL" emu avd name
find "$AVD_HOME" -type f -name "$AVD_NAME.ini" -print
~~~

预期依次为 device、1、35、arm64-v8a，并打印与 worker 完全一致的 AVD 名。否则停止，不能把另一台模拟器当目标。

### 7.2 人工登录

在 Android Studio Emulator 窗口内为每个启用 AVD 手动完成 Facebook 登录、验证码和设备确认。桌面浏览器登录不算模拟器登录。

登录未完成、仍在登录页或验证码待处理时，不提交任务。登录成功也不保证目标帖子 Reaction 名单可见。

### 7.3 CHECK

自动化用：

~~~sh
bash setup.sh --check
~~~

需要图形终端才用：

~~~sh
bash CHECK.command
~~~

CHECK.command 最后等待 Return。预期 JSON 的 host_ready 为 true，Python 依赖包、adb、emulator、启用 worker AVD/采集端口和网络配置检查均为 ok:true。这里的 `frida` 检查是 Python 包版本存在；doctor 不检查人工登录、Android CA、设备上的 Frida binary/daemon 或目标帖子，不能单独作为设备验收。

再次运行：

再次执行第 6 节 Frida 代码块中的 `prepare_device.py` 检查，保留 `PREPARE_EXIT` 和 JSON gate；不要用 `|| true` 替代退出码检查。

host_ready=false 或 prepare_device 的 device/API/ABI/Frida/CA 不合格时停止；UI Helper 单项失败必须记录为非阻断已知缺项。

### 7.4 START

在专用终端运行并保持窗口打开：

~~~sh
bash START.command
~~~

若 CONFIGURE 已启动 API，START 会打印 Already running: http://127.0.0.1:<port>，这是复用，不是错误。若端口属于其他服务，停止，识别占用者并改 facebook.local.json 的 port；不要杀未知进程。

第二终端检查回环 API：

~~~sh
API_PORT="${API_PORT:-$(.venv/bin/python -c 'import facebook_local; print(facebook_local.configuration()["port"])')}"
API="${API:-http://127.0.0.1:$API_PORT}"
curl -fsS "$API/health"
curl -fsS "$API/v1/capabilities"
~~~

预期 health 有 service:ready、mode:primary-standby-serial、worker 和 capture_lease；capabilities 在 macOS 有 capture_pcap_supported:false。API 必须只绑定 loopback。

## 8. 端口、网络和代理

典型端口是 API 8765、capture 18081、native reserve 18766、模拟器 5554/5555；以实际 JSON 为准：

~~~sh
SERIAL="${SERIAL:-$(.venv/bin/python -c 'from facebook_capture_api import load_worker_config; print(next(w.serial for w in load_worker_config() if w.enabled))')}"
WORKER_CAPTURE="${WORKER_CAPTURE:-$(.venv/bin/python -c 'from facebook_capture_api import load_worker_config; print(next(w.capture_port for w in load_worker_config() if w.enabled))')}"
WORKER_NATIVE="${WORKER_NATIVE:-$(.venv/bin/python -c 'from facebook_capture_api import load_worker_config; print(next(w.native_port for w in load_worker_config() if w.enabled))')}"
API_PORT="${API_PORT:-$(.venv/bin/python -c 'import facebook_local; print(facebook_local.configuration()["port"])')}"
lsof -nP -iTCP:"$API_PORT" -sTCP:LISTEN || true
lsof -nP -iTCP:"$WORKER_CAPTURE" -sTCP:LISTEN || true
lsof -nP -iTCP:"$WORKER_NATIVE" -sTCP:LISTEN || true
adb devices
adb -s "$SERIAL" shell settings get global http_proxy
adb -s "$SERIAL" reverse --list
~~~

任务未运行时预期 capture 空闲，proxy 通常为 null/:0，无未知 reverse。端口占用先用 lsof 识别；未知服务不得停止。不要 adb reverse --remove-all。

直连低影响检查：

~~~sh
curl -fsS -o /dev/null -w '%{http_code}\n' --max-time 15 https://www.facebook.com/
~~~

预期为 2xx/3xx。直连不需要 Clash、小火箭或 7897；确需上游时填无凭据的 HTTP(S) proxy origin，再执行 CHECK，不把密码嵌入 URL。

## 9. 单链接低压验收

从 FACEBOOK_BROWSER_SAMPLES.md 选一个接收者账号已确认 Reaction 可见的帖子，并在 Android 中确认同一账号能打开它。首次样本建议先不选 PCAP；若设备已有 su 0 与 /system/bin/tcpdump，可在 save_artifacts 中加入 pcap，runner 会验证本次 Android 进程、停止及拉取校验。

### 9.1 bounded sample

替换占位符后只提交一次。此处 `$SERIAL`、`$WORKER_CAPTURE` 和 `$WORKER_NATIVE` 已由第 5 节变量段读取；API 会从 worker 配置选择实际设备：

~~~sh
export POST_URL='https://www.facebook.com/<author>/posts/<numeric-post-id>'
PAYLOAD="$(.venv/bin/python - <<'PY'
import json, os
print(json.dumps({
    "post_urls": [os.environ["POST_URL"]],
    "collect": ["reactions"],
    "save_artifacts": ["mitm", "safe_capture", "progress"],
    "reaction_scroll_limit": 3,
}))
PY
)"
RESPONSE_FILE="/tmp/facebook-submit-$$.json"
HTTP_STATUS="$(curl -sS -o "$RESPONSE_FILE" -w '%{http_code}' -X POST "$API/v1/batches" \
  -H 'Content-Type: application/json' --data "$PAYLOAD")"
test "$HTTP_STATUS" = 202 || {
    printf 'submit failed: HTTP %s\n' "$HTTP_STATUS" >&2
    cat "$RESPONSE_FILE" >&2
    exit 2
}
RESPONSE="$(cat "$RESPONSE_FILE")"
printf '%s\n' "$RESPONSE"
BATCH_ID="$(printf '%s' "$RESPONSE" | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["batch_id"])')"
JOB_ID="$(printf '%s' "$RESPONSE" | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["posts"][0]["job_id"])')"
printf 'batch=%s job=%s\n' "$BATCH_ID" "$JOB_ID"
~~~

预期 HTTP 202 JSON 含 batch_id 和 posts[0].job_id。若 PCAP 前置检查失败，先检查既有 Android root/tcpdump（不自动安装或重启 ADB），或明确不选 pcap 后只重新提交一次；URL 校验失败则换帖子专用 HTTPS URL。

轮询到终态；十分钟内没有终态时停止并诊断，不能重复提交同一链接：

~~~sh
TERMINAL_STATUS=''
for attempt in $(seq 1 120); do
    SNAPSHOT="$(curl -fsS "$API/v1/batches/$BATCH_ID/progress")" || exit 1
    printf '%s\n' "$SNAPSHOT"
    STATUS="$(printf '%s' "$SNAPSHOT" | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin).get("status", ""))')"
    case "$STATUS" in
        completed|completed_with_errors) TERMINAL_STATUS="$STATUS"; break ;;
    esac
    sleep 5
done
test -n "$TERMINAL_STATUS" || {
    printf 'poll timed out; inspect and cancel explicitly, do not submit a duplicate\n' >&2
    exit 2
}
curl -fsS "$API/v1/jobs/$JOB_ID" > "/tmp/facebook-$JOB_ID.json"
curl -fsS "$API/v1/jobs/$JOB_ID/progress"
~~~

bounded 成功必须同时满足：job completed；completion_scope=bounded-sample；status 文件 exit_code=0、pagination_outcome=absolute-cap、completion_claim_allowed=false；报告目标 matched、来源单一、无 parse/rate-limit/session restriction；分析 JSON 有效且 CSV 至少包含一个有 `user_id` 的数据行。只有 CSV 表头不算抓到用户。这只证明 bounded sample，不证明全量。

API 会把每次 runner 写入 runs/.../attempt-001/ 的 android-capture-status.json 保留在 capture_run_dir/attempt_dirs；检查 job manifest 返回的 capture_run_dir，不要假定 status 文件一定位于批次的稳定根目录。

failed、completed_with_errors、visibility-limited、no-growth、time-budget、throttled 或 1675004 出现时停止，保留产物，不立即重试。

### 9.2 直接 runner 诊断（可选）

只在 API 没有 queued/running 任务且该 serial 的进程锁、capture/native 端口都空闲时使用；否则先通过 API 正常取消并等待清理，或使用另一套明确隔离的 serial/端口。输出目录必须新建或为空：

~~~sh
RUN_DIR="$PWD/runs/manual-bounded-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$RUN_DIR"
test -z "$(ls -A "$RUN_DIR" 2>/dev/null)"
.venv/bin/python facebook_android_capture.py \
  --serial "$SERIAL" \
  --post-url "$POST_URL" \
  --output-dir "$RUN_DIR" \
  --capture-port "$WORKER_CAPTURE" \
  --native-port "$WORKER_NATIVE" \
  --save-artifacts mitm,safe_capture,progress \
  --max-scrolls 3
printf 'runner exit=%s\n' "$?"
~~~

退出码 0 代表已接受的 terminal 或 bounded 结果；2 代表部分/失败，130/143 代表取消。检查 android-capture-status.json，不要只看退出码。

## 10. 全量与分页语义

| 请求 | 行为 | 可接受结果 |
| --- | --- | --- |
| 省略 reaction_scroll_limit | API 不传 --max-scrolls，runner 默认 0，持续至 has_next_page=false，默认最多 900 秒。 | completion_scope=terminal、stop_reason=terminal、严格目标/来源且无错误，才可声称全量终点。 |
| reaction_scroll_limit: null | 与省略相同，显式性为 false。 | 同上。 |
| reaction_scroll_limit: 1..300 | 以该值作为 bounded scroll 上限；scroll-budget 持久化为 absolute-cap。 | completion_scope=bounded-sample，即使 job completed 也不是全量。 |
| 0 或超出范围 | API 校验失败。 | 停止并修正请求。 |

全量请求示例（示例不选可选 PCAP，设备满足前置时可加入 pcap）：

~~~sh
FULL_PAYLOAD="$(.venv/bin/python - <<'PY'
import json, os
print(json.dumps({
    "post_urls": [os.environ["POST_URL"]],
    "collect": ["reactions"],
    "save_artifacts": ["mitm", "safe_capture", "progress"],
}))
PY
)"
curl -fsS -X POST "$API/v1/batches" \
  -H 'Content-Type: application/json' --data "$FULL_PAYLOAD"
~~~

全量必须有 reaction_integrity.completion_claim_allowed=true、最后成功页 has_next_page=false、目标和来源严格匹配、无 parse error/rate limit/session restriction。页面到底、人数显示、首屏用户或 HTTP 200 都不等于 terminal。Facebook 报告总数大于当前账号可见身份数时记录 visibility/count difference，不补猜用户。

## 11. 批量、主备串行和断点

~~~sh
curl -fsS "$API/health"
~~~

预期 mode=primary-standby-serial。一个调度器和一个队列同时只使用一个 active worker；第一个启用 worker 是 primary，后续已配置且启用的 worker 才是 standby。干净安装默认只有 worker-01。standby 只在 primary 设备故障被确认且已知 capture 进程停止后接管；普通帖子错误、限流或可见性不足不会触发换设备。

每批最多 100 个去重 URL，任务不跨 worker 手工并发。故障会生成 attempt-001、attempt-002，checkpoint 记录本地观察到的页数、用户数、终点/限流和去重证据；它明确 cross_worker_cursor=unsupported、cursor_material=not-persisted，因此是重新验证和合并，不是跨账号 cursor 精确续抓。

API 重启后，`queued` 在没有遗留进程证据时保持排队；`running` 在确认 capture lease 已清理时转为 `queued`，并标记 `interrupted by API service restart; safely requeued`，保留 checkpoint/attempt。`cancelling` 保留取消意图，不自动恢复入队；旧进程仍在运行或 PID 身份无法验证时才转为 `failed` 并阻塞 worker takeover。若 `capture_lease.state=uncertain` 或 `process_state` 是 stop-unverified/restart-unverified，先确认已知进程身份。1675004/429 是平台限流；保留部分结果并遵守 cooldown_seconds，不换账号续同一帖。完整字段见 [API 恢复语义](../API_USAGE.md#取消和重启恢复)。

## 12. 日志、产物和结果验收

manifest：api-jobs/fb_<id>.job.json、api-batches/batch_<id>.batch.json。运行目录通常是 runs/batch_<id>/post-001/ 或 runs/api-fb_<id>/，attempt 在其下。重点文件：

- capture.log、mitmdump.log、可选 frida.log
- safe-facebook-http-capture.jsonl、facebook-live-progress.json
- readonly-ui/exploration-diagnostics.json
- analysis/facebook-sample-patterns.json
- analysis/facebook-reactor-users.csv
- android-capture-status.json
- batch-summary.json、batch-posts.csv、batch-reactor-users.csv、batch-user-post-relations.csv

检查状态：

~~~sh
JOB_JSON="/tmp/facebook-$JOB_ID.json"
RUN_DIR="$(.venv/bin/python -c 'import json,sys; data=json.load(open(sys.argv[1])); print(data["run_dir"])' "$JOB_JSON")"
CAPTURE_DIR="$(.venv/bin/python -c 'import json,sys; data=json.load(open(sys.argv[1])); print(data.get("capture_run_dir") or data["run_dir"])' "$JOB_JSON")"
test -s "$RUN_DIR/analysis/facebook-sample-patterns.json"
test -s "$RUN_DIR/analysis/facebook-reactor-users.csv"
.venv/bin/python - "$RUN_DIR" "$CAPTURE_DIR" <<'PY'
import json
import sys
from pathlib import Path
root = Path(sys.argv[1])
capture = Path(sys.argv[2])
status = json.loads((capture / "android-capture-status.json").read_text())
report = json.loads((root / "analysis/facebook-sample-patterns.json").read_text())
target = report.get("target_verification") or {}
integrity = report.get("reaction_integrity") or {}
print(json.dumps({
    "exit_code": status.get("exit_code"),
    "stop_reason": status.get("stop_reason"),
    "pagination_outcome": status.get("pagination_outcome"),
    "completion_scope": status.get("completion_scope"),
    "completion_claim_allowed": status.get("completion_claim_allowed"),
    "target_status": target.get("status"),
    "matched_post_id": target.get("matched_post_id"),
    "integrity_status": integrity.get("integrity_status"),
    "last_successful_page_has_next": integrity.get("last_successful_page_has_next"),
    "rate_limited_records": integrity.get("rate_limited_records"),
}, ensure_ascii=False, indent=2))
PY
~~~

预期 bounded 为 `pagination_outcome=absolute-cap`、`completion_scope=bounded-sample`（底层 `stop_reason` 通常为 `scroll-budget`），全量为 `pagination_outcome=terminal`、`completion_scope=terminal`。文件缺失、来源不一致、parse error 或 cleanup_errors 非空只能记失败/部分。

## 13. 正常停止、取消和重启

优先通过 API 取消，等待终态后确认清理：

~~~sh
curl -fsS -X POST "$API/v1/batches/$BATCH_ID/cancel"
TERMINAL_STATUS=''
for attempt in $(seq 1 60); do
    SNAPSHOT="$(curl -fsS "$API/v1/batches/$BATCH_ID/progress")" || exit 1
    printf '%s\n' "$SNAPSHOT"
    STATUS="$(printf '%s' "$SNAPSHOT" | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin).get("status", ""))')"
    case "$STATUS" in
        completed|completed_with_errors) TERMINAL_STATUS="$STATUS"; break ;;
    esac
    sleep 2
done
test -n "$TERMINAL_STATUS" || {
    printf 'cancellation did not reach a terminal batch state; inspect lease and process identity\n' >&2
    exit 2
}
adb -s "$SERIAL" shell settings get global http_proxy
adb -s "$SERIAL" reverse --list
~~~

预期 proxy 回到任务前的值（常见 null/:0），列表无本次 reverse。若 proxy 被外部改动，runner 会保留当前值并报告清理错误；不要强写未知值。

确认任务收尾后，在 START.command 终端按 Ctrl+C。Mac 或模拟器重启后必须重新 bash CONFIGURE.command 恢复 CA mount/Frida，再人工检查登录、CHECK、START。历史任务可加载不等于运行中任务自动恢复。

## 14. 安全升级与回滚准备

升级前先取消所有 queued/running 任务并按上一节停止 API。不要在采集进程、mitmdump 或分析器仍在写文件时拉取代码。把三个本机配置备份到仓库外的私有目录；不存在的文件跳过：

~~~sh
UPGRADE_BACKUP="$HOME/.facebook-reaction-capture-backups/upgrade-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$UPGRADE_BACKUP"
chmod 700 "$UPGRADE_BACKUP"
for file in facebook.local.json facebook_workers.json facebook.setup.local.json; do
    test ! -f "$file" || cp -p "$file" "$UPGRADE_BACKUP/$file"
done
git status --short --branch
git fetch origin --prune
git rev-list --left-right --count HEAD...origin/main
~~~

若 `git status` 显示跟踪源码有本地修改，或当前分支不是预期分支，停止并先审查；不要 reset、checkout 覆盖或自动 stash。确认工作树可更新且只有远端领先时：

~~~sh
git pull --ff-only origin main
git rev-parse HEAD
bash setup.sh --install-only
~~~

`--ff-only` 失败表示历史分叉，交给仓库维护者处理。依赖更新后先执行第 17 节静态测试，再运行 CONFIGURE 恢复设备运行时状态并做 bounded sample。升级失败时保留旧 checkout、仓库外配置备份和错误输出；不要删除 AVD、`.setup-cache`、运行证据或账号数据来回滚。

## 15. 故障决策树

| 现象 | 先执行 | 停止条件和处理 |
| --- | --- | --- |
| 非 Darwin/非 arm64/Python 不是已验收的 3.12/3.13 | uname -s; uname -m; python3 --version | 安装正确环境后重试；停止，不强行配置。 |
| `.command` 双击无反应或提示无法验证 | 在仓库根目录运行 `bash CONFIGURE.command` | 记录终端的真实错误；不要关闭 Gatekeeper 或移除整个系统的安全属性。 |
| SDK 工具缺失 | command -v sdkmanager avdmanager adb emulator | 安装/修正 SDK；停止。 |
| license/package 失败 | sdkmanager --licenses; sdkmanager --list | 使用者确认许可并修复网络；停止，不换 Google Play/x86。 |
| Emulator 报 HVF/架构/立即退出 | uname -m; emulator -version; 查看 api-jobs/emulator-<port>-bootstrap.log | 更新原生 Android Emulator，确认 ARM64 镜像和可用磁盘；不要用 Rosetta+x86 作为正式方案。 |
| AVD 同名但 ABI/API/path 不对 | adb -s <serial> emu avd name; cat "$ANDROID_AVD_HOME/<name>.avd/config.ini" | 保留原 AVD，使用新的唯一 avd_name；不要 delete avd 或删 .avd。 |
| serial offline/端口占用 | adb devices; lsof -nP -iTCP:<port> | 识别占用者、修正配置；未知进程不得 kill。 |
| APK 缺失/包名错误 | find setup-input -name '*.apk'; apkanalyzer manifest application-id <apk> | 提供合法 com.facebook.katana ARM64 APK；停止。 |
| `INSTALL_FAILED_NO_MATCHING_ABIS` 或 split 缺失 | 核对所有合法 split APK 和其中的 arm64-v8a/native 文件 | 使用同一版本的完整 ARM64/universal split 集；不要混用版本或改签 Facebook APK。 |
| CA 失败 | prepare_device.py ... --install-ca | 仅接受 API35/rootable/-writable-system；否则停止。 |
| Frida 不匹配或无 daemon | adb -s <serial> root；再用 `shell /data/local/tmp/frida-server --version` 与 `shell pidof frida-server` 核对 | 使用匹配 Android ARM64 ELF；正常 CONFIGURE 以 `-D` 启动 daemon；不用 darwin/x86，不替换未知运行 server，也不要运行无参数前台 server。 |
| API 未就绪/PCAP 前置失败 | curl "$API/health"、检查 payload | 确认本项目端口与选定设备的 root/tcpdump；仅在明确不需 PCAP 时去掉 pcap；不杀未知服务、不重复提交。 |
| 每个链接仍弹“选择打开方式” | `adb shell pm path com.facebook.katana`，查看 capture.log 中显式 component 启动结果 | 新 runner 固定 `com.facebook.katana/.IntentUriHandler`；若 activity 被新版移除就停止并记录，不用坐标自动点击系统 resolver。 |
| 目标不匹配/visibility/no-growth/time-budget | 查看 status、progress、analysis | 记部分/失败，保留 evidence，不声称全量。 |
| 1675004/429/session restriction | 查看 progress 限流字段 | 停止、冷却、保留结果，不重试/换账号续同一帖。 |
| cancel/restart 后 lease uncertain | 读取 manifest 的 process_pid、创建时间、状态 | 只有身份可验证才处理；否则停止，不 kill -9。 |
| API 重启后 failed | curl "$API/v1/jobs/<id>"; find runs -path '*checkpoint*' | 记录 no-auto-resume；重新提交需重新校验，不能假称 cursor 续跑。 |

`visibility-limited`、`friends-only-names` 或 `query-not-triggered` 表示当前账号/UI 没有暴露可枚举身份，本身不是账号封禁证据。`429`、GraphQL `1675004`、`session_restricted=true` 或 Facebook 明确的临时功能限制才是限制证据。目标帖子公开可见也不保证 Reaction 用户身份公开；不得补猜缺失用户。

## 16. 敏感数据和危险操作

只留本机：账号、Cookie、OTP、会话状态、FACEBOOK_GRAPH_ACCESS_TOKEN、~/.mitmproxy 私钥、模拟器磁盘、APK、Frida、原始 MITM/JSONL/PCAP、用户 CSV 和完整日志。

~~~sh
git check-ignore -v \
  facebook.local.json \
  facebook_workers.json \
  facebook.setup.local.json \
  setup-input/example.apk \
  runs/example/safe-facebook-http-capture.jsonl \
  runs/example/raw-facebook-http.mitm
~~~

主机 CA 私钥在仓库外，单独确认它存在且不要纳入任何 Git 操作：

~~~sh
test -f "$HOME/.mitmproxy/mitmproxy-ca-cert.pem"
find "$HOME/.mitmproxy" -type f -print
~~~

预期本机配置、APK、PEM、MITM、JSONL、PCAP 被 .gitignore 命中。未命中时停止，不 git add -f。

禁止：rm -rf、git clean -fdx、avdmanager delete avd、删除 AVD/.setup-cache；复制他人 AVD/账号/浏览器状态/CA 私钥；下载未核验 APK/Frida；adb reverse --remove-all；kill 未确认 PID；API 绑定 0.0.0.0 或公网隧道；把代理密码写进 URL；限流后反复提交。

## 17. 静态、CI 与真实 M4 证据

本机静态和单元测试：

~~~sh
for script in CONFIGURE.command CHECK.command START.command setup.sh macos/start_facebook_workers.sh; do
    bash -n "$script"
done
.venv/bin/python -m pip check
.venv/bin/python -m unittest \
  tests.test_facebook_local tests.test_facebook_network tests.test_facebook_android_capture \
  tests.test_facebook_mac_device tests.test_facebook_macos tests.test_facebook_capture_orchestrator \
  tests.test_facebook_capture_api tests.test_facebook_worker_bootstrap tests.test_facebook_setup \
  tests.test_facebook_warm_start
node --check web/app.js
node --check web_v2/app.js
~~~

预期所有命令退出 0。github workflow 的 macos-latest 仅有 Python 3.12、依赖、单测、Node 语法和源码导出，不含接收者 M4、Android AVD、APK、账号、CA 或 Frida；CI 通过不得写成 M1-M4 真机通过。

有 GitHub CLI 读取权限时，记录目标提交和这次提交触发的两个矩阵 job，不能拿旧 run 代替：

~~~sh
git rev-parse HEAD
gh run list --workflow check.yml --branch main --limit 5
gh run view <run-id>
gh run watch <run-id> --exit-status
~~~

把 `<run-id>` 替换成目标提交对应的最新 run。必须分别看到 `contracts (macos-latest)` 与 `contracts (windows-latest)` 成功；没有 `gh` 时在仓库 Actions 页面按 commit SHA 核对。任一 job 失败就读取失败 step 和日志，修复后由新提交重新验证，不重跑旧证据来掩盖问题。

真实 M4 交付至少要有日期化、脱敏的：

1. bash setup.sh --check 的退出码和 host_ready:true；
2. adb 的 device、boot 1、API 35、ABI arm64-v8a、准确 AVD identity；
3. prepare_device.py 的 device/Frida/CA 检查，以及 UI Helper 是否为非阻断缺项；
4. /health 的 service:ready、primary-standby-serial、worker 和 capture lease；
5. 一个 bounded sample 的 manifest、status、progress、解析报告和 CSV；
6. 一个省略 reaction_scroll_limit 的真实单链接任务，completion_scope=terminal、stop_reason=terminal、completion_claim_allowed=true 且 has_next_page=false；
7. 取消/终止后的 proxy/reverse 清理读回；
8. 模拟器重启后再次 CONFIGURE、CHECK 和小帖任务结果。

任一缺失时，只能写“静态/主机/前置/部分证据”，不能写“Mac 已完整支持”。
