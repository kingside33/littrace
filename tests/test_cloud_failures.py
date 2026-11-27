"""云端失败诊断与停留状态，全部使用模拟请求。"""

import io
import json
import os
from pathlib import Path
import socket
import socketserver
import ssl
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kbapp.storage.store import Store
from kbapp.integrations.cloud import complete, failure_details, CloudOutputLimitError
from kbapp.workflows.dashboard import dashboard
from kbapp.workflows.agents import create_run

# 每个用例都要在 setUp 里通过 save_settings 写入 api_key,而这会调用 DPAPI 密钥保护。
# 该保护只在 Windows 提供(见 kbapp/core/secrets.py),非 Windows 上是设计边界而非缺陷,
# 因此整个类按平台如实跳过,而不是让整套件在 Linux 上红灯。
DPAPI_REQUIRED = pytest.mark.skipif(
    os.name != 'nt',
    reason='DPAPI 密钥保护仅 Windows 提供,见 kbapp/core/secrets.py',
)


class _RecordingTarget(BaseHTTPRequestHandler):
    """记录收到的请求；若被跟随，则像一个正常模型端点那样返回可解析 JSON。"""

    def _handle(self):
        self.server.records.append(
            {
                'method': self.command,
                'path': self.path,
                'authorization': self.headers.get('Authorization'),
            }
        )
        body = json.dumps(
            {
                'choices': [{'message': {'content': '{"ok":true}'}}],
                'usage': {'prompt_tokens': 2, 'completion_tokens': 3},
            }
        ).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_POST = _handle
    do_GET = _handle

    def log_message(self, *args):
        pass


class _Redirector(_RecordingTarget):
    """POST 回 301/302；`/final` 是同源跳转的终点，返回正常响应。"""

    def do_POST(self):
        # 读完请求正文再回响应：否则 Windows 会在客户端读到响应前报
        # ConnectionAborted（服务器关闭时正文仍在途），把守卫结论盖掉。
        length = int(self.headers.get('Content-Length') or 0)
        if length:
            self.rfile.read(length)
        if self.path == '/final':
            self._handle()
            return
        self.server.records.append(
            {
                'method': self.command,
                'path': self.path,
                'authorization': self.headers.get('Authorization'),
            }
        )
        self.send_response(self.server.code)
        self.send_header('Location', self.server.location)
        self.end_headers()


class _RecordingTCP(socketserver.BaseRequestHandler):
    def handle(self):
        self.server.connections.append(self.request.recv(4096))


