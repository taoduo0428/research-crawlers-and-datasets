"""Network contract and Windows runtime simulations; never use a real ADB device."""
import base64
import http.client
import http.server
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest

from facebook_network import LEGACY_UPSTREAM, NetworkConfig, load_network

ROOT = Path(__file__).resolve().parents[1]
PS = shutil.which("powershell.exe")


def powershell(code, mode=None, upstream=None):
    env = os.environ.copy()
    for name, value in (("FACEBOOK_NETWORK_MODE", mode), ("FACEBOOK_UPSTREAM_PROXY", upstream)):
        env.pop(name, None)
        if value is not None:
            env[name] = value
    script = "[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); $ErrorActionPreference='Stop'; . './facebook_transport.ps1'; " + code
    encoded = base64.b64encode(script.encode("utf-16le")).decode()
    return subprocess.run([PS, "-NoProfile", "-EncodedCommand", encoded], cwd=ROOT,
                          env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=35)


class ConfigTests(unittest.TestCase):
    def test_defaults_and_environment(self):
        self.assertEqual(load_network({}), NetworkConfig())
        self.assertEqual(load_network({}, legacy=True).upstream_url, LEGACY_UPSTREAM)
        self.assertEqual(load_network({"network": {}}, legacy=True).mode, "direct")
        self.assertEqual(load_network({}).environment(), {
            "FACEBOOK_NETWORK_MODE": "direct", "FACEBOOK_UPSTREAM_PROXY": ""})

    def test_explicit_modes(self):
        for url in ("http://127.0.0.1:18090", "https://proxy.example:443", "http://[::1]:8080"):
            n = load_network({"network": {"mode": "upstream", "upstream_url": url}})
            self.assertEqual(n.environment()["FACEBOOK_UPSTREAM_PROXY"], url)
            self.assertEqual(n.mitmproxy_mode, "upstream:" + url)
        self.assertEqual(NetworkConfig().mitmproxy_mode, "regular")

    def test_invalid_config_is_sanitized(self):
        bad = [None, [], {"network": None}, {"network": []},
               {"network": {"mode": "other"}}, {"network": {"mode": "upstream"}},
               {"network": {"mode": "direct", "upstream_url": 12}}]
        bad += [{"network": {"mode": "upstream", "upstream_url": url}} for url in
                ("socks5://localhost:1234", "http://user:SECRET@localhost:80",
                 "https://host/?SECRET", "https://host/#SECRET", "http://host/SECRET",
                 "http://host:0", "http://host:65536", "http://host:",
                 "http://", "http://[invalid", " http://host", "http://host\n")]
        for settings in bad:
            with self.subTest(settings=settings):
                with self.assertRaises(ValueError) as cm:
                    load_network(settings)
                self.assertNotIn("SECRET", str(cm.exception))


