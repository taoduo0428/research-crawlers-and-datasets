"""Native macOS AVD launch without touching Windows paths or account data."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time


def console_port(serial: str) -> int:
    match = re.fullmatch(r'emulator-(\d+)', serial)
    if not match or not 5554 <= int(match[1]) <= 5682 or int(match[1]) % 2:
        raise ValueError('Use an even emulator console port between 5554 and 5682')
    return int(match[1])


def start_emulator(serial: str, avd_name: str, timeout: int = 180) -> None:
    if sys.platform != 'darwin':
        raise RuntimeError('Native Mac launch requires macOS')
    emulator, adb = shutil.which('emulator'), shutil.which('adb')
    if not emulator or not adb:
        raise RuntimeError('Set sdk_root in facebook.local.json and install Android SDK emulator/platform-tools')
    port = console_port(serial)
    for candidate in (port, port + 1):
        with socket.socket() as probe:
            try:
                probe.bind(('127.0.0.1', candidate))
            except OSError as exc:
                raise RuntimeError(f'Emulator port {candidate} is occupied; inspect that device or configure another serial') from exc
    avd_home = Path(os.environ.get('ANDROID_AVD_HOME', str(Path.home() / '.android/avd')))
    if not (avd_home / (avd_name + '.ini')).is_file():
        raise RuntimeError(f'AVD {avd_name} does not exist; create the native host architecture image described in macos/README.md')
    log_dir = Path(__file__).resolve().parent / 'api-jobs'
    log_dir.mkdir(exist_ok=True)
    with (log_dir / f'{serial}-bootstrap.log').open('ab') as log:
        process = subprocess.Popen([emulator, '-avd', avd_name, '-port', str(port), '-writable-system',
                                    '-no-snapshot', '-netdelay', 'none', '-netspeed', 'full'],
                                   stdout=log, stderr=subprocess.STDOUT,
                                   stdin=subprocess.DEVNULL, start_new_session=True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'Emulator exited; inspect api-jobs/{serial}-bootstrap.log')
        result = subprocess.run([adb, '-s', serial, 'shell', 'getprop', 'sys.boot_completed'],
                                capture_output=True, text=True, timeout=8, check=False)
        if result.returncode == 0 and result.stdout.strip() == '1':
            return
        time.sleep(2)
    raise RuntimeError(f'Emulator did not boot within {timeout}s; it remains available for manual inspection')
