"""GET /api/v2/entitlements 的端点契约（issue #47）。"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['KB_NO_STARTUP_JOBS'] = '1'
os.environ['KB_NO_REFERENCE_WORKER'] = '1'
from fastapi.testclient import TestClient
from kbapp.core.io import TZ
from kbapp.core.quota import day_of
from kbapp.integrations import entitlements
from kbapp.storage.store import Store
from support import service_for

NOW = datetime.now(TZ)
TOP_KEYS = {'api_version', 'tier', 'source', 'reason', 'checked_at',
            'lease_expires_at', 'limits', 'usage', 'commercial'}
LIMIT_KEYS = {'papers_per_run', 'references_per_paper',
              'verifier_units_per_run', 'analysis_tasks_per_day'}
FREE_LIMITS = {'papers_per_run': 20, 'references_per_paper': 10,
               'verifier_units_per_run': 20, 'analysis_tasks_per_day': 3}


def response(**overrides):
    payload = {'schema': 1, 'product': 'littrace', 'tier': 'free', 'entitlements': [],
               'lease_expires_at': (datetime.now(TZ) + timedelta(hours=1)).isoformat()}
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


class Transport:
    def __init__(self, reply):
        self.reply = reply

    def __call__(self):
        if isinstance(self.reply, BaseException):
            raise self.reply
        return self.reply


class EntitlementsApi(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.service = service_for(self.store)
        # 进程级解析器是全局状态；每个用例先回到默认值，再在结束时清空。
        entitlements.reset()
        self.addCleanup(entitlements.reset)
        self.client = TestClient(self.service.app)

    def get(self):
        return self.client.get('/api/v2/entitlements')

    def record(self, subject):
        self.store.quota_record(day_of(datetime.now(TZ)), 'analysis_task', subject, 'free')

    def quota_rows(self):
        with self.store.db() as db:
            return db.execute('SELECT COUNT(*) FROM quota_events').fetchone()[0]

    def test_public_get_returns_exact_contract_shape(self):
        response_ = self.get()
        self.assertEqual(response_.status_code, 200)
        data = response_.json()
        self.assertEqual(set(data), TOP_KEYS)
        self.assertEqual(set(data['limits']), LIMIT_KEYS)
        self.assertEqual(set(data['usage']), {'day', 'analysis_tasks', 'remaining'})
        self.assertEqual(set(data['commercial']), {'base_url', 'reachable'})

    def test_default_is_unconfigured_free(self):
        data = self.get().json()
        self.assertEqual(data['api_version'], 'v2')
        self.assertEqual(data['tier'], 'free')
        self.assertEqual(data['source'], 'unconfigured')
        self.assertEqual(data['reason'], 'not_configured')
        self.assertIsNone(data['checked_at'])
        self.assertIsNone(data['lease_expires_at'])
        self.assertEqual(data['limits'], FREE_LIMITS)
        self.assertEqual(data['usage']['day'], day_of(datetime.now(TZ)))
        self.assertEqual(data['usage']['analysis_tasks'], 0)
        self.assertEqual(data['usage']['remaining'], 3)
        self.assertEqual(data['commercial'],
                         {'base_url': 'http://127.0.0.1:8766', 'reachable': False})

    def test_usage_counts_recorded_events_and_never_goes_negative(self):
        self.record('run-1')
        self.record('run-2')
        data = self.get().json()
        self.assertEqual(data['usage']['analysis_tasks'], 2)
        self.assertEqual(data['usage']['remaining'], 1)
        self.record('run-3')
        data = self.get().json()
        self.assertEqual(data['usage']['analysis_tasks'], 3)
        self.assertEqual(data['usage']['remaining'], 0)

    def test_pro_limits_are_null_and_reachable(self):
        lease = (datetime.now(TZ) + timedelta(hours=1)).isoformat()
        entitlements.install(Transport((200, response(tier='pro', lease_expires_at=lease))))
        data = self.get().json()
        self.assertEqual(data['tier'], 'pro')
        self.assertEqual(data['source'], 'commercial')
        self.assertIsNone(data['reason'])
        self.assertEqual(data['limits'], {key: None for key in LIMIT_KEYS})
        self.assertIsNone(data['usage']['remaining'])
        self.assertTrue(data['commercial']['reachable'])
        self.assertEqual(data['lease_expires_at'], lease)

    def test_unreachable_commercial_still_returns_200(self):
        entitlements.install(Transport(ConnectionRefusedError('refused')))
        response_ = self.get()
        self.assertEqual(response_.status_code, 200)
        data = response_.json()
        self.assertEqual(data['tier'], 'free')
        self.assertEqual(data['source'], 'commercial-unavailable')
        self.assertEqual(data['reason'], 'commercial_not_running')
        self.assertFalse(data['commercial']['reachable'])

    def test_invalid_commercial_response_still_returns_200(self):
        entitlements.install(Transport((200, 'not json')))
        response_ = self.get()
        self.assertEqual(response_.status_code, 200)
        data = response_.json()
        self.assertEqual(data['source'], 'commercial-invalid')
        self.assertEqual(data['reason'], 'commercial_invalid_response')

    def test_endpoint_does_not_write_quota_events(self):
        before = self.quota_rows()
        entitlements.install(Transport((200, response(tier='pro'))))
        self.get()
        self.assertEqual(self.quota_rows(), before)


if __name__ == '__main__':
    unittest.main()
