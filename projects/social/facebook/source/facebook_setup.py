"""Repeatable Apple Silicon setup. Never copies accounts or overwrites AVDs."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import lzma
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import facebook_local as local
from macos.prepare_device import Device, SetupError, FRIDA, validate_elf

ROOT = Path(__file__).resolve().parent
PACKAGE = "com.facebook.katana"
IMAGE = "system-images;android-35;google_apis;arm64-v8a"
APP_LINK_DOMAINS = ("facebook.com", "www.facebook.com", "m.facebook.com")
FRIDA_API = "https://api.github.com/repos/frida/frida/releases/tags/{}"
FRIDA_RELEASES = {
    "17.17.0": {
        "name": "frida-server-17.17.0-android-arm64.xz",
        "url": "https://github.com/frida/frida/releases/download/17.17.0/frida-server-17.17.0-android-arm64.xz",
        "sha256": "09d1fad867b27d69562a79289f4c412e85867f5d38ab72877036ed35e4223021",
    },
}


def command(argv, *, timeout=30, input=None):
    """Run a host command without a shell and convert failures into user guidance."""
    try:
        result = subprocess.run(list(map(str, argv)), input=input, capture_output=True,
                                text=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise SetupError(f"缺少命令 {Path(argv[0]).name}；请先安装并配置 Android Studio/SDK 依赖。") from exc
    except subprocess.TimeoutExpired as exc:
        raise SetupError(f"命令 {Path(argv[0]).name} 超时；请确认模拟器、SDK 和网络仍可用后重试。") from exc
    except OSError as exc:
        raise SetupError(f"无法运行命令 {Path(argv[0]).name}：{exc}。") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        suffix = f"；{detail[-1][:240]}" if detail else ""
        raise SetupError(f"步骤失败：{Path(argv[0]).name}；请检查 SDK、网络或安装文件（退出码 {result.returncode}）{suffix}。")
    return result.stdout.strip()


def read_profile(root):
    path = root / "facebook.setup.local.json"
    if not path.exists():
        try:
            example = root / "facebook.setup.example.json"
            with path.open("x", encoding="utf-8") as stream:
                stream.write(example.read_text(encoding="utf-8"))
        except FileExistsError:
            pass
        except OSError as exc:
            raise SetupError(f"无法创建本机配置 {path}：请确认项目目录可写。") from exc
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SetupError(f"无法读取 facebook.setup.local.json：请修复 JSON 后重试。") from exc
    if not isinstance(value, dict):
        raise SetupError("facebook.setup.local.json 必须是 JSON 对象。")
    for key in ("download_frida", "install_sdk_packages", "configure_app_links"):
        if not isinstance(value.get(key), bool):
            raise SetupError(f"{key} 必须为 true/false。")
    if not isinstance(value.get("facebook_apks"), list) or not all(
            isinstance(p, str) and p.strip() for p in value["facebook_apks"]):
        raise SetupError("facebook_apks 必须是 APK 路径数组。")
    if not isinstance(value.get("frida_server"), str):
        raise SetupError("frida_server 必须是路径字符串。")
    return value


def fetch(url, limit):
    try:
        with urlopen(Request(url, headers={"User-Agent": "facebook-reaction-capture-setup"}), timeout=30) as response:
            data = response.read(limit + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise SetupError(f"无法访问官方 Frida 下载地址；请检查网络，或填写已核验的本地 frida_server。") from exc
    if len(data) > limit:
        raise SetupError("下载文件超出大小上限，未安装。")
    return data


def download_frida(cache, *, fetch_fn=fetch):
    try:
        version = importlib.metadata.version("frida")
    except importlib.metadata.PackageNotFoundError as exc:
        raise SetupError("Python frida 依赖未安装；请先完成 CONFIGURE.command 的虚拟环境安装。") from exc
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise SetupError("仅支持稳定版 Frida 自动下载；请填写已核验的本地 frida_server。")

    pinned = FRIDA_RELEASES.get(version)
    name = f"frida-server-{version}-android-arm64.xz"
    expected_url = f"https://github.com/frida/frida/releases/download/{version}/{name}"
    if pinned:
        name = pinned["name"]
        expected_url = pinned["url"]
        expected_digest = pinned["sha256"]
    else:
        try:
            release = json.loads(fetch_fn(FRIDA_API.format(version), 2 * 1024 * 1024))
        except (TypeError, ValueError) as exc:
            raise SetupError("官方 Frida Release 元数据无效；请填写已核验的本地 frida_server。") from exc
        if not isinstance(release, dict):
            raise SetupError("官方 Frida Release 元数据无效；请填写已核验的本地 frida_server。")
        asset = next((a for a in release.get("assets", []) if a.get("name") == name), None)
        if not asset or asset.get("browser_download_url") != expected_url:
            raise SetupError("官方 Release 未找到匹配的 Android ARM64 Frida；可在配置中填写本地 frida_server。")
        digest = asset.get("digest") or ""
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
            raise SetupError("官方文件缺少 SHA256，未安装；请下载并核实后填写本地 frida_server。")
        expected_digest = digest.split(":", 1)[1]

    cache = Path(cache).expanduser().resolve()
    target = cache / name.removesuffix(".xz")
    if target.is_file():
        try:
            validate_elf(target.read_bytes()[:20])
        except (OSError, SetupError):
            pass
        else:
            return target

    packed = fetch_fn(expected_url, 64 * 1024 * 1024)
    if hashlib.sha256(packed).hexdigest() != expected_digest:
        raise SetupError("Frida 下载 SHA-256 不匹配，未安装；请重试或填写已核验的本地 frida_server。")
    decoder = lzma.LZMADecompressor()
    try:
        binary = decoder.decompress(packed, max_length=256 * 1024 * 1024)
    except lzma.LZMAError as exc:
        raise SetupError("Frida 压缩包无法解压，未安装。") from exc
    if not decoder.eof:
        raise SetupError("Frida 解压超出上限或文件不完整，未安装。")
    validate_elf(binary[:20])
    try:
        cache.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=cache, delete=False) as stream:
            temp = Path(stream.name)
            stream.write(binary)
        os.replace(temp, target)
    except OSError as exc:
        raise SetupError(f"无法写入 Frida 缓存 {target}；请确认项目目录可写。") from exc
    finally:
        if "temp" in locals() and temp.exists():
            temp.unlink()
    return target


def find_sdk_tool(sdk, name):
    sdk = Path(sdk).expanduser()
    roots = [sdk / "cmdline-tools" / "latest" / "bin"]
    versions = sdk / "cmdline-tools"
    if versions.is_dir():
        roots.extend(path / "bin" for path in sorted(versions.iterdir(), reverse=True) if path.is_dir() and path.name != "latest")
    for root in roots:
        candidate = root / name
        if candidate.is_file():
            return candidate
    raise SetupError(
        f"缺少 Android SDK 命令 {name}；请在 Android Studio → SDK Manager → SDK Tools 安装 "
        "Android SDK Command-line Tools (latest)，然后再次运行 CONFIGURE.command。"
    )


def ensure_java():
    configured = os.environ.get("JAVA_HOME", "").strip()
    bundled = Path("/Applications/Android Studio.app/Contents/jbr/Contents/Home")
    if configured:
        java_home = Path(configured).expanduser()
        java = java_home / "bin" / "java"
        if not java.is_file():
            raise SetupError(
                f"JAVA_HOME 指向 {java_home} 但缺少 bin/java；请修正 JAVA_HOME，或安装 Android Studio 自带 JDK "
                f"（{bundled}）。"
            )
    elif (bundled / "bin" / "java").is_file():
        os.environ["JAVA_HOME"] = str(bundled)
        java = bundled / "bin" / "java"
    else:
        java = shutil.which("java")
        if not java:
            raise SetupError(
                "未找到 Java。请安装 Android Studio，或设置 JAVA_HOME；Android Studio 自带 JDK 路径为 "
                f"{bundled}，然后再次运行 CONFIGURE.command。"
            )
    command([str(java), "-version"], timeout=15)


def run_sdkmanager(argv, *, timeout, label):
    try:
        result = subprocess.run(list(map(str, argv)), timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise SetupError(f"找不到 {label}；请确认 Android SDK Command-line Tools 已安装。") from exc
    except subprocess.TimeoutExpired as exc:
        raise SetupError(f"{label} 超时；请检查网络和 Android Studio SDK 状态后重试。") from exc
    except OSError as exc:
        raise SetupError(f"无法运行 {label}：{exc}。") from exc
    if result.returncode:
        raise SetupError(f"{label} 失败（退出码 {result.returncode}）；请确认 JAVA_HOME、网络和官方许可状态。")


def ensure_sdk_packages(profile, sdk, manager):
    packages = ["platform-tools", "emulator", "platforms;android-35", IMAGE]
    required = {
        "platform-tools": sdk / "platform-tools" / "adb",
        "emulator": sdk / "emulator" / "emulator",
        "platforms;android-35": sdk / "platforms" / "android-35" / "android.jar",
        IMAGE: sdk / "system-images" / "android-35" / "google_apis" / "arm64-v8a" / "package.xml",
    }
    missing = [name for name, path in required.items() if not path.is_file()]
    if not missing:
        return
    if not profile["install_sdk_packages"]:
        raise SetupError("SDK 组件缺失：" + ", ".join(missing) + "；启用 install_sdk_packages 或在 SDK Manager 中安装。")
    print("首次安装 Android SDK：请阅读终端中的官方许可，按提示选择。", flush=True)
    run_sdkmanager([manager, f"--sdk_root={sdk}", "--licenses"], timeout=600, label="Android SDK licenses")
    run_sdkmanager([manager, f"--sdk_root={sdk}", *packages], timeout=1800, label="Android SDK packages")
    remaining = [name for name, path in required.items() if not path.is_file()]
    if remaining:
        raise SetupError("SDK 安装后仍缺少：" + ", ".join(remaining) + "；请在 Android Studio SDK Manager 中检查 SDK location。")


def _validate_avd_files(avd_home, name, ini):
    try:
        values = dict(line.split("=", 1) for line in ini.read_text(encoding="utf-8").splitlines() if "=" in line)
    except (OSError, UnicodeError) as exc:
        raise SetupError(f"已有 AVD {name} 的索引无法读取；未覆盖设备，请人工检查。") from exc
    raw_path = values.get("path", "").strip()
    if not raw_path:
        raise SetupError(f"已有 AVD {name} 缺少 path；保留原设备，请人工检查。")
    avd_path = Path(raw_path).expanduser()
    if not avd_path.is_absolute():
        avd_path = avd_home / avd_path
    avd_path = avd_path.resolve()
    if avd_path.name != name + ".avd" or not avd_path.is_dir():
        raise SetupError(f"已有 AVD {name} 的 path 指向其他或不存在的设备；未覆盖，请检查 AVD_HOME。")
    config = avd_path / "config.ini"
    try:
        text = config.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise SetupError(f"已有 AVD {name} 缺少可读 config.ini；未覆盖设备，请人工检查。") from exc
    if not re.search(r"^abi\.type\s*=\s*arm64-v8a\s*$", text, re.M) or not re.search(
            r"^image\.sysdir\.1\s*=.*android-35[/\\]+google_apis[/\\]+arm64-v8a(?:[/\\]+)?\s*$", text, re.M):
        raise SetupError("已有 AVD 不是 Android 35 Google APIs ARM64；未覆盖，请配置新的 avd_name。")


def ensure_avd(worker, settings, sdk, run=command, avdmanager=None):
    """Create only a missing, explicitly named AVD and reject stale collisions."""
    name = str(worker.avd_name).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", name):
        raise SetupError(f"worker {worker.worker_id} 的 avd_name 不安全或无效；请使用字母、数字、点、下划线和短横线。")
    raw_avd_home = str(settings.get("avd_home") or "").strip()
    if not raw_avd_home:
        raise SetupError("facebook.local.json 缺少 avd_home；请运行本机初始化后重试。")
    if os.name != "nt" and re.match(r"^[A-Za-z]:[\\/]", raw_avd_home):
        raise SetupError(f"avd_home 使用了 Windows 路径：{raw_avd_home}；请为 Mac 配置本机 AVD_HOME。")
    avd_home = Path(raw_avd_home).expanduser()
    avd_home = avd_home.resolve()
    try:
        avd_home.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SetupError(f"无法创建 AVD_HOME {avd_home}；请确认目录权限。") from exc
    ini = avd_home / (name + ".ini")
    avd_dir = avd_home / (name + ".avd")
    if ini.is_file():
        _validate_avd_files(avd_home, name, ini)
        return False
    if avd_dir.exists():
        raise SetupError(f"存在同名 AVD 数据但缺少索引：{avd_dir}；未覆盖，请人工检查。")
    manager = Path(avdmanager) if avdmanager else find_sdk_tool(sdk, "avdmanager")
    try:
        run([manager, "create", "avd", "-n", name, "-k", IMAGE], timeout=120, input="no\n")
    except SetupError as exc:
        raise SetupError(f"无法创建 AVD {name}，可能存在同名或残留设备；未删除任何现有文件。{exc}") from exc
    if not ini.is_file():
        raise SetupError(f"avdmanager 未创建预期索引 {ini}；未继续操作，请检查 AVD_HOME。")
    _validate_avd_files(avd_home, name, ini)
    return True


def ensure_device_identity(worker, adb, run=command):
    name = run([adb, "-s", worker.serial, "emu", "avd", "name"])
    lines = [line.strip() for line in name.splitlines() if line.strip() and line.strip() != "OK"]
    if not lines:
        raise SetupError("无法读取模拟器 AVD 身份；请确认 serial 已完成启动。")
    if lines[0] != worker.avd_name:
        raise SetupError("模拟器端口上运行的是其他 AVD；未修改设备，请检查 serial。")


def device_lock_path(serial):
    key = hashlib.sha256(serial.encode()).hexdigest()[:24]
    return Path(tempfile.gettempdir()) / f"facebook-android-{key}.lock"


def acquire_device_lock(serial):
    """Match facebook_android_capture.py's per-serial nonblocking lock."""
    try:
        import fcntl
    except ImportError as exc:
        raise SetupError("当前 macOS Python 缺少 fcntl，无法安全判断设备是否正在采集。") from exc
    path = device_lock_path(serial)
    try:
        handle = path.open("a")
    except OSError as exc:
        raise SetupError(f"无法打开设备并发锁 {path}；请确认临时目录可写。") from exc
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise SetupError("该 serial 正在被另一个 Android 采集任务使用；请等待任务结束后再运行 CONFIGURE.command。") from exc
    return handle


