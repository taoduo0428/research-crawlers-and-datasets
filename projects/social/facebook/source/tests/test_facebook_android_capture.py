import unittest
import subprocess
from pathlib import Path
from unittest.mock import patch, Mock
from facebook_android_capture import (
    CaptureError, nodes_from_xml, reaction_entry, visibility_limited,
    progress_state, parse_args, validate_options, CaptureRunner, Runtime,
    FACEBOOK_URL_COMPONENT, FACEBOOK_URL_INTENT_FLAGS,
)


def ui_node(label, resource='feed_like_count', long_click='false'):
    return nodes_from_xml('<hierarchy><node package="com.facebook.katana" '
        'enabled="true" clickable="true" bounds="[0,0][200,100]" '
        f'long-clickable="{long_click}" resource-id="com.facebook.katana:id/{resource}" '
        f'text="{label}" /></hierarchy>')[0]


class AndroidCaptureContracts(unittest.TestCase):
    def test_default_scroll_budget_is_unlimited_and_zero_is_valid(self):
        args = parse_args(['--serial', 'emulator-5554', '--post-url',
                           'https://www.facebook.com/123/posts/456', '--output-dir', 'runs/test'])
        self.assertEqual(args.max_scrolls, 0)
        args.max_scrolls = 0
        self.assertEqual(validate_options(args, {'FACEBOOK_NETWORK_MODE': 'direct'})[1], 'regular')

    def test_explicit_scroll_budget_is_reported_as_absolute_cap(self):
        args = parse_args(['--serial', 'emulator-5554', '--post-url',
                           'https://www.facebook.com/123/posts/456', '--output-dir', 'runs/test',
                           '--max-scrolls', '2'])
        runner = CaptureRunner(args)
        self.assertEqual(runner.pagination_outcome('scroll-budget'), 'absolute-cap')
        self.assertEqual(runner.pagination_outcome('terminal'), 'terminal')

    def test_capture_children_have_no_terminal_or_stdin_dependency(self):
        runtime = Runtime()
        log = Mock()
        with patch('facebook_android_capture.subprocess.Popen') as popen:
            runtime.spawn(['capture'], log, {})
        kwargs = popen.call_args.kwargs
        self.assertIs(kwargs['stdin'], subprocess.DEVNULL)
        self.assertTrue(kwargs['start_new_session'])

    def test_navigate_uses_explicit_facebook_component_and_reuse_flags(self):
        args = parse_args(['--serial', 'emulator-5554', '--post-url',
                           'https://www.facebook.com/123/posts/456', '--output-dir', 'runs/test'])
        runner = CaptureRunner(args)
        runner.adb = Mock(side_effect=['Status: ok', ''])
        runner.wait_for = Mock()
        runner.dump = Mock(return_value=[ui_node('334')])
        runner.navigate()
        self.assertEqual(runner.adb.call_args_list[0].args, (
            'shell', 'am', 'start', '-W', '-a', 'android.intent.action.VIEW', '-d',
            args.post_url, '-n', FACEBOOK_URL_COMPONENT, '-f', FACEBOOK_URL_INTENT_FLAGS,
        ))

    def test_cleanup_restores_only_owned_proxy_and_children(self):
        args = parse_args(['--serial', 'emulator-5554', '--post-url',
                          'https://www.facebook.com/123/posts/456', '--output-dir', 'runs/test'])
        runner = CaptureRunner(args)
        runner.old_proxy = ':0'
        runner.proxy_dirty = runner.reverse_owned = True
        runner.adb = Mock(side_effect=['127.0.0.1:18081', '', ':0', ''])
        child = Mock()
        child.poll.return_value = None
        runner.children = [child]
        runner.cleanup()
        self.assertEqual(runner.cleanup_errors, [])
        runner.adb.assert_any_call('shell', 'settings', 'put', 'global', 'http_proxy', ':0')
        child.terminate.assert_called_once()
        child.wait.assert_called_once_with(timeout=8)

    def test_cleanup_preserves_concurrent_proxy_change(self):
        args = parse_args(['--serial', 'emulator-5554', '--post-url',
                          'https://www.facebook.com/123/posts/456', '--output-dir', 'runs/test'])
        runner = CaptureRunner(args)
        runner.old_proxy, runner.proxy_dirty = ':0', True
        runner.adb = Mock(return_value='127.0.0.1:19999')
        runner.cleanup()
        self.assertEqual(runner.adb.call_count, 1)
        self.assertIn('restore Android http_proxy', runner.cleanup_errors)

    def test_never_taps_like_action_instead_of_count(self):
        for label in ('Like', 'Liked', '赞', 'Like button'):
            with self.subTest(label=label), self.assertRaises(CaptureError):
                reaction_entry([ui_node(label)])
        self.assertEqual(reaction_entry([ui_node('334')])['label'], '334')

    def test_ambiguous_counts_are_rejected(self):
        with self.assertRaises(CaptureError):
            reaction_entry([ui_node('334'), ui_node('63')])

    def test_browser_observed_visibility_restriction(self):
        text = ('You can see the total number of people who reacted to this post, '
                'but only the names of friends who have reacted will be shown.')
        self.assertTrue(visibility_limited([{'label': text}]))
        self.assertFalse(visibility_limited([{'label': '334 reactions'}]))

    def test_no_records_never_mean_terminal(self):
        self.assertEqual(progress_state([], '123')[1], 'pending')

    def test_wrong_source_and_throttling_override_terminal(self):
        with patch('facebook_android_capture.summarize_reaction_records', return_value={
                'observed_source_post_ids': ['other'], 'terminal_page_seen': True}):
            self.assertEqual(progress_state([], '123')[1], 'wrong-source')
        with patch('facebook_android_capture.summarize_reaction_records', return_value={
                'observed_source_post_ids': ['123'], 'rate_limit_count': 1, 'terminal_page_seen': True}):
            self.assertEqual(progress_state([], '123')[1], 'throttled')

    def test_regular_mode_accepts_android_pcap_selection(self):
        args = parse_args(['--serial', 'emulator-5554', '--post-url',
                          'https://www.facebook.com/watch/?v=1069930052107630',
                          '--output-dir', 'runs/test'])
        self.assertEqual(validate_options(args, {'FACEBOOK_NETWORK_MODE': 'direct'})[1], 'regular')
        args.save_artifacts = 'pcap'
        self.assertEqual(validate_options(args, {'FACEBOOK_NETWORK_MODE': 'direct'}), ({'pcap'}, 'regular'))


