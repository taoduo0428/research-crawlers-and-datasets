import json
import os
from pathlib import Path
import tempfile
import unittest
import http.client
import threading
import socket
import sys
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from facebook_local import configuration, environment, initialize, choose_port


class LocalDeploymentTests(unittest.TestCase):
    def test_exported_mac_entrypoint_is_executable_lf_and_hash_matches(self):
        import hashlib
        import zipfile
        from tools.export_source import release_files, write_archive
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'release-files.txt').write_text('CONFIGURE.command\n', encoding='utf-8')
            (root/'CONFIGURE.command').write_bytes(b'#!/bin/bash\r\nexit 0\r\n')
            archive = root/'source.zip'
            write_archive(release_files(root), archive)
            with zipfile.ZipFile(archive) as bundle:
                name = 'facebook/CONFIGURE.command'
                data = bundle.read(name)
                self.assertNotIn(b'\r', data)
                self.assertEqual(bundle.getinfo(name).external_attr >> 16, 0o100755)
                self.assertIn(hashlib.sha256(data).hexdigest(), bundle.read('facebook/SHA256SUMS.txt').decode())

    def test_new_registry_skips_busy_console_or_adb_pair_then_preserves_serial(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'facebook.local.example.json').write_text('{}', encoding='utf-8')
            template = {'workers': [{'worker_id': 'worker-01', 'serial': 'emulator-5554',
                                    'avd_name': 'test', 'capture_port': 18081, 'native_port': 18766}]}
            (root/'facebook_workers.example.json').write_text(json.dumps(template), encoding='utf-8')
            with patch('facebook_local.port_available', side_effect=lambda p: p not in (5555, 5556)):
                initialize(root)
            registry = root/'facebook_workers.json'
            self.assertEqual(json.loads(registry.read_text())['workers'][0]['serial'], 'emulator-5558')
            before = registry.read_bytes()
            with patch('facebook_local.port_available', return_value=False):
                initialize(root)
            self.assertEqual(registry.read_bytes(), before)

    def test_port_selection_skips_live_listener_and_reserved(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            occupied = listener.getsockname()[1]
            if occupied > 65000:
                self.skipTest('OS chose port too close to upper bound')
            reserved = {occupied + 1}
            chosen = choose_port(occupied, reserved)
            self.assertNotIn(chosen, (occupied, occupied + 1))
            self.assertIn(chosen, reserved)

    def test_mac_configuration_discovers_native_sdk_and_direct_route(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sdk = root / 'Library/Android/sdk'
            (sdk / 'emulator').mkdir(parents=True)
            (sdk / 'emulator/emulator').touch()
            with patch('facebook_local.sys.platform', 'darwin'), patch('facebook_local.Path.home', return_value=root), patch.dict(os.environ, {}, clear=True):
                settings = configuration(root)
                environment(settings, root)
                self.assertEqual(settings['sdk_root'], str(sdk))
                self.assertEqual(settings['network']['mode'], 'direct')
                self.assertEqual(os.environ['FACEBOOK_ADB_EXE'], str((sdk / 'platform-tools/adb').resolve()))

    def test_old_windows_settings_preserve_upstream_route(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'facebook.local.json').write_text('{}', encoding='utf-8')
            with patch('facebook_local.sys.platform', 'win32'):
                settings = configuration(root)
                self.assertEqual(settings['network']['mode'], 'upstream')
                self.assertEqual(settings['network']['upstream_url'], 'http://127.0.0.1:7897')

    def test_mac_api_dispatch_has_no_powershell_or_windows_path(self):
        from facebook_capture_api import build_capture_command
        with patch('facebook_capture_api.sys.platform', 'darwin'):
            command = build_capture_command({'post_url': 'https://www.facebook.com/123/posts/456', 'save_artifacts': ['progress']}, run_dir=Path('runs/test'))
        self.assertTrue(any(arg.endswith('facebook_android_capture.py') for arg in command))
        self.assertNotIn('-File', command)
        self.assertIn('--save-artifacts', command)

    def test_v2_serves_assets_but_never_parent_files(self):
        from start_ui_v2 import V2ProxyHandler
        server = ThreadingHTTPServer(('127.0.0.1', 0), V2ProxyHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for path, expected in [('/', 200), ('/app.js', 200), ('/../README.md', 404), ('/%2e%2e/README.md', 404), ('/..\\README.md', 400)]:
                connection = http.client.HTTPConnection(*server.server_address, timeout=5)
                connection.request('GET', path)
                response = connection.getresponse()
                response.read()
                self.assertEqual(response.status, expected, path)
                connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_initialize_preserves_user_registry_and_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('facebook.local', 'facebook_workers'):
                (root / (name + '.example.json')).write_text('{}', encoding='utf-8')
            initialize(root)
            registry = root / 'facebook_workers.json'
            registry.write_bytes(b'{"existing_account":true}')
            initialize(root)
            self.assertEqual(registry.read_bytes(), b'{"existing_account":true}')

    def test_explicit_paths_survive_spaces_and_non_ascii(self):
        with tempfile.TemporaryDirectory(prefix='fb deploy ') as directory:
            root = Path(directory)
            sdk, avd = root / 'SDK 路径', root / 'AVD 路径'
            (root / 'facebook.local.json').write_text(json.dumps({'sdk_root': str(sdk), 'avd_home': str(avd), 'port': 18765}), encoding='utf-8')
            with patch.dict(os.environ):
                settings = configuration(root)
                environment(settings, root)
                self.assertEqual(os.environ['FACEBOOK_SDK_ROOT'], str(sdk.resolve()))
                self.assertEqual(os.environ['FACEBOOK_AVD_HOME'], str(avd.resolve()))
                self.assertTrue(os.environ['FACEBOOK_ADB_EXE'].endswith('adb.exe' if sys.platform == 'win32' else 'adb'))
                self.assertEqual(settings['port'], 18765)

    def test_rejects_invalid_ports_before_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for port in (True, '8765', -1, 65536):
                (root / 'facebook.local.json').write_text(json.dumps({'port': port}), encoding='utf-8')
                with self.subTest(port=port), self.assertRaises(ValueError):
                    configuration(root)

    def test_ui_files_are_utf8_and_title_closes(self):
        root = Path(__file__).resolve().parents[1]
        for folder in ('web', 'web_v2'):
            html = (root / folder / 'index.html').read_text(encoding='utf-8')
            self.assertNotIn('\ufffd', html)
            self.assertIn('</title>', html)
            self.assertNotIn('?/span>', html)

    def test_v2_first_page_test_mode_is_opt_in_and_sends_explicit_one_page_limit(self):
        root = Path(__file__).resolve().parents[1]
        html = (root / 'web_v2' / 'index.html').read_text(encoding='utf-8')
        app = (root / 'web_v2' / 'app.js').read_text(encoding='utf-8')

        checkbox_line = next(
            line for line in html.splitlines() if 'id="reactionFirstPageTest"' in line
        )
        self.assertIn('type="checkbox"', checkbox_line)
        self.assertNotIn('checked', checkbox_line)
        self.assertIn('reactionFirstPageTest', app)
        self.assertIn('requestBody.reaction_scroll_limit = 1', app)
        self.assertIn('if (firstPageTestMode)', app)


if __name__ == '__main__':
    unittest.main()
