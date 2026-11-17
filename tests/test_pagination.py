"""H04/H05：服务端分页、稳定排序与批量查询。"""
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
from kbapp.research.evidence import latest_many, artifact
from support import service_for


class Pagination(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.store.put_papers([{'id': 'a', 'title': 'RRT sampling study', 'kind': 'library', 'year': 2020},
                               {'id': 'b', 'title': 'Informed sampling study', 'kind': 'library', 'year': 2021},
                               {'id': 'c', 'title': 'Coverage planning study', 'kind': 'library', 'year': 2022}])
        for src, dst, kind, updated in [('b', 'a', 'improves', '2026-11-01'), ('c', 'a', 'improves', '2026-11-02'), ('c', 'b', 'improves', '2026-11-03')]:
            self.store.relation({'src': src, 'dst': dst, 'kind': kind, 'status': 'candidate', 'level': 'inference', 'evidence': [{'quote': 'x'}], 'updated': updated})
        with self.store.db() as db:
            db.execute("UPDATE relations SET updated='2026-11-01' WHERE src='b'")
            db.execute("UPDATE relations SET updated='2026-11-02' WHERE src='c' AND dst='a'")
            db.execute("UPDATE relations SET updated='2026-11-03' WHERE src='c' AND dst='b'")
            for n in range(5):
                db.execute('INSERT INTO approvals VALUES(?,?,?,?,?,?,?,?)', (f'p{n}', 'pending', f'原因{n}', f'问题{n}', f'2026-11-0{n + 1}', f'2026-11-0{n + 1}', '', ''))
        self.service = service_for(self.store)

    def client(self):
        with patch.object(self.service, 'store', self.store):
            return TestClient(self.service.app)

    def test_approvals_are_paginated_with_stable_order_and_projection(self):
        client = self.client()
        first = client.get('/api/approvals', params={'offset': 0, 'limit': 2}).json()
        self.assertEqual(first['total'], 5)
        self.assertEqual(first['pending'], 5)
        self.assertTrue(first['has_more'])
        self.assertEqual(len(first['items']), 2)
        self.assertEqual([item['paper_id'] for item in first['items']], ['p4', 'p3'], '最近创建优先，同时间按 paper_id')
        self.assertNotIn('raw_zotero', first['items'][0]['paper'])
        last = client.get('/api/approvals', params={'offset': 4, 'limit': 2}).json()
        self.assertEqual(len(last['items']), 1)
        self.assertFalse(last['has_more'])
        status = client.get('/api/approvals', params={'status': 'rejected'}).json()
        self.assertEqual(status['total'], 0)

    def test_relations_are_paginated_in_stable_order(self):
        client = self.client()
        first = client.get('/api/relations', params={'status': 'candidate', 'offset': 0, 'limit': 2}).json()
        second = client.get('/api/relations', params={'status': 'candidate', 'offset': 2, 'limit': 2}).json()
        self.assertEqual(first['total'], 3)
        self.assertTrue(first['has_more'])
        self.assertFalse(second['has_more'])
        first_ids = [r['id'] for r in first['items']]
        second_ids = [r['id'] for r in second['items']]
        self.assertFalse(set(first_ids) & set(second_ids), '分页不得重复或遗漏')
        self.assertEqual(first['items'][0]['updated'], '2026-11-03')
        self.assertEqual(len(first_ids) + len(second_ids), 3)

    def test_chain_summary_is_paginated_and_sorted(self):
        client = self.client()
        data = client.get('/api/chains/summary', params={'offset': 0, 'limit': 1}).json()
        self.assertGreaterEqual(data['total'], 1)
        self.assertLessEqual(len(data['items']), 1)
        self.assertTrue(data['has_more'])
        item = data['items'][0]
        for key in ('name', 'nodes', 'edges', 'pending', 'fulltext', 'value'):
            self.assertIn(key, item)
        self.assertEqual(client.get('/api/chains/summary', params={'sort': 'unknown'}).status_code, 400)

    def test_latest_many_batches_latest_artifact_per_subject(self):
        artifact(self.store, 'PaperUnderstanding', 'a', 'fp1', {'coverage': {'complete': False}}, 'partial')
        artifact(self.store, 'PaperUnderstanding', 'b', 'fp2', {'coverage': {'complete': True}}, 'verified')
        result = latest_many(self.store, 'PaperUnderstanding', ['a', 'b', 'missing'])
        self.assertEqual(set(result), {'a', 'b'})
        self.assertTrue(result['b']['coverage']['complete'])
        self.assertFalse(result['a']['coverage']['complete'])


if __name__ == '__main__':
    unittest.main()
