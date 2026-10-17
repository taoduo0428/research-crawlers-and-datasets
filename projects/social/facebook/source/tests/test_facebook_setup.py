import json
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import facebook_setup as setup
from macos.prepare_device import SetupError


def make_worker():
    return SimpleNamespace(
        worker_id="worker-01",
        serial="emulator-5554",
        avd_name="fb-macos-api35-worker-01",
        capture_port=18081,
        native_port=18766,
        enabled=True,
    )


class FakeDevice:
    def __init__(self, serial, sdk_root, events=None):
        self.serial = serial
        self.sdk_root = sdk_root
        self.events = events if events is not None else []
        self.package = False
        self.frida_binary = False
        self.frida_running = False
        self.ca_ready = False
        self.app_started = False

    def base(self):
        self.events.append(("base", self.serial))
        return {"abi": "arm64-v8a", "api": "35"}

    def adb_command(self, *args, timeout=20):
        self.events.append(("adb", args))
        if args[:2] == ("shell", "pm",):
            if args[2:5] == ("list", "packages", setup.PACKAGE):
                return f"package:{setup.PACKAGE}\n" if self.package else ""
            if args[2:4] == ("set-app-links-user-selection", "--user"):
                return ""
        if args[:2] == ("shell", "am"):
            self.app_started = True
            self.events.append(("app-started", None))
            return "Status: ok\n"
        if args[:2] == ("shell", setup.FRIDA):
            self.frida_running = True
            return ""
        if args[:2] == ("shell", "kill"):
            self.frida_running = False
            return ""
        if args and args[0] in {"root", "wait-for-device"}:
            return ""
        if args and args[0] in {"install", "install-multiple"}:
            self.package = True
            self.events.append(("facebook-installed", None))
            return "Success\n"
        raise AssertionError(f"unexpected adb command: {args}")

    def shell(self, script, timeout=20):
        self.events.append(("shell", script))
        if script == "ps -A -o PID,NAME":
            lines = ["PID NAME", "100 zygote", "101 zygote64"]
            if self.app_started:
                lines.append(f"102 {setup.PACKAGE}")
            if self.frida_running:
                lines.append("103 frida-server")
            return "\n".join(lines)
        if script == "readlink -f /proc/103/exe":
            return setup.FRIDA if self.frida_running else ""
        raise AssertionError(f"unexpected shell command: {script}")

    def frida(self):
        self.events.append(("frida-check", None))
        if not self.frida_binary:
            raise SetupError("target Frida missing")
        return {"version": "17.17.0"}

    def install_frida(self, path):
        self.events.append(("frida-install", Path(path)))
        if self.frida_running:
            raise SetupError("frida-server is running")
        self.frida_binary = True

    def verify_ca(self):
        self.events.append(("ca-check", self.app_started))
        if not self.ca_ready:
            raise SetupError("CA namespace missing")
        return {"runtime_only": True}

    def install_ca(self):
        self.events.append(("ca-install", self.app_started))
        if not self.app_started:
            raise AssertionError("CA must be installed while Facebook process exists")
        self.ca_ready = True


class Snapshot:
    def __init__(self, initial=None):
        self.state = initial or {"state": "offline", "boot_completed": False}
        self.calls = 0

    def __call__(self, serial):
        self.calls += 1
        return dict(self.state)