def release_device_lock(handle):
    try:
        import fcntl
        fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        handle.close()


def wait_for_snapshot(serial, snapshot_fn, *, timeout=180, sleep=time.sleep, clock=time.monotonic):
    deadline = clock() + timeout
    last = "offline"
    while True:
        snapshot = snapshot_fn(serial)
        last = f"{snapshot.get('state', 'unknown')}/{snapshot.get('boot_completed', False)}"
        if snapshot.get("state") == "device" and snapshot.get("boot_completed"):
            return snapshot
        if clock() >= deadline:
            raise SetupError(f"模拟器 {serial} 未在 {timeout} 秒内完成 Android 启动（当前 {last}）；请检查 AVD 日志。")
        sleep(min(2, max(0.05, deadline - clock())))


def ensure_worker_started(worker, snapshot_fn, start_fn, *, sleep=time.sleep, clock=time.monotonic):
    snapshot = snapshot_fn(worker.serial)
    if snapshot.get("state") == "device":
        return wait_for_snapshot(worker.serial, snapshot_fn, sleep=sleep, clock=clock)
    try:
        start_fn(worker.serial, worker.avd_name)
    except (OSError, RuntimeError, SetupError) as exc:
        if "port" not in str(exc).lower() or "occup" not in str(exc).lower():
            raise SetupError(f"模拟器 {worker.serial} 启动失败：{exc}") from exc
        try:
            return wait_for_snapshot(worker.serial, snapshot_fn, sleep=sleep, clock=clock)
        except SetupError as wait_error:
            raise SetupError(f"模拟器端口可能被其他 AVD 占用：{exc}；{wait_error}") from wait_error
    return wait_for_snapshot(worker.serial, snapshot_fn, sleep=sleep, clock=clock)


