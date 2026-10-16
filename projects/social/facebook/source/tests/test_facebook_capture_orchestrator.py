from pathlib import Path
import base64
import json
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from analyze_facebook_capture import target_url_digest, verify_target_capture

ROOT = Path(__file__).resolve().parents[1]
RUN = (ROOT / "run_facebook_capture.ps1").read_text(encoding="utf-8-sig")
BATCH = (ROOT / "run_facebook_batch.ps1").read_text(encoding="utf-8-sig")
QUEUE = (ROOT / "run_facebook_post_queue.ps1").read_text(encoding="utf-8-sig")
EXPLORE = (ROOT / "explore_facebook_readonly.ps1").read_text(encoding="utf-8-sig")
OPEN = (ROOT / "open_facebook_post_readonly.ps1").read_text(encoding="utf-8-sig")
SSL_UNPIN = (ROOT / "facebook_ssl_unpin.js").read_text(encoding="utf-8-sig")
ADDON = (ROOT / "facebook_capture_addon_safe.py").read_text(encoding="utf-8-sig")
PROGRESS = (ROOT / "facebook_capture_progress.py").read_text(encoding="utf-8-sig")
PROBE = (ROOT / "probe_facebook_reaction_health.ps1").read_text(encoding="utf-8-sig")
START_API = (ROOT / "start_facebook_capture_api.ps1").read_text(encoding="utf-8-sig")
TRANSPORT = (ROOT / "facebook_transport.ps1").read_text(encoding="utf-8-sig")
STOP_AVD = (ROOT / "stop_facebook_avd_clean.ps1").read_text(encoding="utf-8-sig")
STOP_ALL = (ROOT / "stop_all_facebook.ps1").read_text(encoding="utf-8-sig")
STOP_CMD = (ROOT / "STOP.cmd").read_text(encoding="utf-8-sig")
ADD_WORKER = (ROOT / "add_facebook_worker.ps1").read_text(encoding="utf-8-sig")
ADD_WORKER_CMD = (ROOT / "add_facebook_worker.cmd").read_text(encoding="utf-8-sig")
CERTIFICATE = (ROOT / "facebook_certificate.ps1").read_text(encoding="utf-8-sig")
START_STABLE = (ROOT / "start_facebook_stable.ps1").read_text(encoding="utf-8-sig")
NATIVE_HELPER = (ROOT / "android-ui-dump-helper" / "src" / "com" / "openai" / "facebookuidump" / "DumpInstrumentation.java").read_text(encoding="utf-8-sig")
WEB_V2_APP = (ROOT / "web_v2" / "app.js").read_text(encoding="utf-8-sig")
WEB_APP = (ROOT / "web" / "app.js").read_text(encoding="utf-8-sig")


class CancellationConsumerContractTests(unittest.TestCase):
    def test_v2_ui_keeps_cancelling_batches_active_and_visible(self):
        self.assertIn('"cancelling"', WEB_V2_APP)
        self.assertIn('new Set(["running", "queued", "cancelling"])', WEB_V2_APP)
        self.assertIn('(counts.running || 0) + (counts.queued || 0) + (counts.cancelling || 0)', WEB_V2_APP)
        self.assertIn('post.status === "cancelling"', WEB_V2_APP)

    def test_legacy_ui_keeps_cancelling_jobs_polling_and_cancellable(self):
        self.assertIn('cancelling:', WEB_APP)
        self.assertIn('status === "cancelling"', WEB_APP)
        self.assertIn('(counts.running || 0) + (counts.queued || 0) + (counts.cancelling || 0)', WEB_APP)


class StopAllRuntimeContractTests(unittest.TestCase):
    def test_stop_cmd_delegates_to_dynamic_project_stop_script(self):
        self.assertIn('stop_all_facebook.ps1', STOP_CMD)
        self.assertIn('facebook_workers.json', STOP_ALL)
        self.assertIn('[string]$worker.serial', STOP_ALL)
        self.assertIn('[string]$worker.avd_name', STOP_ALL)
        self.assertIn('Stop-FacebookTransportWatch', STOP_ALL)
        self.assertIn('facebook_capture_api.py', STOP_ALL)
        self.assertIn('start_ui_v2.py', STOP_ALL)
        self.assertIn('run_facebook_capture.ps1', STOP_ALL)
        self.assertIn('facebook_capture_addon_safe.py', STOP_ALL)
        self.assertIn("@('emulator.exe', 'qemu-system-x86_64.exe')", STOP_ALL)
        self.assertNotIn('emulator-5554', STOP_ALL)
        self.assertNotIn('fb-x86_64-api35-worker-02', STOP_ALL)
        self.assertNotIn('adb.exe kill-server', STOP_ALL)


class ReactionSummaryIconFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._powershell = shutil.which("powershell.exe") or shutil.which("pwsh")
        if not cls._powershell:
            raise unittest.SkipTest("PowerShell is required for the selector fixture tests")

        get_nodes_start = EXPLORE.index("function Get-UiNodes")
        get_nodes_end = EXPLORE.index("function Find-Node", get_nodes_start)
        icon_start = EXPLORE.index("function Find-ReactionSummaryIconNode")
        icon_end = EXPLORE.index("function Find-ShareCountNode", icon_start)
        cls._selector_functions = EXPLORE[get_nodes_start:get_nodes_end] + EXPLORE[icon_start:icon_end]

    @staticmethod
    def _node(
        bounds,
        *,
        text="",
        content_desc="",
        class_name="android.view.View",
        package="com.facebook.katana",
        clickable=False,
        scrollable=False,
        naf=False,
    ):
        return (
            f'<node text="{text}" content-desc="{content_desc}" class="{class_name}" '
            f'package="{package}" clickable="{str(clickable).lower()}" '
            f'scrollable="{str(scrollable).lower()}" NAF="{str(naf).lower()}" '
            f'bounds="{bounds}" />'
        )

    @classmethod
    def _fixture(
        cls,
        *,
        row=(752, 868),
        candidate_bounds=None,
        candidate_label="",
        candidate_package="com.facebook.katana",
        candidate_class="android.view.ViewGroup",
        candidate_clickable=True,
        candidate_naf=True,
        include_candidate=True,
        include_like=True,
        include_comment=True,
        include_share=True,
        viewport_bounds="[0,179][1080,1719]",
        composer_bounds=None,
    ):
        y1, y2 = row
        nodes = [
            cls._node(
                viewport_bounds,
                class_name="androidx.recyclerview.widget.RecyclerView",
                scrollable=True,
            )
        ]
        if composer_bounds:
            nodes.append(
                cls._node(
                    composer_bounds,
                    class_name="android.widget.AutoCompleteTextView",
                    clickable=True,
                )
            )
        if include_candidate:
            nodes.append(
                cls._node(
                    candidate_bounds or f"[964,{y1}][1048,{y2}]",
                    content_desc=candidate_label,
                    class_name=candidate_class,
                    package=candidate_package,
                    clickable=candidate_clickable,
                    naf=candidate_naf,
                )
            )
        if include_like:
            nodes.append(cls._node(f"[0,{y1}][190,{y2}]", text="Like button", class_name="android.widget.Button", clickable=True))
        if include_comment:
            nodes.append(cls._node(f"[190,{y1}][361,{y2}]", text="Comment", class_name="android.widget.Button", clickable=True))
        if include_share:
            nodes.append(cls._node(f"[361,{y1}][532,{y2}]", text="Share button", class_name="android.widget.Button", clickable=True))
        return "<hierarchy>" + "".join(nodes) + "</hierarchy>"

    def _find(self, xml):
        with tempfile.TemporaryDirectory(prefix="facebook-selector-") as temp_dir:
            xml_path = Path(temp_dir) / "fixture.xml"
            xml_path.write_text(xml, encoding="utf-8")
            powershell_path = str(xml_path).replace("'", "''")
            script = (
                "$ErrorActionPreference = 'Stop'\n"
                + "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)\n"
                + self._selector_functions
                + "\n$result = Find-ReactionSummaryIconNode -Path '"
                + powershell_path
                + "'\n"
                + "if ($null -eq $result) { 'null' } else { $result | ConvertTo-Json -Compress }\n"
            )
            encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
            completed = subprocess.run(
                [self._powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8-sig",
                capture_output=True,
                timeout=30,
                check=False,
            )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"PowerShell selector fixture failed:\nstdout={completed.stdout}\nstderr={completed.stderr}",
        )
        output = completed.stdout.strip()
        if output == "null":
            return None
        return json.loads(output)

    def test_y752_reaction_summary_icon_is_found(self):
        result = self._find(self._fixture(row=(752, 868)))
        self.assertIsNotNone(result)
        self.assertEqual(result["Y1"], 752)

    def test_y1113_reaction_summary_icon_is_found(self):
        result = self._find(self._fixture(row=(1113, 1229)))
        self.assertIsNotNone(result)
        self.assertEqual(result["Y1"], 1113)

    @classmethod
    def _compact_chinese_group_fixture(cls):
        # Geometry from the failed group permalink: the unlabeled summary sits
        # to the right of a localized, narrower Like/Comment/Share action row.
        return "<hierarchy>" + "".join([
            cls._node("[0,179][1080,1719]", class_name="androidx.recyclerview.widget.RecyclerView", scrollable=True),
            cls._node("[0,986][171,1102]", text="赞按钮，双击并长按即可给评论留下心情。", class_name="android.widget.Button", clickable=True),
            cls._node("[171,986][288,1102]", content_desc="评论", class_name="android.widget.Button", clickable=True),
            cls._node("[288,986][440,1102]", text="分享按钮。双击即可分享帖子。", class_name="android.widget.Button", clickable=True),
            cls._node("[1006,986][1048,1102]", class_name="android.view.ViewGroup", clickable=True, naf=True),
            cls._node("[210,1736][1048,1840]", class_name="android.widget.AutoCompleteTextView", clickable=True),
        ]) + "</hierarchy>"

    def test_compact_chinese_group_summary_icon_is_found(self):
        result = self._find(self._compact_chinese_group_fixture())
        self.assertEqual(result["Bounds"], "[1006,986][1048,1102]")

    def test_summary_rejects_share_overlapping_comment(self):
        xml = self._compact_chinese_group_fixture().replace("[288,986][440,1102]", "[200,986][440,1102]")
        self.assertIsNone(self._find(xml))

    def test_source_discovery_scan_prefers_summary_over_comment_composer(self):
        start = EXPLORE.index("function Find-EntryWithFeedScan")
        end = EXPLORE.index("function Find-StrictTargetReactionEntry", start)
        # Exercise the real scan and selector together; no ADB is involved.
        functions = self._selector_functions + "\n" + EXPLORE[start:end] + """
function Get-PageState { return 'placeholder-or-unknown' }
function Get-FeedSurfaceState { return 'unknown' }
function Find-ReactionCountNode { return $null }
function Find-CommentsEntryNode { return [pscustomobject]@{ Label = 'Comment' } }
function Invoke-FacebookAdbQuiet { throw 'Unexpected gesture before summary selection' }
function Invoke-ScanFixture([string]$Path) {
    $scan = Find-EntryWithFeedScan 'reaction' $Path 'reaction' 1
    return $scan.Entry
}
"""
        with tempfile.TemporaryDirectory(prefix="facebook-group-scan-") as temp:
            path = Path(temp) / "fixture.xml"
            path.write_text(self._compact_chinese_group_fixture(), encoding="utf-8")
            script = "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)\n$ErrorActionPreference='Stop'\n" + functions
            script += "\nInvoke-ScanFixture '" + str(path).replace("'", "''") + "' | ConvertTo-Json -Compress"
            result = subprocess.run([self._powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", base64.b64encode(script.encode("utf-16-le")).decode("ascii")], capture_output=True, encoding="utf-8-sig", timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["Bounds"], "[1006,986][1048,1102]")

    def test_other_content_viewport_row_is_found(self):
        result = self._find(self._fixture(row=(900, 1016)))
        self.assertIsNotNone(result)
        self.assertEqual(result["Y1"], 900)

    def test_comment_reaction_row_is_not_accepted(self):
        result = self._find(
            self._fixture(row=(1300, 1410), candidate_label="Like Alice's comment button")
        )
        self.assertIsNone(result)

    def test_missing_share_is_not_accepted(self):
        self.assertIsNone(self._find(self._fixture(include_share=False)))

    def test_bare_comment_action_is_not_accepted(self):
        self.assertIsNone(self._find(self._fixture(include_candidate=False, include_like=False, include_share=False)))

    def test_non_facebook_candidate_is_not_accepted(self):
        self.assertIsNone(self._find(self._fixture(candidate_package="com.example.other")))

    def test_header_candidate_is_not_accepted(self):
        self.assertIsNone(self._find(self._fixture(row=(120, 236))))

    def test_composer_obscured_candidate_is_not_accepted(self):
        self.assertIsNone(
            self._find(self._fixture(row=(1300, 1450), composer_bounds="[32,1400][1048,1500]"))
        )

    def test_abnormally_sized_candidate_is_not_accepted(self):
        self.assertIsNone(self._find(self._fixture(row=(752, 950))))


class SelectorFixtureInvocationTests(unittest.TestCase):
    def test_selector_fixture_is_independent_of_cp1252_stdin_defaults(self):
        selector = ReactionSummaryIconFixtureTests()
        selector._powershell = shutil.which("powershell.exe") or shutil.which("pwsh")
        if not selector._powershell:
            self.skipTest("PowerShell is required for the selector fixture tests")
        ReactionSummaryIconFixtureTests.setUpClass()
        with mock.patch("subprocess._text_encoding", return_value="cp1252"):
            result = selector._find(selector._fixture(row=(752, 868)))
        self.assertEqual(result["Y1"], 752)


class StrictTargetFallbackTests(unittest.TestCase):
    @staticmethod
    def _pfbid_navigation_story(post_id="123456789", author_id="987654321"):
        return {
            "__typename": "Story",
            "post_id": post_id,
            "legacy_api_story_id": f"{author_id}_{post_id}",
            "actors": [{"id": author_id}],
            "feedback": {"legacy_api_post_id": post_id},
            "wwwURL": "https://www.facebook.com/987654321/posts/pfbidNewToken456",
        }

    def test_pfbid_navigation_accepts_rotated_token_only_for_exact_request(self):
        requested_url = "https://www.facebook.com/example/posts/pfbidOldToken123"
        app_navigation_url = "https://facebook.com/example/posts/pfbidOldToken123"
        relay_story_id = base64.b64encode(
            b"S:_I987654321:123456789:123456789"
        ).decode("ascii")
        record = {
            "friendly_name": "FBVideoHomeExternalPostLinkQuery",
            "status_code": 200,
            "request_navigation_url_hash": target_url_digest(app_navigation_url),
            "response_body": json.dumps(
                {
                    "data": {
                        "video_home_deeplink": self._pfbid_navigation_story(),
                        "story_reference": {"__typename": "Story", "id": relay_story_id},
                    }
                }
            ),
        }

        verified = verify_target_capture([], [(12, record)], expected_url=requested_url)

        self.assertEqual(verified["status"], "matched")
        self.assertEqual(verified["matched_by"], "pfbid-external-post-link-navigation")
        self.assertEqual(verified["matched_post_id"], "123456789")
        self.assertEqual(verified["resolved_author_id"], "987654321")
        self.assertEqual(verified["matched_legacy_api_story_id"], "987654321_123456789")

    def test_pfbid_navigation_rejects_unbound_ambiguous_or_wrong_post_responses(self):
        requested_url = "https://www.facebook.com/example/posts/pfbidOldToken123"
        exact_hash = target_url_digest(requested_url)
        story = self._pfbid_navigation_story()
        cases = {
            "missing-hash": {
                "friendly_name": "FBVideoHomeExternalPostLinkQuery",
                "status_code": 200,
                "response_body": json.dumps({"data": {"node": story}}),
            },
            "wrong-hash": {
                "friendly_name": "FBVideoHomeExternalPostLinkQuery",
                "status_code": 200,
                "request_navigation_url_hash": target_url_digest(requested_url + "?wrong=1"),
                "response_body": json.dumps({"data": {"node": story}}),
            },
            "feed-query": {
                "friendly_name": "fresh_feed_new_data_fetch",
                "status_code": 200,
                "request_navigation_url_hash": exact_hash,
                "response_body": json.dumps({"data": {"node": story}}),
            },
            "multiple-posts": {
                "friendly_name": "FBVideoHomeExternalPostLinkQuery",
                "status_code": 200,
                "request_navigation_url_hash": exact_hash,
                "response_body": json.dumps(
                    {
                        "data": [
                            story,
                            self._pfbid_navigation_story(post_id="2", author_id="22"),
                        ]
                    }
                ),
            },
            "second-incomplete-post": {
                "friendly_name": "FBVideoHomeExternalPostLinkQuery",
                "status_code": 200,
                "request_navigation_url_hash": exact_hash,
                "response_body": json.dumps(
                    {
                        "data": [
                            story,
                            {
                                "__typename": "Story",
                                "post_id": "2",
                                "legacy_api_story_id": "22_2",
                                "feedback": {"legacy_api_post_id": "2"},
                            },
                        ]
                    }
                ),
            },
            "unidentified-story": {
                "friendly_name": "FBVideoHomeExternalPostLinkQuery",
                "status_code": 200,
                "request_navigation_url_hash": exact_hash,
                "response_body": json.dumps(
                    {
                        "data": [
                            story,
                            {"__typename": "Story", "id": "opaque"},
                        ]
                    }
                ),
            },
            "inconsistent-legacy-id": {
                "friendly_name": "FBVideoHomeExternalPostLinkQuery",
                "status_code": 200,
                "request_navigation_url_hash": exact_hash,
                "response_body": json.dumps(
                    {
                        "data": {
                            "node": {
                                **story,
                                "legacy_api_story_id": "22_123456789",
                            }
                        }
                    }
                ),
            },
            "conflicting-legacy-ids": {
                "friendly_name": "FBVideoHomeExternalPostLinkQuery",
                "status_code": 200,
                "request_navigation_url_hash": exact_hash,
                "response_body": json.dumps(
                    {
                        "data": [
                            story,
                            {
                                **story,
                                "legacy_api_story_id": "987654321_2",
                            },
                        ]
                    }
                ),
            },
        }

        for name, candidate in cases.items():
            with self.subTest(name=name):
                self.assertEqual(
                    verify_target_capture([], [(20, candidate)], expected_url=requested_url)["status"],
                    "not-matched",
                )

        exact = dict(cases["missing-hash"])
        exact["request_navigation_url_hash"] = exact_hash
        self.assertEqual(
            verify_target_capture(
                [],
                [(21, exact)],
                expected_post_id="999",
                expected_url=requested_url,
            )["status"],
            "not-matched",
        )

    def test_multi_worker_ports_are_forwarded_through_capture_stack(self):
        self.assertIn('[int]$ReactionNativeScrollPort = 18766', RUN)
        self.assertGreaterEqual(RUN.count("'-ReactionNativeScrollPort'"), 1)
        self.assertIn('[int]$ReactionNativeScrollPort = 18766', EXPLORE)
        self.assertIn('-Port $ReactionNativeScrollPort -TimeoutMs $nativeScrollTimeoutMs', EXPLORE)
        self.assertIn("Get-FacebookCaptureMarkerPath -Serial $Serial", TRANSPORT)
        self.assertIn("Set-FacebookCaptureMarker -Serial $Serial", RUN)
        self.assertIn("Clear-FacebookCaptureMarker -Serial $Serial", RUN)

    def test_reaction_probe_forwards_secondary_worker_transport_and_frida_options(self):
        self.assertIn('[int]$ProxyPort = 8081', PROBE)
        self.assertIn('[int]$ReactionNativeScrollPort = 18766', PROBE)
        self.assertIn('[switch]$EnableFridaSslUnpin', PROBE)
        self.assertIn("'-ProxyPort', [string]$ProxyPort", PROBE)
        self.assertIn("'-ReactionNativeScrollPort', [string]$ReactionNativeScrollPort", PROBE)
        self.assertIn("if ($EnableFridaSslUnpin)", PROBE)
        self.assertIn("$argsList += '-EnableFridaSslUnpin'", PROBE)

    def test_frida_unpin_is_opt_in_and_bound_to_selected_serial(self):
        self.assertIn('[switch]$EnableFridaSslUnpin', RUN)
        self.assertIn("'-D', $Serial", RUN)
        self.assertIn("@('shell','am','start','-n','com.facebook.katana/.LoginActivity')", RUN)
        self.assertIn("'-p', $facebookPid", RUN)
        self.assertIn("@('shell', 'pidof', 'fb-fs17')", RUN)
        self.assertIn("@('shell', 'su', '0', $serverPath, '-D')", RUN)
        self.assertIn("'--eternalize'", RUN)
        self.assertIn("if ($EnableFridaSslUnpin)", RUN)
        self.assertIn("if ($EnableFridaSslUnpin) { $targetArgs += @('-KeepFacebookProcess') }", RUN)
        self.assertIn('[switch]$KeepFacebookProcess', OPEN)
        self.assertIn("if ($KeepFacebookProcess)", OPEN)

    def test_frida_server_has_detached_fallback_after_daemonize_failure(self):
        daemonize = RUN.index("@('shell', 'su', '0', $serverPath, '-D')")
        detached = RUN.index("nohup $serverPath >/data/local/tmp/fb-fs17-start.log 2>&1 &")
        final_failure = RUN.index('throw "Frida server did not start on $Serial"')
        self.assertLess(daemonize, detached)
        self.assertLess(detached, final_failure)

    def test_frida_attach_retries_slow_facebook_cold_start(self):
        first_launch = RUN.index("@('shell','am','start','-n','com.facebook.katana/.LoginActivity')")
        cold_warning = RUN.index("Facebook cold start exceeded 15 seconds")
        retry_launch = RUN.index("@('shell','am','start','-W','-n','com.facebook.katana/.LoginActivity')")
        attach = RUN.index("'-p', $facebookPid")
        self.assertLess(first_launch, cold_warning)
        self.assertLess(cold_warning, retry_launch)
        self.assertLess(retry_launch, attach)

    def test_frida_attach_accepts_original_main_pid_when_pidof_includes_phantom_child(self):
        start = RUN.index("function Start-FacebookWithFridaSslUnpin")
        end = RUN.index("function Get-LatestDiagnosticAction", start)
        block = RUN[start:end]
        self.assertIn("$currentFacebookPids", block)
        self.assertIn("$facebookPid -notin $currentFacebookPids", block)
        self.assertNotIn("([string]$pidResult.Stdout).Trim() -ne $facebookPid", block)

    def test_frida_attach_waits_for_content_ready_and_refreshes_main_pid(self):
        start = RUN.index("function Start-FacebookWithFridaSslUnpin")
        end = RUN.index("function Get-LatestDiagnosticAction", start)
        block = RUN[start:end]
        ready = block.index("Wait-FacebookUiReady")
        refreshed_pid = block.index("$facebookPid = Get-FacebookMainProcessId", ready)
        attach = block.index("'-p', $facebookPid")
        self.assertLess(ready, refreshed_pid)
        self.assertLess(refreshed_pid, attach)
        self.assertIn("else { $stablePidSamples = 0 }", block)

    def test_stable_start_allows_realistic_cold_boot_budget(self):
        self.assertIn("[int]$BootTimeoutSeconds = 240", START_STABLE)
        self.assertIn("AddSeconds([Math]::Max(20, $BootTimeoutSeconds))", START_STABLE)

    def test_selected_capture_port_is_forwarded_through_transport_recovery(self):
        confirm_start = TRANSPORT.index("function Confirm-FacebookUiOnline")
        confirm_block = TRANSPORT[confirm_start:]
        self.assertIn("[int]$CapturePort = 8081", confirm_block)
        self.assertIn("Ensure-FacebookProjectTransport -Serial $Serial -CapturePort $CapturePort", confirm_block)
        self.assertIn("Ensure-FacebookReverseMappings -Serial $Serial -Ports @(7897, $CapturePort)", confirm_block)
        self.assertIn("Confirm-FacebookUiOnline -Serial $Serial -CapturePort $ProxyPort", RUN)
        self.assertIn("Confirm-FacebookUiOnline -Serial $Serial -CapturePort $CapturePort", START_STABLE)
        self.assertIn("[int]$ProxyPort = 8081", BATCH)
        self.assertNotIn("@(7897, 8081)", BATCH)
        self.assertIn("'-ProxyPort', [string]$ProxyPort", BATCH)
        self.assertIn("[int]$ProxyPort = 8081", OPEN)
        self.assertIn("Ensure-FacebookProjectTransport -Serial $Serial -CapturePort $ProxyPort", OPEN)
        self.assertIn("[int]$ProxyPort = 8081", EXPLORE)
        self.assertIn("Ensure-FacebookProjectTransport -Serial $Serial -CapturePort $ProxyPort", EXPLORE)
        retry_target = RUN[RUN.index("$retryTargetArgs = @("):]
        retry_readonly = RUN[RUN.index("$retryReadonlyArgs = @("):]
        self.assertIn("'-ProxyPort', [string]$ProxyPort", retry_target)
        self.assertIn('"-ProxyPort", [string]$ProxyPort', retry_readonly)
        fallback_target = RUN[RUN.index("$targetOpenArgs = @("):]
        fallback_explorer = RUN[RUN.index("$fallbackArgs = @("):]
        self.assertIn("'-ProxyPort', [string]$ProxyPort", fallback_target)
        self.assertIn("'-ProxyPort', [string]$ProxyPort", fallback_explorer)
        self.assertIn("Start-FacebookTransportWatch -Serial $Serial -BasePath $Base -CapturePort $ProxyPort", BATCH)

    def test_frida_process_is_preserved_across_native_fallback_search(self):
        fallback_target = RUN[RUN.index("$targetOpenArgs = @("):]
        self.assertIn("if ($EnableFridaSslUnpin) { $targetOpenArgs += '-KeepFacebookProcess' }", fallback_target)
        search_start = OPEN.index("function Open-BySearch")
        search = OPEN[search_start:]
        self.assertIn("if (-not $KeepFacebookProcess)", search)
        composite_start = fallback_target.index("$record.composite_deeplink_attempted = $true")
        composite = fallback_target[composite_start:]
        self.assertIn("if (-not $EnableFridaSslUnpin)", composite)

    def test_frida_unpin_uses_coldstart_x509_without_mbedtls_or_loader_hooks(self):
        self.assertIn('Module.findGlobalExportByName("X509_verify_cert")', SSL_UNPIN)
        self.assertIn('Process.findModuleByName("libcoldstart.so")', SSL_UNPIN)
        self.assertIn('coldstart.findExportByName("X509_verify_cert")', SSL_UNPIN)
        self.assertIn("const retryTimer = setInterval", SSL_UNPIN)
        self.assertIn("clearInterval(retryTimer)", SSL_UNPIN)
        self.assertNotIn('findExportByName("mbedtls_x509_crt_verify")', SSL_UNPIN)
        self.assertNotIn('Module.findGlobalExportByName("android_dlopen_ext")', SSL_UNPIN)
        self.assertNotIn('Module.findGlobalExportByName("dlopen")', SSL_UNPIN)

    def test_secondary_worker_management_creates_clean_avd_and_protects_primary(self):
        path = ROOT / "manage_facebook_workers.ps1"
        self.assertTrue(path.is_file())
        text = path.read_text(encoding="utf-8-sig") if path.is_file() else ""
        self.assertIn("avdmanager.bat", text)
        self.assertIn("create", text)
        self.assertIn("system-images;android-35;google_apis;x86_64", text)
        self.assertIn("Worker-01 cannot be created or replaced", text)
        self.assertNotIn("Copy-Item", text)
        for action in ("Create", "Start", "Status", "Enable", "Disable"):
            self.assertIn(action, text)

    def test_one_click_worker_bootstrap_has_resumable_manual_login_boundary(self):
        self.assertIn("[string]$Action = 'Add'", ADD_WORKER)
        for action in ("'Add'", "'Resume'", "'Finalize'", "'Status'"):
            self.assertIn(action, ADD_WORKER)
        self.assertIn("'reserve', '--config'", ADD_WORKER)
        self.assertIn("-Status 'awaiting-login'", ADD_WORKER)
        self.assertIn("-Status 'failed'", ADD_WORKER)
        self.assertIn("-Status 'ready'", ADD_WORKER)
        self.assertIn("add_facebook_worker.ps1", ADD_WORKER_CMD)
        self.assertIn("%*", ADD_WORKER_CMD)

    def test_worker_bootstrap_copies_only_apks_and_frida_not_account_data(self):
        self.assertIn("'shell','pm','path','com.facebook.katana'", ADD_WORKER)
        self.assertIn("@('install-multiple','-r') + $localPaths", ADD_WORKER)
        self.assertIn("@('pull','/data/local/tmp/fb-fs17'", ADD_WORKER)
        lowered = ADD_WORKER.lower()
        self.assertNotIn("/data/user/0", lowered)
        self.assertNotIn("userdata-qemu", lowered)
        self.assertNotIn("cookies", lowered)

    def test_android15_ca_install_is_verified_in_system_and_conscrypt_stores(self):
        self.assertIn("-subject_hash_old", CERTIFICATE)
        self.assertIn("@('remount')", CERTIFICATE)
        self.assertIn("/system/etc/security/cacerts/$certificateHash.0", CERTIFICATE)
        self.assertIn("/apex/com.android.conscrypt/cacerts/$certificateHash.0", CERTIFICATE)
        self.assertIn("nsenter -t $pid -m -- mount --bind", CERTIFICATE)
        self.assertIn("conscrypt_runtime_installed = $true", CERTIFICATE)
        self.assertIn("[switch]$WritableSystem", START_STABLE)
        self.assertIn("[switch]$RestoreMitmCertificate", START_STABLE)
        self.assertIn("if ($WritableSystem -or $RestoreMitmCertificate)", START_STABLE)
        self.assertIn("$emulatorArgs += '-writable-system'", START_STABLE)

    def test_finalize_probes_before_enabling_worker(self):
        login_index = ADD_WORKER.index("Assert-FacebookLoginCompleted -Serial $serial")
        probe_index = ADD_WORKER.index("& $PowerShellExe -NoProfile -ExecutionPolicy Bypass -File $ProbeScript")
        probe_passed_index = ADD_WORKER.index("-Stage $script:CurrentStage", probe_index)
        enable_index = ADD_WORKER.index('/v1/workers/$id/enable')
        self.assertLess(login_index, probe_index)
        self.assertLess(probe_index, probe_passed_index)
        self.assertLess(probe_passed_index, enable_index)

    def test_finalize_detects_logged_out_activity_before_capture(self):
        self.assertIn("function Assert-FacebookLoginCompleted", ADD_WORKER)
        self.assertIn("[^\\s]*loggedout", ADD_WORKER)
        self.assertIn("analytics_module=com\\.bloks\\.www\\.caa\\.login", ADD_WORKER)
        self.assertIn("-Status 'awaiting-login'", ADD_WORKER)

    def test_numeric_story_url_is_normalized_to_shell_safe_post_path(self):
        self.assertIn("(?:(?:www|m)\\.)?facebook\\.com", OPEN)
        self.assertIn("story_fbid=(pfbid[A-Za-z0-9]+|[0-9]{1,32})", OPEN)
        self.assertIn('return "https://www.facebook.com/$($owner.Groups[1].Value)/posts/$($story.Groups[1].Value)"', OPEN)

    def test_watch_video_url_is_normalized_to_supported_mobile_reel_detail(self):
        self.assertIn("/watch/\\?v=[0-9]{1,32}", OPEN)
        self.assertIn("(?:^|[?&])v=([0-9]{1,32})(?:&|$)", OPEN)
        self.assertIn('return "https://www.facebook.com/reel/$($video.Groups[1].Value)/"', OPEN)
        self.assertIn("$mobileTargetUrl = Convert-ToFacebookIntentUrl $ResolvedUrl", OPEN)
        self.assertIn("$ResolvedUrl = $mobileTargetUrl", OPEN)

    def test_non_reel_target_uses_unique_network_identity_not_viewport_body(self):
        final_gate = OPEN[OPEN.index("$verificationReportPath = Join-Path $verifyDir"):]
        self.assertIn("target-network-match-has-no-numeric-post-id", final_gate)
        self.assertIn("target-network-match-is-not-a-unique-post-identity", final_gate)
        self.assertIn("$identityAuthors.Count -ne 1", final_gate)
        self.assertIn("target-ui-invalid-after-network-match", final_gate)
        self.assertNotIn("target-ui-not-bound-after-network-match", final_gate)
        self.assertNotIn("Invoke-TargetReadinessProbe $UiPath 5000 -RequireDetail", final_gate)

    def test_pfbid_network_identity_is_reopened_once_as_numeric_target(self):
        self.assertIn("reopening verified pfbid identity as numeric target", OPEN)
        self.assertIn('"https://www.facebook.com/$($script:ResolvedAuthorId)/posts/$($script:ResolvedPostId)"', OPEN)
        self.assertIn("$Method += '-verified-numeric-reopen'", OPEN)
        self.assertIn("$verifiedBoundary = $script:VerificationMinLine", OPEN)
        self.assertIn("$script:VerificationMinLine = $verifiedBoundary", OPEN)

    def test_reel_ui_authorizes_only_bounded_fresh_source_discovery(self):
        self.assertIn("function Test-ReelDetailSurface", OPEN)
        self.assertIn("reel-ui-source-discovery-required", OPEN)
        self.assertIn("$screenRight * 0.70", OPEN)
        self.assertIn("$script:PrimarySourceDiscoveryAllowed = $true", RUN)
        self.assertIn("one fresh unique source_post_id is required before pagination", RUN)
        self.assertIn('"-AllowSourceDiscovery"', RUN)
        self.assertIn("$sourceDiscovery.source_post_id", RUN)

    def test_android_target_url_uses_explicit_facebook_component_without_resolver(self):
        self.assertIn("function Start-FacebookUrlBounded", TRANSPORT)
        self.assertIn("'-n', $component", TRANSPORT)
        self.assertIn("$flags = '0x34000000'", TRANSPORT)
        self.assertIn("Start-FacebookUrlBounded -Serial $Serial -Url $intentUrl", OPEN)
        self.assertNotIn("Resolve-FacebookIntentChooser", OPEN)
        self.assertNotIn("'-p', 'com.facebook.katana'", OPEN)

    def test_native_permalink_fallbacks_use_explicit_facebook_url_launcher(self):
        self.assertGreaterEqual(RUN.count("Start-FacebookUrlBounded -Serial $Serial -Url $record.deeplink"), 2)
        self.assertNotIn("'-p', 'com.facebook.katana'", RUN)

    def test_target_open_promotes_inline_feed_card_with_fresh_target_boundary(self):
        self.assertIn("Package = [string]$attrs['package']", OPEN)
        self.assertIn("function Open-InlineFeedCardDetail", OPEN)
        self.assertIn("Shared with|Shared to", OPEN)
        self.assertIn("\\u5206\\u4eab\\u5bf9\\u8c61|\\u5206\\u4eab\\u5c0d\\u8c61", OPEN)
        self.assertIn("[:\\uFF1A]", OPEN)
        helper = OPEN[OPEN.index("function Open-InlineFeedCardDetail"):]
        boundary = helper.index("Set-VerificationBoundary")
        tap = helper.index("Tap-Node $timestamp")
        self.assertLess(boundary, tap)
        self.assertIn("Dump-Ui $Path", helper[tap:])
        self.assertIn("$Method += '-timestamp-detail'", OPEN)

    def test_avd_stop_cleans_emulator_wrapper_and_qemu_child(self):
        self.assertIn("@('emulator.exe', 'qemu-system-x86_64.exe')", STOP_AVD)
        self.assertGreaterEqual(STOP_AVD.count("-match '(?i)-avd\\s+fb-x86_64-api35(?:\\s|$)'"), 2)
        self.assertIn("remaining_avd_processes", STOP_AVD)

    def test_capture_can_own_raw_network_pcap_lifecycle(self):
        self.assertIn('[switch]$EnableRawNetworkCapture', RUN)
        self.assertIn('raw-network.pcap', RUN)
        self.assertIn('Start-FacebookRawNetworkCapture', RUN)
        self.assertIn('Stop-FacebookRawNetworkCapture', RUN)
        self.assertIn("@('shell','su','0','id','-u')", RUN)
        self.assertIn("'/system/bin/tcpdump'", RUN)
        self.assertIn("'kill','-2'", RUN)
        self.assertIn("'pull'", RUN)
        self.assertIn("'rm','-f'", RUN)

    def test_pcap_hashing_does_not_require_get_file_hash_module(self):
        self.assertIn("[Security.Cryptography.SHA256]::Create()", RUN)
        self.assertIn("[BitConverter]::ToString($hashBytes).Replace('-', '')", RUN)
        self.assertNotIn("Get-FileHash", RUN)

    def test_optional_pcap_failure_is_reported_after_analysis_outputs(self):
        analyzer = RUN.index('if (Test-Path $Jsonl) {', RUN.index('if ($EnableRawNetworkCapture'))
        pcap_failure = RUN.index('throw "Raw network capture was requested but did not finalize:', analyzer)
        self.assertLess(analyzer, pcap_failure)

    def test_capture_supports_safe_deterministic_output_dir_for_api_jobs(self):
        self.assertIn('[string]$OutputDir = ""', RUN)
        self.assertIn('OutputDir must resolve inside the project runs directory', RUN)
        self.assertIn('$RunsRoot = [IO.Path]::GetFullPath((Join-Path $Base "runs"))', RUN)

    def test_active_wrappers_use_deterministic_windows_powershell_executable(self):
        for name, text in (("capture", RUN), ("batch", BATCH), ("probe", PROBE)):
            with self.subTest(script=name):
                self.assertIn("System32\\WindowsPowerShell\\v1.0\\powershell.exe", text)
                self.assertIn("$PowerShellExe", text)
                self.assertNotIn("& powershell", text)

    def test_active_python_entrypoints_use_deterministic_launcher_or_shared_resolver(self):
        for name, text in (("capture", RUN), ("batch", BATCH), ("explore", EXPLORE), ("target-open", OPEN), ("api-start", START_API)):
            with self.subTest(script=name):
                deterministic = "Join-Path $env:SystemRoot 'py.exe'" in text or "Resolve-FacebookPythonExe -BasePath $Base" in text
                self.assertTrue(deterministic)
                self.assertIn("$PythonExe", text)
                self.assertNotIn("& python", text)
                self.assertNotIn("& py", text)
                self.assertNotIn("\npython ", text)
                self.assertNotIn("\n    python ", text)

    def test_capture_resolves_mitmdump_from_active_python_runtime(self):
        self.assertIn("Resolve-FacebookPythonExe -BasePath $Base", RUN)
        self.assertIn("Resolve-FacebookMitmdumpExe -PythonExe $PythonRuntimeExe", RUN)
        self.assertIn("$mitmdumpCommand = Get-Command mitmdump.exe", RUN)
        self.assertIn("Start-Process -FilePath $MitmdumpExe", RUN)
        self.assertNotIn('throw "mitmdump is not on PATH"', RUN)

    def test_reaction_pagination_records_speed_telemetry_without_changing_rate_controls(self):
        start = EXPLORE.index("function Scroll-ReactionAdaptive")
        end = EXPLORE.index("function Is-ReactionTabLabel", start)
        block = EXPLORE[start:end]
        self.assertIn("$paginationStarted = Get-Date", block)
        self.assertIn("elapsed_ms = $paginationElapsedMs", block)
        self.assertIn("seconds_per_growth", block)
        self.assertIn("growths_per_minute", block)
        diagnostic_start = EXPLORE.index("function Add-PaginationDiagnostic")
        diagnostic = EXPLORE[diagnostic_start:diagnostic_start + 10000]
        self.assertIn("elapsed_ms", diagnostic)
        self.assertIn("seconds_per_growth", diagnostic)
        self.assertIn("growths_per_minute", diagnostic)

    def test_deep_completion_requires_all_terminal_not_merely_no_target_mismatch(self):
        marker = "$Diagnostics['reaction_screening']['deep_pagination_runs']"
        start = EXPLORE.index(marker) - 2600
        block = EXPLORE[start:start + 4200]
        self.assertIn("$allPaginationRuns", block)
        self.assertIn("[string]$_.reaction_key -eq '__ALL__'", block)
        self.assertIn("$completionTerminalSeen", block)
        self.assertIn("Get-SafeBoolProperty $completionRun 'terminal_seen' $false", block)
        self.assertIn("[string]$completionRun.stop_reason -eq 'terminal'", block)
        self.assertIn("'terminal-complete'", block)
        self.assertIn("'partial-open-tail'", block)
        self.assertIn("deep_completion_status", block)
        self.assertIn("deep_completion_reason", block)
        self.assertNotIn("deep_capture_completed'] = (-not $targetMismatchSeen)", block)

    def test_reaction_unlimited_mode_has_no_absolute_cap_stop(self):
        start = EXPLORE.index("function Scroll-ReactionAdaptive")
        end = EXPLORE.index("function Is-ReactionTabLabel", start)
        block = EXPLORE[start:end]
        self.assertIn("$unlimited = ($ReactionAbsoluteCap -le 0)", block)
        self.assertIn("while (($unlimited -or $attempts -lt $absoluteCap) -and -not $initialSourceGuardFailed)", block)
        self.assertIn("if (-not $unlimited -and $currentBudget -ge $absoluteCap)", block)
        self.assertIn("if (-not $unlimited -and $attempts -ge $absoluteCap", block)
        self.assertIn("$currentBudget + $extensionChunk", block)
        self.assertIn("unlimited = $unlimited", block)

    def test_positive_reaction_cap_remains_supported_for_bounded_tests(self):
        start = EXPLORE.index("function Scroll-ReactionAdaptive")
        end = EXPLORE.index("function Is-ReactionTabLabel", start)
        block = EXPLORE[start:end]
        self.assertIn("[Math]::Max($baseBudget, $ReactionAbsoluteCap)", block)
        self.assertIn("$stopReason = 'absolute-cap'", block)
        self.assertIn("[Math]::Min($absoluteCap, $currentBudget + $extensionChunk)", block)

    def test_capture_defaults_remove_reaction_and_flow_hard_limits(self):
        self.assertIn("[int]$DurationSeconds = 0", RUN)
        self.assertIn("[int]$ReactionAbsoluteCap = 0", RUN)
        self.assertIn("[int]$MaxFlows = 0", RUN)
        self.assertIn("[int]$DurationSeconds = 0", BATCH)
        self.assertIn("[int]$ReactionAbsoluteCap = 0", BATCH)
        self.assertIn("[int]$MaxFlows = 0", BATCH)
        self.assertIn("[int]$ReactionAbsoluteCap = 0", EXPLORE)

    def test_reaction_probe_only_never_enters_deep_pagination(self):
        self.assertIn("[switch]$ReactionProbeOnly", RUN)
        self.assertIn("[switch]$ReactionProbeOnly", EXPLORE)
        self.assertGreaterEqual(RUN.count('if ($ReactionProbeOnly) { $readonlyArgs += "-ReactionProbeOnly" }'), 1)
        self.assertIn('if (-not $screenTab -and -not $ReactionProbeOnly) {', EXPLORE)
        self.assertIn('$screenProbeScrollLimit = if ($ReactionProbeOnly) { 0 }', EXPLORE)
        self.assertIn("deep_capture_requested = [bool]($deepCaptureAllowed -and -not $ReactionProbeOnly)", EXPLORE)
        self.assertIn("elseif ($ReactionProbeOnly) {", EXPLORE)
        self.assertIn("no pagination or category traversal was attempted", EXPLORE)
        self.assertIn("ReactionProbeOnly: fallback path skipped", RUN)

    def test_reaction_probe_only_exposes_health_state(self):
        self.assertIn("probe_only = [bool]$ReactionProbeOnly", EXPLORE)
        self.assertIn("probe_status = if", EXPLORE)
        self.assertIn("'blocked-session-restriction'", EXPLORE)
        self.assertIn("'blocked-rate-limit'", EXPLORE)
        self.assertIn("'initial-ready'", EXPLORE)
        self.assertIn("latest_graphql_error_code", EXPLORE)

    def test_reaction_health_probe_wrapper_is_initial_list_only(self):
        self.assertIn("'-ReactionProbeOnly'", PROBE)
        self.assertIn("'-ScrollCount', '0'", PROBE)
        self.assertIn("'-ReactionScreenScrollCount', '0'", PROBE)
        self.assertNotIn("-ReactionAbsoluteCap", PROBE)
        self.assertIn("No page2 pagination", PROBE)
        self.assertIn("[probe-result]", PROBE)
        self.assertIn("page2_requests={8}", PROBE)
        self.assertIn("ProbeOnly invariant violated", PROBE)
        self.assertIn("$probeExitCode = 2", PROBE)

    def test_reaction_health_probe_fails_closed_when_screening_is_missing_or_not_ready(self):
        self.assertIn("$screenProperty = $diag.PSObject.Properties['reaction_screening']", PROBE)
        self.assertIn("$probeStatus = if ($screen -and $screen.PSObject.Properties['probe_status'])", PROBE)
        self.assertIn("'not-ready'", PROBE)
        self.assertIn("$diagnosticsObserved = $false", PROBE)
        self.assertIn("reaction_screening was not written", PROBE)
        self.assertIn("no fresh diagnostics", PROBE)
        self.assertIn("$probeStatus -ne 'initial-ready'", PROBE)
        self.assertIn("$probeExitCode = 1", PROBE)
        self.assertIn("failure_reason=", PROBE)

    def test_capture_duration_zero_disables_fixed_deadline_but_positive_is_supported(self):
        self.assertIn("$captureDeadline = if ($DurationSeconds -gt 0)", RUN)
        self.assertIn("(Get-Date).AddSeconds($DurationSeconds)", RUN)
        self.assertIn("else { $null }", RUN)
        self.assertIn("if ($captureDeadline) {", RUN)

    def test_fast_reaction_pagination_uses_capture_polling_and_periodic_ui_dump(self):
        start = EXPLORE.index("function Scroll-ReactionAdaptive")
        end = EXPLORE.index("function Is-ReactionTabLabel", start)
        block = EXPLORE[start:end]
        self.assertIn("Start-Sleep -Milliseconds $activePollIntervalMs", block)
        self.assertIn("$periodicUiCheck", block)
        self.assertIn("$needsUiDump", block)
        self.assertIn("progress_poll_count", block)
        self.assertIn("fast_poll_growth_hits", block)
        self.assertIn("ui_dump_count", block)
        hot = block[block.index("Invoke-ReactionTriggerStrategy $strategy"):block.index("if ([int]$next.latest_line")]
        self.assertNotIn("Start-Sleep -Seconds $WaitSeconds", hot)

    def test_reaction_loader_applies_bounded_backpressure_before_more_swipes(self):
        start = EXPLORE.index("function Scroll-ReactionAdaptive")
        end = EXPLORE.index("function Is-ReactionTabLabel", start)
        block = EXPLORE[start:end]
        self.assertIn("$loaderBackpressureTimeoutMs", block)
        self.assertIn("$loaderNode = Get-UiNodes $dumpPath", block)
        self.assertIn("$_.Class -match 'ProgressBar'", block)
        self.assertIn("reaction-loader-backpressure", block)
        self.assertIn("$loaderRateLimitLine = Get-ReactionRateLimitLine $loaderProgress", block)
        self.assertIn("$loaderRateLimitLine -gt $lastHandledRateLimitLine", block)
        self.assertIn("$loaderBackpressureGrowthHits++", block)
        self.assertIn("loader_backpressure_cycles", block)
        self.assertIn("loader_backpressure_polls", block)
        self.assertIn("loader_backpressure_growth_hits", block)

    def test_fast_reaction_pagination_keeps_source_guard_before_growth_acceptance(self):
        start = EXPLORE.index("function Scroll-ReactionAdaptive")
        end = EXPLORE.index("function Is-ReactionTabLabel", start)
        block = EXPLORE[start:end]
        poll_guard = block.index("Test-ExpectedSourceGuard $polled 'reaction' 'reaction-pagination'")
        growth_accept = block.index("$fastGrowth = $true", poll_guard)
        self.assertLess(poll_guard, growth_accept)
        self.assertIn("Test-ExpectedSourceGuard $refreshed 'reaction' 'reaction-pagination'", block)

    def test_capture_progress_prefers_live_snapshot_with_strict_boundary_fallback(self):
        start = EXPLORE.index("function Get-LiveCaptureProgress")
        end = EXPLORE.index("function Get-ReactionGroup", start)
        block = EXPLORE[start:end]
        self.assertIn("facebook-live-progress.json", block)
        self.assertIn("[int]$source.first_line -lt $minLine", block)
        self.assertIn("[int]$untracked.latest_line -ge $minLine", block)
        self.assertIn("observed_source_post_ids = $observed", block)
        self.assertIn("$familyPageSucceeded = [bool]([int]$source.page_count -gt 0)", block)
        self.assertIn("-not $familyPageSucceeded -or $restrictionTargetsFamily", block)
        self.assertIn("session_restricted = $freshSessionRestriction", block)
        self.assertIn("$live = Get-LiveCaptureProgress $Family", block)
        self.assertIn("$script:CaptureProgressLiveHits++", block)
        self.assertIn("$script:CaptureProgressFallbackHits++", block)
        self.assertLess(block.index("Get-LiveCaptureProgress $Family"), block.index("facebook_capture_progress.py"))

    def test_session_restriction_is_run_wide_until_the_family_returns_a_page(self):
        start = EXPLORE.index("function Get-LiveCaptureProgress")
        end = EXPLORE.index("function Get-ReactionGroup", start)
        block = EXPLORE[start:end]
        self.assertIn("[int]$source.first_line -lt $minLine", block)
        self.assertIn("$restrictionTargetsFamily", block)
        self.assertIn("$familyPageSucceeded", block)
        self.assertIn("session_records, session_parse_errors = iter_records(args.input, args.max_records, min_line=1)", PROGRESS)
        self.assertIn("summary.update(_session_restriction_summary(session_records, successful_prefix))", PROGRESS)

    def test_single_pagination_run_stays_array_for_completion_selection(self):
        self.assertIn(
            "$completionRuns = @(if ($allPaginationRuns.Count -gt 0) { $allPaginationRuns } else { $paginationRuns })",
            EXPLORE,
        )
        self.assertIn("$completionRuns[-1]", EXPLORE)

    def test_missing_pagination_completion_flags_are_fail_safe(self):
        self.assertIn("function Get-SafeBoolProperty", EXPLORE)
        diagnostic_start = EXPLORE.index("function Add-PaginationDiagnostic")
        diagnostic = EXPLORE[diagnostic_start:diagnostic_start + 5000]
        self.assertIn("terminal_seen = Get-SafeBoolProperty $Result 'terminal_seen' $false", diagnostic)
        self.assertIn("open_seen = Get-SafeBoolProperty $Result 'open_seen' $false", diagnostic)

    def test_safe_bool_property_reads_ordered_dictionary_keys(self):
        start = EXPLORE.index("function Get-SafeBoolProperty")
        end = EXPLORE.index("function Get-ReactionRateLimitLine", start)
        block = EXPLORE[start:end]
        self.assertIn("$Object -is [System.Collections.IDictionary]", block)
        self.assertIn("$Object.Contains($Name)", block)
        self.assertIn("return [bool]$Object[$Name]", block)

    def test_live_progress_does_not_replace_source_guard(self):
        start = EXPLORE.index("function Test-ExpectedSourceGuard")
        block = EXPLORE[start:start + 2200]
        self.assertIn("$observed.Count -eq 1", block)
        self.assertIn("$observed[0] -eq [string]$ExpectedSourcePostId", block)
        self.assertIn("stopping this family before further pagination", block)

    def test_pagination_diagnostics_expose_live_progress_hit_counts(self):
        start = EXPLORE.index("function Add-PaginationDiagnostic")
        block = EXPLORE[start:start + 12000]
        self.assertIn("capture_progress_live_hits", block)
        self.assertIn("capture_progress_fallback_hits", block)

    def test_fast_reaction_parameters_are_forwarded_to_capture_paths(self):
        for marker in ("ReactionProgressPollIntervalMs", "ReactionProgressPollTimeoutMs", "ReactionLiveProgressPollIntervalMs", "ReactionLiveProgressPollTimeoutMs", "ReactionSteadyNoGrowthPollTimeoutMs", "ReactionLongSwipeStartY", "ReactionLongSwipeEndY", "ReactionLongSwipeDurationMs", "ReactionPipelineBurstSize", "ReactionPipelineSwipeGapMs", "ReactionNativeScrollEnabled", "ReactionNativeScrollTimeoutMs", "ReactionUiDumpEveryN", "ReactionMinCycleMs", "ReactionInitialPaginationCooldownSeconds", "ReactionRateLimitBackoffSeconds", "ReactionRateLimitMaxConsecutive", "ReactionRateLimitMaxBackoffSeconds", "ReactionNoGrowthUiDumpEveryN"):
            self.assertGreaterEqual(RUN.count(marker), 4)
            self.assertGreaterEqual(BATCH.count(marker), 2)

    def test_rate_limit_backoff_is_exponential_bounded_and_circuit_breaks(self):
        start = EXPLORE.index("function Scroll-ReactionAdaptive")
        end = EXPLORE.index("function Is-ReactionTabLabel", start)
        block = EXPLORE[start:end]
        self.assertIn("$consecutiveRateLimits++", block)
        self.assertIn("[Math]::Pow(2", block)
        self.assertIn("$ReactionRateLimitMaxBackoffSeconds", block)
        self.assertIn("$consecutiveRateLimits -ge $rateLimitMaxConsecutive", block)
        self.assertIn("$stopReason = 'rate-limited'", block)
        self.assertIn("$consecutiveRateLimits = 0", block[block.index("if ($grew) {"):])
        self.assertIn("preserving cursor checkpoint", block)

    def test_fresh_rate_limit_is_handled_before_growth_metric(self):
        start = EXPLORE.index("function Scroll-ReactionAdaptive")
        end = EXPLORE.index("function Is-ReactionTabLabel", start)
        block = EXPLORE[start:end]
        fresh_limit = block.index("$nextRateLimited = [bool](")
        limit_branch = block.index("if ($nextRateLimited) {", fresh_limit)
        growth_metric = block.index("$metric = Get-ProgressMetric $next 'reaction'", limit_branch)
        self.assertLess(limit_branch, growth_metric)
        limited_block = block[limit_branch:growth_metric]
        self.assertIn("$lastHandledRateLimitLine = $nextRateLimitLine", limited_block)
        self.assertIn("$nextRateLimitLine = Get-ReactionRateLimitLine $next", block)
        self.assertIn("$consecutiveRateLimits++", limited_block)
        self.assertIn("$stopReason = 'rate-limited'", limited_block)
        self.assertIn("continue", limited_block)
        self.assertNotIn("$consecutiveRateLimits = 0", limited_block)
        self.assertNotIn("$growthEvents++", limited_block)

    def test_session_restriction_stops_before_next_reaction_trigger(self):
        start = EXPLORE.index("function Scroll-ReactionAdaptive")
        end = EXPLORE.index("function Is-ReactionTabLabel", start)
        block = EXPLORE[start:end]
        restriction = block.index("$stopReason = 'session-restricted'")
        trigger = block.index("Invoke-ReactionTriggerStrategy $strategy")
        self.assertLess(restriction, trigger)
        self.assertIn("session_restriction_api_error_code", block)
        self.assertIn("'session-restricted'", block[block.index("if (-not $unlimited -and $attempts -ge $absoluteCap"):])

    def test_session_restriction_is_explicit_in_all_first_result_handling(self):
        marker = "Reaction ALL-first pagination stopped by"
        start = EXPLORE.index(marker) - 500
        block = EXPLORE[start:start + 1100]
        self.assertIn("@('rate-limited', 'session-restricted')", block)
        self.assertIn("category tabs will not be opened", block)

    def test_all_stream_is_deep_paginated_before_any_category_tap(self):
        start = EXPLORE.index("# The initial reactors list is the ALL stream")
        block = EXPLORE[start:start + 3200]
        all_scroll = block.index("Scroll-ReactionAdaptive 'reaction-all-scroll'")
        category_tap = block.index('Tap-Node $candidate ("reaction filter')
        self.assertLess(all_scroll, category_tap)
        self.assertIn("$allGroup = Get-ReactionGroup $allProgress '__ALL__'", block)
        self.assertIn("for ($tab = 0; -not $allFirstHandled", block)
        self.assertIn("@('rate-limited', 'session-restricted')", block)
        self.assertIn("category tabs will not be opened for this job", block)

    def test_fast_reaction_defaults_are_throttled_for_android_stability(self):
        self.assertIn("[int]$ReactionProgressPollIntervalMs = 500", EXPLORE)
        self.assertIn("[int]$ReactionProgressPollTimeoutMs = 2200", EXPLORE)
        self.assertIn("[int]$ReactionLiveProgressPollIntervalMs = 150", EXPLORE)
        self.assertIn("[int]$ReactionLiveProgressPollTimeoutMs = 1200", EXPLORE)
        self.assertIn("[int]$ReactionSteadyNoGrowthPollTimeoutMs = 500", EXPLORE)
        self.assertIn("[int]$ReactionLongSwipeStartY = 1700", EXPLORE)
        self.assertIn("[int]$ReactionLongSwipeEndY = 250", EXPLORE)
        self.assertIn("[int]$ReactionLongSwipeDurationMs = 300", EXPLORE)
        self.assertIn("[int]$ReactionPipelineBurstSize = 2", EXPLORE)
        self.assertIn("[int]$ReactionPipelineSwipeGapMs = 150", EXPLORE)
        self.assertIn("[int]$ReactionNativeScrollEnabled = 1", EXPLORE)
        self.assertIn("[int]$ReactionNativeScrollTimeoutMs = 2500", EXPLORE)
        self.assertIn("[int]$ReactionUiDumpEveryN = 64", EXPLORE)
        self.assertIn("[int]$ReactionMinCycleMs = 700", EXPLORE)
        self.assertIn("[int]$ReactionInitialPaginationCooldownSeconds = 4", EXPLORE)
        self.assertIn("[int]$ReactionNoGrowthUiDumpEveryN = 1", EXPLORE)

    def test_all_user_capture_entrypoints_enable_native_reaction_guard_by_default(self):
        for name, text in (
            ("capture", RUN),
            ("batch", BATCH),
            ("queue", QUEUE),
            ("explore", EXPLORE),
        ):
            with self.subTest(script=name):
                self.assertIn("[int]$ReactionNativeScrollEnabled = 1", text)

    def test_all_first_pagination_cools_down_before_first_page2_trigger(self):
        start = EXPLORE.index("function Scroll-ReactionAdaptive")
        end = EXPLORE.index("function Is-ReactionTabLabel", start)
        block = EXPLORE[start:end]
        cooldown = block.index("Reaction ALL-first initial pagination cooldown")
        sleep = block.index("Start-Sleep -Seconds $initialPaginationCooldownSeconds", cooldown)
        trigger = block.index("Invoke-ReactionTriggerStrategy $strategy", sleep)
        self.assertLess(cooldown, sleep)
        self.assertLess(sleep, trigger)
        self.assertIn("$PreferredReactionKey -eq '__ALL__'", block)
        self.assertIn("reaction-initial-pagination-cooldown", block)
        self.assertIn("initial_pagination_cooldown_applied_seconds", block)

    def test_no_growth_path_checks_loader_on_first_miss(self):
        start = EXPLORE.index("function Scroll-ReactionAdaptive")
        end = EXPLORE.index("function Is-ReactionTabLabel", start)
        block = EXPLORE[start:end]
        self.assertIn("$noGrowthUiDumpEveryN", block)
        self.assertIn("$noGrowthUiCheck", block)
        self.assertIn("(($strategyIdle + 1) % $noGrowthUiDumpEveryN)", block)
        self.assertIn("$needsUiDump = (-not $next) -or $noGrowthUiCheck -or $periodicUiCheck", block)
        self.assertNotIn("$needsUiDump = (-not $next) -or (-not $fastGrowth)", block)

    def test_reaction_hot_loop_enforces_minimum_cycle(self):
        start = EXPLORE.index("function Scroll-ReactionAdaptive")
        end = EXPLORE.index("function Is-ReactionTabLabel", start)
        block = EXPLORE[start:end]
        self.assertIn("$minCycleMs = [Math]::Max(0, $ReactionMinCycleMs)", block)
        self.assertIn("$cycleStarted = Get-Date", block)
        self.assertGreaterEqual(block.count("Wait-ReactionMinCycle $cycleStarted $minCycleMs"), 2)
        self.assertIn("min_cycle_ms = $minCycleMs", block)

    def test_live_progress_uses_faster_host_poll_without_changing_ui_min_cycle(self):
        start = EXPLORE.index("function Scroll-ReactionAdaptive")
        end = EXPLORE.index("function Is-ReactionTabLabel", start)
        block = EXPLORE[start:end]
        self.assertIn("$livePollIntervalMs = [Math]::Max(50, $ReactionLiveProgressPollIntervalMs)", block)
        self.assertIn("$livePollTimeoutMs = [Math]::Max($livePollIntervalMs, $ReactionLiveProgressPollTimeoutMs)", block)
        self.assertIn("$steadyNoGrowthPollTimeoutMs = [Math]::Max($livePollIntervalMs, $ReactionSteadyNoGrowthPollTimeoutMs)", block)
        self.assertIn("$strategyIdle -lt 2", block)
        self.assertIn("$steadyShortPollCycles++", block)
        self.assertIn("steady_short_poll_cycles = $steadyShortPollCycles", block)
        self.assertIn("[string]$current.progress_source -eq 'live'", block)
        self.assertIn("$activePollIntervalMs", block)
        self.assertIn("$activePollTimeoutMs", block)
        self.assertIn("$livePollCycles++", block)
        self.assertIn("$fallbackPollCycles++", block)
        self.assertIn("Start-Sleep -Milliseconds $activePollIntervalMs", block)
        self.assertIn("$minCycleMs = [Math]::Max(0, $ReactionMinCycleMs)", block)
        self.assertIn("Wait-ReactionMinCycle $cycleStarted $minCycleMs", block)

    def test_long_reaction_trigger_uses_fast_fling_parameters_but_keeps_min_cycle(self):
        start = EXPLORE.index("function Invoke-ReactionTriggerStrategy")
        end = EXPLORE.index("function Wait-ReactionMinCycle", start)
        block = EXPLORE[start:end]
        self.assertIn("$ReactionLongSwipeStartY", block)
        self.assertIn("$ReactionLongSwipeEndY", block)
        self.assertIn("$ReactionLongSwipeDurationMs", block)
        self.assertIn("[int]$ReactionMinCycleMs = 700", EXPLORE)

    def test_reaction_pipeline_batches_long_swipes_before_progress_poll(self):
        start = EXPLORE.index("function Scroll-ReactionAdaptive")
        end = EXPLORE.index("function Is-ReactionTabLabel", start)
        block = EXPLORE[start:end]
        self.assertIn("$pipelineEnabled = ($pipelineBurstSize -gt 1)", block)
        self.assertIn("$usePipelineBurst", block)
        self.assertIn("for ($burstSwipe = 0; $burstSwipe -lt $swipesThisCycle; $burstSwipe++)", block)
        self.assertIn("Start-Sleep -Milliseconds $pipelineSwipeGapMs", block)
        self.assertLess(block.index("for ($burstSwipe = 0;"), block.index("$pollDeadline = (Get-Date).AddMilliseconds"))
        self.assertIn("pipeline_enabled = $pipelineEnabled", block)
        self.assertIn("burst_count = $burstCount", block)
        self.assertIn("burst_swipes = $burstSwipes", block)
        self.assertIn("burst_growth_hits = $burstGrowthHits", block)
        self.assertIn("effective_swipes_per_growth", block)

    def test_native_reaction_scroll_is_persistent_guarded_and_stops_before_adb_fallback(self):
        self.assertIn("ACTION_SCROLL_FORWARD", NATIVE_HELPER)
        self.assertIn("injectInputEvent", NATIVE_HELPER)
        self.assertIn('"SWIPE".equals(command)', NATIVE_HELPER)
        self.assertIn('"CHECK_REACTION".equals(command)', NATIVE_HELPER)
        self.assertIn('"OK REACTION " + rect.left', NATIVE_HELPER)
        self.assertIn("MotionEvent.ACTION_DOWN", NATIVE_HELPER)
        self.assertIn("MotionEvent.ACTION_MOVE", NATIVE_HELPER)
        self.assertIn("MotionEvent.ACTION_UP", NATIVE_HELPER)
        self.assertIn('"com.facebook.katana"', NATIVE_HELPER)
        self.assertIn('cls.contains("RecyclerView")', NATIVE_HELPER)
        self.assertIn("node.getChildCount() > best.getChildCount()", NATIVE_HELPER)
        self.assertIn("hasReactionTitle", NATIVE_HELPER)
        self.assertIn('value.startsWith("心情选项卡栏")', NATIVE_HELPER)
        self.assertIn("ServerSocket", NATIVE_HELPER)
        self.assertIn("function Start-FacebookNativeScrollController", TRANSPORT)
        self.assertIn("function Invoke-FacebookNativeReactionCheck", TRANSPORT)
        self.assertIn("function Invoke-FacebookNativeReactionScroll", TRANSPORT)
        self.assertIn('"SCROLL {0} {1}"', TRANSPORT)
        self.assertIn("'^OK SCROLL ([0-9]+)$'", TRANSPORT)
        self.assertIn("$result.Response -eq 'ERR root-null'", TRANSPORT)
        self.assertIn("Start-Sleep -Milliseconds 700", TRANSPORT)
        self.assertIn("$requiredCommandTimeoutMs", TRANSPORT)
        self.assertIn("$effectiveTimeoutMs", TRANSPORT)
        self.assertIn("$nativeCommandTimeoutFloorMs = 30000", TRANSPORT)
        self.assertIn("$requiredVersionCode = 5", TRANSPORT)
        self.assertIn("Start-FacebookNativeScrollController -Serial $Serial -Port $Port -TimeoutSeconds 8", TRANSPORT)
        start = EXPLORE.index("function Scroll-ReactionAdaptive")
        end = EXPLORE.index("function Is-ReactionTabLabel", start)
        block = EXPLORE[start:end]
        native_guard = block.index("Invoke-FacebookNativeReactionCheck")
        adb_trigger = block.index("Invoke-ReactionTriggerStrategy $strategy", native_guard)
        self.assertLess(native_guard, adb_trigger)
        self.assertIn("$nativeGuardFailed", block)
        self.assertIn("Invoke-ReactionTriggerStrategy $strategy ([string]$nativeResult.Response)", block)
        self.assertIn("$stopReason = 'native-scroll-unavailable'", block)
        self.assertIn("stopping before any unguarded ADB fallback", block)
        self.assertIn("reaction-title-not-confirmed", block)
        self.assertIn("native-loss", block)
        self.assertIn("native_scroll_attempts = $nativeScrollAttempts", block)
        self.assertIn("native_scroll_success = $nativeScrollSuccess", block)
        self.assertIn("native_scroll_fallbacks = $nativeScrollFallbacks", block)
        self.assertIn("native_scroll_partial_bursts = $nativeScrollPartialBursts", block)
        self.assertIn("$nativeScrollSuccess++", block)
        self.assertIn("$nativeScrollSwipes++", block)

    def test_reaction_pagination_recovers_or_stops_when_the_list_is_lost(self):
        start = EXPLORE.index("function Scroll-ReactionAdaptive")
        end = EXPLORE.index("function Is-ReactionTabLabel", start)
        block = EXPLORE[start:end]
        self.assertIn("$reactionListLosses", block)
        self.assertIn("$reactionListRecoveries", block)
        self.assertIn("Get-PageState $dumpPath", block)
        self.assertIn("KEYCODE_BACK", block)
        self.assertIn("$stopReason = 'reaction-list-lost'", block)
        self.assertIn("reaction_list_losses = $reactionListLosses", block)
        self.assertIn("reaction_list_recoveries = $reactionListRecoveries", block)

    def test_reaction_recovery_strategy_resets_to_fast_long_after_growth(self):
        start = EXPLORE.index("function Scroll-ReactionAdaptive")
        end = EXPLORE.index("function Is-ReactionTabLabel", start)
        block = EXPLORE[start:end]
        grew = block.index("if ($grew) {")
        reset = block.index("if ($strategyIndex -ne 0) {", grew)
        self.assertGreater(reset, grew)
        self.assertIn("$strategyIndex = 0", block[reset:reset + 700])
        self.assertIn("$strategyResetsToLong++", block[reset:reset + 700])
        self.assertIn("strategy_resets_to_long = $strategyResetsToLong", block)
        self.assertIn("'wait-long' {", EXPLORE)
        self.assertIn("Start-Sleep -Seconds ([Math]::Max(1, $WaitSeconds))", EXPLORE)

    def test_open_stream_budget_cannot_preempt_pending_recovery_strategies(self):
        start = EXPLORE.index("function Scroll-ReactionAdaptive")
        end = EXPLORE.index("function Is-ReactionTabLabel", start)
        block = EXPLORE[start:end]
        self.assertIn("$recoveryStrategyPending = ($strategyIndex -lt $strategies.Count)", block)
        self.assertIn("$isOpen -and ($recentGrowth -or $recoveryStrategyPending)", block)
        recovery = block.index("$recoveryStrategyPending = ($strategyIndex -lt $strategies.Count)")
        no_growth_stop = block.index("$stopReason = 'open-no-recent-growth'", recovery)
        exhausted = block.index("$stopReason = 'ui-trigger-exhausted'", recovery)
        self.assertLess(recovery, no_growth_stop)
        self.assertLess(recovery, exhausted)

    def test_transport_exposes_generic_anr_wait_with_legacy_wrapper(self):
        self.assertIn("function Dismiss-FacebookAnrWait", TRANSPORT)
        self.assertIn("android:id/aerr_wait", TRANSPORT)
        self.assertIn("function Dismiss-FacebookSystemUiAnr", TRANSPORT)
        self.assertIn("Dismiss-FacebookAnrWait -Serial $Serial -Raw $Raw", TRANSPORT)

    def test_adb_ready_recovery_handles_missing_transport(self):
        self.assertIn("ADB device remained missing; restarting ADB server", TRANSPORT)
        self.assertIn("$missingSeenAt", TRANSPORT)

    def test_fast_ui_dump_self_heals_missing_helper_once(self):
        start = TRANSPORT.index("function Invoke-FacebookFastUiDump")
        end = TRANSPORT.index("function Get-FacebookNativeScrollPort", start)
        block = TRANSPORT[start:end]
        ensure_call = "Ensure-FacebookUiDumpHelper -Serial $Serial -BasePath $PSScriptRoot"
        self.assertEqual(block.count(ensure_call), 1)
        self.assertLess(block.index("Test-FacebookUiDumpHelperInstalled"), block.index(ensure_call))
        self.assertLess(block.index(ensure_call), block.index("'shell','am','instrument'"))
        self.assertIn("Error='helper-not-installed'", block)

    def test_capture_readiness_recovers_both_system_and_app_anr(self):
        start = RUN.index("function Wait-FacebookUiReady")
        end = RUN.index("function Wait-FacebookUiSettledInPlace", start)
        block = RUN[start:end]
        self.assertIn("'system-ui-not-responding','app-not-responding'", block)
        self.assertIn("Dismiss-FacebookAnrWait", block)
        self.assertIn("choosing Wait", block)

    def test_launch_delegates_first_probe_to_bounded_readiness_wait(self):
        start = RUN.index("$launchOk = Start-FacebookAppBounded")
        end = RUN.index("for ($i = 0; $i -lt $ScrollCount; $i++)", start)
        block = RUN[start:end]
        self.assertIn("Wait-FacebookUiReady $FacebookReadyTimeoutSeconds", block)
        self.assertNotIn("Start-Sleep -Seconds 3", block)

    def test_ui_dump_handles_system_anr_with_wait_and_backoff(self):
        start = EXPLORE.index("function Dump-Ui([string]$Name)")
        end = EXPLORE.index("function Get-UiNodes", start)
        block = EXPLORE[start:end]
        self.assertIn("Get-FacebookUiBlockingState", block)
        self.assertIn("Dismiss-FacebookSystemUiAnr", block)
        self.assertIn("Start-Sleep -Milliseconds 2500", block)
        self.assertGreaterEqual(block.count("Get-ExplorerUiSnapshot"), 2)
        snapshot_start = EXPLORE.index("function Get-ExplorerUiSnapshot")
        snapshot_end = EXPLORE.index("function Dump-Ui", snapshot_start)
        self.assertIn("Invoke-FacebookUiSnapshot", EXPLORE[snapshot_start:snapshot_end])
        self.assertIn("System UI ANR persisted after Wait/backoff", block)

    def test_target_open_prefers_closing_blocking_system_anr(self):
        start = OPEN.index("function Dump-Ui([string]$Path)")
        end = OPEN.index("function Get-UiNodes", start)
        block = OPEN[start:end]
        self.assertIn("android:id/aerr_close", block)
        self.assertIn("$anrClose -or $blockingState -eq 'system-ui-not-responding'", block)
        self.assertIn("$anrDismissed", block)
        self.assertIn("Dismiss-FacebookSystemUiAnr", block)
        self.assertIn("Android ANR dialog detected", block)

    def test_diagnostics_file_lock_is_nonfatal_and_retried(self):
        start = EXPLORE.index("function Save-Diagnostics")
        end = EXPLORE.index("function Test-ExpectedSourceGuard", start)
        block = EXPLORE[start:end]
        self.assertIn("$maxAttempts = 8", block)
        self.assertIn("catch [IO.IOException]", block)
        self.assertIn("catch [UnauthorizedAccessException]", block)
        self.assertIn("Start-Sleep -Milliseconds", block)
        self.assertIn("capture will continue", block)
        self.assertNotIn("return $true", block)
        self.assertNotIn("return $false", block)
        self.assertNotIn("Set-Content", block)

    def test_diagnostics_writer_has_no_success_stream_boolean_output(self):
        start = EXPLORE.index("function Save-Diagnostics")
        end = EXPLORE.index("function Test-ExpectedSourceGuard", start)
        block = EXPLORE[start:end]
        self.assertIn("[switch]$Throttle", block)
        self.assertIn("$script:DiagnosticsSaveThrottleMs", block)
        self.assertNotIn("return $true", block)
        self.assertNotIn("return $false", block)

    def test_source_guard_sampling_keeps_per_poll_validation_without_per_poll_disk_write(self):
        self.assertIn("target_guard_check_count = 0", EXPLORE)
        self.assertIn("target_guard_matched_count = 0", EXPLORE)
        self.assertIn("target_guard_mismatch_count = 0", EXPLORE)
        self.assertIn("$script:DiagnosticsSaveThrottleMs = 2000", EXPLORE)
        start = EXPLORE.index("function Test-ExpectedSourceGuard")
        end = EXPLORE.index("function Add-Diagnostic", start)
        guard = EXPLORE[start:end]
        self.assertIn("$guardCheckCount % 50", guard)
        self.assertIn("Count -gt 256", guard)
        self.assertIn("if ($matched) { Save-Diagnostics -Throttle | Out-Null } else { Save-Diagnostics | Out-Null }", guard)
        self.assertIn("return $matched", guard)

    def test_diagnostics_throttle_skips_only_telemetry_writes(self):
        start = EXPLORE.index("function Save-Diagnostics")
        end = EXPLORE.index("function Test-ExpectedSourceGuard", start)
        block = EXPLORE[start:end]
        self.assertIn("[switch]$Throttle", block)
        self.assertIn("$script:DiagnosticsLastSaveAt", block)
        self.assertIn("$script:DiagnosticsSaveThrottleMs", block)
        self.assertIn("$script:DiagnosticsLastSaveAt = Get-Date", block)

    def test_addon_zero_max_flows_means_unlimited(self):
        self.assertIn('FACEBOOK_CAPTURE_MAX_FLOWS", "0"', ADDON)
        self.assertIn("if MAX_FLOWS > 0 and FLOW_COUNT >= MAX_FLOWS:", ADDON)

    def test_target_url_force_stops_facebook_before_fresh_verification_boundary(self):
        start = OPEN.index("function Open-Url([string]$Url)")
        end = OPEN.index("function Open-BySearch", start)
        block = OPEN[start:end]
        force = block.index("'shell', 'am', 'force-stop', 'com.facebook.katana'")
        boundary = block.index("Set-VerificationBoundary")
        view = block.index("android.intent.action.VIEW")
        self.assertLess(force, boundary)
        self.assertLess(boundary, view)
        self.assertNotIn("settings put global http_proxy", block)
        self.assertNotIn("pm clear", block)

    def test_target_open_ui_dump_prefers_snapshot_helper_with_instrumentation_fallback(self):
        start = OPEN.index("function Dump-Ui([string]$Path)")
        end = OPEN.index("function Get-UiNodes", start)
        block = OPEN[start:end]
        self.assertIn("Invoke-FacebookUiSnapshot", block)
        self.assertIn("LegacyRetryCount 1", block)
        self.assertIn("FastTimeoutSeconds 10", block)
        self.assertIn("Get-FacebookUiBlockingState", block)
        self.assertIn("Dismiss-FacebookSystemUiAnr", block)
        self.assertIn("Start-Sleep -Milliseconds 2000", block)
        helper = block.index("Invoke-FacebookUiSnapshot")
        legacy = block.index("shell uiautomator dump")
        self.assertLess(helper, legacy)

    def test_share_short_url_is_navigation_not_self_verification(self):
        self.assertIn("function Test-FacebookShareNavigationUrl", OPEN)
        self.assertIn("'share-url-navigation'", OPEN)
        self.assertIn("Open-Url $ResolvedUrl", OPEN)
        self.assertIn("--require-target-match", OPEN)
        self.assertNotIn("share-url-navigation-confirmed", OPEN)

    def test_share_short_url_is_resolved_to_canonical_target_before_android_view(self):
        self.assertIn("resolve_facebook_share_url.py", OPEN)
        self.assertIn("share-url-http-redirect", OPEN)
        self.assertLess(OPEN.index("resolve_facebook_share_url.py"), OPEN.index("Open-Url $ResolvedUrl"))

    def test_final_analysis_reuses_verified_canonical_target_url(self):
        self.assertIn("$script:ResolvedTargetUrl = $verifiedExpectedUrl", RUN)
        self.assertIn('@("--expected-url", $script:ResolvedTargetUrl)', RUN)
        self.assertNotIn('@("--expected-url", $PostUrl)', RUN)

    def test_primary_target_metadata_only_promoted_after_matched_verification(self):
        marker = "if ($verification -and [string]$verification.status -eq 'matched')"
        start = RUN.index(marker)
        end = RUN.index("elseif ($LaunchFacebook)", start)
        block = RUN[start:end]
        self.assertIn("$script:PrimaryVerifiedPostId = $matchedPostId", block)
        self.assertIn("$script:PrimaryVerifiedAuthorId = $matchedAuthorId", block)
        self.assertIn("$script:ResolvedCaptureSourcePostId = $matchedPostId", block)

    def test_cached_group_permalink_defers_proof_to_exact_reactor_source(self):
        self.assertIn("cached-group-permalink-source-proof", RUN)
        self.assertIn("$OpenReactionList", RUN)
        self.assertIn("/groups/([0-9]{1,32})/permalink/([0-9]{1,32})", RUN)
        self.assertIn("$script:PrimaryVerifiedPostId = $cachedPermalinkPostId", RUN)
        self.assertIn("$script:ResolvedCaptureSourcePostId = $cachedPermalinkPostId", RUN)
        self.assertIn("$script:ResolvedTargetUrl = $cachedResolvedUrl", RUN)

    def test_direct_group_permalink_fallback_requires_exact_requested_tokens(self):
        start = RUN.index("$cachedPermalinkMatch = [regex]::Match")
        end = RUN.index("if (-not $targetOpenSucceeded)", start)
        block = RUN[start:end]
        self.assertIn("$requestedPermalinkMatch", block)
        self.assertIn("$cachedGroupPermalinkMatchesRequested", block)
        self.assertIn("$cachedPermalinkGroupId", block)
        self.assertIn("[string]$cachedDiagnostic.method -eq 'url-deeplink'", block)
        self.assertIn("$requestedPermalinkMatch.Success", block)
        self.assertIn("$cachedMethodAllowed", block)
        self.assertIn("cached-group-permalink-source-proof", block)

    def test_strict_primary_reaction_fallback_uses_readonly_reactors_deeplink(self):
        start = RUN.index("$usePrimaryVerifiedReactionTarget = $true")
        block = RUN[start:start + 6500]
        self.assertIn("?open_reactors_list=true", block)
        self.assertIn("$record.family_capture_start_line = Get-CaptureJsonlLineCount", block)
        self.assertIn("'-ExpectedSourcePostId', $record.post_id", RUN)
        self.assertIn("'-CaptureMinLine', [string]([int]$record.family_capture_start_line + 1)", RUN)

    def test_source_mismatch_fallback_retries_only_the_verified_target(self):
        self.assertIn("$reactionTargetMismatch", RUN)
        self.assertIn("$diag.target_guard.expected_post_id -eq [string]$script:PrimaryVerifiedPostId", RUN)
        self.assertIn("source mismatch; retrying readonly reactors list for strict target", RUN)
        mismatch = RUN.index("$reactionTargetMismatch -and $record.reaction_eligible")
        observed = RUN.index("elseif ($sourceIds.Count -eq 1)", mismatch)
        self.assertLess(mismatch, observed)
        self.assertIn("$record.post_id = [string]$script:PrimaryVerifiedPostId", RUN[mismatch:observed])

    def test_preopened_reaction_requires_fresh_exact_source_proof_before_deep(self):
        start = EXPLORE.index("$initialReactionProgress = Get-CaptureProgress 'reaction'")
        end = EXPLORE.index("if (($AutoScreenThenDeep -or $ReactionProbeOnly) -and $deepCaptureAllowed)", start)
        block = EXPLORE[start:end]
        self.assertIn("[int]$initialReactionProgress.query_records -gt 0", block)
        self.assertIn("$observedProof.Count -eq 1", block)
        self.assertIn("$observedProof[0] -eq [string]$ExpectedSourcePostId", block)
        self.assertIn("$deepCaptureAllowed = $false", block)
        self.assertIn("verified-deeplink-source-unproven", block)
        self.assertIn("'preopened-deeplink'", block)
        self.assertLess(EXPLORE.index("$proofMatched = [bool](", start), end)

    def test_general_source_guard_rejects_mismatch(self):
        start = EXPLORE.index("function Test-ExpectedSourceGuard")
        block = EXPLORE[start:start + 2200]
        self.assertIn("$matched = ($observed.Count -eq 1 -and $observed[0] -eq [string]$ExpectedSourcePostId)", block)
        self.assertIn("return $matched", block)
        self.assertIn("stopping this family before further pagination", block)

    def test_primary_explorer_is_bound_to_strict_target_family_window(self):
        marker = '$primaryFamilyCaptureStartLine = Get-CaptureJsonlLineCount'
        start = RUN.index(marker)
        block = RUN[start:start + 3500]
        self.assertIn('"-ExpectedSourcePostId", [string]$script:PrimaryVerifiedPostId', block)
        self.assertIn('"-CaptureMinLine", [string]([int]$primaryFamilyCaptureStartLine + 1)', block)
        self.assertIn('primary explorer bound to post=', block)

    def test_strict_target_reaction_scan_does_not_use_generic_cross_post_swipe(self):
        start = EXPLORE.index("function Find-StrictTargetReactionEntry")
        end = EXPLORE.index("function Scroll-And-Dump", start)
        block = EXPLORE[start:end]
        self.assertIn("strict-target-entry-reveal", block)
        self.assertIn("AutoCompleteTextView", block)
        self.assertIn("RecyclerView", block)
        self.assertIn("shell input swipe 540 1500 540 650 550", block)
        self.assertIn("strict-detail-reveal", block)
        self.assertNotIn("shell input swipe 540 1650 540 850 500", block)
        self.assertIn("Test-ExpectedSourceGuard", block)

    def test_strict_target_reaction_group_permalink_allows_one_structured_reveal(self):
        start = EXPLORE.index("function Find-StrictTargetReactionEntry")
        end = EXPLORE.index("function Find-StrictTargetShareEntry", start)
        block = EXPLORE[start:end]
        self.assertIn("$groupRevealUsed = $false", block)
        self.assertIn("$groupBack", block)
        self.assertIn("$groupSearch", block)
        self.assertIn("$groupStoryOptions", block)
        self.assertIn("$groupLargeImage", block)
        self.assertIn("$confirmedGroupPermalink", block)
        self.assertIn("$groupCommentEntry = Find-CommentsEntryNode $path", block)
        self.assertIn("One reveal is the entire allowance", block)

    def test_strict_reaction_normalization_uses_the_scanned_comment_entry(self):
        self.assertIn("-RequireDetailEntry -Entry $commentEntry", EXPLORE)
        open_start = EXPLORE.index("function Open-PostDetailViaComments")
        open_end = EXPLORE.index("function Reveal-NormalizedPostSummary", open_start)
        block = EXPLORE[open_start:open_end]
        self.assertIn("if ($Entry) { $Entry }", block)

    def test_strict_target_share_scan_is_target_preserving_and_guarded(self):
        start = EXPLORE.index("function Find-StrictTargetShareEntry")
        end = EXPLORE.index("function Scroll-And-Dump", start)
        block = EXPLORE[start:end]
        self.assertIn("Find-ShareCountNode", block)
        self.assertIn("Find-CommentsDetailEntryNode", block)
        self.assertIn("strict-target-share-entry-reveal", block)
        self.assertIn("AutoCompleteTextView", block)
        self.assertIn("RecyclerView", block)
        self.assertIn("shell input swipe 540 1500 540 650 550", block)
        self.assertNotIn("shell input swipe 540 1650 540 850 500", block)
        self.assertIn("Test-ExpectedSourceGuard", block)

    def test_strict_target_share_group_permalink_allows_only_one_structured_reveal(self):
        start = EXPLORE.index("function Find-StrictTargetShareEntry")
        end = EXPLORE.index("function Scroll-And-Dump", start)
        block = EXPLORE[start:end]
        self.assertIn("$groupRevealUsed = $false", block)
        self.assertIn("$groupBack", block)
        self.assertIn("$groupSearch", block)
        self.assertIn("$groupStoryOptions", block)
        self.assertIn("$groupLargeImage", block)
        self.assertIn("$confirmedGroupPermalink", block)
        self.assertIn("$groupRevealUsed = $true", block)
        group_tail = block[block.index("if ($confirmedGroupPermalink) {"):]
        self.assertIn("$groupEntry = Find-ShareCountNode $path", group_tail)
        self.assertIn("One reveal is the entire allowance", group_tail)
        self.assertNotIn("Find-FeedShareMetricHotspotNode", block)

    def test_group_share_falls_back_to_common_single_post_route_when_inline_is_unavailable(self):
        self.assertIn("$useInlineGroupRoute = $false", OPEN)
        self.assertIn("$inlineGroupRouteCandidate = $false", OPEN)
        start = OPEN.index("$preserveInlineGroup =")
        end = OPEN.index("Write-Diagnostic $true 'target-confirmed-in-capture'", start)
        block = OPEN[start:end]
        self.assertIn("Group inline surface is not applicable; continuing with common single-post detail route.", block)
        self.assertIn("Group inline surface did not prove timestamp promotion; continuing with common single-post detail route.", block)
        self.assertIn("Group inline Share proof was not unique; continuing with common single-post detail route.", block)
        self.assertIn("if (-not $useInlineGroupRoute)", block)
        self.assertIn("if ($inlineGroupRouteCandidate)", block)
        self.assertNotIn("Group Share must start on an inline group surface", block)

    def test_group_share_common_route_keeps_strict_target_verification(self):
        start = OPEN.index("if (-not $useInlineGroupRoute)")
        end = OPEN.index("if ($inlineGroupRouteCandidate)", start)
        block = OPEN[start:end]
        self.assertIn("target-network-match-is-not-a-unique-post-identity", block)
        self.assertIn("$script:ResolvedAuthorId = [string]$identityAuthors[0]", block)
        self.assertIn("$script:TargetUiState -ne 'facebook-content'", block)

    def test_strict_target_share_path_uses_detail_scan_and_skips_visual_probe(self):
        start = EXPLORE.index("if ($OpenShareList) {")
        end = EXPLORE.index("$script:ExplorerUiSnapshotMode = 'reaction'", start)
        block = EXPLORE[start:end]
        self.assertIn("Find-StrictTargetShareEntry", block)
        self.assertIn("ExpectedSourcePostId", block)
        self.assertIn("[string]::IsNullOrWhiteSpace([string]$ExpectedSourcePostId)", block)
        self.assertIn("Open-PostDetailViaComments $sharePath 'share' -RequireDetailEntry", block)
        self.assertIn("no-share-count-after-strict-target-detail-scan", EXPLORE)

    def test_share_comments_wait_for_late_count_without_using_share_composer(self):
        self.assertIn("[int]$ShareCountReadyTimeoutSeconds = 15", EXPLORE)
        start = EXPLORE.index("function Wait-NormalizedShareCount")
        end = EXPLORE.index("function Find-EntryWithFeedScan", start)
        helper = EXPLORE[start:end]
        self.assertIn("[DateTime]::UtcNow.AddSeconds", helper)
        self.assertIn("Find-ShareCountNode $current", helper)
        self.assertIn("Start-Sleep -Milliseconds", helper)
        self.assertNotIn("Find-ActionBarNode", helper)
        share_start = EXPLORE.index("if ($OpenShareList) {")
        reaction_start = EXPLORE.index("$script:ExplorerUiSnapshotMode = 'reaction'", share_start)
        share_block = EXPLORE[share_start:reaction_start]
        self.assertIn(
            "Wait-NormalizedShareCount $sharePath 'share' $ShareCountReadyTimeoutSeconds 2",
            share_block,
        )
        self.assertNotIn("Reveal-NormalizedPostSummary $sharePath 'share'", share_block)

    def test_all_strict_target_reaction_list_opens_require_fresh_source_proof(self):
        start = EXPLORE.index("$initialReactionProgress = Get-CaptureProgress 'reaction'")
        end = EXPLORE.index("if (($AutoScreenThenDeep -or $ReactionProbeOnly) -and $deepCaptureAllowed)", start)
        block = EXPLORE[start:end]
        self.assertIn("if (-not [string]::IsNullOrWhiteSpace([string]$ExpectedSourcePostId))", block)
        self.assertIn("$observedProof.Count -eq 1", block)
        self.assertIn("$observedProof[0] -eq [string]$ExpectedSourcePostId", block)
        self.assertIn("'direct-ui-reaction-count'", block)
        self.assertIn("$deepCaptureAllowed = $false", block)

    def test_strict_target_reaction_path_uses_target_preserving_entry_scan(self):
        start = EXPLORE.index("if ($OpenReactionList) {")
        block = EXPLORE[start:start + 2500]
        self.assertIn("Find-StrictTargetReactionEntry", block)
        self.assertIn("ExpectedSourcePostId", block)

    def test_reaction_comments_surface_reveals_numeric_summary_before_count_lookup(self):
        start = EXPLORE.index("Open-PostDetailViaComments $reactionPath 'reaction' -RequireDetailEntry")
        block = EXPLORE[start:start + 4200]
        reveal = block.index("Reveal-NormalizedPostSummary $reactionPath 'reaction' 3")
        lookup = block.index("$reactionEntry = Find-ReactionCountNode $reactionPath")
        self.assertLess(reveal, lookup)
        self.assertIn("comments-summary-reveal", block)
        self.assertIn("reaction_native_fallback", block)

    def test_reaction_count_finder_does_not_accept_bare_like_action_labels(self):
        start = EXPLORE.index("function Find-ReactionCountNode")
        end = EXPLORE.index("function Find-ShareCountNode", start)
        block = EXPLORE[start:end]
        self.assertIn("feed_like_count", block)
        self.assertIn("[0-9][0-9,.", block)
        self.assertNotIn("Like|", block)
        self.assertNotIn("\\u8d5e|", block)

    def test_comment_detail_finder_excludes_bare_comment_action(self):
        start = EXPLORE.index("function Find-CommentsDetailEntryNode")
        end = EXPLORE.index("function Get-FeedSurfaceState", start)
        block = EXPLORE[start:end]
        self.assertIn("/feed_comment_count", block)
        self.assertIn("Comment|Comments", block)
        self.assertIn("\\u8bc4\\u8bba", block)
        self.assertIn("return $false", block)

    def test_strict_reaction_scan_limits_bare_comment_action_to_confirmed_group(self):
        start = EXPLORE.index("function Find-StrictTargetReactionEntry")
        end = EXPLORE.index("function Scroll-And-Dump", start)
        block = EXPLORE[start:end]
        self.assertIn("Find-CommentsDetailEntryNode", block)
        group_branch = block.index("if ($confirmedGroupPermalink) {")
        self.assertGreater(block.index("$groupCommentEntry = Find-CommentsEntryNode $path"), group_branch)
        self.assertLess(block.index("$commentEntry = Find-CommentsDetailEntryNode $path"), group_branch)

    def test_strict_reaction_normalization_requires_comment_detail_entry(self):
        marker = "if (-not $reactionPreopened -and -not $reactionEntry -and $reactionPhotoBackDepth -eq 0) {"
        start = EXPLORE.index(marker)
        end = EXPLORE.index("if (-not $reactionPreopened -and -not $reactionEntry) {", start)
        block = EXPLORE[start:end]
        self.assertIn("Open-PostDetailViaComments $reactionPath 'reaction' -RequireDetailEntry", block)
        self.assertIn("no-reaction-count-after-strict-target-detail-scan", EXPLORE)

    def test_reaction_summary_icon_entry_is_structurally_separate_from_like_action(self):
        start = EXPLORE.index("function Find-ReactionSummaryIconNode")
        end = EXPLORE.index("function Find-ShareCountNode", start)
        block = EXPLORE[start:end]
        self.assertIn("$_.Naf", block)
        self.assertIn("android.view.ViewGroup", block)
        self.assertIn("$_.X1 -ge 900", block)
        self.assertIn("$contentViewports", block)
        self.assertIn("$_.Scrollable", block)
        self.assertIn("RecyclerView|ScrollView", block)
        self.assertIn("$_.Y2 -ge $candidate.Y2", block)
        self.assertIn("AutoCompleteTextView|EditText", block)
        self.assertIn("$candidate.Y2 -ge $composer.Y1", block)
        self.assertIn("$rowTolerance = 16", block)
        self.assertIn("$_.Package -eq 'com.facebook.katana'", block)
        self.assertIn("Like button", block)
        self.assertIn("Comment|Comments", block)
        self.assertIn("Share button", block)
        self.assertIn("Label = 'reaction-icons-summary'", block)

    def test_comments_normalization_accepts_reaction_summary_icon(self):
        reveal_start = EXPLORE.index("function Reveal-NormalizedPostSummary")
        reveal_end = EXPLORE.index("function Find-EntryWithFeedScan", reveal_start)
        reveal = EXPLORE[reveal_start:reveal_end]
        self.assertIn("Find-ReactionSummaryIconNode $current", reveal)

        normalized_start = EXPLORE.index("elseif ($normalized.State -eq 'comments-surface')")
        normalized_end = EXPLORE.index("if ($reactionEntry -and $Diagnostics", normalized_start)
        normalized = EXPLORE[normalized_start:normalized_end]
        self.assertIn("Find-ReactionSummaryIconNode $reactionPath", normalized)

    def test_strict_target_prefers_numeric_or_reaction_icon_summary_entry(self):
        start = EXPLORE.index("function Find-StrictTargetReactionEntry")
        end = EXPLORE.index("function Scroll-And-Dump", start)
        block = EXPLORE[start:end]
        numeric = block.index("Find-ReactionCountNode")
        icons = block.index("Find-ReactionSummaryIconNode")
        comments = block.index("Find-CommentsDetailEntryNode")
        self.assertLess(numeric, icons)
        self.assertLess(icons, comments)

    def test_strict_target_reaction_entry_failure_gets_bounded_same_target_retry(self):
        marker = 'strict target Reaction summary was not rendered; reopening the same verified target'
        start = RUN.index(marker) - 2200
        block = RUN[start:start + 9000]
        self.assertIn('$primaryReactionRetryLimit = 1', block)
        self.assertIn("no-reaction-count-after-strict-target-detail-scan", block)
        self.assertIn("target-open-retry-{0:00}", block)
        self.assertIn("'-PostUrl', $PostUrl", block)
        self.assertIn("'-PostId', $PostId", block)
        self.assertIn("'-CaptureMinLine', [string]([int]$retryOpenBoundary + 1)", block)
        self.assertIn("$retryResolvedPostId -ne [string]$script:PrimaryVerifiedPostId", block)
        self.assertIn('"-ExpectedSourcePostId", [string]$script:PrimaryVerifiedPostId', block)
        self.assertIn('"-CaptureMinLine", [string]([int]$primaryFamilyCaptureStartLine + 1)', block)
        self.assertIn('retry explorer bound to post=', block)
        self.assertIn('Invoke-NativePermalinkFallback', RUN[RUN.index(marker):])

    def test_new_strict_target_entry_failure_is_explicit_fallback_engineering_reason(self):
        self.assertIn("'no-reaction-count-after-strict-target-detail-scan'", RUN)

    def test_all_reaction_terminal_stops_redundant_category_tabs_only_after_diagnostic(self):
        start = EXPLORE.index('$paginationResult = Scroll-ReactionAdaptive ("reaction-category-')
        block = EXPLORE[start:start + 1800]
        diagnostic = block.index('Add-PaginationDiagnostic ($tab + 1) $candidate $paginationResult')
        all_terminal = block.index("[string]$paginationResult.reaction_key -eq '__ALL__' -and (Get-SafeBoolProperty $paginationResult 'terminal_seen' $false)")
        mismatch = block.index("[string]$paginationResult.stop_reason -eq 'target-mismatch'")
        self.assertLess(diagnostic, all_terminal)
        self.assertLess(all_terminal, mismatch)
        self.assertIn('Reaction All tab reached terminal; skipping redundant per-reaction category tabs.', block)
        self.assertNotIn("if ([bool]$paginationResult.terminal_seen) { break }", block)


if __name__ == "__main__":
    unittest.main()
