"""#81：密文不可解时设置保存不得锁死，也不得回显内部异常。

DPAPI 密文绑定写入它的 Windows 用户；数据目录搬迁、备份还原到重装系统或重建
用户配置后，settings.json 里的 api_key / openalex_key 会「保存着却解不开」。
本文件用一个 DPAPI 替身复现该状态（离线、跨平台），锁定五条行为：

1. settings() 不把解密异常抛给调用方；non-public 视图用 None 区分「存了但读不出」
   与 ''（没存）。
2. 清除密钥的载荷（clearKeyPayload）在密文不可解时仍然成功。
3. 用户没动密钥时，其他字段照常保存，原始密文逐字节写回。
4. 用户写入新值（含空串清除）时直接覆盖，不需要先解开旧值。
5. 400 的 detail 只有用户能理解的话，不含异常类名或 Incorrect padding。

正常路径（密文可解）另有用例，锚定语义与 #81 之前一致。
"""

import base64
import itertools
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['KB_NO_STARTUP_JOBS'] = '1'
os.environ['KB_NO_REFERENCE_WORKER'] = '1'

from fastapi.testclient import TestClient  # noqa: E402

from kbapp.core.io import atomic  # noqa: E402
from kbapp.core.secrets import SecretDecryptError, SecretProtectionUnavailable  # noqa: E402
from kbapp.integrations.cloud import complete, probe_models  # noqa: E402
from kbapp.storage.store import Store  # noqa: E402
from support import service_for  # noqa: E402

# 非 DPAPI 串：origin/main 的 protect(..., decrypt=True) 在这里抛
# binascii.Error: Incorrect padding。
BLOB = 'not-a-dpapi-blob'
PREFIX = 'dpapi:'
# 每次加密产出的密文都不同，模拟真实 CryptProtectData 的随机性。确定性替身会
# 让「原样写回」与「解密再重加密」产出同一字符串，那条断言就成了恒真
# （lessons L-002）。
_encrypt_counter = itertools.count(1)


def fake_dpapi(text, decrypt=False):
    """DPAPI 替身：密文 = PREFIX + 序号 + ':' + base64(明文)。

    序号让每次加密都不同，`BLOB` 模拟别的 Windows 用户写的密文（抛
    SecretDecryptError，与真实 provider 同一异常类别）。
    """
    if not text:
        return ''
    if not decrypt:
        return PREFIX + str(next(_encrypt_counter)) + ':' + base64.b64encode(text.encode('utf-8')).decode('ascii')
    if text == BLOB:
        raise SecretDecryptError('Windows 密钥保护失败')
    if isinstance(text, str) and text.startswith(PREFIX):
        return base64.b64decode(text[len(PREFIX):].split(':', 1)[1]).decode('utf-8')
    raise SecretDecryptError('Windows 密钥保护失败')


def no_protector(text, decrypt=False):
    """非 Windows 的真实行为：任何非空值都抛「本平台没有保护器」。"""
    if not text:
        return ''
    raise SecretProtectionUnavailable('密钥保护仅支持当前 Windows 用户，其他平台需配置密钥存储适配器')


class CiphertextCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch('kbapp.storage.store.protect', fake_dpapi)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.store = Store(Path(self.temp.name))
        self.service = service_for(self.store)
        self.client = TestClient(self.service.app)
        self.headers = {'x-kb-token': self.service.TOKEN}

    def write_settings(self, data):
        atomic(self.store.paths.state / 'settings.json', data)

    def read_raw(self):
        path = self.store.paths.state / 'settings.json'
        return json.loads(path.read_text(encoding='utf-8'))

    def post(self, payload):
        return self.client.post('/api/settings', json=payload, headers=self.headers)


class UnreadableView(CiphertextCase):
    """解不开的密文是「有密钥」的第三种状态，不是一个异常。"""

    def test_settings_does_not_raise_and_distinguishes_unreadable_from_absent(self):
        self.write_settings({'api_key': BLOB, 'openalex_key': BLOB})
        current = self.store.settings()
        self.assertIsNone(current['api_key'])
        self.assertIsNone(current['openalex_key'])
        # '' 仍然表示「没存」：两种状态可分辨。
        self.write_settings({'api_key': '', 'openalex_key': ''})
        self.assertEqual(self.store.settings()['api_key'], '')
        self.assertEqual(self.store.settings()['openalex_key'], '')

    def test_public_view_and_get_settings_do_not_raise(self):
        self.write_settings({'api_key': BLOB, 'openalex_key': BLOB})
        public = self.store.settings(public=True)
        # 密文确实存着，界面上仍是「已保存」；值本身永远不会回显。
        self.assertIs(public['api_key'], True)
        self.assertIs(public['openalex_key'], True)
        response = self.client.get('/api/settings')
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIs(body['api_key'], True)
        self.assertNotIn(BLOB, json.dumps(body))


