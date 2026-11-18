"""H01/H02：/api/v2 契约、枚举校验与旧接口兼容。"""
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
from fastapi.testclient import TestClient
from kbapp.storage.store import Store
from kbapp.search.indexing import build_keyword_index
from support import service_for


def corpus_rows():
    rows = []
    for n in range(1, 7):
        pid = f'p{n % 2 + 1}'
        rows.append({'id': f'metadata:p{n % 2 + 1}', 'paper_id': pid, 'kind': 'metadata', 'title': f'Paper {n % 2 + 1}', 'text': f'Paper {n % 2 + 1} adaptive sampling'})
    for n in range(1, 13):
        pid = f'p{n % 2 + 1}'
        rows.append({'id': f'text:chunk{n}', 'paper_id': pid, 'kind': 'paper_text', 'title': f'Paper {n % 2 + 1}', 'page_start': n,
                     'text': f'Chunk {n} about adaptive sampling under static conditions.'})
    return rows


class ApiV2(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.store.put_papers([{'id': 'p1', 'title': 'Paper 1', 'kind': 'library', 'year': 2021},
                               {'id': 'p2', 'title': 'Paper 2', 'kind': 'library', 'year': 2022}])
        index = self.store.paths.indexes / 'versions' / 'v2-test'
        index.mkdir(parents=True)
        rows = corpus_rows()
        (index / 'corpus.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')
        (index / 'corpus_meta.json').write_text(json.dumps({'embedding': {'revision': 'test'}, 'sources': len(rows), 'vector_ids': [], 'vector_count': 0, 'vector_pending': len(rows)}), encoding='utf-8')
        build_keyword_index(index, rows)
        self.store.set_state('active_version', 'v2-test')
        self.service = service_for(self.store)

    def client(self):
        with patch.object(self.service, 'store', self.store):
            return TestClient(self.service.app)

    def test_meta_lists_enums_and_nullable_semantics(self):
        data = self.client().get('/api/v2/meta').json()
        self.assertEqual(data['api_version'], 'v2')
        self.assertIn('paper_text', data['source_kinds'])
        self.assertIn('blocked', data['task_states'])
        self.assertIn('fulltext', data['relation_grades'])
        self.assertIn('null', data['nullable_semantics'])

    def test_search_v2_returns_typed_hits(self):
        response = self.client().get('/api/v2/search', params={'q': 'adaptive sampling', 'k': 5, 'vectors': 'false'})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['query'], 'adaptive sampling')
        self.assertEqual(data['mode'], 'bm25')
        self.assertTrue(data['results'])
        hit = data['results'][0]
        self.assertIn('id', hit)
        self.assertIn('kind', hit)
        self.assertIn('text', hit)
        self.assertEqual(data['coverage']['sources'], len(corpus_rows()))

    def test_search_v2_validates_parameters(self):
        client = self.client()
        self.assertEqual(client.get('/api/v2/search', params={'q': ''}).status_code, 422)
        self.assertEqual(client.get('/api/v2/search', params={'q': 'x', 'k': 0}).status_code, 422)
        self.assertEqual(client.get('/api/v2/search', params={'q': 'x', 'kind': 'unknown-kind'}).status_code, 422)
        self.assertEqual(client.get('/api/v2/search', params={'q': 'x', 'level': 'unknown'}).status_code, 422)
        self.assertEqual(client.get('/api/v2/search', params={'q': 'x', 'vectors': 'false', 'level': 'paper'}).status_code, 200)

    def test_legacy_search_endpoint_still_works(self):
        response = self.client().get('/api/search', params={'q': 'adaptive sampling', 'k': 3, 'vectors': 'false', 'wait_vectors': 'false'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('results', response.json())

    def test_status_tasks_and_paper_contracts(self):
        client = self.client()
        status = client.get('/api/v2/status')
        self.assertEqual(status.status_code, 200)
        self.assertIn('parse_issues', status.json())
        self.assertIn('invalidations', status.json())
        tasks = client.get('/api/v2/tasks')
        self.assertEqual(tasks.status_code, 200)
        self.assertIn('items', tasks.json())
        paper = client.get('/api/v2/paper', params={'paper': 'p1'})
        self.assertEqual(paper.status_code, 200)
        self.assertEqual(paper.json()['paper']['title'], 'Paper 1')
        self.assertEqual(client.get('/api/v2/paper', params={'paper': 'missing'}).status_code, 404)

    def test_paper_v2_is_read_only_unless_refresh_is_requested(self):
        """条目列按行读取本接口；默认不得创建抓取任务。

        `paper_view` 经 `references.view` 在 `auto=True` 时执行
        `enqueue()`：写入 reference_jobs，worker 随后发起外部 OpenAlex
        请求并回写服务库。v1 `GET /api/paper` 保留该行为（详情页打开即补全
        参考文献，tests/test_references.py 有既有断言），v2 必须默认关闭，
        否则浏览（或排序）一次列表就会为整库排队外部抓取。
        """
        def jobs():
            with self.store.db() as db:
                return db.execute('SELECT paper_id, status FROM reference_jobs').fetchall()

        client = self.client()
        before = jobs()
        body = client.get('/api/v2/paper', params={'paper': 'p1'})
        self.assertEqual(body.status_code, 200)
        self.assertEqual(jobs(), before, 'default v2 read must not queue reference jobs')
        # 只读取数仍然给全字段，列需要的 relations / understanding 都在。
        payload = body.json()
        self.assertIn('relations', payload)
        self.assertIn('understanding', payload)
        self.assertIn('references', payload)
        self.assertEqual(payload['references']['job']['status'], 'cached')

        # 显式 opt-in 才恢复 v1 的排队行为。
        opted = client.get('/api/v2/paper', params={'paper': 'p1', 'refresh_references': 'true'})
        self.assertEqual(opted.status_code, 200)
        self.assertEqual(
            [row['status'] for row in jobs()], ['queued'],
            'refresh_references=true still queues, so the v1 workflow stays reachable',
        )


if __name__ == '__main__':
    unittest.main()