def wait_for_device_boot(device, *, timeout=180, sleep=time.sleep, clock=time.monotonic):
    deadline = clock() + timeout
    last = "设备未就绪"
    while True:
        try:
            return device.base()
        except SetupError as exc:
            last = str(exc)
        if clock() >= deadline:
            raise SetupError(f"Android 设备未在 {timeout} 秒内完成启动：{last}")
        sleep(min(2, max(0.05, deadline - clock())))


def package_installed(device):
    """Use pm list packages: pm path returns a failure for a normal missing package on some images."""
    try:
        output = device.adb_command("shell", "pm", "list", "packages", PACKAGE)
    except SetupError as exc:
        raise SetupError("无法查询 Facebook 安装状态；请确认 Android 已完成启动且 adb shell 可用。") from exc
    return any(line.strip() == "package:" + PACKAGE for line in output.splitlines())


def resolve_apks(root, profile):
    configured = profile["facebook_apks"]
    paths = [Path(value).expanduser() for value in configured]
    if not paths:
        paths = sorted((root / "setup-input").glob("*.apk"))
    return [path if path.is_absolute() else root / path for path in paths]


def install_facebook(root, profile, device, sdk):
    if package_installed(device):
        return False
    apks = resolve_apks(root, profile)
    if not apks:
        raise SetupError("模拟器已准备。请把合法取得的 Facebook ARM64 APK（拆分包放全部 APK）放入 setup-input 文件夹，再次双击 CONFIGURE.command。")
    for path in apks:
        if not path.is_file() or path.suffix.lower() != ".apk":
            raise SetupError(f"APK 文件无效或不存在：{path}。请只填写合法取得的 .apk 文件。")
    analyzer = find_sdk_tool(sdk, "apkanalyzer")
    for path in apks:
        try:
            package_id = command([analyzer, "manifest", "application-id", path], timeout=60)
        except SetupError as exc:
            raise SetupError(f"无法验证 APK 包名 {path}；请确认 Android SDK Command-line Tools 已安装。") from exc
        if package_id != PACKAGE:
            raise SetupError(f"APK 包名不是 {PACKAGE}：{path}；未安装。")
    action = "install-multiple" if len(apks) > 1 else "install"
    result = device.adb_command(action, *map(str, apks), timeout=180)
    if not any(line.strip() == "Success" for line in result.splitlines()):
        raise SetupError("Facebook 安装未返回 Success，未继续启动应用。")
    if not package_installed(device):
        raise SetupError("Facebook 安装命令成功但 package manager 未发现 com.facebook.katana；未继续配置。")
    return True