class ClearEscapeHatch(CiphertextCase):
    """清除密钥是泄漏时的逃生舱，密文不可解时也必须可用。"""

    def test_clear_key_payload_succeeds_with_unreadable_ciphertext(self):
        self.write_settings(
            {
                'api_base': 'https://api.example.org/v1',
                'model': 'm',
                'api_key': BLOB,
                'openalex_key': BLOB,
                'budget_mode': 'unlimited',
                'cloud_enabled': True,
            }
        )
        response = self.post({'api_key': '', 'cloud_enabled': False})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIs(body['api_key'], False)
        self.assertIs(body['cloud_enabled'], False)
        raw = self.read_raw()
        self.assertEqual(raw['api_key'], '')
        self.assertIs(raw['cloud_enabled'], False)
        # 没被这个载荷碰到的 openalex_key 仍按原密文保留。
        self.assertEqual(raw['openalex_key'], BLOB)


class UntouchedSecret(CiphertextCase):
    """用户没动密钥时，原始密文必须原样写回，不能变成空串或被重加密。"""

    def test_unrelated_save_keeps_the_ciphertext_byte_for_byte(self):
        self.write_settings({'api_key': BLOB, 'openalex_key': BLOB, 'model': 'old-model'})
        response = self.post({'model': 'new-model', 'network_depth': 3})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['model'], 'new-model')
        raw = self.read_raw()
        self.assertEqual(raw['api_key'], BLOB)
        self.assertEqual(raw['openalex_key'], BLOB)
        self.assertEqual(raw['model'], 'new-model')
        self.assertIsNone(self.store.settings()['api_key'])

    def test_unrelated_save_works_even_with_cloud_left_enabled(self):
        # 不碰云开关的保存（例如连接组的 zotero_dir）不能因为旧密文读不出而锁死。
        self.write_settings(
            {
                'api_base': 'https://api.example.org/v1',
                'model': 'm',
                'api_key': BLOB,
                'budget_mode': 'unlimited',
                'cloud_enabled': True,
            }
        )
        response = self.post({'zotero_dir': 'D:/Zotero'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['zotero_dir'], 'D:/Zotero')
        self.assertEqual(self.read_raw()['api_key'], BLOB)

    def test_settings_page_payload_with_cloud_already_enabled_saves_unrelated_field(self):
        # 设置页每次保存都提交整张表单（settings.js:18），所以载荷里恒有
        # `cloud_enabled`；密码框未改动时发 null（settings.js:20）。存量已启用
        # 且密文读不出时，改 zotero_dir 仍须成功并保留原密文，否则真实客户端
        # 又被锁死——这正是 #81 要消除的状态。
        self.write_settings(
            {
                'api_base': 'https://api.example.org/v1',
                'model': 'm',
                'api_key': BLOB,
                'openalex_key': BLOB,
                'budget_mode': 'unlimited',
                'cloud_enabled': True,
                'zotero_dir': 'D:/old',
            }
        )
        payload = {
            'cloud_enabled': True,
            'budget_mode': 'unlimited',
            'relation_extractor': 'cloud',
            'api_base': 'https://api.example.org/v1',
            'cloud_proxy': '',
            'model': 'm',
            'api_key': None,
            'openalex_key': None,
            'external_requests_per_run': 100,
            'cloud_requests_per_run': 12,
            'zotero_dir': 'D:/new',
            'local_text_model': 'gemma4:e4b',
            'local_text_context': 4096,
            'ocr_pages_per_run': 30,
            'ocr_regions_per_run': 30,
            'network_depth': 2,
            'network_depth_unlimited': False,
            'daily_budget': 0.0,
            'input_price': 0.0,
            'output_price': 0.0,
        }
        response = self.post(payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['zotero_dir'], 'D:/new')
        raw = self.read_raw()
        self.assertEqual(raw['zotero_dir'], 'D:/new')
        self.assertEqual(raw['api_key'], BLOB)
        self.assertEqual(raw['openalex_key'], BLOB)
        self.assertIs(raw['cloud_enabled'], True)


class SuppliedSecret(CiphertextCase):
    """用户写入新值（含空串）时直接覆盖，不需要先解开旧值。"""

    def test_a_typed_key_replaces_an_unreadable_ciphertext(self):
        self.write_settings({'api_key': BLOB})
        response = self.post({'api_key': '  sk-new  '})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIs(response.json()['api_key'], True)
        self.assertEqual(self.store.settings()['api_key'], 'sk-new')
        raw = self.read_raw()
        # 新值被重新加密（不是原样保存明文），且旧密文已被覆盖。
        self.assertTrue(raw['api_key'].startswith(PREFIX))
        self.assertNotEqual(raw['api_key'], BLOB)
        self.assertNotIn('sk-new', raw['api_key'])

    def test_clearing_to_empty_overwrites_without_decrypting(self):
        self.write_settings({'api_key': BLOB})
        response = self.post({'api_key': ''})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.read_raw()['api_key'], '')
        self.assertEqual(self.store.settings()['api_key'], '')


