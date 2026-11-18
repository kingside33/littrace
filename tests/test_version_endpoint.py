"""S3：GET /api/version 版本协商端点；公开、无鉴权，与 /api/v2/meta 不冲突。"""
import os
import re
import sys
import tempfile
import unittest
from importlib import metadata
from pathlib import Path
from unittest.mock import patch
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

    def installed_version(self):
        try:
            return metadata.version('littrace')
        except metadata.PackageNotFoundError:
            self.skipTest('未安装 littrace（pyproject 允许免安装跑测试），由兜底用例覆盖该模式')

    def test_version_service_version_matches_package_metadata(self):
        expected = self.installed_version()
        data = self.client().get('/api/version').json()
        self.assertEqual(data['service_version'], expected)
        self.assertEqual(data['api_version'], 'v2')

    def test_version_service_version_falls_back_to_pyproject_when_not_installed(self):
        # pyproject 的 pythonpath = ["."] 明确支持未执行 pip install -e . 时直接跑测试；
        # 该模式下没有包元数据，唯一出处 pyproject.toml 的兜底分支必须同样返回版本。
        def missing(name):
            raise metadata.PackageNotFoundError(name)
        with patch.object(metadata, 'version', missing):
            response = self.client().get('/api/version')
        self.assertEqual(response.status_code, 200)
        # 断言对象仍是唯一出处 pyproject.toml 的 [project].version，不引入第二份版本常量。
        pyproject = Path(__file__).resolve().parents[1] / 'pyproject.toml'
        expected = re.search(
            r'^version\s*=\s*"([^"]+)"', pyproject.read_text(encoding='utf-8'), re.MULTILINE
        ).group(1)
        self.assertEqual(response.json()['service_version'], expected)
        self.assertEqual(response.json()['api_version'], 'v2')

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