def _frida_pids(device):
    try:
        output = device.shell("ps -A -o PID,NAME")
    except SetupError as exc:
        raise SetupError("无法检查设备上的 Frida daemon；请确认 adb root 和 Android shell 可用。") from exc
    pids = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].isdigit() and (
                parts[-1] == "frida-server" or parts[-1].endswith("/frida-server")):
            pid = parts[0]
            try:
                executable = device.shell(f"readlink -f /proc/{pid}/exe").strip()
            except SetupError:
                continue
            if executable.removesuffix(" (deleted)") == FRIDA:
                pids.append(pid)
    return pids


def stop_frida_server(device):
    pids = _frida_pids(device)
    if not pids:
        return False
    for pid in pids:
        device.adb_command("shell", "kill", pid, timeout=10)
    remaining = _frida_pids(device)
    for pid in remaining:
        device.adb_command("shell", "kill", "-9", pid, timeout=10)
    if _frida_pids(device):
        raise SetupError("已有 Frida daemon 无法停止，未替换设备上的 Frida；请停止占用它的采集进程后重试。")
    return True


def probe_frida(serial, run=command):
    return run([
        sys.executable, "-c",
        "import frida,sys; frida.get_device(sys.argv[1],timeout=5).enumerate_processes()",
        serial,
    ], timeout=12)


