"""Portable local launcher. No device changes are made by init/doctor."""
from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from urllib.request import urlopen
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent


def configuration(root: Path = ROOT) -> dict:
    path = root / 'facebook.local.json'
    settings = json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
    if not isinstance(settings, dict):
        raise ValueError('facebook.local.json must be a JSON object')
    port = settings.get('port', 8765)
    if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
        raise ValueError('port must be an integer from 1024 to 65535')
    if not isinstance(settings.get('open_browser', True), bool):
        raise ValueError('open_browser must be true or false')
    sdk = settings.get('sdk_root') or os.environ.get('ANDROID_SDK_ROOT') or os.environ.get('ANDROID_HOME')
    if not sdk:
        candidates = ([Path.home() / 'Library/Android/sdk'] if sys.platform == 'darwin' else
                      [Path('D:/Android/SdkManual'), Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'Android/Sdk'])
        executable = 'emulator.exe' if sys.platform == 'win32' else 'emulator'
        sdk = next((str(p) for p in candidates if (p / 'emulator' / executable).is_file()), '')
    avd = settings.get('avd_home') or os.environ.get('ANDROID_AVD_HOME')
    if not avd:
        legacy = Path('D:/Android/AVDManual')
        avd = str(legacy if sdk and Path(sdk) == Path('D:/Android/SdkManual') and legacy.is_dir() else Path.home() / '.android/avd')
    from facebook_network import load_network
    network = load_network(settings, legacy=path.exists() and sys.platform == 'win32')
    return {**settings, 'port': port, 'sdk_root': str(sdk or ''), 'avd_home': str(avd),
            'network': {'mode': network.mode, 'upstream_url': network.upstream_url}}


def port_available(port: int) -> bool:
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', port))
            return True
        except OSError:
            return False


def choose_port(preferred: int, reserved: set[int]) -> int:
    for port in range(preferred, min(preferred + 1000, 65536)):
        if port not in reserved and port_available(port):
            reserved.add(port)
            return port
    raise ValueError(f'No available port near {preferred}; configure ports explicitly')