class MacActiveRunnerPairingTests(unittest.TestCase):
    """Host-independent contracts; no native Mac, ADB or Facebook execution."""

    @patch('facebook_capture_api.sys.platform', 'darwin')
    def test_five_url_families_roundtrip_through_active_runner(self):
        import facebook_capture_api as api
        from pathlib import Path
        from tempfile import TemporaryDirectory
        urls = (
            'https://www.facebook.com/123/posts/456',
            'https://www.facebook.com/share/p/fixture/',
            'https://www.facebook.com/example/posts/pfbidFixture',
            'https://www.facebook.com/groups/123/permalink/456/',
            'https://www.facebook.com/reel/456/',
        )
        with TemporaryDirectory() as temporary, \
                patch('facebook_android_capture.subprocess.run', side_effect=AssertionError('no subprocess')), \
                patch('facebook_android_capture.subprocess.Popen', side_effect=AssertionError('no child')):
            output = Path(temporary) / 'Mac 输出 fixture'
            for url in urls:
                with self.subTest(url=url):
                    request = api.normalize_request({'post_url': url, 'collect': ['reactions']})
                    command = api.build_capture_command(request, serial='emulator-5556',
                        run_dir=output, proxy_port=18082, native_port=18767)
                    self.assertEqual(Path(command[1]).name, 'facebook_android_capture.py')
                    self.assertNotIn('--max-scrolls', command)
                    args = parse_args(command[2:])
                    self.assertEqual(args.post_url, request['post_url'])
                    self.assertEqual(args.serial, 'emulator-5556')
                    self.assertEqual((args.capture_port, args.native_port), (18082, 18767))
                    self.assertEqual(Path(args.output_dir), output)
                    self.assertEqual(args.max_scrolls, 0)
                    selected, mode = validate_options(args, {'FACEBOOK_NETWORK_MODE': 'direct'})
                    self.assertEqual(set(selected), {'safe_capture', 'progress'})
                    self.assertEqual(mode, 'regular')
            self.assertFalse(output.exists())

    @patch('facebook_capture_api.sys.platform', 'darwin')
    def test_explicit_cap_post_id_and_frida_flag_are_not_lost(self):
        import facebook_capture_api as api
        request = api.normalize_request({'post_url': 'https://www.facebook.com/123/posts/456',
            'post_id': '456', 'reaction_scroll_limit': 7,
            'save_artifacts': ['mitm', 'safe_capture', 'progress']})
        args = parse_args(api.build_capture_command(request, run_dir=Path('runs/not-executed'), frida_ssl_unpin=True)[2:])
        self.assertEqual(args.max_scrolls, 7)
        self.assertEqual(args.post_id, '456')
        self.assertTrue(args.frida_ssl_unpin)
        self.assertEqual(set(validate_options(args, {'FACEBOOK_NETWORK_MODE': 'direct'})[0]),
                         {'mitm', 'safe_capture', 'progress'})

    @patch('facebook_capture_api.sys.platform', 'darwin')
    def test_shares_are_forwarded_to_active_runner(self):
        import facebook_capture_api as api
        for collect in (['shares'], ['reactions', 'shares']):
            with self.subTest(collect=collect):
                request = api.normalize_request({'post_url': 'https://www.facebook.com/123/posts/456',
                                                 'collect': collect})
                args = parse_args(api.build_capture_command(request, run_dir=Path('runs/not-executed'))[2:])
                self.assertEqual(set(args.collect.split(',')), set(collect))

    @patch('facebook_capture_api.sys.platform', 'darwin')
    def test_pcap_off_macos_rejected_before_any_native_tool_or_device_probe(self):
        import facebook_capture_api as api
        request = api.normalize_request({'post_url': 'https://www.facebook.com/123/posts/456',
                                         'save_artifacts': ['pcap']})
        runner = CaptureRunner(parse_args(api.build_capture_command(request, run_dir=Path('runs/not-executed'))[2:]))
        runner.rt = Mock()
        runner.adb = Mock(side_effect=AssertionError('no real device probe'))
        with patch('facebook_android_capture.platform.system', return_value='Windows'), self.assertRaisesRegex(CaptureError, 'requires macOS'):
            runner.preflight()
        runner.rt.which.assert_not_called()
        runner.adb.assert_not_called()

    def test_child_stop_failure_is_retained_and_other_handles_close(self):
        args = parse_args(['--serial', 'emulator-5554', '--post-url',
            'https://www.facebook.com/123/posts/456', '--output-dir', 'runs/not-executed'])
        runner = CaptureRunner(args)
        child, log, lease = Mock(), Mock(), Mock()
        child.poll.return_value = None
        child.wait.side_effect = subprocess.TimeoutExpired('fixture', 8)
        runner.children, runner.logs, runner.leases = [child], [log], [lease]
        runner.adb = Mock(side_effect=AssertionError('no device mutation'))
        runner.cleanup()
        self.assertEqual(runner.cleanup_errors, ['stop owned child'])
        child.terminate.assert_called_once()
        child.kill.assert_called_once()
        self.assertEqual(child.wait.call_count, 2)
        log.close.assert_called_once()
        lease.close.assert_called_once()
        runner.adb.assert_not_called()


