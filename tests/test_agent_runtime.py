import base64
import contextlib
import http.server
import json
import os
import pathlib
import shutil
import subprocess
import threading
import unittest

PHP = os.environ.get('LIBRESPEED_TEST_PHP') or shutil.which('php')
ROOT = pathlib.Path(__file__).resolve().parents[1]


@contextlib.contextmanager
def backend_fixture(code=200, content=b'', redirect=None):
    received = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def respond(self):
            size = int(self.headers.get('Content-Length', '0'))
            uploaded = self.rfile.read(size) if size else b''
            received.append({'token': self.headers.get('X-LibreSpeed-Token'), 'upload': uploaded})
            self.send_response(code)
            if redirect:
                self.send_header('Location', redirect)
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        do_GET = respond
        do_POST = respond

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', received
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@unittest.skipUnless(PHP, '需要 PHP CLI 和 curl；GitHub Linux 作业必须执行本组测试')
class AgentRuntimeTests(unittest.TestCase):
    def php(self, expression, arguments=None):
        encoded = base64.b64encode(json.dumps(arguments or {}).encode()).decode()
        source = (
            "require 'new/project/vps-agent/common.php';"
            f"$args=json_decode(base64_decode('{encoded}'),true);"
            'echo json_encode(' + expression + ');'
        )
        result = subprocess.run([PHP, '-r', source], cwd=ROOT, capture_output=True,
                                text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_download_rejects_http_200_error_document(self):
        with backend_fixture(content=b'<html>wrong page</html>') as (url, _):
            result = self.php('measure_download($args["url"],1,5)', {'url': url})
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['expectedBytes'], 1048576)

    def test_download_accepts_exact_requested_bytes(self):
        with backend_fixture(content=b'x' * 1048576) as (url, _):
            result = self.php('measure_download($args["url"],1,5)', {'url': url})
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['bytes'], 1048576)

    def test_latency_rejects_http_200_error_document(self):
        with backend_fixture(content=b'wrong page') as (url, _):
            result = self.php('measure_latency($args["url"],3,1)', {'url': url})
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['lossPct'], 100)

    def test_upload_sends_full_nonzero_payload(self):
        with backend_fixture() as (url, received):
            result = self.php('measure_upload($args["url"],1,5)', {'url': url})
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(len(received[0]['upload']), 1048576)
        self.assertNotEqual(received[0]['upload'], b'0' * 1048576)

    def test_upload_rejects_http_200_error_document(self):
        with backend_fixture(content=b'<html>wrong page</html>') as (url, _):
            result = self.php('measure_upload($args["url"],1,5)', {'url': url})
        self.assertEqual(result['status'], 'error')

    def test_reverse_response_redacts_json_escaped_token(self):
        secret = 'test-"quoted-token'
        body = json.dumps({'status': 'ok', 'echo': secret, secret: 'key'}).encode()
        with backend_fixture(content=body) as (url, _):
            result = self.php('post_json($args["url"],[],$args["token"],5)', {'url': url, 'token': secret})
        self.assertEqual(result['status'], 'ok')
        self.assertNotIn(secret, str(result))

    def test_reverse_request_does_not_follow_redirect(self):
        with backend_fixture(content=b'{"status":"ok"}') as (destination, received):
            with backend_fixture(code=302, redirect=destination) as (source, _):
                result = self.php('post_json($args["url"],[],"test-token",5)', {'url': source})
        self.assertEqual(result['status'], 'error')
        self.assertEqual(received, [])

    def test_reverse_http_error_is_not_hidden_by_success_json(self):
        with backend_fixture(code=500, content=b'{"status":"ok"}') as (url, _):
            result = self.php('post_json($args["url"],[],"test-token",5)', {'url': url})
        self.assertEqual(result['status'], 'error')

    def test_fractional_parameter_is_rejected(self):
        result = self.php('bounded_int(["pingCount"=>3.5],"pingCount",10,3,60)')
        self.assertEqual(result['error'], 'invalid_parameter')

    def test_non_http_protocol_is_rejected(self):
        result = self.php('normalize_base_url("ftp://example.test")')
        self.assertEqual(result['error'], 'unsupported_scheme')

    def test_get_cannot_start_measurement(self):
        result = self.php('require_post()')
        self.assertEqual(result['error'], 'method_not_allowed')