# All external I/O in the transport is replaced before any runtime helper is called.
SIMULATION = r"""
$script:proxy = '127.0.0.1:7897'
$script:ports = @()
$script:capture = $null
$script:probes = @()
function Ensure-FacebookAdbReady { return $true }
function Ensure-FacebookAndroidServicesReady { return $true }
function Get-FacebookActiveDefaultNetwork { return '100' }
function Get-FacebookCaptureMarker { return $script:capture }
function Test-FacebookHostListener { param($Port) $script:probes += $Port; return $true }
function Test-FacebookUpstream { return $true }
function Test-FacebookDeviceProxyPort { param($Serial,$Port) $script:probes += $Port; return $true }
function Invoke-FacebookAdbCommand {
    param($Serial,$Arguments,$TimeoutSeconds)
    $out = ''
    if ($Arguments[0] -eq 'reverse') {
        if ($Arguments[1] -eq '--list') {
            $out = ($script:ports | ForEach-Object { "emulator tcp:$_ tcp:$_" }) -join "`n"
        } else { $script:ports += [int]($Arguments[1] -replace 'tcp:','') }
    } elseif ($Arguments[0] -eq 'shell' -and $Arguments[1] -eq 'settings') {
        if ($Arguments[2] -eq 'put') { $script:proxy = $Arguments[5] }
        $out = $script:proxy
    } else { throw 'Unexpected simulated ADB command' }
    return [pscustomobject]@{ Success=$true; Stdout=$out; TimedOut=$false; ExitCode=0 }
}
$cold = Initialize-FacebookColdBootTransport -Serial simulation -Ports @(Get-FacebookNormalReversePorts -CapturePort 18081)
$normal = Ensure-FacebookProjectTransport -Serial simulation -CapturePort 18081
$script:capture = [pscustomobject]@{ port=18081 }
$during = Ensure-FacebookProjectTransport -Serial simulation -CapturePort 18081
$script:capture = $null
$restored = Ensure-FacebookProjectTransport -Serial simulation -CapturePort 18081
[pscustomobject]@{ cold=$cold; normal=$normal; during=$during; restored=$restored;
    probes=$script:probes; ports=$script:ports; mitm=(Get-FacebookMitmMode) } | ConvertTo-Json -Depth 5 -Compress
"""


@unittest.skipUnless(PS, "Windows PowerShell required")
class WindowsNetworkTests(unittest.TestCase):
    def test_lifecycle_both_modes_and_legacy(self):
        for mode, url, expected, normal_port in (
            ("direct", "", ":0", None),
            ("upstream", "http://127.0.0.1:18090", "127.0.0.1:18090", 18090),
            ("upstream", "http://proxy.example:3128", "proxy.example:3128", None),
            (None, None, "127.0.0.1:7897", 7897),
        ):
            with self.subTest(mode=mode, url=url):
                result = powershell(SIMULATION, mode, url)
                self.assertEqual(result.returncode, 0, result.stderr)
                data = json.loads(result.stdout)
                self.assertEqual(data["normal"]["proxy"], expected)
                self.assertEqual(data["during"]["proxy"], "127.0.0.1:18081")
                self.assertEqual(data["restored"]["proxy"], expected)
                self.assertTrue(data["cold"]["reverse_capture"])
                self.assertTrue(data["restored"]["reverse_normal"])
                self.assertEqual(set(data["ports"]), {18081} | ({normal_port} if normal_port else set()))
                if mode == "direct":
                    self.assertEqual(data["probes"], [])
                    self.assertEqual(data["mitm"], "regular")
                if mode == "upstream":
                    self.assertNotIn(7897, data["probes"])
                    self.assertEqual(data["mitm"], "upstream:" + url)

    def test_powershell_validation_and_https(self):
        for url in ("http://user:SECRET@host", "http://host:0", "http://host:65536",
                    "http://host:", "http://host/SECRET", "http://host/?SECRET", "socks5://host"):
            result = powershell("Get-FacebookNetworkConfig | Out-Null", "upstream", url)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("SECRET", result.stdout + result.stderr)
        result = powershell("Get-FacebookNetworkConfig | Select-Object mode,normal_proxy,relay_port | ConvertTo-Json", "upstream", "https://proxy.example")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = json.loads(result.stdout)
        self.assertGreaterEqual(config["relay_port"], 20000)
        self.assertEqual(config["normal_proxy"], f"127.0.0.1:{config['relay_port']}")

    def test_direct_cannot_start_clash_or_probe_host(self):
        result = powershell("function Test-FacebookHostListener { throw 'must not probe' }; "
                            "function Start-Process { throw 'must not launch' }; "
                            "Ensure-FacebookNormalNetwork", "direct", "")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_facebook_url_launcher_binds_explicit_component_and_flags(self):
        result = powershell(r"""
$script:captured = @()
function Invoke-FacebookAdbCommand {
    param($Serial, $Arguments, $TimeoutSeconds)
    $script:captured = @($Arguments)
    return [pscustomobject]@{ Success=$true; Stdout='Status: ok'; Stderr=''; ExitCode=0 }
}
if (-not (Start-FacebookUrlBounded -Serial simulation -Url 'https://www.facebook.com/123/posts/456')) {
    throw 'explicit URL launcher returned failure'
}
$script:captured | ConvertTo-Json -Compress
""")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [
            "shell", "am", "start", "-W", "-a", "android.intent.action.VIEW",
            "-d", "https://www.facebook.com/123/posts/456", "-n",
            "com.facebook.katana/.IntentUriHandler", "-f", "0x34000000",
        ])

    def test_ps_syntax_and_capture_collision(self):
        result = powershell(r"""
foreach ($file in @('facebook_transport.ps1','run_facebook_capture.ps1','start_facebook_stable.ps1','facebook_network_watch.ps1')) {
    $tokens=$null; $errors=$null
    $ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $PWD $file),[ref]$tokens,[ref]$errors)
    if ($errors.Count) { throw ($errors | Out-String) }
    if ($file -eq 'run_facebook_capture.ps1') {
        $node=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Clear-StaleProjectCaptureListener'},$true)
        Invoke-Expression $node.Extent.Text
    }
}
function Get-CapturePortListenerPids { return @(424242) }
function Stop-Process { throw 'Must not kill unknown process' }
if ((Clear-StaleProjectCaptureListener 18081) -ne 424242) { throw 'lost collision' }
""")
        self.assertEqual(result.returncode, 0, result.stderr)