class SetupIntegrationTests(unittest.TestCase):
    def make_sdk(self, root):
        sdk = root / "sdk"
        for relative in (
            "platform-tools/adb",
            "emulator/emulator",
            "platforms/android-35/android.jar",
            "system-images/android-35/google_apis/arm64-v8a/package.xml",
            "cmdline-tools/latest/bin/sdkmanager",
            "cmdline-tools/latest/bin/avdmanager",
            "cmdline-tools/latest/bin/apkanalyzer",
        ):
            path = sdk / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fake", encoding="ascii")
        return sdk

    def make_profile(self, root, apk):
        (root / "facebook.setup.local.json").write_text(json.dumps({
            "facebook_apks": [apk.name],
            "frida_server": "frida-server",
            "download_frida": False,
            "install_sdk_packages": True,
            "configure_app_links": True,
        }), encoding="utf-8")
        (root / "frida-server").write_bytes(b"local server")

    def fake_host_command(self, argv, *, timeout=30, input=None):
        if "apkanalyzer" in str(argv[0]):
            return setup.PACKAGE
        if "emu" in argv:
            return "fb-macos-api35-worker-01\nOK"
        return ""

    def fake_avd_run(self, avd_home):
        worker = make_worker()

        def run(argv, *, timeout=30, input=None):
            if len(argv) >= 2 and str(argv[1]) == "create":
                avd = avd_home / f"{worker.avd_name}.avd"
                avd.mkdir(parents=True, exist_ok=True)
                (avd / "config.ini").write_text(
                    "abi.type=arm64-v8a\n"
                    "image.sysdir.1=system-images/android-35/google_apis/arm64-v8a/\n",
                    encoding="utf-8",
                )
                (avd_home / f"{worker.avd_name}.ini").write_text(
                    f"path={avd}\n", encoding="utf-8"
                )
                return ""
            if "emu" in argv:
                return f"{worker.avd_name}\nOK"
            raise AssertionError(f"unexpected host command: {argv}")

        return run

    def run_provision(self, root, sdk, avd, device, snapshot, start, probe, run):
        worker = make_worker()
        import facebook_capture_api
        locks = []

        def lock_factory(serial):
            lock = object()
            locks.append((serial, lock))
            return lock

        def lock_release(lock):
            self.assertIn(lock, [item[1] for item in locks])

        with patch.object(setup.sys, "platform", "darwin"), \
                patch.object(setup.platform, "machine", return_value="arm64"), \
                patch.object(setup.local, "initialize"), \
                patch.object(setup.local, "configuration", return_value={
                    "sdk_root": str(sdk), "avd_home": str(avd),
                }), \
                patch.object(setup.local, "environment"), \
                patch.object(setup, "ensure_java"), \
                patch.object(setup, "command", side_effect=self.fake_host_command), \
                patch.object(facebook_capture_api, "load_worker_config", return_value=[worker]):
            setup.provision(
                root,
                run=run,
                snapshot_fn=snapshot,
                start_fn=start,
                device_factory=lambda serial, sdk_root: device,
                frida_probe_fn=probe,
                sleep=lambda _seconds: None,
                lock_factory=lock_factory,
                lock_release=lock_release,
            )
        self.assertEqual([serial for serial, _lock in locks], [worker.serial])

    def test_initial_and_repeated_provisioning_repairs_only_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sdk = self.make_sdk(root)
            avd = root / "avd"
            apk = root / "facebook.apk"
            apk.write_bytes(b"fake apk")
            self.make_profile(root, apk)
            events = []
            device = FakeDevice("emulator-5554", sdk, events)
            snapshot = Snapshot()
            starts = []

            def start(serial, avd_name):
                starts.append((serial, avd_name))
                snapshot.state = {"state": "device", "boot_completed": True}

            probe_calls = []

            def probe(serial):
                probe_calls.append(serial)
                if len(probe_calls) == 1:
                    raise SetupError("Frida daemon not running")

            run = self.fake_avd_run(avd)
            self.run_provision(root, sdk, avd, device, snapshot, start, probe, run)
            self.run_provision(root, sdk, avd, device, snapshot, start, probe, run)

            self.assertEqual(starts, [("emulator-5554", "fb-macos-api35-worker-01")])
            self.assertEqual(sum(event[0] == "frida-install" for event in events), 1)
            self.assertEqual(sum(event[0] == "ca-install" for event in events), 1)
            self.assertEqual(sum(event[0] == "facebook-installed" for event in events), 1)
            self.assertEqual(sum(len(argv) > 3 and argv[1:4] == ("pm", "list", "packages")
                                 for kind, argv in events if kind == "adb"), 3)
            self.assertTrue(any(kind == "ca-install" and app_started for kind, app_started in events))
            self.assertTrue((root / "setup-input").is_dir())
            self.assertTrue((root / ".setup-cache/configured").is_file())

    def test_booting_device_is_waited_without_starting_a_second_avd(self):
        worker = make_worker()
        snapshots = iter([
            {"state": "device", "boot_completed": False},
            {"state": "device", "boot_completed": True},
        ])
        start = Mock(side_effect=AssertionError("must wait for existing emulator"))
        result = setup.ensure_worker_started(worker, lambda _serial: next(snapshots), start,
                                             sleep=lambda _seconds: None)
        self.assertTrue(result["boot_completed"])
        start.assert_not_called()

    def test_avd_data_without_index_is_a_non_destructive_collision(self):
        with tempfile.TemporaryDirectory() as directory:
            avd_home = Path(directory)
            (avd_home / (make_worker().avd_name + ".avd")).mkdir()
            run = Mock()
            with self.assertRaisesRegex(SetupError, "缺少索引"):
                setup.ensure_avd(make_worker(), {"avd_home": str(avd_home)}, Path(directory), run=run)
            run.assert_not_called()

    def test_missing_package_probe_does_not_use_pm_path(self):
        calls = []

        class MissingPackage:
            def adb_command(self, *args, **kwargs):
                calls.append(args)
                return ""

        self.assertFalse(setup.package_installed(MissingPackage()))
        self.assertEqual(calls, [("shell", "pm", "list", "packages", setup.PACKAGE)])

    def test_invalid_apk_package_is_rejected_before_install(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sdk = self.make_sdk(root)
            apk = root / "wrong.apk"
            apk.write_bytes(b"fake apk")
            device = FakeDevice("emulator-5554", sdk)
            profile = {
                "facebook_apks": [str(apk)],
                "frida_server": "",
                "download_frida": False,
                "install_sdk_packages": True,
                "configure_app_links": True,
            }
            with patch.object(setup, "command", return_value="com.example.other"):
                with self.assertRaisesRegex(SetupError, "不是 com.facebook.katana"):
                    setup.install_facebook(root, profile, device, sdk)
            self.assertFalse(any(event[0] == "facebook-installed" for event in device.events))

    def test_live_frida_daemon_is_stopped_before_reinstall(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            server = root / "frida-server"
            server.write_bytes(b"local")
            events = []
            device = FakeDevice("emulator-5554", root / "sdk", events)
            device.frida_running = True
            profile = {
                "frida_server": str(server),
                "download_frida": False,
            }
            setup.ensure_frida(device, device.serial, root, profile, probe=lambda _serial: None)
            kinds = [event[0] for event in events]
            kill_index = events.index(("adb", ("shell", "kill", "103")))
            self.assertLess(kill_index, kinds.index("frida-install"))
            self.assertTrue(device.frida_binary)

    def test_same_name_non_frida_process_is_never_killed(self):
        with tempfile.TemporaryDirectory() as directory:
            device = FakeDevice("emulator-5554", Path(directory))
            device.frida_running = True
            original_shell = device.shell

            def shell(script, timeout=20):
                if script == "readlink -f /proc/103/exe":
                    return "/data/local/tmp/other-server"
                return original_shell(script, timeout=timeout)

            device.shell = shell
            self.assertFalse(setup.stop_frida_server(device))
            self.assertNotIn(("adb", ("shell", "kill", "103")), device.events)

    def test_pinned_frida_digest_rejects_mismatched_download_without_api_call(self):
        with tempfile.TemporaryDirectory() as directory:
            fetch = Mock(return_value=b"wrong bytes")
            with patch.object(setup.importlib.metadata, "version", return_value="17.17.0"):
                with self.assertRaisesRegex(SetupError, "SHA-256 不匹配"):
                    setup.download_frida(Path(directory), fetch_fn=fetch)
            self.assertEqual(fetch.call_count, 1)
            self.assertIn("github.com/frida/frida/releases/download/17.17.0", fetch.call_args.args[0])
            self.assertNotIn("api.github.com", fetch.call_args.args[0])

    def test_configure_command_keeps_failure_visible(self):
        # This curated public repository omits the historical interactive
        # macOS launcher. Test the documented setup.sh fallback instead;
        # do not manufacture a fake one-click setup script.
        source = Path(__file__).resolve().parents[1]
        launcher = source / "CONFIGURE.command"
        if not launcher.is_file():
            setup_text = (source / "setup.sh").read_text(encoding="utf-8")
            self.assertIn("set -eu", setup_text)
            self.assertIn('exec "$PYTHON" facebook_local.py start', setup_text)
            return
        text = launcher.read_text(encoding="utf-8")
        self.assertIn("trap on_exit EXIT", text)
        self.assertIn("read -r -p", text)
        self.assertNotIn("exec /bin/bash ./setup.sh", text)

    @unittest.skipUnless(sys.platform == "darwin", "fcntl contention is verified on macOS CI")
    def test_serial_lock_is_nonblocking_and_lock_file_is_preserved(self):
        serial = "emulator-5554"
        first = setup.acquire_device_lock(serial)
        try:
            with self.assertRaisesRegex(SetupError, "正在被另一个"):
                setup.acquire_device_lock(serial)
        finally:
            path = setup.device_lock_path(serial)
            setup.release_device_lock(first)
        self.assertTrue(path.is_file())


if __name__ == "__main__":
    unittest.main()