class ApiErrorVocabulary(CiphertextCase):
    """400 的 detail 只能是用户能理解的话，不回显 Python 内部字符串。"""

    LEAKS = ('Error', 'padding', 'binascii', 'ValueError', 'Traceback', 'base64')

    def assert_sanitized(self, detail):
        for leaked in self.LEAKS:
            self.assertNotIn(leaked, detail)
        self.assertTrue(detail.strip(), 'detail must say something the user can act on')

    def test_enabling_cloud_with_unreadable_key_names_the_fix(self):
        self.write_settings(
            {
                'api_base': 'https://api.example.org/v1',
                'model': 'm',
                'api_key': BLOB,
                'budget_mode': 'unlimited',
                'cloud_enabled': False,
            }
        )
        response = self.post({'cloud_enabled': True})
        self.assertEqual(response.status_code, 400)
        detail = response.json()['detail']
        self.assertEqual(detail, '保存的密钥无法读取，请重新填写')
        self.assert_sanitized(detail)

    def test_other_validation_errors_are_not_contaminated_by_decrypt_failure(self):
        self.write_settings({'api_key': BLOB})
        response = self.post({'api_base': 'http://public.example/v1'})
        self.assertEqual(response.status_code, 400)
        self.assert_sanitized(response.json()['detail'])


class CloudConsumerGuard(CiphertextCase):
    """settings() 的 None 契约必须被云端调用链消费，不能退化成 TypeError/幽灵用量。"""

    def test_complete_refuses_unreadable_key_before_reserving_budget(self):
        self.write_settings(
            {
                'api_base': 'https://api.example.org/v1',
                'model': 'm',
                'api_key': BLOB,
                'budget_mode': 'unlimited',
                'cloud_enabled': True,
            }
        )
        with self.assertRaisesRegex(ValueError, '保存的密钥无法读取，请重新填写') as caught:
            complete(self.store, 'prompt', 'test')
        message = str(caught.exception)
        self.assertNotIn('NoneType', message)
        self.assertNotIn('TypeError', message)
        # 拒绝发生在 reserve 之前：没有 reserved 用量行，也就不会计入当日额度。
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM usage').fetchone()[0], 0)


class ProbeUnreadableKey(CiphertextCase):
    """探测不得把「读不出」当成「没配」：不发请求，也不谎报「鉴权被接受」。"""

    def test_probe_reports_key_unreadable_without_sending_a_request(self):
        self.write_settings({'api_base': 'https://api.example.org/v1', 'api_key': BLOB})
        with patch('kbapp.integrations.cloud.build_opener') as factory, patch(
            'kbapp.integrations.cloud.urlopen'
        ) as direct:
            result = probe_models(self.store)
        # 没有发起任何请求：不把键当空串发一个无鉴权头的探测。
        factory.assert_not_called()
        direct.assert_not_called()
        self.assertEqual(result['code'], 'key_unreadable')
        self.assertEqual(result['verdict'], 'failed')
        # 也不能误导成「鉴权被拒绝 / 密钥写错了」。
        self.assertNotIn('鉴权被接受', result['summary'])

    def test_probe_code_is_in_the_closed_vocabulary(self):
        from kbapp.integrations.cloud import PROBE_CODES

        self.assertIn('key_unreadable', PROBE_CODES)
        self.assertIn('key_platform_unsupported', PROBE_CODES)

    def test_probe_distinguishes_platform_unavailable_from_a_broken_blob(self):
        """无保护器的平台上「重填密钥」永远无效，探测不能说同一句话。"""
        from kbapp.integrations.cloud import _probe_summary

        self.write_settings({'api_base': 'https://api.example.org/v1', 'api_key': 'anything'})
        with patch('kbapp.storage.store.protect', no_protector), patch(
            'kbapp.integrations.cloud.build_opener'
        ) as factory:
            result = probe_models(self.store)
        factory.assert_not_called()
        self.assertEqual(result['code'], 'key_platform_unsupported')
        self.assertEqual(result['verdict'], 'failed')
        # 不能说「请重新填写」——本平台重填不可能成功。
        self.assertNotIn('重新填写', result['summary'])
        self.assertIn('Windows', result['summary'])
        # 与「密文损坏」是两句话，用户能分辨该做什么。
        self.assertNotEqual(result['summary'], _probe_summary('key_unreadable'))


