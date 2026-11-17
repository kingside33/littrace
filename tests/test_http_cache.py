"""H09：静态资源 ETag/304、GZip 压缩与内容哈希缓存。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['KB_NO_STARTUP_JOBS'] = '1'
os.environ['KB_NO_REFERENCE_WORKER'] = '1'
from fastapi.testclient import TestClient
from kbapp.storage.store import Store
from support import service_for


class HttpCache(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.service = service_for(self.store)

    def client(self):
        with patch.object(self.service, 'store', self.store):
            return TestClient(self.service.app)

    def test_static_assets_revalidate_with_etag(self):
        client = self.client()
        first = client.get('/app.js')
        self.assertEqual(first.status_code, 200)
        etag = first.headers.get('etag')
        self.assertTrue(etag)
        second = client.get('/app.js', headers={'If-None-Match': etag})
        self.assertEqual(second.status_code, 304)
        self.assertEqual(second.content, b'')
        css = client.get('/app.css')
        self.assertEqual(css.status_code, 200)
        self.assertTrue(css.headers.get('etag'))
        self.assertEqual(client.get('/app.css', headers={'If-None-Match': css.headers['etag']}).status_code, 304)

    def test_homepage_is_etag_cached(self):
        client = self.client()
        first = client.get('/')
        self.assertEqual(first.status_code, 200)
        self.assertIn('研究', first.text)
        self.assertEqual(client.get('/', headers={'If-None-Match': first.headers['etag']}).status_code, 304)

    def test_large_json_responses_are_compressed(self):
        client = self.client()
        response = client.get('/api/status', headers={'Accept-Encoding': 'gzip'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get('content-encoding'), 'gzip')




class LibraryList(unittest.TestCase):
    """H03：列表接口只返回展示字段，大字段留给详情。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.store.put_papers([{'id': 'p', 'title': 'Paper', 'kind': 'library', 'abstract': 'A', 'raw_zotero': {'huge': 'x' * 500}, 'refs': [{'raw': 'ref'}], 'creators': [{'name': 'n'}], 'collections': ['C'], 'tags': ['T']}])

    def test_library_list_omits_detail_only_fields(self):
        from fastapi import FastAPI
        from kbapp.web.document_api import install
        app = FastAPI()
        install(app, self.store)
        with TestClient(app) as client:
            data = client.get('/api/library').json()
        item = data['items'][0]
        self.assertEqual(item['title'], 'Paper')
        self.assertEqual(item['abstract'], 'A')
        for field in ('raw_zotero', 'refs', 'creators'):
            self.assertNotIn(field, item)
        self.assertEqual(data['collections'], ['C'])
        self.assertEqual(data['total'], 1)


if __name__ == '__main__':
    unittest.main()