class MacShareIntegrationTests(unittest.TestCase):
    def make_runner(self, directory, collect='shares', artifacts='safe_capture,progress'):
        return CaptureRunner(parse_args(['--serial', 'emulator-5554', '--post-url',
            'https://www.facebook.com/42/posts/123', '--output-dir', str(directory),
            '--collect', collect, '--save-artifacts', artifacts]))

    @staticmethod
    def share_record(source='123', more=False, edges=None, status=200):
        import base64, json
        from urllib.parse import urlencode
        feedback = base64.b64encode(('feedback:' + source).encode()).decode()
        return {'friendly_name': 'ResharesOfContentFeedQuery', 'status_code': status,
            'request_body': urlencode({'variables': json.dumps({'feedback_id': feedback})}),
            'response_body': json.dumps({'data': {'node': {'reshares': {
                'edges': edges if edges is not None else [{'node': {'id': 's1', 'actors': [{'id': 'u1', 'name': 'Fixture'}]}}],
                'page_info': {'has_next_page': more, 'end_cursor': 'fixture'}}}}})}

    def test_safe_count_selector_never_selects_composer_or_ambiguous_post(self):
        from facebook_android_capture import share_entry, share_scroll_rect
        for label in ('Share', 'Share post', '分享', '分享按钮'):
            with self.subTest(label=label), self.assertRaises(CaptureError):
                share_entry([ui_node(label, 'feed_share_button')])
        node = ui_node('16 shares', 'feed_share_count')
        self.assertIs(share_entry([node]), node)
        with self.assertRaises(CaptureError):
            share_entry([node, ui_node('4 shares')])
        with self.assertRaises(CaptureError):
            share_scroll_rect([dict(node, scrollable='true')])

    def test_share_progress_requires_query_source_terminal_and_clean_tail(self):
        from facebook_android_capture import share_progress_state
        self.assertEqual(share_progress_state([], '123')[1], 'pending')
        for record, expected in ((self.share_record(), 'terminal'),
                                 (self.share_record(edges=[]), 'terminal'),
                                 (self.share_record(more=True), 'pending'),
                                 (self.share_record(source='999'), 'wrong-source'),
                                 (self.share_record(status=429), 'response-error')):
            with self.subTest(expected=expected):
                self.assertEqual(share_progress_state([(1, record)], '123')[1], expected)
        self.assertEqual(share_progress_state([(1, self.share_record()),
            (2, self.share_record(more=True))], '123')[1], 'invalid-pagination')
        malformed = self.share_record(); malformed['response_body'] = '{}'
        self.assertEqual(share_progress_state([(1, self.share_record()), (2, malformed)], '123')[1], 'response-error')

    def test_real_analyzer_and_api_accept_terminal_visible_subset_and_empty(self):
        import json, tempfile
        from facebook_capture_api import build_api_result
        for edges, expected_count in ((None, 1), ([], 0)):
            with self.subTest(count=expected_count), tempfile.TemporaryDirectory() as directory:
                runner = self.make_runner(directory)
                runner.source, runner.share_boundary, runner.share_stop_reason = '123', 2, 'terminal'
                runner.share_entry_label = '16 shares'
                navigation = {'friendly_name': 'NativePermalinkQuery', 'status_code': 200,
                    'response_body': json.dumps({'data': {'node': {'__typename': 'Story', 'id': '123',
                        'post_id': '123', 'actors': [{'id': '42', 'name': 'Owner'}],
                        'url': runner.args.post_url}}})}
                runner.capture.write_text(json.dumps(navigation) + '\n' + json.dumps(self.share_record(edges=edges)) + '\n', encoding='utf-8')
                self.assertEqual(runner.analyze(), 0)
                runner.validate_result()
                result = build_api_result(runner.out, {'collect': ['shares'], 'post_url': runner.args.post_url})
                self.assertEqual(len(result['share_users']), expected_count)
                self.assertEqual(result['completeness']['shares']['reported_total_count'], 16)
                self.assertTrue(result['completeness']['shares']['limited'])
                self.assertEqual(result['completeness']['shares']['identity_gap'], 16-expected_count)
                if not expected_count:
                    self.assertEqual(result['result_outcome']['code'], 'empty-lists')

    def test_analyzer_excludes_old_same_source_share_rows_but_retains_target(self):
        import json, tempfile
        from facebook_capture_api import build_api_result
        with tempfile.TemporaryDirectory() as directory:
            runner = self.make_runner(directory)
            runner.source, runner.share_boundary, runner.share_stop_reason = '123', 3, 'terminal'
            nav = {'friendly_name': 'NativePermalinkQuery', 'status_code': 200,
                'response_body': json.dumps({'data': {'node': {'__typename': 'Story', 'post_id': '123',
                    'actors': [{'id': '42'}], 'url': runner.args.post_url}}})}
            old = self.share_record(edges=[{'node': {'id': 'old', 'actors': [{'id': 'old-user'}]}}])
            runner.capture.write_text('\n'.join(json.dumps(r) for r in (nav, old, self.share_record()))+'\n', encoding='utf-8')
            self.assertEqual(runner.analyze(), 0)
            runner.validate_result()
            result = build_api_result(runner.out, {'collect': ['shares'], 'post_url': runner.args.post_url})
            self.assertEqual([r['id'] for r in result['share_users']], ['u1'])
            report = json.loads((runner.out/'analysis/facebook-sample-patterns.json').read_text())
            self.assertEqual(report['share_min_line'], 3)
            self.assertEqual(report['target_verification']['status'], 'matched')

    def test_combined_real_analysis_preserves_reaction_and_share_completion(self):
        import json, tempfile
        from facebook_capture_api import build_api_result
        with tempfile.TemporaryDirectory() as directory:
            runner = self.make_runner(directory, 'reactions,shares')
            runner.source, runner.share_boundary, runner.share_stop_reason = '123', 3, 'terminal'
            runner.entry_label = '1'
            runner.pagination_stop_reason, runner.pagination_state = 'terminal', 'terminal'
            nav = {'friendly_name': 'NativePermalinkQuery', 'status_code': 200,
                'response_body': json.dumps({'data': {'node': {'__typename': 'Story', 'post_id': '123',
                    'actors': [{'id': '42'}], 'url': runner.args.post_url}}})}
            reaction = self.share_record()
            reaction['friendly_name'] = 'FeedbackReactorsGraphService'
            reaction['response_body'] = json.dumps({'data': {'node': {'reactors': {
                'edges': [{'node': {'id': 'r1', 'name': 'Reaction Fixture'}}],
                'page_info': {'has_next_page': False, 'end_cursor': 'done'}}}}})
            runner.capture.write_text('\n'.join(json.dumps(r) for r in (nav, reaction, self.share_record()))+'\n', encoding='utf-8')
            self.assertEqual(runner.analyze(), 0)
            runner.validate_result()
            result = build_api_result(runner.out, {'collect': ['reactions', 'shares'], 'post_url': runner.args.post_url})
            self.assertEqual(result['completion_scope'], 'terminal')
            self.assertEqual([r['id'] for r in result['reaction_users']], ['r1'])
            self.assertEqual([r['id'] for r in result['share_users']], ['u1'])
            live = json.loads((runner.out/'facebook-live-progress.json').read_text())
            self.assertIn('reaction', live['families'])
            self.assertIn('share', live['families'])

    def test_stale_terminal_before_share_boundary_does_not_complete_fresh_phase(self):
        import json, tempfile
        with tempfile.TemporaryDirectory() as directory:
            runner = self.make_runner(directory)
            runner.source, runner.share_boundary = '123', 2
            runner.capture.write_text(json.dumps(self.share_record()) + '\n', encoding='utf-8')
            with self.assertRaisesRegex(CaptureError, 'Fresh Share phase'):
                runner.validate_result()

    def test_combined_cannot_start_share_after_failed_reaction_acceptance(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            runner = self.make_runner(directory, 'reactions,shares')
            runner.navigate, runner.paginate, runner.analyze = Mock(), Mock(return_value='terminal'), Mock(return_value=0)
            runner.validate_reaction_result = Mock(side_effect=CaptureError('fixture incomplete'))
            runner.navigate_shares = Mock()
            with self.assertRaises(CaptureError):
                runner.run_collection()
            runner.navigate_shares.assert_not_called()

    def test_combined_reopens_exact_locked_target_after_valid_reaction_phase(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            runner = self.make_runner(directory, 'reactions,shares')
            runner.source = '123'
            runner.navigate, runner.paginate, runner.analyze = Mock(), Mock(return_value='terminal'), Mock(return_value=0)
            runner.validate_reaction_result = Mock()
            runner.navigate_shares, runner.paginate_shares = Mock(), Mock(return_value='terminal')
            self.assertEqual(runner.run_collection(), 'terminal')
            runner.validate_reaction_result.assert_called_once()
            runner.navigate_shares.assert_called_once_with(reopen=True)

    def test_combined_terminal_shares_preserve_bounded_reaction_outcome(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            runner = self.make_runner(directory, 'reactions,shares')
            runner.navigate = Mock()
            runner.paginate = Mock(return_value='scroll-budget')
            runner.analyze = Mock(return_value=0)
            runner.validate_reaction_result = Mock()
            runner.navigate_shares = Mock()
            runner.paginate_shares = Mock(return_value='terminal')
            self.assertEqual(runner.run_collection(), 'scroll-budget')
            runner.validate_reaction_result.assert_called_once()
            runner.navigate_shares.assert_called_once_with(reopen=True)

    def test_standalone_share_terminal_uses_common_success_outcome(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            runner = self.make_runner(directory, 'shares')
            runner.navigate_shares, runner.paginate_shares = Mock(), Mock(return_value='terminal')
            self.assertEqual(runner.run_collection(), 'terminal')
            runner.navigate_shares.assert_called_once_with(reopen=False)


class MacApiLogHandoffTests(unittest.TestCase):
    def test_actual_api_child_uses_owned_log_and_reaches_runner_status(self):
        import json, tempfile
        import facebook_capture_api as api
        native_popen = subprocess.Popen
        child_code = """
import os, sys, types
import facebook_android_capture as cap
from unittest.mock import Mock
if os.name == 'nt':
    sys.modules['fcntl'] = types.SimpleNamespace(LOCK_EX=1, LOCK_NB=2, flock=Mock())
r = cap.CaptureRunner(cap.parse_args(sys.argv[1:]))
r.preflight = Mock()
r.start_transport = Mock()
r.run_collection = Mock(side_effect=cap.Cancelled(15))
r.analyze = Mock(return_value=2)
sys.exit(r.execute())
"""
        commands = []
        def launch(command, **kwargs):
            commands.append(command)
            return native_popen([command[0], '-c', child_code, *command[2:]], **kwargs)
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)/'attempt'
            request = api.normalize_request({'post_url': 'https://www.facebook.com/42/posts/123', 'collect': ['shares']})
            with patch.object(api.sys, 'platform', 'darwin'), patch.object(api, 'ensure_emulator'), \
                    patch.object(api.subprocess, 'Popen', side_effect=launch):
                with self.assertRaisesRegex(api.ApiError, 'exit 143'):
                    api.run_capture_job(request, 'log-fixture', run_dir=run_dir, timeout_seconds=15)
            status = json.loads((run_dir/'android-capture-status.json').read_text(encoding='utf-8'))
            self.assertEqual(status['exit_code'], 143)
            self.assertFalse(status['completion_claim_allowed'])
            self.assertEqual(status['cleanup_errors'], [])
            self.assertEqual(status['collect'], ['shares'])
            self.assertTrue((run_dir/'capture.log').is_file())
            self.assertTrue((run_dir/'.android-capture-owner').is_file())
            self.assertIn('--api-log', commands[0])

    def test_output_guard_allows_only_actual_inherited_regular_log(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = MacShareIntegrationTests().make_runner(root)
            runner.args.api_log = True
            log = root/'capture.log'
            with log.open('w', encoding='utf-8') as handle, patch('facebook_android_capture.sys.stdout', handle):
                runner._prepare_output_dir()
                other = root/'old-evidence.json'
                other.write_text('preserved', encoding='utf-8')
                with self.assertRaisesRegex(CaptureError, 'only its inherited'):
                    runner._prepare_output_dir()
                self.assertEqual(other.read_text(), 'preserved')
                other.unlink()  # Only this temporary fixture's own file.
            with (root/'different.log').open('w') as handle, patch('facebook_android_capture.sys.stdout', handle):
                # Remove the temporary alternate name from this runner's directory,
                # while leaving its handle open to test descriptor mismatch.
                runner.out = root/'second'
                runner.out.mkdir()
                (runner.out/'capture.log').write_text('old log')
                with self.assertRaisesRegex(CaptureError, 'not the inherited'):
                    runner._prepare_output_dir()
                self.assertEqual((runner.out/'capture.log').read_text(), 'old log')

    def test_standalone_rejects_existing_log_and_api_flag_rejects_empty_output(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = MacShareIntegrationTests().make_runner(root)
            runner.args.api_log = True
            with self.assertRaisesRegex(CaptureError, 'only its inherited'):
                runner._prepare_output_dir()
            runner.args.api_log = False
            (root/'capture.log').write_text('old', encoding='utf-8')
            with self.assertRaisesRegex(CaptureError, 'new or empty'):
                runner._prepare_output_dir()
            self.assertEqual((root/'capture.log').read_text(), 'old')

    def test_api_guard_rejects_log_symlink_even_if_stdout_targets_it(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root/'target.log'
            output = root/'output'
            output.mkdir()
            try:
                (output/'capture.log').symlink_to(target)
            except OSError:
                self.skipTest('This host cannot create temporary file symlinks')
            runner = MacShareIntegrationTests().make_runner(output)
            runner.args.api_log = True
            with target.open('w') as handle, patch('facebook_android_capture.sys.stdout', handle):
                with self.assertRaisesRegex(CaptureError, 'not the inherited'):
                    runner._prepare_output_dir()


class MacPcapIntegrationTests(unittest.TestCase):
    def make_runner(self, directory):
        runner = MacShareIntegrationTests().make_runner(directory, artifacts='pcap')
        runner.pcap_remote = '/data/local/tmp/facebook-' + runner.run_id + '.pcap'
        runner.pcap_pid, runner.pcap_started = 1234, True
        runner.selected = {'pcap'}
        runner.rt.sleep = Mock()
        return runner

    def test_execute_cancellation_runs_pcap_cleanup_and_persists_failure_status(self):
        import json, tempfile, types
        from facebook_android_capture import Cancelled
        for cleanup_failure in (False, True):
            with self.subTest(cleanup_failure=cleanup_failure), tempfile.TemporaryDirectory() as directory:
                runner = self.make_runner(directory)
                runner.out = Path(directory)/'capture'; runner.capture = runner.out/'safe-facebook-http-capture.jsonl'
                runner.preflight = Mock()
                runner.start_transport = Mock()
                runner.start_pcap = Mock()
                runner.run_collection = Mock(side_effect=Cancelled(15))
                runner.stop_pcap = Mock(side_effect=CaptureError('fixture stop timeout') if cleanup_failure else None)
                runner.analyze = Mock(return_value=2)
                fake_fcntl = types.SimpleNamespace(LOCK_EX=1, LOCK_NB=2, flock=Mock())
                with patch.dict('sys.modules', {'fcntl': fake_fcntl}), patch('facebook_android_capture.signal.signal', return_value=None):
                    self.assertEqual(runner.execute(), 143)
                runner.stop_pcap.assert_called_once()
                status = json.loads((runner.out/'android-capture-status.json').read_text())
                self.assertFalse(status['completion_claim_allowed'])
                self.assertTrue(status['partial_evidence_retained'])
                self.assertEqual(bool(status['cleanup_errors']), cleanup_failure)

    def test_pcap_ownership_requires_exact_executable_and_this_run_path(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            runner = self.make_runner(directory)
            for raw in ('/system/bin/tcpdump\0-w\0' + runner.pcap_remote + '\0', 'STOPPED'):
                runner.adb = Mock(return_value=raw)
                self.assertIn(runner._pcap_state(), ('owned', 'stopped'))
            for raw in ('', '/system/bin/tcpdump\0-w\0/another.pcap\0', '/system/bin/sh\0' + runner.pcap_remote):
                runner.adb = Mock(return_value=raw)
                with self.assertRaisesRegex(CaptureError, 'identity'):
                    runner._pcap_state()

    def test_pcap_verified_pull_publication_and_scoped_remote_removal(self):
        import hashlib, json, tempfile
        with tempfile.TemporaryDirectory() as directory:
            runner = self.make_runner(directory)
            data = bytes.fromhex('d4c3b2a1020004000000000000000000ffff000001000000')
            digest = hashlib.sha256(data).hexdigest()
            runner._pcap_state = Mock(side_effect=['owned', 'owned', 'stopped'])
            def adb(*args):
                if 'sha256sum' in args: return digest + '  ' + runner.pcap_remote
                if args[0] == 'pull': Path(args[-1]).write_bytes(data)
                return ''
            runner.adb = Mock(side_effect=adb)
            runner.stop_pcap()
            self.assertEqual((runner.out/'raw-network.pcap').read_bytes(), data)
            meta = json.loads((runner.out/'raw-network-capture.json').read_text())
            self.assertEqual(meta['sha256'], digest)
            self.assertEqual(meta['capture_location'], 'android')
            self.assertFalse(runner.pcap_started)
            runner.adb.assert_any_call('shell', 'su', '0', 'kill', '-2', '1234')
            runner.adb.assert_any_call('shell', 'su', '0', 'rm', '-f', runner.pcap_remote, runner.pcap_remote+'.pid')

    def test_preexited_pcap_producer_is_not_finalized_even_with_valid_file(self):
        import hashlib, tempfile
        with tempfile.TemporaryDirectory() as directory:
            runner = self.make_runner(directory)
            runner._pcap_state = Mock(return_value='stopped')
            data = bytes.fromhex('d4c3b2a1020004000000000000000000ffff000001000000')
            def adb(*args):
                if 'sha256sum' in args: return hashlib.sha256(data).hexdigest() + ' fixture'
                if args[0] == 'pull': Path(args[-1]).write_bytes(data)
                return ''
            runner.adb = Mock(side_effect=adb)
            with self.assertRaisesRegex(CaptureError, 'exited before requested stop'):
                runner.stop_pcap()
            runner.adb.assert_not_called()
            self.assertTrue(runner.pcap_started)
            self.assertFalse((runner.out/'raw-network.pcap').exists())
            self.assertFalse((runner.out/'raw-network-capture.json').exists())

    def test_hash_mismatch_never_publishes_or_removes_remote_evidence(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            runner = self.make_runner(directory)
            runner._pcap_state = Mock(side_effect=['owned', 'owned', 'stopped'])
            def adb(*args):
                if 'sha256sum' in args: return '0'*64 + '  fixture'
                if args[0] == 'pull': Path(args[-1]).write_bytes(bytes.fromhex('d4c3b2a1')+bytes(20))
                return ''
            runner.adb = Mock(side_effect=adb)
            with self.assertRaisesRegex(CaptureError, 'checksum mismatch'):
                runner.stop_pcap()
            self.assertFalse((runner.out/'raw-network.pcap').exists())
            self.assertTrue(runner.pcap_started)
            self.assertFalse(any('rm' in call.args for call in runner.adb.call_args_list))

    def test_preexited_producer_fails_execute_but_still_restores_owned_proxy(self):
        import json, tempfile, types
        with tempfile.TemporaryDirectory() as directory:
            runner = self.make_runner(directory)
            runner.out = Path(directory)/'capture'
            runner.capture = runner.out/'safe-facebook-http-capture.jsonl'
            runner.preflight, runner.start_transport, runner.start_pcap = Mock(), Mock(), Mock()
            runner.run_collection = Mock(return_value='terminal')
            runner.analyze, runner.validate_result = Mock(return_value=0), Mock()
            runner._pcap_state = Mock(return_value='stopped')
            runner.proxy_dirty, runner.old_proxy = True, ':0'
            runner.adb = Mock(side_effect=['127.0.0.1:18081', '', ':0'])
            fake_fcntl = types.SimpleNamespace(LOCK_EX=1, LOCK_NB=2, flock=Mock())
            with patch.dict('sys.modules', {'fcntl': fake_fcntl}), patch('facebook_android_capture.signal.signal', return_value=None):
                self.assertEqual(runner.execute(), 2)
            runner.adb.assert_any_call('shell', 'settings', 'put', 'global', 'http_proxy', ':0')
            status = json.loads((runner.out/'android-capture-status.json').read_text())
            self.assertFalse(status['completion_claim_allowed'])
            self.assertTrue(status['partial_evidence_retained'])
            self.assertEqual(status['cleanup_errors'], ['finalize owned Android PCAP'])
            self.assertFalse(any(c.args[0] == 'pull' or 'kill' in c.args or 'rm' in c.args for c in runner.adb.call_args_list))

    def test_pcap_stop_timeout_is_bounded_and_never_pulls_or_kills_unrelated_pid(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            runner = self.make_runner(directory)
            runner._pcap_state = Mock(return_value='owned')
            runner.adb = Mock(return_value='')
            with self.assertRaisesRegex(CaptureError, 'stop unverified'):
                runner.stop_pcap()
            self.assertEqual(runner.rt.sleep.call_count, 15)
            self.assertEqual(len(runner.adb.call_args_list), 2)
            self.assertTrue(all(c.args[:4] == ('shell', 'su', '0', 'kill') for c in runner.adb.call_args_list))
            runner._pcap_state = Mock(side_effect=CaptureError('identity changed'))
            runner.adb.reset_mock()
            with self.assertRaises(CaptureError): runner.stop_pcap()
            runner.adb.assert_not_called()

    def test_requested_pcap_missing_prevents_success(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            runner = self.make_runner(directory)
            runner.pcap_started = False
            with self.assertRaisesRegex(CaptureError, 'PCAP was not finalized'):
                runner.validate_result()

    def test_pcap_timeout_retains_cleanup_block_and_still_restores_proxy(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            runner = self.make_runner(directory)
            runner.stop_pcap = Mock(side_effect=CaptureError('uncertain remote process'))
            runner.proxy_dirty, runner.old_proxy = True, ':0'
            runner.adb = Mock(side_effect=['127.0.0.1:18081', '', ':0'])
            runner.cleanup()
            self.assertEqual(runner.cleanup_errors, ['finalize owned Android PCAP'])
            runner.adb.assert_any_call('shell', 'settings', 'put', 'global', 'http_proxy', ':0')

    def test_start_records_ownership_before_remote_timeout(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            runner = self.make_runner(directory)
            runner.pcap_started, runner.pcap_pid = False, None
            runner.adb = Mock(side_effect=CaptureError('timeout after launch'))
            with self.assertRaises(CaptureError): runner.start_pcap()
            self.assertTrue(runner.pcap_started)
            self.assertIn(runner.run_id, runner.pcap_remote)
            self.assertIn('/system/bin/tcpdump', runner.adb.call_args.args[-1])


if __name__ == '__main__':
    unittest.main()
