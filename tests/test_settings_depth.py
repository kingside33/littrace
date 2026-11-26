"""上游追溯层数的设置链路：键表示、校验、保存回读契约与行为隔离。

「不限层数」的唯一持久化表示是布尔键 network_depth_unlimited（不复用 -1/0/
'unlimited' 哨兵：-1 是明确非法值，0 会与「0 层」混淆，整型键存不进字符串）。
network_depth 是深度载体，键级校验只接受 1–100 的正整数：布尔、小数（禁止
静默截断为 2）与非数值显式拒绝，数值字符串沿用旧行为接受。所有校验都在
atomic 写之前完成，失败自然保留上一次有效设置。本文件全部离线。
"""

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
from kbapp.integrations.openalex import OpenAlex  # noqa: E402
from kbapp.storage.store import Store  # noqa: E402
from support import service_for  # noqa: E402

KEY = 'synthetic-openalex-key'


def identity_protect(value, decrypt=False):
    """DPAPI 替身：原样返回，让含密钥的用例在非 Windows 上也能跑（同 test_settings_probe.py）。"""
    return value


def write_settings(store, data):
    """把手写 JSON 原样落进 <data>/state/settings.json，模拟旧版本或手工编辑的配置文件。"""
    atomic(store.paths.state / 'settings.json', data)


def work(wid, title='A paper', doi=None, refs=(), count=12):
    """与 test_references.py 同形的原始 OpenAlex 假响应。"""
    return {
        'id': 'https://openalex.org/' + wid,
        'title': title,
        'doi': 'https://doi.org/' + doi if doi else None,
        'publication_year': 2020,
        'referenced_works': ['https://openalex.org/' + x for x in refs],
        'cited_by_count': count,
    }


