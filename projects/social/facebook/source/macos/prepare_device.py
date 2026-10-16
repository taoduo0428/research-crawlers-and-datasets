"""Darwin Android ARM64 prerequisites; default CLI is a read-only doctor.

Public doctor(serial, sdk_root=None, *, runner=None, ca_dir=None) and
prepare(serial, sdk_root=None, *, frida_server=None, helper_apk=None,
install_ca=False, runner=None, ca_dir=None) return JSON-compatible dictionaries
with ok/checks/actions/errors. runner(argv, timeout=seconds) returns a
CompletedProcess. No server is started: the capture runner owns its lifecycle.
CA mounts are runtime state and must be checked again after emulator reboot.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import subprocess
import tempfile
import uuid

SYSTEM = "/system/etc/security/cacerts"
CONSCRYPT = "/apex/com.android.conscrypt/cacerts"
FRIDA = "/data/local/tmp/frida-server"
PACKAGE = "com.openai.facebookuidump"


class SetupError(RuntimeError):
    pass


def run_command(argv, *, timeout=20):
    """Never invoke a host shell; every subprocess has a deadline."""
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                          check=False)


class Device:
    def __init__(self, serial, sdk_root=None, *, runner=None, ca_dir=None):
        self.run = runner or run_command
        self.result = {"ok": False, "serial": serial, "checks": {},
                       "actions": [], "errors": []}
        if platform.system() != "Darwin":
            raise SetupError("Run this prerequisite CLI on Darwin (macOS).")
        if not re.fullmatch(r"[A-Za-z0-9_.:-]+", serial or ""):
            raise SetupError("A valid explicit --serial is required.")
        root = sdk_root or os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME")
        candidate = Path(root).expanduser() / "platform-tools/adb" if root else None
        self.adb = str(candidate) if candidate and candidate.is_file() else None
        if root and not self.adb:
            raise SetupError("SDK root has no platform-tools/adb; install local Android platform-tools.")
        self.adb = self.adb or shutil.which("adb")
        if not self.adb:
            default = Path.home() / "Library/Android/sdk/platform-tools/adb"
            self.adb = str(default) if default.is_file() else None
        if not self.adb:
            raise SetupError("adb missing; specify --sdk-root or add local adb to PATH.")
        self.serial = serial
        self.ca_dir = Path(ca_dir or Path.home() / ".mitmproxy").expanduser().resolve()

    def command(self, argv, timeout=20):
        try:
            response = self.run(list(map(str, argv)), timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SetupError(f"Command failed or timed out: {argv[0]} ({type(exc).__name__})") from exc
        if response.returncode:
            # Do not echo arbitrary remote output (it can contain sensitive data).
            raise SetupError(f"Command failed (exit {response.returncode}): {argv[0]} {argv[1:4]}")
        return response.stdout.strip()

    def adb_command(self, *args, timeout=20):
        return self.command([self.adb, "-s", self.serial, *args], timeout)

    def shell(self, script, timeout=20):
        return self.adb_command("shell", script, timeout=timeout)

    def check(self, name, callback):
        try:
            self.result["checks"][name] = {"ok": True, "details": callback()}
        except SetupError as exc:
            self.result["checks"][name] = {"ok": False, "error": str(exc)}
            self.result["errors"].append(f"{name}: {exc}")

    def base(self):
        if self.adb_command("get-state") != "device":
            raise SetupError("Selected device is not online; authorize this serial first.")
        if self.adb_command("shell", "getprop", "sys.boot_completed") != "1":
            raise SetupError("Wait for Android boot completion.")
        abi = self.adb_command("shell", "getprop", "ro.product.cpu.abi")
        if abi != "arm64-v8a":
            raise SetupError(f"M4 setup requires Android arm64-v8a, got {abi!r}; create a fresh ARM64 AVD.")
        return {"abi": abi, "api": self.adb_command("shell", "getprop", "ro.build.version.sdk")}

    def helper(self):
        if not self.adb_command("shell", "pm", "path", PACKAGE).startswith("package:"):
            raise SetupError("UI helper missing; provide --helper-apk with the local helper APK.")
        text = self.adb_command("shell", "dumpsys", "package", PACKAGE)
        version = re.search(r"\bversionCode=(\d+)\b", text)
        if not version or int(version[1]) < 5:
            raise SetupError("UI helper versionCode must be >=5; provide the current local helper APK.")
        instrumentation = self.adb_command("shell", "pm", "list", "instrumentation")
        if f"{PACKAGE}/.DumpInstrumentation" not in instrumentation:
            raise SetupError("UI helper DumpInstrumentation is not registered.")
        return {"package": PACKAGE, "version_code": int(version[1])}

    def frida(self, remote=FRIDA):
        header = self.shell(f"od -An -tx1 -N20 {shlex.quote(remote)}")
        try:
            validate_elf(bytes.fromhex(header))
        except ValueError as exc:
            raise SetupError("Cannot read target Frida ELF architecture.") from exc
        version = self.shell(f"{shlex.quote(remote)} --version", timeout=10)
        if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.]+)?", version):
            raise SetupError("Target Frida --version did not return a version.")
        try:
            client_version = importlib.metadata.version("frida")
        except importlib.metadata.PackageNotFoundError as exc:
            raise SetupError("Install matching local Python frida package before preparing the server.") from exc
        if client_version != version:
            raise SetupError(f"Frida mismatch: target {version}, Python client {client_version}; supply a matching Android ARM64 server.")
        return {"path": remote, "abi": "arm64-v8a", "version": version,
                "running_verified": False}

    def certificate(self):
        cert = self.ca_dir / "mitmproxy-ca-cert.pem"
        if not cert.is_file():
            raise SetupError("Public mitmproxy CA missing; --install-ca permits local mitmdump generation.")
        data = cert.read_bytes()
        if b"PRIVATE KEY" in data or len(re.findall(b"-----BEGIN CERTIFICATE-----", data)) != 1:
            raise SetupError("Refusing CA material containing a private key or multiple/no certificates.")
        openssl = shutil.which("openssl")
        if not openssl:
            raise SetupError("Local openssl is required for the Android legacy CA subject hash.")
        digest = self.command([openssl, "x509", "-inform", "PEM", "-subject_hash_old",
                               "-in", cert, "-noout"])
        if not re.fullmatch(r"[0-9a-fA-F]{8}", digest):
            raise SetupError("OpenSSL returned an invalid legacy CA hash.")
        return cert, data, digest.lower()

    def namespaces(self):
        pids = self.shell("pidof zygote zygote64").split()
        if not pids or any(not p.isdigit() for p in pids):
            raise SetupError("No valid zygote PIDs found; cannot certify app namespace readiness.")
        # Process names only: never dump UI, app files, accounts or session data.
        processes = self.shell("ps -A -o PID,NAME")
        for line in processes.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0].isdigit() and (parts[1] == "com.facebook.katana" or parts[1].startswith("com.facebook.katana:")):
                pids.append(parts[0])
        return sorted(set(pids), key=int)

    def verify_ca(self):
        _, data, digest = self.certificate()
        expected = hashlib.sha256(data).hexdigest()
        if self.adb_command("shell", "id", "-u") != "0":
            raise SetupError("CA namespace verification requires root adbd on a rooted emulator; doctor will not enable root.")
        paths = [f"{SYSTEM}/{digest}.0", f"{CONSCRYPT}/{digest}.0"]
        commands = [f"sha256sum {path}" for path in paths]
        pids = self.namespaces()
        commands.extend(f"nsenter -t {pid} -m -- sha256sum {paths[1]}" for pid in pids)
        for command in commands:
            output = self.shell(command)
            if not output.split() or output.split()[0] != expected:
                raise SetupError("CA content differs in system/Conscrypt/app namespace; rerun --install-ca on a writable rooted emulator.")
        return {"certificate_hash": digest, "system_path": paths[0],
                "conscrypt_path": paths[1], "namespace_pids": pids,
                "runtime_only": True, "tls_capture_verified": False}

    def generate_ca(self):
        if (self.ca_dir / "mitmproxy-ca-cert.pem").is_file():
            return
        mitmdump = shutil.which("mitmdump")
        if not mitmdump:
            raise SetupError("No local mitmdump; install mitmproxy locally and retry --install-ca.")
        # Run only our process, without a listener; mitmproxy initializes its CA
        # before the running hook. Its private key stays in the host confdir.
        self.ca_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(prefix="facebook-ca-") as temporary:
            hook = Path(temporary) / "exit_after_ca.py"
            hook.write_text("from mitmproxy import ctx\ndef running():\n    ctx.master.shutdown()\n", encoding="utf-8")
            self.command([mitmdump, "--set", f"confdir={self.ca_dir}", "--set",
                          "server=false", "-s", hook], timeout=30)
        self.certificate()
        self.result["actions"].append("generated_host_ca_with_local_mitmdump")

    def install_ca(self):
        if self.adb_command("shell", "getprop", "ro.kernel.qemu") != "1":
            raise SetupError("CA installation supports a rooted writable emulator only.")
        if self.adb_command("shell", "getprop", "ro.build.version.sdk") != "35":
            raise SetupError("Supported CA install path is Android 15/API 35 only.")
        self.adb_command("root")
        self.adb_command("wait-for-device", timeout=30)
        if self.adb_command("shell", "id", "-u") != "0":
            raise SetupError("Root adbd unavailable; use a rootable Android 15 ARM64 emulator image.")
        try:
            self.shell(f"test -w {SYSTEM} && test -d {CONSCRYPT} && command -v nsenter")
        except SetupError as exc:
            raise SetupError("CA store is not writable or nsenter unavailable. Launch emulator with -writable-system and prepare adb root/remount manually; reboot if requested, then retry. No automatic verity change or reboot performed.") from exc
        pids = self.namespaces()
        self.generate_ca()
        _, data, digest = self.certificate()
        system = f"{SYSTEM}/{digest}.0"
        remote = f"/data/local/tmp/facebook-ca-{uuid.uuid4().hex}.0"
        # Push a validated snapshot, not a mutable source file or private .pem.
        with tempfile.TemporaryDirectory(prefix="facebook-public-ca-") as temporary:
            public = Path(temporary) / "mitmproxy-ca-cert.pem"
            public.write_bytes(data)
            try:
                self.adb_command("push", str(public), remote)
                self.shell(f"cp {remote} {system} && chown root:root {system} && chmod 0644 {system} && restorecon {system}")
            finally:
                self.shell(f"rm -f {remote}")
        self.result["actions"].append("installed_public_ca_system_store")
        self.shell(f"mount --bind {SYSTEM} {CONSCRYPT}")
        for pid in pids:
            self.shell(f"nsenter -t {pid} -m -- mount --bind {SYSTEM} {CONSCRYPT}")
        self.verify_ca()
        self.result["actions"].append("verified_conscrypt_zygote_and_facebook_namespaces")

    def install_frida(self, local):
        path = Path(local).expanduser().resolve()
        with path.open("rb") as source:
            validate_elf(source.read(20))
        remote = f"/data/local/tmp/facebook-frida-{uuid.uuid4().hex}"
        try:
            self.adb_command("push", str(path), remote, timeout=60)
            self.shell(f"chmod 0755 {remote}")
            self.frida(remote)
            # Never replace or kill a running server; its owner must stop it.
            if self.shell("ps -A -o NAME").splitlines().count("frida-server"):
                raise SetupError("frida-server is running; stop it through its owning runner before replacing it.")
            self.shell(f"mv {remote} {FRIDA} && chmod 0755 {FRIDA}")
            self.frida()
            self.result["actions"].append("installed_verified_frida_server_not_started")
        finally:
            self.shell(f"rm -f {remote}")

    def install_helper(self, local):
        path = Path(local).expanduser().resolve()
        if not path.is_file() or path.suffix.lower() != ".apk":
            raise SetupError("--helper-apk must be an existing local APK.")
        for service in ("activity", "package", "window", "accessibility"):
            if self.adb_command("shell", "service", "check", service) != f"Service {service}: found":
                raise SetupError(f"Android framework service {service} is not ready.")
        remote = f"/data/local/tmp/facebook-helper-{uuid.uuid4().hex}.apk"
        try:
            self.adb_command("push", str(path), remote, timeout=60)
            if self.adb_command("shell", "pm", "install", "-r", "-t", remote, timeout=60) != "Success":
                raise SetupError("Android package manager did not report Success installing helper.")
            self.helper()
            self.result["actions"].append("installed_verified_ui_helper")
        finally:
            self.shell(f"rm -f {remote}")


def validate_elf(header):
    if len(header) < 20 or header[:6] != b"\x7fELF\x02\x01" or header[18:20] != b"\xb7\x00":
        raise SetupError("Frida server must be an uncompressed Android ARM64 ELF64 (not a Windows/Mac/x86 binary).")


def prepare(serial, sdk_root=None, *, frida_server=None, helper_apk=None,
            install_ca=False, runner=None, ca_dir=None):
    """Install only explicitly supplied components, then report all readiness checks.

    ok means prerequisites checked, not a running Frida service or proven capture.
    Individual actions may succeed even when another check fails; inspect actions.
    """
    device = None
    try:
        device = Device(serial, sdk_root, runner=runner, ca_dir=ca_dir)
        device.result["checks"]["device"] = {"ok": True, "details": device.base()}
        for name, value, install in (("frida_install", frida_server, device.install_frida),
                                      ("helper_install", helper_apk, device.install_helper)):
            if value is not None:
                device.check(name, lambda value=value, install=install: install(value))
        if install_ca:
            device.check("ca_install", device.install_ca)
        device.check("frida", device.frida)
        device.check("helper", device.helper)
        device.check("ca", device.verify_ca)
        device.result["ok"] = not device.result["errors"]
        return device.result
    except (SetupError, OSError) as exc:
        result = device.result if device else {"ok": False, "serial": serial,
                                               "checks": {}, "actions": [], "errors": []}
        result["errors"].append(str(exc))
        return result


def doctor(serial, sdk_root=None, *, runner=None, ca_dir=None):
    """Read-only: no root/remount/push/install/generation/reboot/server startup."""
    return prepare(serial, sdk_root, runner=runner, ca_dir=ca_dir)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--sdk-root")
    parser.add_argument("--frida-server", help="Local uncompressed Android ARM64 server; install only, not start")
    parser.add_argument("--helper-apk", help="Local UI dump helper APK (versionCode >=5)")
    parser.add_argument("--install-ca", action="store_true", help="Explicitly generate/install public mitmproxy CA on writable rooted Android 15 emulator")
    result = prepare(**vars(parser.parse_args(argv)))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
