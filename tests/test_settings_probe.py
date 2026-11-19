"""S7：设置页「测试连接」的服务端探测端点 POST /api/settings/test。

探测是**零成本**的：只用 GET /models，不走 /chat/completions，不 reserve、
不 settle、不写 usage。因此这里的分支用例全部用模拟请求，不联网、不加载模型。

密钥保护（DPAPI）只在写入非空 api_key 时触发（kbapp/core/secrets.py 对空串提前
返回）。分支用例一律 patch 掉 protect，让它们在两条 ubuntu leg 上都跑——照抄
test_cloud_failures.py 的 DPAPI_REQUIRED 整体跳过会让 CI 对这个新端点零覆盖。
"""

import io
import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['KB_NO_STARTUP_JOBS'] = '1'
os.environ['KB_NO_REFERENCE_WORKER'] = '1'

from fastapi.testclient import TestClient  # noqa: E402

from kbapp.integrations.cloud import (  # noqa: E402
    PROBE_CODES,
    PROBE_TIMEOUT_S,
    PROBE_VERDICTS,
    probe_code,
    probe_models,
    probe_url,
)
from kbapp.storage.store import Store  # noqa: E402
from support import service_for  # noqa: E402

KEY = 'synthetic-private-test-key'


def identity_protect(value, decrypt=False):
    """DPAPI 的替身：原样返回，让 save_settings 在非 Windows 上也能跑。"""
    return value


class ProbeUrl(unittest.TestCase):
    """探测地址的拼接，与 _complete() 的 /chat/completions 形状保持一致。"""

    def test_appends_models_to_a_base(self):
        self.assertEqual(probe_url('https://api.example.org/v1'), 'https://api.example.org/v1/models')
        self.assertEqual(probe_url('https://api.example.org/v1/'), 'https://api.example.org/v1/models')
        self.assertEqual(probe_url('https://api.example.org'), 'https://api.example.org/models')

    def test_keeps_an_address_that_already_ends_with_models(self):
        self.assertEqual(
            probe_url('https://api.example.org/v1/models'), 'https://api.example.org/v1/models'
        )

    def test_strips_chat_completions_before_appending(self):
        # save_settings 允许填完整端点；朴素拼接会得到 /chat/completions/models。
        self.assertEqual(
            probe_url('https://api.example.org/v1/chat/completions'),
            'https://api.example.org/v1/models',
        )

    def test_drops_query_and_fragment(self):
        # 朴素拼接会把查询参数塞进路径中间，得到一个既不真实也会外泄参数的地址。
        self.assertEqual(
            probe_url('https://api.example.org/v1?key=leak#frag'),
            'https://api.example.org/v1/models',
        )

    def test_empty_or_relative_base_has_no_probe_url(self):
        for value in ['', '   ', 'example.org/v1', None]:
            self.assertEqual(probe_url(value), '', f'unexpected url for {value!r}')


class ProbeCodeTable(unittest.TestCase):
    """failure_details 的输出 → 探测词表，逐行断言 issue 里的映射表。"""

    def test_every_mapped_code_is_in_the_vocabulary(self):
        # 词表是封闭的：任何映射结果都必须落在 PROBE_CODES 里，否则插件会拿到
        # 一个没有 Fluent 文案的值。
        rows = [
            {'code': 'connection'},
            {'code': 'dns'},
            {'code': 'tls_certificate'},
            {'code': 'tls'},
            {'code': 'timeout'},
            {'code': 'response'},
            {'code': 'http', 'http_status': 401},
            {'code': 'http', 'http_status': 403},
            {'code': 'http', 'http_status': 302},
            {'code': 'http', 'http_status': 404},
            {'code': 'http', 'http_status': 405},
            {'code': 'http', 'http_status': 501},
            {'code': 'http', 'http_status': 500},
            {'code': 'http', 'http_status': 429},
            {},
        ]
        for row in rows:
            with self.subTest(row=row):
                self.assertIn(probe_code(row), PROBE_CODES)

    def test_the_mapping_table_row_by_row(self):
        self.assertEqual(probe_code({'code': 'connection'}), 'unreachable')
        self.assertEqual(probe_code({'code': 'dns'}), 'dns')
        # 证书失败不并入 tls：两种处置完全不同。
        self.assertEqual(probe_code({'code': 'tls_certificate'}), 'tls_certificate')
        self.assertEqual(probe_code({'code': 'tls'}), 'tls')
        self.assertEqual(probe_code({'code': 'timeout'}), 'timeout')
        for status in (401, 403):
            with self.subTest(status=status):
                self.assertEqual(probe_code({'code': 'http', 'http_status': status}), 'auth')
        for status in (302, 303, 307, 308):
            with self.subTest(status=status):
                self.assertEqual(
                    probe_code({'code': 'http', 'http_status': status}), 'redirect_refused'
                )
        for status in (404, 405, 501):
            with self.subTest(status=status):
                self.assertEqual(
                    probe_code({'code': 'http', 'http_status': status}), 'probe_unsupported'
                )
        for status in (400, 429, 500, 503):
            with self.subTest(status=status):
                self.assertEqual(probe_code({'code': 'http', 'http_status': status}), 'http')

    def test_response_code_is_not_in_the_vocabulary(self):
        # failure_details 会产出 'response'，但探测不解析正文，它不该成为对外词。
        self.assertNotIn('response', PROBE_CODES)
        self.assertIn(probe_code({'code': 'response'}), PROBE_CODES)