def ensure_frida(device, serial, root, profile, *, run=command, probe=None):
    try:
        device.frida()
    except SetupError as initial_error:
        frida_path = profile["frida_server"]
        if not frida_path:
            if not profile["download_frida"]:
                raise SetupError("设备 Frida 不可用；请填写 frida_server，或启用 download_frida。") from initial_error
            frida_path = download_frida(root / ".setup-cache")
        path = Path(frida_path).expanduser()
        if not path.is_absolute():
            path = root / path
        if not path.is_file():
            raise SetupError(f"frida_server 文件不存在：{path}。")
        stop_frida_server(device)
        try:
            device.install_frida(path)
            device.frida()
        except (OSError, SetupError) as exc:
            raise SetupError(f"Frida server 安装或版本校验失败：{exc}") from exc

    probe = probe or (lambda selected_serial: probe_frida(selected_serial, run=run))
    try:
        probe(serial)
        return
    except SetupError:
        stop_frida_server(device)
        device.adb_command("shell", FRIDA, "-D", timeout=10)
    try:
        probe(serial)
    except SetupError as exc:
        raise SetupError("Frida server 已安装但无法连接；请确认 Python frida 与 Android ARM64 server 版本一致。") from exc


def wait_for_app_process(device, *, timeout=30, sleep=time.sleep, clock=time.monotonic):
    deadline = clock() + timeout
    while True:
        try:
            output = device.shell("ps -A -o PID,NAME")
        except SetupError as exc:
            raise SetupError("无法确认 Facebook 进程是否启动；请确认 APK 安装完整。") from exc
        if any(line.split() and line.split()[-1].startswith(PACKAGE) for line in output.splitlines()):
            return
        if clock() >= deadline:
            raise SetupError("Facebook 应用未在启动等待时间内出现；请在模拟器中检查 APK 和登录界面。")
        sleep(min(1, max(0.05, deadline - clock())))


