import contextlib
import http.server
import importlib
import io
import json
import pathlib
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'new'))
deploy = importlib.import_module('deploy_vps_pair')
wizard = importlib.import_module('interactive_vps_pair')


class ReviewRegressions(unittest.TestCase):
    def test_eof_never_confirms_or_advances_a_step(self):
        calls = [lambda: wizard.prompt_yes_no('开始'), lambda: wizard.prompt_text('地址', 'saved.example'),
                 lambda: wizard.prompt_choice('动作', {'2': '测速'}, '2'), lambda: wizard.pause_step('下一步')]
        for call in calls:
            with self.subTest(call=call), mock.patch('builtins.input', side_effect=EOFError):
                with self.assertRaises(EOFError):
                    call()

    def test_token_input_does_not_write_before_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            token_file = pathlib.Path(directory) / '.vps_token'
            token_file.write_text('existing-token', encoding='utf-8')
            with mock.patch.object(wizard, 'TOKEN_FILE', token_file), \
                 mock.patch.object(wizard, 'prompt_yes_no', return_value=False), \
                 mock.patch.object(wizard.getpass, 'getpass', return_value='replacement-token'), \
                 mock.patch.object(deploy, 'save_token') as save:
                self.assertEqual(wizard.load_or_prompt_token(True), 'replacement-token')
                save.assert_not_called()
                self.assertEqual(token_file.read_text(), 'existing-token')

    def test_post_timeout_does_not_launch_a_second_speedtest(self):
        with mock.patch.object(wizard, 'http_request_once', return_value={'ok': False, 'statusCode': None}), \
             mock.patch.object(wizard.time, 'sleep'):
            result = wizard.http_request('http://test.example/pair.php', method='POST', payload={})
            self.assertEqual(result['attempt'], 1)
            wizard.http_request_once.assert_called_once()

    def test_authentication_error_is_not_retried(self):
        with mock.patch.object(wizard, 'http_request_once', return_value={'ok': False, 'statusCode': 401}), \
             mock.patch.object(wizard.time, 'sleep'):
            self.assertEqual(wizard.http_request('http://test.example/health.php')['attempt'], 1)

    def test_html_success_page_is_not_a_healthy_agent(self):
        node = wizard.NodeConfig('fixed', 'example.test', 8080, 22, 'root')
        with mock.patch.object(wizard, 'http_request', return_value={'ok': True, 'statusCode': 200, 'json': None}):
            self.assertFalse(wizard.run_health(node, 'test-token')['ok'])

    def test_null_result_is_a_failure_not_a_crash(self):
        report = {'status': 'ok', 'forward': {'result': None}, 'reverse': {'result': []}}
        self.assertFalse(deploy.pair_report_ok(report))
        self.assertFalse(wizard.report_summary({'ok': True, 'json': report})['ok'])

    def test_response_body_cannot_leak_current_token_into_reports(self):
        secret = 'test-response-secret'
        with http_fixture(lambda h: (200, {}, json.dumps({'echo': secret}).encode())) as url:
            response = wizard.http_request_once(url, token=secret)
        self.assertNotIn(secret, json.dumps(response))

    def test_redirect_does_not_forward_token_to_another_server(self):
        received = []
        def capture(handler):
            received.append(handler.headers.get('X-LibreSpeed-Token'))
            return 200, {}, b'{}'
        with http_fixture(capture) as destination:
            with http_fixture(lambda h: (302, {'Location': destination}, b'')) as source:
                response = wizard.http_request_once(source, token='private-test-token')
        self.assertFalse(response['ok'])
        self.assertEqual(received, [])

    def test_saved_nodes_roundtrip_contains_no_credentials(self):
        fixed = wizard.NodeConfig('fixed', '192.0.2.10', 8080, 22, 'root')
        target = wizard.NodeConfig('target', '198.51.100.20', 8081, 2222, 'root', 'https')
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(wizard, 'CONFIG_FILE', pathlib.Path(directory) / 'nodes.json'):
            wizard.save_nodes(fixed, target)
            data = wizard.load_saved_nodes()
            self.assertEqual(data['target']['ssh_port'], 2222)
            self.assertEqual(data['target']['scheme'], 'https')
            text = wizard.CONFIG_FILE.read_text(encoding='utf-8')
            self.assertNotIn('password', text)
            self.assertNotIn('token', text)

    def test_corrupt_saved_config_is_not_silently_used(self):
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(wizard, 'CONFIG_FILE', pathlib.Path(directory) / 'nodes.json'):
            wizard.CONFIG_FILE.write_text('{"version":1,"fixed":null}', encoding='utf-8')
            with self.assertRaises(ValueError):
                wizard.load_saved_nodes()

    def test_token_rotation_preserves_previous_working_token(self):
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(deploy, 'TOKEN_FILE', pathlib.Path(directory) / '.vps_token'):
            deploy.save_token('old-valid-token')
            deploy.save_token('new-valid-token')
            self.assertEqual(deploy.TOKEN_FILE.read_text().strip(), 'new-valid-token')
            self.assertEqual(deploy.TOKEN_FILE.with_name('.vps_token.previous').read_text().strip(), 'old-valid-token')
            self.assertEqual(len(list(pathlib.Path(directory).iterdir())), 2)

    def test_missing_build_input_does_not_leave_temporary_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            with mock.patch.object(deploy, 'PROJECT_DIR', root), mock.patch.object(deploy.tempfile, 'tempdir', directory):
                with self.assertRaises(FileNotFoundError):
                    deploy.make_archive()
            self.assertEqual(list(root.iterdir()), [])

    def test_host_validation_and_ipv6_url(self):
        self.assertEqual(deploy.node_url('2001:db8::1', 8080), 'http://[2001:db8::1]:8080')
        for value in ('http://example.test', 'example.test:80', 'user@example.test', 'bad\nname', '-bad.example'):
            with self.subTest(value=value), self.assertRaises(deploy.argparse.ArgumentTypeError):
                deploy.validate_host(value)

    def test_default_budget_counts_both_directions(self):
        self.assertIn('192 MiB', wizard.traffic_budget(64, 32))
        self.assertIn('96 MiB', wizard.traffic_budget(64, 32))

    def test_secondary_cleanup_failure_does_not_mask_successful_deployment(self):
        remote = mock.MagicMock()
        remote.__enter__.return_value = remote
        remote.run.side_effect = [None, None, RuntimeError('cleanup transport failed')]
        with mock.patch.object(deploy, 'Remote', return_value=remote), contextlib.redirect_stdout(io.StringIO()):
            deploy.deploy_node('fixed', '192.0.2.10', 22, 'root', 'test-password', 8080,
                               'http://192.0.2.10:8080', 'http://198.51.100.20:8080', 'test-token', 'test.tar.gz')
        self.assertEqual(remote.run.call_count, 3)

    def test_first_deployment_cancel_writes_nothing(self):
        with isolated_wizard() as root, \
             mock.patch.object(wizard, 'prompt_choice', side_effect=['3', 'http', 'http']), \
             mock.patch.object(wizard, 'prompt_host', side_effect=['192.0.2.10', '198.51.100.20']), \
             mock.patch.object(wizard, 'prompt_int', side_effect=lambda label, default, *args: default), \
             mock.patch.object(wizard, 'prompt_text', return_value='root'), \
             mock.patch.object(wizard, 'prompt_yes_no', side_effect=[True, True, False]), \
             mock.patch.object(wizard.getpass, 'getpass', side_effect=['', 'test-password']), \
             mock.patch.object(deploy, 'load_paramiko'), \
             mock.patch.object(deploy, 'Remote') as remote, \
             mock.patch.object(wizard, 'http_request') as http:
            self.assertEqual(wizard.main(), 1)
            remote.assert_not_called()
            http.assert_not_called()
            self.assertEqual(list(root.iterdir()), [])

    def run_existing_nodes(self, action, healthy):
        with isolated_wizard() as root:
            wizard.TOKEN_FILE.write_text('test-existing-token', encoding='utf-8')
            fixed = wizard.NodeConfig('fixed', '192.0.2.10', 8080, 22, 'root')
            target = wizard.NodeConfig('target', '198.51.100.20', 8080, 22, 'root')
            wizard.save_nodes(fixed, target)
            health = {'ok': healthy, 'publicUrl': fixed.url, 'checks': {
                'agentHealth': {'ok': healthy, 'statusCode': 200 if healthy else 401,
                                'json': {'agentVersion': '1.3.0', 'node': {}}}}}
            with mock.patch.object(wizard, 'prompt_choice', side_effect=[action, 'http', 'http']), \
                 mock.patch.object(wizard, 'prompt_text', side_effect=lambda label, default, *args: default), \
                 mock.patch.object(wizard, 'prompt_yes_no', return_value=True), \
                 mock.patch.object(wizard, 'run_health', return_value=health), \
                 mock.patch.object(wizard, 'run_pair_report') as pair, \
                 mock.patch.object(wizard.getpass, 'getpass') as password, \
                 mock.patch.object(deploy, 'Remote') as remote:
                exit_code = wizard.main()
                password.assert_not_called()
                remote.assert_not_called()
                pair.assert_not_called()
            data = json.loads(next(wizard.REPORT_DIR.glob('*.json')).read_text(encoding='utf-8'))
            markdown = next(wizard.REPORT_DIR.glob('*.md')).read_text(encoding='utf-8')
            self.assertNotIn('test-existing-token', markdown)
            self.assertIsNone(data['outcome']['pairOk'])
            return exit_code, markdown

    def test_daily_health_uses_saved_config_without_ssh(self):
        code, markdown = self.run_existing_nodes('1', True)
        self.assertEqual(code, 0)
        self.assertIn('未执行', markdown)

    def test_failed_health_prevents_speedtest_and_records_skipped_state(self):
        code, markdown = self.run_existing_nodes('2', False)
        self.assertEqual(code, 3)
        self.assertIn('未启动', markdown)
        self.assertIn('健康检查未通过', markdown)


@contextlib.contextmanager
def isolated_wizard():
    with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
        root = pathlib.Path(directory)
        for module, name, path in ((wizard, 'TOKEN_FILE', '.vps_token'), (deploy, 'TOKEN_FILE', '.vps_token'),
                                   (wizard, 'CONFIG_FILE', '.vps_pair.json'), (wizard, 'REPORT_DIR', 'reports')):
            stack.enter_context(mock.patch.object(module, name, root / path))
        stack.enter_context(mock.patch.object(wizard, 'pause_step'))
        stack.enter_context(mock.patch.object(wizard, 'configure_console'))
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        yield root


@contextlib.contextmanager
def http_fixture(respond):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            code, headers, body = respond(self)
            self.send_response(code)
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