class ProbeModels(unittest.TestCase):
    """probe_models() 的结论与副作用。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        patcher = patch('kbapp.storage.store.protect', identity_protect)
        patcher.start()
        self.addCleanup(patcher.stop)

    def configure(self, **overrides):
        values = {
            'api_base': 'https://example.org/v1',
            'model': 'test-model',
            'api_key': KEY,
            'budget_mode': 'unlimited',
            'cloud_enabled': False,
        }
        values.update(overrides)
        self.store.save_settings(values)

    def test_unconfigured_base_reports_not_configured_without_a_request(self):
        with patch('kbapp.integrations.cloud.urlopen') as direct, patch(
            'kbapp.integrations.cloud.build_opener'
        ) as factory:
            result = probe_models(self.store)
        direct.assert_not_called()
        factory.assert_not_called()
        self.assertEqual(result['code'], 'not_configured')
        self.assertEqual(result['verdict'], 'failed')

    def test_probe_works_before_cloud_is_enabled(self):
        # 用户要先验证再启用；要求 cloud_enabled=true 会让这个端点失去意义。
        self.configure(cloud_enabled=False)
        body = io.BytesIO(json.dumps({'data': [{'id': 'test-model'}]}).encode())
        with patch('kbapp.integrations.cloud.build_opener') as factory:
            factory.return_value.open.return_value.__enter__.return_value.read.return_value = (
                body.read()
            )
            factory.return_value.open.return_value.__enter__.return_value.status = 200
            result = probe_models(self.store)
        self.assertEqual(result['code'], 'ok')
        self.assertEqual(result['verdict'], 'ok')
        self.assertEqual(result['model_listed'], True)

    def test_probe_writes_no_usage_and_no_last_model_error(self):
        self.configure(cloud_enabled=False)
        exc = HTTPError('https://example.org/v1/models', 401, 'Unauthorized', {}, io.BytesIO(b'x'))
        with patch('kbapp.integrations.cloud.build_opener') as factory:
            factory.return_value.open.side_effect = exc
            result = probe_models(self.store)
        self.assertEqual(result['code'], 'auth')
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM usage').fetchone()[0], 0)
        self.assertIsNone(self.store.state('last_model_error'))

    def test_bearer_header_is_sent_when_a_key_exists(self):
        self.configure()
        body = json.dumps({'data': [{'id': 'test-model'}]}).encode()
        with patch('kbapp.integrations.cloud.build_opener') as factory:
            handle = factory.return_value.open.return_value.__enter__.return_value
            handle.read.return_value = body
            handle.status = 200
            probe_models(self.store)
        request = factory.return_value.open.call_args.args[0]
        self.assertEqual(request.get_header('Authorization'), 'Bearer ' + KEY)
        self.assertEqual(request.get_method(), 'GET')
        self.assertEqual(request.full_url, 'https://example.org/v1/models')

    def test_no_bearer_header_when_the_key_is_empty(self):
        # 发出字面量 "Bearer " 会让部分网关回 401，把「本机无鉴权端点」误报成认证失败。
        self.configure(api_key='')
        body = json.dumps({'data': []}).encode()
        with patch('kbapp.integrations.cloud.build_opener') as factory:
            handle = factory.return_value.open.return_value.__enter__.return_value
            handle.read.return_value = body
            handle.status = 200
            probe_models(self.store)
        request = factory.return_value.open.call_args.args[0]
        self.assertIsNone(request.get_header('Authorization'))

    def test_probe_budget_is_the_module_constant(self):
        # 探测预算必须严格小于插件客户端超时，否则用户先看到「无法连接本地服务」。
        self.configure()
        body = json.dumps({'data': []}).encode()
        with patch('kbapp.integrations.cloud.build_opener') as factory:
            handle = factory.return_value.open.return_value.__enter__.return_value
            handle.read.return_value = body
            handle.status = 200
            probe_models(self.store)
        # 传下去的是**剩余**预算而非常量原值：整个请求共用一个 deadline，否则
        # 代理腿失败后回退腿再拿一份，最坏 2×5s 正好撞上客户端 10s 超时。
        budget = factory.return_value.open.call_args.kwargs['timeout']
        self.assertGreater(budget, 0)
        self.assertLessEqual(budget, PROBE_TIMEOUT_S)
        self.assertLess(PROBE_TIMEOUT_S * 1000, 10000)

    def test_the_probe_shares_one_deadline_across_both_legs(self):
        # 代理腿失败后回退系统网络时，回退腿拿到的是**剩余**预算：总耗时因此
        # 不超过 PROBE_TIMEOUT_S。每条腿各给一份会得到 2×5s=10s，与客户端超时
        # 相等，用户看到的仍是「无法连接本地服务」。
        self.configure(cloud_proxy='http://127.0.0.1:7897')
        with patch('kbapp.integrations.cloud.build_opener') as factory:
            # First opener (proxy leg) refuses; the fallback opener answers.
            factory.return_value.open.side_effect = [
                URLError(ConnectionRefusedError('no proxy')),
                self._ok_handle(b'{}'),
            ]
            probe_models(self.store)
        self.assertEqual(factory.call_count, 2, 'proxy leg, then the fallback leg')
        fallback_budget = factory.return_value.open.call_args_list[-1].kwargs['timeout']
        self.assertGreater(fallback_budget, 0)
        self.assertLessEqual(fallback_budget, PROBE_TIMEOUT_S)

    @staticmethod
    def _ok_handle(body):
        import io

        class _Response(io.BytesIO):
            status = 200

        class _Ctx:
            def __enter__(self):
                return _Response(body)

            def __exit__(self, *exc):
                return False

        return _Ctx()

    def test_guard_rejects_a_cross_origin_redirect_and_allows_the_same_origin(self):
        # 守卫本身必须有锚：删掉 SameHostRedirectHandler 后，映射表用例仍然全绿，
        # 于是这道安全修复可以在无人察觉的情况下消失。
        from urllib.request import Request

        from kbapp.integrations.cloud import SameHostRedirectHandler

        guard = SameHostRedirectHandler()

        def followed(origin, target):
            request = Request(origin)
            return guard.redirect_request(request, None, 302, 'Found', {}, target) is not None

        # Same origin, including spellings that are still one origin.
        self.assertTrue(followed('https://api.example.org/v1/models', 'https://api.example.org/x'))
        self.assertTrue(followed('https://API.example.org/v1', 'https://api.example.org/x'))
        self.assertTrue(followed('https://api.example.org/v1', 'https://api.example.org:443/x'))
        self.assertTrue(followed('http://127.0.0.1:8765/a', 'http://127.0.0.1:8765/b'))
        # A different host, scheme or port must not receive the bearer key.
        self.assertFalse(followed('https://api.example.org/v1', 'https://attacker.invalid/x'))
        self.assertFalse(followed('https://api.example.org/v1', 'http://api.example.org/x'))
        self.assertFalse(followed('https://api.example.org/v1', 'https://api.example.org:8443/x'))
        self.assertFalse(followed('https://api.example.org/v1', 'https://sub.api.example.org/x'))

    def test_the_guard_applies_the_budget_to_the_socket_it_connects(self):
        # 旧实现无条件 `sock.settimeout(150)`，会覆盖 `opener.open(timeout=5)`
        # 设的预算——于是「配了本地代理但代理半死」这一最常见的故障会让探测挂
        # 150 秒，PROBE_TIMEOUT_S 形同虚设。这里驱动真实的 connect()：把父类
        # 的建连替换成「只装一个假 socket」，其余逻辑全部走生产代码。
        import http.client as http_client

        from kbapp.integrations.cloud import ProxyHTTPConnection

        class _FakeSocket:
            def __init__(self):
                self.timeout = 'unset'

            def settimeout(self, value):
                self.timeout = value

        def fake_connect(self):
            self.sock = _FakeSocket()

        def budgeted():
            return ProxyHTTPConnection('example.org', attempt={'sent': False, 'read_timeout': PROBE_TIMEOUT_S})

        def legacy():
            return ProxyHTTPConnection('example.org', attempt={'sent': False})

        with patch.object(http_client.HTTPConnection, 'connect', fake_connect):
            probed = budgeted()
            probed.connect()
            self.assertEqual(probed.sock.timeout, PROBE_TIMEOUT_S)
            # The historical default stays 150 when no budget was given, so the
            # existing complete() path is unchanged.
            untouched = legacy()
            untouched.connect()
            self.assertEqual(untouched.sock.timeout, 150)

    def test_the_probe_path_installs_the_guard_and_the_budget(self):
        from kbapp.integrations.cloud import ProxyHTTPHandler, SameHostRedirectHandler

        self.configure(cloud_proxy='http://127.0.0.1:7897')
        body = json.dumps({'data': []}).encode()
        with patch('kbapp.integrations.cloud.build_opener') as factory:
            handle = factory.return_value.open.return_value.__enter__.return_value
            handle.read.return_value = body
            handle.status = 200
            probe_models(self.store)
        handlers = factory.call_args.args
        self.assertTrue(
            any(isinstance(h, SameHostRedirectHandler) for h in handlers),
            'the probe must install SameHostRedirectHandler',
        )
        # The guard's historical 150 s socket default must be replaced by the
        # probe budget, or a half-dead proxy hangs for two and a half minutes.
        proxies = [h for h in handlers if isinstance(h, ProxyHTTPHandler)]
        self.assertTrue(proxies, 'a proxy config must build the proxy handlers')
        for handler in proxies:
            budget = handler.attempt.get('read_timeout')
            self.assertIsNotNone(budget)
            self.assertLessEqual(budget, PROBE_TIMEOUT_S)

    def test_probe_never_echoes_the_body_or_the_key(self):
        self.configure()
        with patch(
            'kbapp.integrations.cloud.build_opener'
        ) as factory:
            factory.return_value.open.side_effect = URLError(
                ConnectionResetError(f'reset leaked {KEY}')
            )
            result = probe_models(self.store)
        serialized = json.dumps(result)
        self.assertNotIn(KEY, serialized)
        self.assertEqual(result['code'], 'unreachable')

    def test_verdict_is_always_one_of_three_values(self):
        for code, expected in [
            ('probe_unsupported', 'unsupported'),
            ('redirect_refused', 'unsupported'),
            ('auth', 'failed'),
            ('timeout', 'failed'),
        ]:
            with self.subTest(code=code):
                self.configure()
                status = 404 if code == 'probe_unsupported' else 302 if code == 'redirect_refused' else 401
                exc = HTTPError(
                    'https://example.org/v1/models', status, 'x', {}, io.BytesIO(b'')
                )
                with patch('kbapp.integrations.cloud.build_opener') as factory:
                    factory.return_value.open.side_effect = exc
                    result = probe_models(self.store)
                self.assertIn(result['verdict'], PROBE_VERDICTS)
                self.assertEqual(result['verdict'], expected)


class ProbeThroughTheApi(unittest.TestCase):
    """经 HTTP 表面：路由存在、要求会话令牌、不接受请求体覆盖配置。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        patcher = patch('kbapp.storage.store.protect', identity_protect)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.service = service_for(self.store)
        self.client = TestClient(self.service.app)
        self.headers = {'x-kb-token': self.service.TOKEN}

    def test_post_requires_a_session_token(self):
        # local_guard 对所有非安全方法要求 x-kb-token（kbapp/web/service.py）。
        self.assertEqual(self.client.post('/api/settings/test').status_code, 403)

    def test_get_is_not_a_route(self):
        response = self.client.get('/api/settings/test', headers=self.headers)
        self.assertEqual(response.status_code, 405)

    def test_body_cannot_override_the_saved_endpoint(self):
        # 唯一一条 URL 校验路径是 save_settings；请求体里的地址必须被忽略，
        # 否则探测就是把 Bearer 密钥发往任意地址的入口。
        self.store.save_settings({'api_base': 'https://example.org/v1', 'api_key': KEY})
        with patch('kbapp.integrations.cloud.build_opener') as factory:
            factory.return_value.open.side_effect = URLError(
                socket.gaierror(socket.EAI_AGAIN, 'resolver')
            )
            response = self.client.post(
                '/api/settings/test',
                headers=self.headers,
                json={'api_base': 'https://attacker.invalid/v1', 'api_key': 'evil'},
            )
        self.assertEqual(response.status_code, 200)
        request = factory.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://example.org/v1/models')
        self.assertEqual(request.get_header('Authorization'), 'Bearer ' + KEY)
        self.assertEqual(response.json()['code'], 'dns')

    def test_response_is_a_closed_vocabulary(self):
        response = self.client.post('/api/settings/test', headers=self.headers)
        data = response.json()
        self.assertEqual(
            set(data),
            {'verdict', 'code', 'summary', 'http_status', 'network_route', 'model_listed'},
        )
        self.assertIn(data['code'], PROBE_CODES)
        self.assertIn(data['verdict'], PROBE_VERDICTS)

    def test_timeout_is_reported_as_timeout_not_as_http(self):
        self.store.save_settings({'api_base': 'https://example.org/v1'})
        with patch('kbapp.integrations.cloud.build_opener') as factory:
            factory.return_value.open.side_effect = URLError(TimeoutError('timed out'))
            data = self.client.post('/api/settings/test', headers=self.headers).json()
        self.assertEqual(data['code'], 'timeout')
        self.assertEqual(data['verdict'], 'failed')