class MitmdumpIntegrationTests(unittest.TestCase):
    def test_real_direct_http(self):
        if os.environ.get("FACEBOOK_RUN_REAL_MITM", "").strip() != "1":
            self.skipTest("real mitmdump integration is opt-in; contract tests remain enabled")
        exe = ROOT / ".venv" / ("Scripts/mitmdump.exe" if os.name == "nt" else "bin/mitmdump")
        if not exe.exists():
            self.skipTest("project .venv mitmdump unavailable")

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                payload = b"facebook-direct-network-test"
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            port = reserve.getsockname()[1]
        process = None
        try:
            with tempfile.TemporaryDirectory(prefix="fb-network-test-") as tmp:
                env = os.environ.copy()
                for key in list(env):
                    if key.lower() in ("http_proxy", "https_proxy", "all_proxy", "no_proxy"):
                        del env[key]
                mode = NetworkConfig().mitmproxy_mode
                if PS:
                    result = powershell("Get-FacebookMitmMode", "direct", "")
                    self.assertEqual(result.returncode, 0, result.stderr)
                    mode = result.stdout.strip()
                with open(Path(tmp) / "mitm.log", "w+b") as log:
                    process = subprocess.Popen([str(exe), "--quiet", "--listen-host", "127.0.0.1",
                        "--listen-port", str(port), "--mode", mode, "--set", f"confdir={tmp}"],
                        stdout=log, stderr=log, env=env)
                    try:
                        for _ in range(100):
                            if process.poll() is not None:
                                self.fail("isolated mitmdump exited")
                            try:
                                with socket.create_connection(("127.0.0.1", port), timeout=.2):
                                    break
                            except OSError:
                                time.sleep(.1)
                        # Absolute-form HTTP request explicitly traverses the proxy, ignoring NO_PROXY.
                        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                        try:
                            connection.request("GET", f"http://127.0.0.1:{server.server_port}/probe")
                            response = connection.getresponse()
                            self.assertEqual(response.status, 200)
                            self.assertEqual(response.read(), b"facebook-direct-network-test")
                        finally:
                            connection.close()
                    finally:
                        if os.name == "nt" and process.poll() is None:
                            # The Windows console entry point can own a Python child.
                            subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                                           capture_output=True, timeout=10, check=True)
                        elif process.poll() is None:
                            process.terminate()
                        process.wait(timeout=10)
        finally:
            if process and process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