class PlatformWithoutProtector(CiphertextCase):
    """非 Windows：没有保护器是「能力缺失」，不是「密文损坏」。"""

    def setUp(self):
        super().setUp()
        # 用真实 secrets.py 在非 Windows 上的行为替换本文件的 DPAPI 替身。
        patcher = patch('kbapp.storage.store.protect', no_protector)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_settings_do_not_raise_and_classify_the_cause(self):
        self.write_settings({'api_key': 'anything'})
        # 读侧不抛（否则每次保存都会被重新锁死），但原因与「密文损坏」分开。
        self.assertIsNone(self.store.settings()['api_key'])
        self.assertEqual(self.store.secret_state('api_key'), 'platform_unavailable')
        self.assertIn('Windows', self.store.secret_advice('api_key'))

    def test_public_view_flags_the_unavailable_secret(self):
        self.write_settings({'api_key': 'anything', 'cloud_enabled': True})
        public = self.store.settings(public=True)
        self.assertIs(public['api_key'], True)
        self.assertEqual(public['secrets_unavailable'], {'api_key': 'platform_unavailable'})
        # 摘要据此不再是一盏「已启用」的绿灯。
        response = self.client.get('/api/settings')
        body = response.json()
        self.assertEqual(body['secrets_unavailable']['api_key'], 'platform_unavailable')

    def test_saving_a_new_key_is_a_readable_400_not_a_500(self):
        response = self.post({'api_key': 'sk-new'})
        self.assertEqual(response.status_code, 400, response.text)
        detail = response.json()['detail']
        self.assertIn('Windows', detail)
        self.assertNotIn('RuntimeError', detail)

    def test_unrelated_fields_still_save(self):
        response = self.post({'model': 'new-model'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['model'], 'new-model')


class OpenAlexKeyAdvice(CiphertextCase):
    """存了但读不出的 OpenAlex key 不能报成「没配 key」。"""

    def test_fetch_prints_a_fixed_hint_never_a_settings_derived_string(self):
        """CLI 侧的日志文案是常量，不由解密路径上的值拼出来（CodeQL 明文泄露）。"""
        import io
        from contextlib import redirect_stdout

        from kbapp.workflows.tools import fetch_openalex

        self.write_settings({'openalex_key': BLOB})
        buffer = io.StringIO()
        with patch.object(fetch_openalex, 'get_api_key', return_value=''), \
                patch.object(fetch_openalex, '_curl_json', return_value={'meta': {}}), \
                patch.object(fetch_openalex, 'key_state', return_value='unreadable'), \
                redirect_stdout(buffer):
            fetch_openalex._query('/tmp', 'filter=doi:10.1/x', 'id')
        logged = buffer.getvalue()
        self.assertIn('已保存的 OpenAlex key 无法读取', logged)
        # 日志里既不能有密文，也不能有明文密钥。
        self.assertNotIn(BLOB, logged)

    def test_openalex_refuses_with_retype_advice_not_apply_advice(self):
        from kbapp.integrations.openalex import OpenAlex

        self.write_settings({'openalex_key': BLOB})
        client = OpenAlex(self.store)
        with self.assertRaises(ValueError) as caught:
            client.get('works/W1')
        message = str(caught.exception)
        self.assertIn('重新填写', message)
        # 默认文案会把人引向「去配置/申请一个 key」，正确动作是重填。
        self.assertNotIn('请在网页配置 OpenAlex key', message)

    def test_trace_task_stops_with_the_retype_advice_in_its_error(self):
        """端到端：追溯停在 awaiting_key 时，任务行的 error 说的是「重填」而非「没配」。"""
        from kbapp.workflows.trace import run_task, start_task

        self.store.put_papers([{'id': 'p1', 'kind': 'library', 'title': 'Seed',
                                'openalex_id': 'W1', 'citekey': 'Seed'}])
        # 直接写坏密文：save_settings 会拒绝写读不出的 key，所以落原始文件。
        self.write_settings({'openalex_key': BLOB})
        tid = start_task(self.store, 'p1', depth=1)['id']
        snapshot = run_task(self.store, tid)
        self.assertEqual(snapshot['status'], 'awaiting_key')
        self.assertIn('重新填写', snapshot['error'])
        self.assertNotIn('缺少 OpenAlex key', snapshot['error'])
    def test_trace_stop_text_names_the_retype_action(self):
        from kbapp.workflows.trace import classify

        self.write_settings({'openalex_key': BLOB})
        message = self.store.secret_advice('openalex_key')
        self.assertIn('重新填写', message)
        self.assertEqual(classify(ValueError(message)), 'awaiting_key')

    def test_network_expand_reports_reason(self):
        from kbapp.library.network import expand

        self.write_settings({'openalex_key': BLOB})
        result = expand(self.store)
        self.assertEqual(result['state'], 'awaiting_openalex_key')
        self.assertEqual(result['reason'], 'unreadable')
        self.assertIn('重新填写', result['message'])


    def test_ensure_research_metadata_reports_unreadable_instead_of_degrading_silently(self):
        """谱系识别入口：读不出时要带重填提示，不能与「没配」得到同一个结果。"""
        from kbapp.library.network import ensure_research_metadata

        external = {'id': 'oa:W1', 'kind': 'external', 'openalex_id': 'W1', 'title': 'Paper'}
        self.write_settings({'openalex_key': BLOB})
        result = ensure_research_metadata(self.store, dict(external))
        # 与「没配」必须可分辨：多一个 scope/reason，且带着可执行的动作。
        self.assertEqual(result['abstract_scope'], 'key_unreadable')
        self.assertIn('重新填写', result['abstract_scope_reason'])
        # 状态里留了一条，供状态页/设置页引导。
        notice = self.store.state('openalex_key_unusable')
        self.assertEqual(notice['reason'], 'unreadable')
        self.assertIn('重新填写', notice['message'])

    def test_ensure_research_metadata_without_a_key_stays_quietly_degraded(self):
        """没配 key 与读不出必须给不同结果，否则这条修复就成了「处处报警」。"""
        from kbapp.library.network import ensure_research_metadata

        external = {'id': 'oa:W1', 'kind': 'external', 'openalex_id': 'W1', 'title': 'Paper'}
        self.write_settings({'openalex_key': ''})
        result = ensure_research_metadata(self.store, dict(external))
        self.assertEqual(result['abstract_scope'], 'unavailable')
        self.assertNotIn('abstract_scope_reason', result)
        self.assertIsNone(self.store.state('openalex_key_unusable'))


class NormalPath(CiphertextCase):
    """密文可解时，读写语义与 #81 之前完全一致。"""

    def test_round_trip_and_untouched_ciphertext_stability(self):
        response = self.post(
            {
                'api_base': 'https://api.example.org/v1',
                'model': 'test-model',
                'api_key': 'sk-live',
                'openalex_key': 'oa-live',
                'budget_mode': 'unlimited',
                'cloud_enabled': True,
            }
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIs(response.json()['api_key'], True)
        self.assertEqual(self.store.settings()['api_key'], 'sk-live')
        self.assertEqual(self.store.settings()['openalex_key'], 'oa-live')
        raw = self.read_raw()
        self.assertNotIn('sk-live', raw['api_key'])
        self.assertNotIn('oa-live', raw['openalex_key'])
        before = (raw['api_key'], raw['openalex_key'])
        # 只改模型：两份密文都不重新加密，值仍可解回。
        response = self.post({'model': 'next-model', 'cloud_enabled': True})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.store.settings()['api_key'], 'sk-live')
        self.assertEqual(self.store.settings()['openalex_key'], 'oa-live')
        raw = self.read_raw()
        self.assertEqual((raw['api_key'], raw['openalex_key']), before)
        # 旧行为：api_key=None 表示保留，语义不变。
        self.store.save_settings({'api_key': None})
        self.assertEqual(self.store.settings()['api_key'], 'sk-live')
        self.assertEqual(self.read_raw()['api_key'], before[0])