@unittest.skipUnless(os.name == 'nt', 'DPAPI 密钥保护仅 Windows 提供，见 kbapp/core/secrets.py')
class ProbeWithRealDpapi(unittest.TestCase):
    """真实 DPAPI 往返：探测读到的是解密后的密钥，一路不发明文日志。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.store.save_settings({'api_base': 'https://example.org/v1', 'api_key': KEY})

    def test_probe_reads_the_decrypted_key_through_dpapi(self):
        body = json.dumps({'data': [{'id': 'test-model'}]}).encode()
        with patch('kbapp.integrations.cloud.build_opener') as factory:
            handle = factory.return_value.open.return_value.__enter__.return_value
            handle.read.return_value = body
            handle.status = 200
            result = probe_models(self.store)
        self.assertEqual(result['code'], 'ok')
        request = factory.return_value.open.call_args.args[0]
        self.assertEqual(request.get_header('Authorization'), 'Bearer ' + KEY)
        # 对外响应永远不含密钥。
        self.assertNotIn(KEY, json.dumps(result))


@pytest.mark.parametrize('code', PROBE_CODES)
def test_every_vocabulary_code_has_a_summary(code):
    """每个词都有可读说明，插件因此不会渲染出裸代码。"""
    from kbapp.integrations.cloud import _probe_summary

    text = _probe_summary(code, 500 if code == 'http' else None)
    assert isinstance(text, str) and text.strip()


def test_no_two_vocabulary_codes_share_a_summary():
    # The parametrised test above is satisfied by `_probe_summary`'s fallback:
    # delete any row from its table and every code still returns a non-empty
    # string. This is the assertion that actually notices — each code must say
    # something *different*, or the user cannot tell the cases apart.
    from kbapp.integrations.cloud import _probe_summary

    summaries = {code: _probe_summary(code) for code in PROBE_CODES}
    assert len(set(summaries.values())) == len(PROBE_CODES), (
        'two probe codes render the same sentence: ' + json.dumps(summaries, ensure_ascii=False)
    )
