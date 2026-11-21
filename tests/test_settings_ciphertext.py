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
import binascii
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
from kbapp.storage.store import Store  # noqa: E402
from support import service_for  # noqa: E402

# 非 DPAPI 串：origin/main 的 protect(..., decrypt=True) 在这里抛
# binascii.Error: Incorrect padding。
BLOB = 'not-a-dpapi-blob'
PREFIX = 'dpapi:'


def fake_dpapi(text, decrypt=False):
    """DPAPI 替身：可解的密文带 PREFIX，BLOB 模拟别的 Windows 用户写的密文。"""
    if not text:
        return ''
    if not decrypt:
        return PREFIX + base64.b64encode(text.encode('utf-8')).decode('ascii')
    if text == BLOB:
        raise binascii.Error('Incorrect padding')
    if isinstance(text, str) and text.startswith(PREFIX):
        return base64.b64decode(text[len(PREFIX):]).decode('utf-8')
    raise RuntimeError('Windows 密钥保护失败')


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


class SuppliedSecret(CiphertextCase):
    """用户写入新值（含空串）时直接覆盖，不需要先解开旧值。"""

    def test_a_typed_key_replaces_an_unreadable_ciphertext(self):
        self.write_settings({'api_key': BLOB})
        response = self.post({'api_key': '  sk-new  '})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIs(response.json()['api_key'], True)
        self.assertEqual(self.store.settings()['api_key'], 'sk-new')
        raw = self.read_raw()
        self.assertEqual(raw['api_key'], PREFIX + base64.b64encode(b'sk-new').decode('ascii'))
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
