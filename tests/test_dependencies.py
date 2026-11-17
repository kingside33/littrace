"""C07：来源变化到索引、关系等下游产物的依赖失效记录。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['KB_NO_STARTUP_JOBS'] = '1'
os.environ['KB_NO_REFERENCE_WORKER'] = '1'
from kbapp.storage.store import Store
from kbapp.research.dependencies import record, recent, resolve


class Dependencies(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)

    def test_record_is_idempotent_and_kinds_are_validated(self):
        first = record(self.store, 'relation', 'r1', '原文来源已变化', ['s1'])
        second = record(self.store, 'relation', 'r1', '原文来源已变化', ['s1'])
        self.assertEqual(first, second)
        self.assertEqual(len(recent(self.store)), 1)
        with self.assertRaises(ValueError):
            record(self.store, 'unknown', 'x', 'bad')

    def test_resolve_marks_record_without_erasing_reason(self):
        rid = record(self.store, 'summary', 'topic-1', '新证据到来，总结需更新')
        resolve(self.store, rid)
        row = recent(self.store)[0]
        self.assertEqual(row['status'], 'resolved')
        self.assertIn('新证据', row['reason'])
        resolve(self.store, rid, '已重新生成')
        self.assertEqual(recent(self.store)[0]['reason'], '已重新生成')

    def test_mark_stale_records_relation_invalidation(self):
        from kbapp.research.verified import mark_stale
        self.store.put_papers([{'id': 'p', 'title': 'P', 'kind': 'library'}, {'id': 'q', 'title': 'Q', 'kind': 'library'}])
        self.store.relation({'src': 'p', 'dst': 'q', 'kind': 'improves', 'status': 'confirmed', 'level': 'fulltext',
                             'reviewed_by': 'm', 'evidence': [{'source_id': 'missing-source', 'quote': 'x'}]})
        index = Path(self.temp.name) / '_index' / 'versions' / 'v1'
        index.mkdir(parents=True)
        (index / 'corpus.jsonl').write_text('', encoding='utf-8')
        mark_stale(self.store, index)
        rows = recent(self.store)
        self.assertTrue(any(r['kind'] == 'relation' and 'missing-source' in r['affected'] for r in rows))
        saved = self.store.relations()[0]
        self.assertTrue(saved.get('evidence_stale'))


if __name__ == '__main__':
    unittest.main()
