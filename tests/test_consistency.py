"""C09：列与 JSON 载荷一致性检查（唯一权威约定）。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['KB_NO_STARTUP_JOBS'] = '1'
os.environ['KB_NO_REFERENCE_WORKER'] = '1'
from kbapp.storage.store import Store
from kbapp.workflows.consistency import check


class Consistency(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.store.put_papers([{'id': 'p', 'title': 'P', 'kind': 'library'}, {'id': 'q', 'title': 'Q', 'kind': 'external'}])
        self.store.relation({'src': 'p', 'dst': 'q', 'kind': 'improves', 'status': 'candidate', 'level': 'inference', 'evidence': []})

    def test_clean_store_is_consistent_and_declares_authoritative_source(self):
        result = check(self.store)
        self.assertTrue(result['ok'])
        self.assertEqual(result['authoritative']['relations'], 'columns')
        self.assertEqual(result['authoritative']['papers'], 'payload')

    def test_column_payload_mismatch_is_reported(self):
        with self.store.db() as db:
            db.execute("UPDATE relations SET status='confirmed'")
        result = check(self.store)
        self.assertFalse(result['ok'])
        self.assertEqual(result['checks']['relations']['mismatches'], 1)
        self.assertEqual(result['checks']['relations']['samples'][0]['fields']['status'][0], 'candidate')

    def test_library_flag_and_orphan_rows_are_reported(self):
        import sqlite3
        from contextlib import closing
        with self.store.db() as db:
            db.execute("UPDATE papers SET library=0 WHERE id='p'")
        with closing(sqlite3.connect(self.store.path)) as raw:
            raw.execute("INSERT INTO paper_works VALUES('ghost','doi:x','doi','high','{}','now')")
            raw.commit()
        result = check(self.store)
        self.assertEqual(result['checks']['papers']['mismatches'], 1)
        self.assertEqual(result['checks']['paper_works']['mismatches'], 1)
        self.assertFalse(result['ok'])


if __name__ == '__main__':
    unittest.main()