class DepthValidation(unittest.TestCase):
    """store.save_settings 的键级校验（POST /api/settings 的校验路径就是它）。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        patcher = patch('kbapp.storage.store.protect', identity_protect)
        patcher.start()
        self.addCleanup(patcher.stop)

    def saved(self):
        current = self.store.settings()
        return current['network_depth'], current['network_depth_unlimited']

    def test_integer_depths_and_numeric_strings_are_accepted(self):
        for value, expected in [(1, 1), (2, 2), (3, 3), (100, 100), ('2', 2), (2.0, 2)]:
            with self.subTest(value=value):
                result = self.store.save_settings({'network_depth': value})
                self.assertEqual(result['network_depth'], expected)
                self.assertEqual(self.store.settings()['network_depth'], expected)

    def test_fractional_depth_is_rejected_without_silent_truncation(self):
        # 当前 main 会把 2.8 截断成 2 静默落盘（int() 截断发生在联合判断之前）；
        # 需求点名要堵的就是这个行为：必须报错，且不得改写已保存的值。
        self.store.save_settings({'network_depth': 3})
        for value in (2.8, '2.8', 3.5):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, '层数应为整数'):
                    self.store.save_settings({'network_depth': value})
        self.assertEqual(self.saved(), (3, False))

    def test_booleans_are_rejected_as_depth_values(self):
        # 通用分支会把 True 变成 1；布尔不是层数，必须显式拒绝。
        self.store.save_settings({'network_depth': 3})
        for value in (True, False):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, '层数应为数字'):
                    self.store.save_settings({'network_depth': value})
        self.assertEqual(self.saved(), (3, False))

    def test_non_numeric_values_are_rejected_as_value_errors(self):
        # web 层只把 ValueError 转 400；None 走 float() 会抛 TypeError（500），
        # 所以键级校验必须把它接住并统一转成 ValueError。
        self.store.save_settings({'network_depth': 3})
        for value in (None, 'abc', [], {}, object()):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, '层数应为数字'):
                    self.store.save_settings({'network_depth': value})
        self.assertEqual(self.saved(), (3, False))

    def test_depth_range_boundaries(self):
        for value in (0, -1, 101):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, '层数应在 1 至 100 之间'):
                    self.store.save_settings({'network_depth': value})
        # 报错文案指路「不限层数」，用户不必去猜哨兵值。
        with self.assertRaisesRegex(ValueError, '不限层数'):
            self.store.save_settings({'network_depth': 101})
        self.assertEqual(self.saved(), (2, False))

    def test_unlimited_is_a_boolean_flag_independent_of_depth(self):
        result = self.store.save_settings({'network_depth_unlimited': True})
        self.assertIs(result['network_depth_unlimited'], True)
        self.assertIs(self.store.settings()['network_depth_unlimited'], True)
        self.assertEqual(self.store.settings()['network_depth'], 2)
        # 深度载体与开关相互独立：unlimited 开着时改深度仍应合法保存。
        self.store.save_settings({'network_depth': 5})
        self.assertEqual(self.saved(), (5, True))
        result = self.store.save_settings({'network_depth_unlimited': False})
        self.assertIs(result['network_depth_unlimited'], False)
        self.assertEqual(self.saved(), (5, False))

    def test_unlimited_as_a_string_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'network_depth_unlimited 应为布尔值'):
            self.store.save_settings({'network_depth_unlimited': 'true'})
        self.assertIs(self.store.settings()['network_depth_unlimited'], False)

    def test_failed_save_keeps_last_valid_settings(self):
        self.store.save_settings({'network_depth': 3, 'model': 'kept-model'})
        for values in (
            {'network_depth': 2.8},
            {'network_depth': None},
            {'network_depth': True},
            {'network_depth': 0},
            {'network_depth_unlimited': 'true'},
            {'network_depth': 101},
        ):
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    self.store.save_settings(values)
        self.assertEqual(self.saved(), (3, False))
        self.assertEqual(self.store.settings()['model'], 'kept-model')

    def test_stored_non_two_depth_no_longer_blocks_unrelated_saves(self):
        # 旧联合判断读的是合并后的 current，存量非 2 值会连 {'model': …} 这样
        # 与层数无关的保存一起锁死；拆分后只保留 daily_time 原规则。
        write_settings(self.store, {'network_depth': 3})
        self.assertEqual(self.saved(), (3, False))
        self.store.save_settings({'model': 'm'})
        self.assertEqual(self.saved(), (3, False))
        self.assertEqual(self.store.settings()['model'], 'm')

    def test_daily_time_rule_is_unchanged(self):
        with self.assertRaisesRegex(ValueError, '当前版本固定北京时间 09:00'):
            self.store.save_settings({'daily_time': '10:00'})
        # 规则仍作用于存量值，不只是提交值。
        write_settings(self.store, {'daily_time': '10:00', 'network_depth': 2})
        with self.assertRaisesRegex(ValueError, '当前版本固定北京时间 09:00'):
            self.store.save_settings({'model': 'm'})

    def test_targeted_depth_save_rewrites_no_other_setting(self):
        # 专项保存（层数 / 不限层数开关）不得改写密钥、预算或其他任何设置；
        # save_settings 是整文件回写，这里逐键锚定回归。
        self.store.save_settings(
            {
                'api_base': 'https://example.org/v1',
                'model': 'test-model',
                'api_key': 'private-test-key',
                'openalex_key': 'openalex-test-key',
                'input_price': 1,
                'output_price': 2,
                'daily_budget': 3,
                'cloud_enabled': True,
                'external_requests_per_run': 7,
            }
        )
        before = self.store.settings()
        self.store.save_settings({'network_depth': 5})
        after_depth = self.store.settings()
        self.assertEqual(self.saved(), (5, False))
        self.store.save_settings({'network_depth_unlimited': True})
        after_unlimited = self.store.settings()
        self.assertEqual(self.saved(), (5, True))
        untouched = {k: v for k, v in before.items() if k not in ('network_depth', 'network_depth_unlimited')}
        for after in (after_depth, after_unlimited):
            self.assertEqual({k: v for k, v in after.items() if k not in ('network_depth', 'network_depth_unlimited')}, untouched)
        self.assertEqual(after_unlimited['api_key'], 'private-test-key')
        self.assertEqual(after_unlimited['openalex_key'], 'openalex-test-key')
        self.assertEqual(after_unlimited['daily_budget'], 3.0)
        self.assertEqual(after_unlimited['external_requests_per_run'], 7)

    def test_legacy_settings_without_the_new_key_default_to_two_layers(self):
        # 旧配置文件没有 network_depth_unlimited：以 DEFAULTS 的 False 补齐，
        # network_depth 取存量值（缺省 2）——旧配置行为就是 2 层。
        write_settings(self.store, {'api_base': 'https://example.org/v1', 'network_depth': 2})
        self.assertEqual(self.saved(), (2, False))
        write_settings(self.store, {})
        self.assertEqual(self.saved(), (2, False))
        # 存量非 2 值原样读出，不被重置。
        write_settings(self.store, {'network_depth': 3})
        self.assertEqual(self.saved(), (3, False))


class DepthApi(unittest.TestCase):
    """经 GET/POST /api/settings 的接口契约（前端读写都走这一对端点）。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        patcher = patch('kbapp.storage.store.protect', identity_protect)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.service = service_for(self.store)
        self.client = TestClient(self.service.app)
        self.headers = {'x-kb-token': self.service.TOKEN}

    def get_depth(self):
        data = self.client.get('/api/settings').json()
        return data['network_depth'], data['network_depth_unlimited']

    def test_post_requires_a_session_token(self):
        # 与 test_workbench.py 的会话锚同形：POST 无 token 一律 403。
        self.assertEqual(self.client.post('/api/settings', json={}).status_code, 403)
        self.assertEqual(
            self.client.post('/api/settings', json={'network_depth': 3}).status_code, 403
        )
        self.assertEqual(self.client.get('/api/settings').status_code, 200)

    def test_unlimited_flag_round_trips_through_the_api(self):
        response = self.client.post(
            '/api/settings', json={'network_depth_unlimited': True}, headers=self.headers
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.get_depth(), (2, True))
        self.assertEqual(
            self.client.post(
                '/api/settings', json={'network_depth_unlimited': False}, headers=self.headers
            ).status_code,
            200,
        )
        self.assertEqual(self.get_depth(), (2, False))

    def test_depth_can_be_saved_while_unlimited_stays_on(self):
        self.client.post('/api/settings', json={'network_depth_unlimited': True}, headers=self.headers)
        response = self.client.post('/api/settings', json={'network_depth': 5}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.get_depth(), (5, True))

    def test_rejected_depth_values_return_400_and_keep_previous_settings(self):
        # 每个非法值都必须是 400（None 尤其不许变成 500），且 GET 前后完全一致。
        before = self.client.get('/api/settings').json()
        for value in (2.8, -1, 0, True, False, None, 'abc', 101):
            with self.subTest(value=value):
                response = self.client.post(
                    '/api/settings', json={'network_depth': value}, headers=self.headers
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(self.client.get('/api/settings').json(), before)

    def test_unlimited_as_a_string_is_a_bad_request(self):
        response = self.client.post(
            '/api/settings', json={'network_depth_unlimited': 'true'}, headers=self.headers
        )
        self.assertEqual(response.status_code, 400)

    def test_numeric_string_depth_is_accepted(self):
        response = self.client.post('/api/settings', json={'network_depth': '2'}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.get_depth(), (2, False))

    def test_daily_time_is_still_fixed(self):
        response = self.client.post('/api/settings', json={'daily_time': '10:00'}, headers=self.headers)
        self.assertEqual(response.status_code, 400)

    def test_legacy_stored_depth_no_longer_bricks_the_api(self):
        write_settings(self.store, {'network_depth': 3})
        self.assertEqual(self.get_depth(), (3, False))
        response = self.client.post('/api/settings', json={'model': 'm'}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.get_depth(), (3, False))


def run_expand_scenario(save):
    """同一追溯场景在给定设置下跑一遍 expand：返回（结果, 请求序列, frontier, 关系）。

    expand 一律不读 network_depth / network_depth_unlimited（SQL 闸 depth<2 与
    入队闸 depth+1<2 硬编码在 kbapp/library/network.py），层数由引擎在任务行里
    快照——这里锚定「改设置不改 expand 的可见行为」。
    """
    from kbapp.library.network import enqueue, expand

    tmp = tempfile.TemporaryDirectory()
    try:
        store = Store(Path(tmp.name))
        store.put_papers(
            [
                {
                    'id': 'p',
                    'kind': 'library',
                    'title': 'Seed',
                    'doi': '10.1234/seed',
                    'year': 2020,
                    'citekey': 'Seed',
                    'openalex_id': 'W1',
                }
            ]
        )
        with patch('kbapp.storage.store.protect', identity_protect):
            store.save_settings(save)
            requests = []

            def fetch(path, params=None):
                requests.append((path, json.dumps(params, sort_keys=True) if params else None))
                if path == 'works/W1':
                    return work('W1', 'Seed', doi='10.1234/seed', refs=['W2'], count=3)
                return {'results': [work('W2', 'Referenced', doi='10.1234/ref', refs=[], count=5)]}

            enqueue(store, 'W1', 'upstream', 0)
            with patch.object(OpenAlex, 'get', side_effect=fetch):
                result = expand(store)
        with store.db() as db:
            frontier = sorted(
                tuple(row) for row in db.execute('SELECT wid,direction,depth,status FROM frontier')
            )
        relations = sorted((r['src'], r['dst'], r['kind']) for r in store.relations())
        return result, requests, frontier, relations
    finally:
        tmp.cleanup()


class ExpandIgnoresDepthSettings(unittest.TestCase):
    """行为隔离：层数设置不改变 expand 的请求集合与 frontier 深度分布。"""

    def test_expand_behaviour_matches_the_default_under_changed_settings(self):
        default = run_expand_scenario({'openalex_key': KEY})
        result, requests, frontier, relations = default
        self.assertEqual(result['state'], 'complete')
        self.assertEqual(
            requests,
            [
                ('works/W1', None),
                ('works', '{"filter": "openalex_id:W2", "per_page": 100}'),
            ],
        )
        self.assertEqual(frontier, [('W1', 'upstream', 0, 'complete'), ('W2', 'upstream', 1, 'complete')])
        self.assertEqual(relations, [('p', 'oa:W2', 'cites')])
        for save in (
            {'openalex_key': KEY, 'network_depth': 1},
            {'openalex_key': KEY, 'network_depth_unlimited': True},
        ):
            with self.subTest(save=save):
                self.assertEqual(run_expand_scenario(save), default)


if __name__ == '__main__':
    unittest.main()
