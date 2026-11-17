"""C10：元数据冲突工作列表、人工选择与撤销；默认不自动合并。"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['KB_NO_STARTUP_JOBS'] = '1'
os.environ['KB_NO_REFERENCE_WORKER'] = '1'
from kbapp.storage.store import Store
from kbapp.library.conflicts import list_conflicts, duplicates, resolve, undo
from kbapp.library.works import rebuild


class Conflicts(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.store.put_papers([{'id': 'p', 'title': 'AI title', 'kind': 'library', 'metadata_source': 'zotero'},
                               {'id': 'q', 'title': 'Q', 'kind': 'library', 'doi': '10.1234/q', 'year': 2020}])
        with self.store.db() as db:
            db.execute("INSERT INTO metadata_conflicts VALUES('c1','p','title',?,?,?,?,?)",
                       (json.dumps('Zotero title'), json.dumps('OpenAlex title'), 'background_enrichment', 'pending', '2026-11-03'))

    def test_pending_conflicts_list_with_paper_context(self):
        rows = list_conflicts(self.store)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['paper_title'], 'AI title')
        self.assertEqual(rows[0]['proposed_value'], 'OpenAlex title')

    def test_resolve_applies_choice_and_undo_restores(self):
        result = resolve(self.store, 'c1', 'proposed')
        self.assertEqual(result['value'], 'OpenAlex title')
        self.assertEqual(self.store.papers()['p']['title'], 'OpenAlex title')
        self.assertEqual(list_conflicts(self.store), [])
        undone = undo(self.store, 'c1')
        self.assertEqual(undone['restored'], 'AI title')
        self.assertEqual(self.store.papers()['p']['title'], 'AI title')
        self.assertEqual(len(list_conflicts(self.store)), 1)

    def test_duplicates_are_reported_without_merging(self):
        self.store.put_papers([{'id': 'd1', 'title': 'Dup', 'kind': 'library', 'doi': '10.1234/d', 'year': 2021},
                               {'id': 'd2', 'title': 'Dup', 'kind': 'library', 'doi': '10.1234/d', 'year': 2021}])
        rebuild(self.store)
        groups = duplicates(self.store)
        self.assertTrue(any(g['work_id'] == 'doi:10.1234/d' and len(g['papers']) == 2 for g in groups))
        self.assertEqual(sorted(self.store.papers()), ['d1', 'd2', 'p', 'q'], '重复条目不得自动合并')


if __name__ == '__main__':
    unittest.main()