def initialize(root: Path = ROOT) -> None:
    reserved = {5037, 5554, 5555, 5556, 5557, 7897}
    registry = root / 'facebook_workers.json'
    if registry.is_file():
        payload = json.loads(registry.read_text(encoding='utf-8-sig'))
        for worker in payload.get('workers', []):
            reserved.update(worker[k] for k in ('capture_port', 'native_port'))
    local = root / 'facebook.local.json'
    if local.is_file():
        reserved.add(configuration(root)['port'])
    # Exclusive creation prevents overwriting another user's config or account registry.
    for target, template in [('facebook.local.json', 'facebook.local.example.json'), ('facebook_workers.json', 'facebook_workers.example.json')]:
        if (root / target).exists():
            continue
        payload = json.loads((root / template).read_text(encoding='utf-8-sig'))
        if target == 'facebook.local.json':
            payload['port'] = choose_port(payload.get('port', 8765), reserved)
            # Existing Windows accounts keep the original transport route.
            if registry.exists() and sys.platform == 'win32':
                payload['network'] = {'mode': 'upstream', 'upstream_url': 'http://127.0.0.1:7897'}
        else:
            emulator_ports = set()
            api_port = configuration(root)['port']
            for worker in payload.get('workers', []):
                for key in ('capture_port', 'native_port'):
                    worker[key] = choose_port(worker[key], reserved)
                if sys.platform == 'darwin':
                    worker['avd_name'] = 'fb-macos-api35-' + worker['worker_id']
                # Allocate both console and ADB ports, only for a new registry.
                for console in range(5554, 5683, 2):
                    if (console not in emulator_ports and api_port not in (console, console + 1) and
                            port_available(console) and port_available(console + 1)):
                        worker['serial'] = f'emulator-{console}'
                        emulator_ports.add(console)
                        break
                else:
                    raise ValueError('No free Android console/ADB port pair; close an unused emulator or configure serial explicitly')
        try:
            with (root / target).open('x', encoding='utf-8') as stream:
                stream.write(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
        except FileExistsError:
            pass


def environment(settings: dict, root: Path = ROOT) -> None:
    os.environ['FACEBOOK_PYTHON_EXE'] = sys.executable
    from facebook_network import load_network
    network = load_network(settings)
    os.environ['FACEBOOK_NETWORK_MODE'] = network.mode
    os.environ['FACEBOOK_UPSTREAM_PROXY'] = network.upstream_url
    paths = [str(Path(sys.executable).parent), str(Path(sys.executable).parent / 'Scripts')]
    if settings['sdk_root']:
        sdk = Path(settings['sdk_root']).expanduser().resolve()
        os.environ['ANDROID_SDK_ROOT'] = os.environ['ANDROID_HOME'] = str(sdk)
        os.environ['FACEBOOK_SDK_ROOT'] = str(sdk)
        os.environ['FACEBOOK_ADB_EXE'] = str(sdk / 'platform-tools' / ('adb.exe' if sys.platform == 'win32' else 'adb'))
        paths += [str(sdk / 'platform-tools'), str(sdk / 'emulator')]
    else:
        paths.append(str(root / 'platform-tools'))
    os.environ['FACEBOOK_AVD_HOME'] = os.environ['ANDROID_AVD_HOME'] = str(Path(settings['avd_home']).expanduser().resolve())
    os.environ['PATH'] = os.pathsep.join(paths + [os.environ.get('PATH', '')])


def doctor(root: Path = ROOT) -> dict:
    settings = configuration(root)
    environment(settings, root)
    checks = []
    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({'name': name, 'ok': ok, 'detail': detail})
    check('python', sys.version_info >= (3, 12), sys.version.split()[0])
    for package in ('mitmproxy', 'frida', 'frida-tools'):
        try:
            version = importlib.metadata.version(package)
            check(package, True, version)
        except importlib.metadata.PackageNotFoundError:
            check(package, False, 'Run START.cmd to install dependencies')
    check('adb', bool(shutil.which('adb')), 'Android SDK platform-tools; set sdk_root in facebook.local.json')
    check('emulator', bool(shutil.which('emulator')), 'Android SDK emulator; set sdk_root in facebook.local.json')
    registry = root / 'facebook_workers.json'
    if registry.is_file():
        from facebook_capture_api import load_worker_config
        workers = load_worker_config(registry)
        for worker in workers:
            if worker.enabled:
                check(worker.worker_id + ':avd', (Path(settings['avd_home']) / (worker.avd_name + '.ini')).is_file(), worker.avd_name)
                with socket.socket() as probe:
                    try:
                        probe.bind(('127.0.0.1', worker.capture_port))
                        available = True
                    except OSError:
                        available = False
                check(worker.worker_id + ':capture-port', available, str(worker.capture_port) + ' (occupied may mean an active capture)')
    else:
        check('worker-registry', False, 'Run START.cmd to initialize config')
    if settings['network']['mode'] == 'upstream':
        upstream = urlsplit(settings['network']['upstream_url'])
        try:
            with socket.create_connection((upstream.hostname, upstream.port or (443 if upstream.scheme == 'https' else 80)), timeout=2):
                proxy = True
        except OSError:
            proxy = False
        check('upstream-proxy', proxy, 'Configured upstream proxy; use direct mode when Facebook is directly accessible')
    else:
        check('network-mode', True, 'Direct internet; no Clash or external proxy required')
    return {'checks': checks, 'host_ready': all(x['ok'] for x in checks),
            'platform': sys.platform, 'capture_pcap_supported': sys.platform in {'win32', 'darwin'},
            'device_acceptance': 'Manual Android setup, login and target probe required; see README.md'}


def start(no_browser: bool = False) -> None:
    if sys.platform not in {'win32', 'darwin'}:
        raise ValueError('The local capture launcher supports Windows and macOS')
    settings = configuration()
    environment(settings)
    from facebook_capture_api import load_worker_config
    workers = load_worker_config()
    if settings['port'] in {p for w in workers for p in (w.capture_port, w.native_port)}:
        raise ValueError('API port conflicts with a worker capture/native port; edit facebook.local.json')
    url = f"http://127.0.0.1:{settings['port']}"
    try:
        with urlopen(url + '/health', timeout=2) as response:
            existing = json.load(response)
        if existing.get('service') != 'ready' or 'workers' not in existing:
            raise ValueError('Configured port belongs to another service; change port in facebook.local.json')
    except OSError:
        existing = None
    if existing is not None:
        print('Already running: ' + url)
        if not no_browser and settings.get('open_browser', True):
            webbrowser.open(url)
        return
    from facebook_capture_api import ApiHandler, JobManager, ThreadingHTTPServer
    # Bind before constructing the manager (which loads persistent state).
    server = ThreadingHTTPServer(('127.0.0.1', settings['port']), ApiHandler)
    server.manager = JobManager()
    print('Local console: ' + url + '\nKeep this window open. Ctrl+C stops the service.')
    if not no_browser and settings.get('open_browser', True):
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['init', 'doctor', 'start', 'shell'])
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    try:
        if args.action == 'init':
            initialize()
            print('Local config ready; existing files preserved.')
        elif args.action == 'doctor':
            report = doctor()
            print(json.dumps(report, ensure_ascii=True, indent=2))
            return 0 if report['host_ready'] else 2
        elif args.action == 'shell':
            environment(configuration())
            command = ['powershell.exe', '-NoProfile', '-NoExit', '-ExecutionPolicy', 'Bypass'] if sys.platform == 'win32' else ['/bin/zsh', '-i']
            return subprocess.call(command, cwd=ROOT)
        else:
            start(args.no_browser)
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
