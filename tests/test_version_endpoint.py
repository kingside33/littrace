"""S3：GET /api/version 版本协商端点；公开、无鉴权，与 /api/v2/meta 不冲突。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['KB_NO_STARTUP_JOBS'] = '1'
os.environ['KB_NO_REFERENCE_WORKER'] = '1'
from fastapi.testclient import TestClient
from kbapp.storage.store import Store
from support import service_for


class VersionEndpoint(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.service = service_for(self.store)

    def client(self):
        return TestClient(self.service.app)

    def test_version_returns_200_with_string_fields(self):
        # TestClient 不带任何鉴权头：GET 公开，与 /api/session 同级。
        response = self.client().get('/api/version')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(set(data), {'service_version', 'api_version'})
        self.assertIsInstance(data['service_version'], str)
        self.assertIsInstance(data['api_version'], str)

    def test_version_service_version_matches_package_metadata(self):
        from importlib import metadata
        data = self.client().get('/api/version').json()
        self.assertEqual(data['service_version'], metadata.version('littrace'))
        self.assertEqual(data['api_version'], 'v2')

    def test_version_does_not_conflict_with_v2_meta(self):
        client = self.client()
        version_data = client.get('/api/version').json()
        meta = client.get('/api/v2/meta').json()
        # api_version 语义一致，均为 'v2'；meta 契约不被改动（不新增 service_version）。
        self.assertEqual(version_data['api_version'], 'v2')
        self.assertEqual(meta['api_version'], 'v2')
        self.assertNotIn('service_version', meta)

    def test_version_has_no_post_route(self):
        # 只声明 GET：带合法会话 token 越过中间件后，对同一路径 POST 返回 405，不引入 POST 契约。
        response = TestClient(self.service.app).post('/api/version', headers={'x-kb-token': self.service.TOKEN})
        self.assertEqual(response.status_code, 405)
