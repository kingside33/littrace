"""U2：条目面板三 section 的只读聚合接口（/api/v2/paper/sections）。

插件只能拿到 Zotero 的 (libraryID, itemKey)，所以它的请求参数是
``z:<library_id>:<zotero_key>``。这里锁定四件事：

1. 一次请求拿齐证据计数、谱系关系与精读覆盖；
2. 文献的 id 是历史 ``doi:`` 形式时，仍能按 zotero_key 解析（不能误报「未索引」）;
3. 索引未发布 / 文献未进语料时按空态返回，而不是报错；
4. 已驳回的关系不计入已确认/待核，引用事实单独计数。

全部离线：TestClient + 临时数据目录，不碰网络与模型。
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

from fastapi.testclient import TestClient

from kbapp.storage.store import Store
from support import service_for


PAPER_ID = 'doi:10.1234/legacy'
PEER_ID = 'z:1:PEER0001'
EMPTY_ID = 'z:1:EMPTY001'


class PaperSections(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.store.put_papers([
            # 历史遗留 id（doi: 形式），zotero_key 仍指向同一个条目。
            {'id': PAPER_ID, 'kind': 'library', 'title': 'Legacy Paper', 'year': 2019,
             'zotero_key': 'ITEM0001', 'library_id': 1, 'doi': '10.1234/legacy'},
            {'id': PEER_ID, 'kind': 'library', 'title': 'Peer Paper', 'year': 2021,
             'zotero_key': 'PEER0001', 'library_id': 1},
            {'id': EMPTY_ID, 'kind': 'library', 'title': 'Empty Paper', 'year': 2020,
             'zotero_key': 'EMPTY001', 'library_id': 1},
        ])
        self.store.relation({'src': PAPER_ID, 'dst': PEER_ID, 'kind': 'inherits', 'status': 'confirmed',
                             'level': 'fulltext', 'reviewed_by': 'reviewer',
                             'evidence': [{'quote': 'original support'}],
                             'explanation': '继承前作的采样方法'}, manual=True)
        self.store.relation({'src': PEER_ID, 'dst': PAPER_ID, 'kind': 'improves', 'status': 'candidate',
                             'level': 'title', 'explanation': '待核改进'})
        self.store.relation({'src': PAPER_ID, 'dst': PEER_ID, 'kind': 'cites', 'status': 'confirmed'})
        self.store.relation({'src': PAPER_ID, 'dst': PEER_ID, 'kind': 'transfers', 'status': 'rejected'})
        version = self.store.paths.indexes / 'versions' / 'v1'
        version.mkdir(parents=True)
        rows = [
            {'id': 'text:1', 'paper_id': PAPER_ID, 'kind': 'paper_text', 'page_start': 2,
             'text': 'adaptive sampling under static conditions'},
            {'id': 'text:2', 'paper_id': PAPER_ID, 'kind': 'paper_text', 'page_start': 5,
             'text': 'coverage experiments'},
            {'id': 'figure:1', 'paper_id': PAPER_ID, 'kind': 'figure', 'page_start': 4,
             'text': 'Figure 3: coverage path'},
            {'id': 'formula:1', 'paper_id': PAPER_ID, 'kind': 'formula', 'page_start': 3,
             'text': 'J = sum(c_i)'},
            {'id': 'text:3', 'paper_id': PEER_ID, 'kind': 'paper_text', 'page_start': 1,
             'text': 'peer text'},
        ]
        (version / 'corpus.jsonl').write_text(
            ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')
        self.store.set_state('active_version', 'v1')
        with self.store.db() as db:
            db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?)',
                       ('doc:1', PAPER_ID, 'ATT0001', 'sha-1', str(self.temp.name), 8, '{}', '2026-01-01'))
            for page, classification, status in (
                    (1, 'NATIVE_TEXT', 'complete'), (2, 'NATIVE_TEXT', 'complete'),
                    (3, 'SCANNED', 'complete'), (4, 'LOW_QUALITY', 'failed')):
                db.execute('INSERT INTO document_pages VALUES(?,?,?,?,?,?,?,?)',
                           ('doc:1', page, classification, status, '', '', '{}', '2026-01-01'))
        from kbapp.research.evidence import artifact
        artifact(self.store, 'PaperUnderstanding', PAPER_ID, 'fp-1',
                 {'paper_id': PAPER_ID,
                  'coverage': {'complete': False, 'total_pages': 8, 'read_pages': [1, 2], 'missing_pages': [4]},
                  'claims': [], 'gaps': ['第4页不可读']}, 'partial')
        self.service = service_for(self.store)

    def client(self):
        with patch.object(self.service, 'store', self.store):
            return TestClient(self.service.app)

    def get(self, paper):
        # 指纹固定为入库时的值，避免测试依赖真实 PDF 哈希。
        with patch('kbapp.research.reading.understanding_fingerprint', return_value='fp-1'):
            return self.client().get('/api/v2/paper/sections', params={'paper': paper})

    def test_snapshot_aggregates_evidence_lineage_and_reading(self):
        response = self.get('z:1:ITEM0001')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        # z:<lib>:<key> 通过 zotero_key 兜底解析到历史 doi id。
        self.assertEqual(data['paper_id'], PAPER_ID)
        self.assertEqual(data['title'], 'Legacy Paper')

        evidence = data['evidence']
        self.assertTrue(evidence['indexed'])
        self.assertEqual(evidence['index_version'], 'v1')
        self.assertEqual(evidence['counts'], {'paper_text': 2, 'figure': 1, 'formula': 1})
        self.assertEqual(evidence['total'], 4)
        self.assertEqual(len(evidence['excerpts']), 2)
        self.assertTrue(all(item['excerpt'] for item in evidence['excerpts']))

        lineage = data['lineage']
        self.assertEqual(lineage['verified'], 1)
        self.assertEqual(lineage['pending'], 1)
        self.assertEqual(lineage['citations'], 1)
        # 已驳回的 transfers 关系不出现，条目里只有方法关系。
        self.assertEqual(len(lineage['items']), 2)
        first = lineage['items'][0]
        self.assertEqual(first['grade'], 'fulltext')
        self.assertEqual(first['label'], '方法继承')
        self.assertEqual(first['peer_title'], 'Peer Paper')
        self.assertEqual(first['direction'], 'following')
        self.assertTrue(all(item['kind'] != 'cites' for item in lineage['items']))

        reading = data['reading']
        self.assertEqual(reading['state'], 'partial')
        self.assertEqual(reading['total_pages'], 8)
        self.assertEqual(reading['read_pages'], 2)
        self.assertEqual(reading['missing_pages'], [4])
        self.assertEqual(reading['classification'], {'NATIVE_TEXT': 2, 'SCANNED': 1, 'LOW_QUALITY': 1})
        self.assertEqual(reading['documents'], 1)
        self.assertTrue(reading['can_parse'])
        self.assertIn('第4页不可读', reading['gaps'])

    def test_unknown_paper_is_404(self):
        response = self.client().get('/api/v2/paper/sections', params={'paper': 'z:1:NOTHERE'})
        self.assertEqual(response.status_code, 404)

    def test_paper_without_corpus_rows_is_unindexed(self):
        data = self.get('z:1:EMPTY001').json()
        self.assertFalse(data['evidence']['indexed'])
        self.assertEqual(data['evidence']['total'], 0)
        self.assertEqual(data['evidence']['counts'], {})
        self.assertEqual(data['lineage']['verified'], 0)
        self.assertEqual(data['lineage']['pending'], 0)
        self.assertEqual(data['reading']['state'], 'unparsed')
        self.assertEqual(data['reading']['classification'], {})
        self.assertEqual(data['reading']['documents'], 0)

    def test_missing_published_index_reports_unindexed(self):
        self.store.set_state('active_version', '')
        data = self.get(PAPER_ID).json()
        self.assertFalse(data['evidence']['indexed'])
        self.assertIsNone(data['evidence']['index_version'])
        self.assertEqual(data['evidence']['total'], 0)

    def test_stale_understanding_is_reported(self):
        with patch('kbapp.research.reading.understanding_fingerprint', return_value='fp-2'):
            response = self.client().get('/api/v2/paper/sections', params={'paper': PAPER_ID})
        reading = response.json()['reading']
        self.assertEqual(reading['state'], 'stale')
        self.assertIn('PDF、模型或提示版本已变化，需重新理解', reading['gaps'])


if __name__ == '__main__':
    unittest.main()
