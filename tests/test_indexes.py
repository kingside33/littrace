"""H06：复合索引存在、被查询计划使用且不改变结果语义。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['KB_NO_STARTUP_JOBS'] = '1'
os.environ['KB_NO_REFERENCE_WORKER'] = '1'
from kbapp.storage.store import Store


class Indexes(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)

    def plan(self, sql, params=()):
        with self.store.db() as db:
            return ' | '.join(str(r[-1]) for r in db.execute('EXPLAIN QUERY PLAN ' + sql, params))

    def test_composite_indexes_exist(self):
        with self.store.db() as db:
            names = {}
            for table in ('relations', 'approvals', 'invalidations', 'research_artifacts'):
                names[table] = {r[1] for r in db.execute('PRAGMA index_list(' + table + ')')}
        self.assertIn('relation_kind_status', names['relations'])
        self.assertIn('approval_status_created', names['approvals'])
        self.assertIn('approval_created', names['approvals'])
        self.assertIn('invalidation_created', names['invalidations'])
        self.assertIn('artifact_type_subject', names['research_artifacts'])

    def test_hot_queries_use_indexes(self):
        self.assertIn('relation_kind_status', self.plan("SELECT payload FROM relations WHERE kind IN ('inherits','improves','combines','transfers') AND status='confirmed'"))
        self.assertIn('approval_created', self.plan('SELECT * FROM approvals ORDER BY created DESC LIMIT 50'))
        self.assertIn('approval_status_created', self.plan("SELECT COUNT(*) FROM approvals WHERE status='pending'"))
        self.assertIn('invalidation_created', self.plan('SELECT * FROM invalidations ORDER BY created DESC LIMIT 10'))

    def test_indexed_queries_return_same_rows(self):
        self.store.put_papers([{'id': 'a', 'title': 'A', 'kind': 'library'}, {'id': 'b', 'title': 'B', 'kind': 'library'}, {'id': 'c', 'title': 'C', 'kind': 'library'}])
        rows = [('b', 'a', 'improves', 'confirmed'), ('c', 'a', 'inherits', 'candidate'), ('c', 'b', 'cites', 'confirmed')]
        for src, dst, kind, status in rows:
            self.store.relation({'src': src, 'dst': dst, 'kind': kind, 'status': status, 'level': 'fulltext', 'evidence': [{'quote': 'x'}]})
        with self.store.db() as db:
            indexed = [r[0] for r in db.execute("SELECT id FROM relations WHERE kind IN ('inherits','improves','combines','transfers') AND status='confirmed'")]
            all_rows = [dict(r) for r in db.execute('SELECT id,kind,status FROM relations')]
        expected = [r['id'] for r in all_rows if r['kind'] in {'inherits', 'improves', 'combines', 'transfers'} and r['status'] == 'confirmed']
        self.assertEqual(sorted(indexed), sorted(expected))


if __name__ == '__main__':
    unittest.main()