@DPAPI_REQUIRED
class CloudFailures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.key = 'synthetic-private-test-key'
        self.store.save_settings(
            {
                'api_base': 'https://example.org/v1',
                'model': 'test',
                'api_key': self.key,
                'budget_mode': 'unlimited',
                'cloud_enabled': True,
            }
        )

    def test_connection_causes_are_recorded_without_retry_or_releasing_cost(self):
        for reason, code in [
            (socket.gaierror(socket.EAI_AGAIN, 'temporary resolver failure'), 'dns'),
            (ssl.SSLCertVerificationError(1, 'certificate failed'), 'tls_certificate'),
            (TimeoutError('timed out'), 'timeout'),
            (ConnectionResetError(10054, 'reset'), 'connection'),
        ]:
            with (
                self.subTest(code=code),
                patch('kbapp.integrations.cloud.build_opener') as factory,
            ):
                factory.return_value.open.side_effect = URLError(reason)
                with self.assertRaisesRegex(ValueError, '费用待确认'):
                    complete(self.store, 'PRIVATE_PROMPT', 'test')
                factory.return_value.open.assert_called_once()
                state = self.store.state('last_model_error')
                self.assertEqual(state['code'], code)
                self.assertEqual(state['reason_type'], type(reason).__name__)
                self.assertFalse(state['usage_recorded'])
                self.assertNotIn('PRIVATE_PROMPT', json.dumps(state))
        with self.store.db() as db:
            rows = db.execute('SELECT actual,status FROM usage').fetchall()
        self.assertEqual(len(rows), 4)
        self.assertTrue(
            all(r['actual'] is None and r['status'] == 'uncertain_or_failed' for r in rows)
        )

    def test_diagnostics_redact_keys_urls_and_authentication(self):
        reason = (
            'connect https://user:pass@example.org/?key='
            + self.key
            + ' Authorization: Bearer ABC123 api_key=OTHERKEY'
        )
        info = failure_details(URLError(reason), self.key)
        value = json.dumps(info)
        for private in [self.key, 'user:pass', 'ABC123', 'OTHERKEY']:
            self.assertNotIn(private, value)
        self.assertLessEqual(len(info['reason']), 240)

    def test_http_failure_records_status_without_response_body(self):
        exc = HTTPError(
            'https://example.org/v1', 401, 'Unauthorized', {}, io.BytesIO(b'PRIVATE_RESPONSE')
        )
        with patch('kbapp.integrations.cloud.build_opener') as factory:
            factory.return_value.open.side_effect = exc
            with self.assertRaisesRegex(ValueError, 'HTTP 401'):
                complete(self.store, 'PRIVATE_PROMPT', 'test')
        factory.return_value.open.assert_called_once()
        state = self.store.state('last_model_error')
        self.assertEqual(state['http_status'], 401)
        self.assertNotIn('PRIVATE_RESPONSE', json.dumps(state))

    def test_definite_length_limit_keeps_usage_and_exception_type(self):
        raw = {
            'choices': [{'message': {'content': None}, 'finish_reason': 'length'}],
            'usage': {'prompt_tokens': 2, 'completion_tokens': 3},
        }
        with patch('kbapp.integrations.cloud.build_opener') as factory:
            factory.return_value.open.return_value = io.BytesIO(json.dumps(raw).encode())
            with self.assertRaisesRegex(CloudOutputLimitError, '用量已记录'):
                complete(self.store, 'prompt', 'test')
        factory.return_value.open.assert_called_once()
        self.assertTrue(self.store.state('last_model_error')['usage_recorded'])
        self.assertTrue(self.store.state('last_model_error')['response_saved'])
        responses = list((self.store.paths.cache / 'model-responses').glob('*.json'))
        self.assertEqual(len(responses), 1)
        saved = json.loads(responses[0].read_text(encoding='utf-8'))
        self.assertEqual(saved['response'], raw)
        self.assertNotIn(self.key, json.dumps(saved))

    def test_dashboard_distinguishes_connection_halt_from_other_failures(self):
        run = create_run(self.store, {'kind': 'lineage'}, start=False)
        with self.store.db() as db:
            db.execute(
                "UPDATE agent_runs SET status='failed',stop=1,error=?,result=? WHERE id=?",
                (
                    '云端调用未完成（URLError），已记录用量',
                    json.dumps({'halt_error': True}),
                    run['id'],
                ),
            )
        item = dashboard(self.store, run['id'])['selected']
        self.assertEqual(item['status'], 'failed')
        self.assertEqual(item['attention']['kind'], 'cloud_connection')
        self.assertEqual(item['actions'], ['resume'])
        self.assertIn('可能重新请求', item['attention']['hint'])
        with self.store.db() as db:
            db.execute(
                'UPDATE agent_runs SET error=? WHERE id=?',
                ('云端调用未完成（HTTP 429）', run['id']),
            )
        item = dashboard(self.store, run['id'])['selected']
        self.assertEqual(item['attention']['kind'], 'cloud_rate_limit')
        self.assertEqual(item['status'], 'failed')
        with self.store.db() as db:
            db.execute(
                'UPDATE agent_runs SET error=? WHERE id=?',
                ('云端调用已返回，但核验结构不完整', run['id']),
            )
        self.assertEqual(
            dashboard(self.store, run['id'])['selected']['attention']['kind'], 'cloud_output'
        )
        with self.store.db() as db:
            db.execute(
                'UPDATE agent_runs SET error=? WHERE id=?', ('分类依据不能定位到题摘', run['id'])
            )
        self.assertIsNone(dashboard(self.store, run['id'])['selected']['attention'])

    def test_proxy_validation_and_clear(self):
        for value in ['http://127.0.0.1:7897', 'http://localhost:7897', 'http://[::1]:7897', '']:
            self.assertEqual(self.store.save_settings({'cloud_proxy': value})['cloud_proxy'], value)
        for value in [
            'socks5://127.0.0.1:7897',
            'http://example.org:7897',
            'http://user:pass@127.0.0.1:7897',
            'http://127.0.0.1',
            'http://127.0.0.1:7897/path',
            'http://127.0.0.1:7897?key=secret',
            'http://127.0.0.1:65536',
        ]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.store.save_settings({'cloud_proxy': value})
        self.assertEqual(self.store.settings()['cloud_proxy'], '')

    def test_explicit_proxy_sends_same_request_once_through_configured_ingress(self):
        self.store.save_settings({'cloud_proxy': 'http://127.0.0.1:7897'})
        raw = {
            'choices': [{'message': {'content': '{"ok":true}'}}],
            'usage': {'prompt_tokens': 2, 'completion_tokens': 3},
        }
        with patch('kbapp.integrations.cloud.build_opener') as factory:
            factory.return_value.open.return_value = io.BytesIO(json.dumps(raw).encode())
            self.assertTrue(complete(self.store, 'prompt', 'test')['ok'])
        factory.assert_called_once()
        factory.return_value.open.assert_called_once()
        self.assertEqual(
            factory.call_args.args[0].proxies,
            {'https': 'http://127.0.0.1:7897', 'http': 'http://127.0.0.1:7897'},
        )
        req = factory.return_value.open.call_args.args[0]
        self.assertEqual(req.full_url, 'https://example.org/v1/chat/completions')
        self.assertEqual(req.get_header('Authorization'), 'Bearer ' + self.key)
        self.assertEqual(json.loads(req.data)['messages'][-1]['content'], 'prompt')

    def test_proxy_connect_failure_falls_back_to_fresh_system_request_once(self):
        self.store.save_settings({'cloud_proxy': 'http://127.0.0.1:7897'})
        raw = {
            'choices': [{'message': {'content': '{"ok":true}'}}],
            'usage': {'prompt_tokens': 2, 'completion_tokens': 3},
        }

        def refused(request, **kwargs):
            request.set_proxy('127.0.0.1:7897', 'http')
            raise URLError(ConnectionRefusedError('proxy unavailable'))

        calls = {'n': 0}

        def refuse_then_answer(request, **kwargs):
            if calls['n'] == 0:
                calls['n'] += 1
                return refused(request, **kwargs)
            return io.BytesIO(json.dumps(raw).encode())

        with patch('kbapp.integrations.cloud.build_opener') as factory:
            factory.return_value.open.side_effect = refuse_then_answer
            self.assertTrue(complete(self.store, 'prompt', 'test')['ok'])
        self.assertEqual(factory.call_count, 2, 'proxy leg, then one fallback leg')
        self.assertEqual(factory.return_value.open.call_count, 2)
        req = factory.return_value.open.call_args_list[1].args[0]
        self.assertEqual(req.host, 'example.org')
        self.assertIsNone(getattr(req, '_tunnel_host', None))
        self.assertEqual(req.get_header('Authorization'), 'Bearer ' + self.key)
        self.assertEqual(json.loads(req.data)['messages'][-1]['content'], 'prompt')
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM usage').fetchone()[0], 1)

    def test_proxy_failure_after_request_start_cannot_fall_back(self):
        from unittest.mock import Mock
        from kbapp.integrations.cloud import ProxyHTTPHandler, ProxyHTTPSConnection

        attempt = {'sent': False}
        connection = ProxyHTTPSConnection('example.org', attempt=attempt)
        connection.sock = Mock()
        connection.sock.sendall.side_effect = TimeoutError('partial write')
        with self.assertRaises(TimeoutError):
            connection.send(b'POST /v1/chat/completions HTTP/1.1')
        self.assertTrue(attempt['sent'])
        connection.close()
        self.store.save_settings({'cloud_proxy': 'http://127.0.0.1:7897'})
        with patch('kbapp.integrations.cloud.build_opener') as factory:

            def failed(*args, **kwargs):
                handler = next(
                    h for h in factory.call_args.args if isinstance(h, ProxyHTTPHandler)
                )
                handler.attempt.update(attempt)
                raise URLError(TimeoutError('response timed out'))

            factory.return_value.open.side_effect = failed
            with self.assertRaisesRegex(ValueError, '费用待确认'):
                complete(self.store, 'prompt', 'test')
        factory.return_value.open.assert_called_once()
        self.assertEqual(self.store.state('last_model_error')['network_route'], 'local_proxy')

    def test_rejected_connect_tunnel_falls_back_before_any_model_bytes(self):
        import threading
        from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

        records = []

        class RejectProxy(BaseHTTPRequestHandler):
            def do_CONNECT(self):
                records.append(
                    {'method': self.command, 'authorization': self.headers.get('Authorization')}
                )
                self.send_response(502)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), RejectProxy)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.store.save_settings({'cloud_proxy': 'http://127.0.0.1:' + str(server.server_port)})
        raw = {
            'choices': [{'message': {'content': '{"ok":true}'}}],
            'usage': {'prompt_tokens': 2, 'completion_tokens': 3},
        }
        from urllib.request import build_opener as real_build_opener
        from kbapp.integrations.cloud import SameHostRedirectHandler

        fallback_requests = []

        class FakeSystemOpener:
            def open(self, request, **kwargs):
                fallback_requests.append(request)
                return io.BytesIO(json.dumps(raw).encode())

        def opener_factory(*handlers):
            # The fallback opener is the one installed with the guard only; the
            # proxy leg must keep its real handlers so the CONNECT is observable.
            if len(handlers) == 1 and isinstance(handlers[0], SameHostRedirectHandler):
                return FakeSystemOpener()
            return real_build_opener(*handlers)

        try:
            with patch('kbapp.integrations.cloud.build_opener', side_effect=opener_factory):
                self.assertTrue(complete(self.store, 'prompt', 'test')['ok'])
            self.assertEqual(len(fallback_requests), 1, 'system fallback runs exactly once')
            self.assertEqual(records, [{'method': 'CONNECT', 'authorization': None}])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)

    def test_explicit_proxy_cannot_be_bypassed_by_system_no_proxy(self):
        from urllib.request import Request
        from kbapp.integrations.cloud import ExplicitProxyHandler

        request = Request('https://example.org/v1/chat/completions', data=b'original-body')
        with patch('urllib.request.proxy_bypass', return_value=True) as bypass:
            ExplicitProxyHandler({'https': 'http://127.0.0.1:7897'}).proxy_open(
                request, 'http://127.0.0.1:7897', 'https'
            )
        bypass.assert_not_called()
        self.assertEqual(request.host, '127.0.0.1:7897')
        self.assertEqual(request._tunnel_host, 'example.org')
        self.assertEqual(request.type, 'https')
        self.assertEqual(request.data, b'original-body')

    def test_network_route_change_reuses_existing_review_cache(self):
        from kbapp.research.review import review_case

        calls = []

        def reviewer(store, prompt, *args, **kwargs):
            calls.append(1)
            claim = json.loads(prompt.split('\n', 1)[1])['claims'][0]
            return {
                'reviews': [
                    {
                        'id': claim['id'],
                        'supported': True,
                        'decision': 'supported',
                        'unsupported_details': [],
                        'reason': 'source supports claim',
                    }
                ]
            }

        source = {
            's': {
                'id': 's',
                'paper_id': 'p',
                'kind': 'paper_text',
                'text': 'Original evidence.',
                'page_start': 1,
            }
        }
        claim = {
            'text': 'Original evidence.',
            'evidence': [{'source_id': 's', 'quote': 'Original evidence.'}],
        }
        with patch('kbapp.integrations.model_identity.signature', return_value='fixed-weights'):
            first = review_case(self.store, claim, source, reviewer, reviewer)
            self.store.save_settings({'cloud_proxy': 'http://127.0.0.1:7897'})
            second = review_case(self.store, claim, source, reviewer, reviewer)
        self.assertEqual(first['status'], 'verified')
        self.assertEqual(second['status'], 'verified')
        self.assertEqual(len(calls), 2)

    @staticmethod
    def _serve(handler, **attributes):
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        server.records = []
        for name, value in attributes.items():
            setattr(server, name, value)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread

    @staticmethod
    def _stop(server, thread):
        server.shutdown()
        server.server_close()
        thread.join(2)

    def _refuse_cross_origin(self, code, kind):
        redirector, redirector_thread = self._serve(_Redirector, code=code, location='')
        target = target_thread = tcp = tcp_thread = None
        try:
            if kind == 'scheme':
                tcp = socketserver.TCPServer(('127.0.0.1', 0), _RecordingTCP)
                tcp.connections = []
                tcp_thread = threading.Thread(target=tcp.serve_forever, daemon=True)
                tcp_thread.start()
                redirector.location = 'https://127.0.0.1:' + str(tcp.server_address[1]) + '/'
            else:
                target, target_thread = self._serve(_RecordingTarget)
                host = 'localhost' if kind == 'host' else '127.0.0.1'
                redirector.location = 'http://' + host + ':' + str(target.server_port) + '/'
            self.store.save_settings(
                {'api_base': 'http://127.0.0.1:' + str(redirector.server_port) + '/v1'}
            )
            # 环境里的 HTTP(S)_PROXY 会把回环请求也带走；这条用例只验证守卫。
            with patch('urllib.request.getproxies', return_value={}):
                with self.assertRaisesRegex(ValueError, 'HTTP ' + str(code)):
                    complete(self.store, 'prompt', 'test')
            self.assertEqual(
                redirector.records,
                [
                    {
                        'method': 'POST',
                        'path': '/v1/chat/completions',
                        'authorization': 'Bearer ' + self.key,
                    }
                ],
                'the guarded request is sent once and never retried',
            )
            if kind == 'scheme':
                self.assertEqual(
                    tcp.connections, [], 'a cross-scheme target must not even be contacted'
                )
            else:
                self.assertEqual(
                    target.records, [], 'the redirect target must receive no request at all'
                )
            state = self.store.state('last_model_error')
            self.assertEqual(state['http_status'], code)
            self.assertNotIn(self.key, json.dumps(state))
        finally:
            self._stop(redirector, redirector_thread)
            if target is not None:
                self._stop(target, target_thread)
            if tcp is not None:
                tcp.shutdown()
                tcp.server_close()
                tcp_thread.join(2)

    def test_cross_origin_redirects_are_refused_for_301_and_302(self):
        for code in (301, 302):
            for kind in ('port', 'host', 'scheme'):
                with self.subTest(code=code, kind=kind):
                    self._refuse_cross_origin(code, kind)

    def test_same_origin_redirects_are_still_followed_for_301_and_302(self):
        for code in (301, 302):
            with self.subTest(code=code):
                server, thread = self._serve(_Redirector, code=code, location='/final')
                try:
                    self.store.save_settings(
                        {'api_base': 'http://127.0.0.1:' + str(server.server_port) + '/v1'}
                    )
                    with patch('urllib.request.getproxies', return_value={}):
                        self.assertTrue(complete(self.store, 'prompt', 'test')['ok'])
                    self.assertEqual(
                        [r['method'] for r in server.records], ['POST', 'GET']
                    )
                    self.assertEqual(
                        [r['path'] for r in server.records],
                        ['/v1/chat/completions', '/final'],
                    )
                    self.assertTrue(
                        all(
                            r['authorization'] == 'Bearer ' + self.key
                            for r in server.records
                        ),
                        'same-origin redirect keeps the Authorization header',
                    )
                finally:
                    self._stop(server, thread)