def ensure_ca(device):
    try:
        return device.verify_ca()
    except SetupError as verify_error:
        try:
            device.install_ca()
            return device.verify_ca()
        except SetupError as install_error:
            raise SetupError(f"CA 系统/Conscrypt/Facebook 命名空间未就绪：{install_error}") from verify_error


def provision(root=ROOT, *, run=command, snapshot_fn=None, start_fn=None,
              device_factory=None, frida_probe_fn=None,
              sleep=time.sleep, clock=time.monotonic,
              lock_factory=None, lock_release=None):
    if sys.platform != "darwin" or platform.machine().lower() not in ("arm64", "aarch64"):
        raise SetupError("此入口用于 Apple Silicon Mac（M1–M4）；Windows 使用 START.cmd。")
    root = Path(root).resolve()
    device_factory = device_factory or Device
    lock_factory = lock_factory or acquire_device_lock
    lock_release = lock_release or release_device_lock
    try:
        (root / "setup-input").mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SetupError("无法创建 setup-input 文件夹；请确认项目目录可写。") from exc
    local.initialize(root)
    profile = read_profile(root)
    settings = local.configuration(root)
    configured_sdk = str(settings.get("sdk_root") or "").strip()
    sdk = Path(configured_sdk or Path.home() / "Library/Android/sdk").expanduser().resolve()
    if configured_sdk and not sdk.is_dir():
        raise SetupError(f"facebook.local.json 的 sdk_root 不存在：{sdk}；请修正路径后重试。")
    settings["sdk_root"] = str(sdk)
    local.environment(settings, root)
    manager = find_sdk_tool(sdk, "sdkmanager")
    ensure_java()
    ensure_sdk_packages(profile, sdk, manager)
    avdmanager = find_sdk_tool(sdk, "avdmanager")
    try:
        from facebook_capture_api import adb_snapshot, load_worker_config
        from facebook_macos_runtime import start_emulator
    except ImportError as exc:
        raise SetupError("Python 采集依赖未完整安装；请重新运行 setup.sh 安装 requirements.txt。") from exc
    snapshot_fn = snapshot_fn or adb_snapshot
    start_fn = start_fn or start_emulator
    try:
        workers = [worker for worker in load_worker_config(root / "facebook_workers.json") if worker.enabled]
    except (OSError, ValueError, RuntimeError) as exc:
        raise SetupError(f"facebook_workers.json 无法用于 macOS 配置：{exc}") from exc
    adb = str(sdk / "platform-tools" / "adb")
    for worker in workers:
        print(f"准备 {worker.worker_id}：检查/创建模拟器，已有账号数据保留。", flush=True)
        ensure_avd(worker, settings, sdk, run=run, avdmanager=avdmanager)
        lock = lock_factory(worker.serial)
        try:
            ensure_worker_started(worker, snapshot_fn, start_fn, sleep=sleep, clock=clock)
            ensure_device_identity(worker, adb, run=run)
            device = device_factory(worker.serial, str(sdk))
            wait_for_device_boot(device, sleep=sleep, clock=clock)
            device.adb_command("root")
            device.adb_command("wait-for-device", timeout=30)
            wait_for_device_boot(device, sleep=sleep, clock=clock)
            install_facebook(root, profile, device, sdk)
            if profile["configure_app_links"]:
                device.adb_command("shell", "pm", "set-app-links-user-selection", "--user", "0",
                                   "--package", PACKAGE, "true", *APP_LINK_DOMAINS)
            device.adb_command("shell", "am", "start", "-W", "-a", "android.intent.action.MAIN",
                               "-c", "android.intent.category.LAUNCHER", "-n", PACKAGE + "/.LoginActivity")
            wait_for_app_process(device, sleep=sleep, clock=clock)
            ensure_ca(device)
            ensure_frida(device, worker.serial, root, profile, run=run, probe=frida_probe_fn)
            print(f"{worker.worker_id} 配置完成。请在模拟器中手动登录 Facebook；登录和实际采集尚未自动验收。", flush=True)
        finally:
            lock_release(lock)
    cache = root / ".setup-cache"
    cache.mkdir(exist_ok=True)
    (cache / "configured").write_text("Run CONFIGURE.command after reboot to restore CA/Frida.\n", encoding="utf-8")


def main():
    try:
        provision()
        return 0
    except (SetupError, OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"配置暂停：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
