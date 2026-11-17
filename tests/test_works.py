"""C01/C02：论文作品关联层、匹配证据、冲突复核与幂等迁移。"""
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
from kbapp.library.works import rebuild, work_of, work_papers, normalize_title


class Works(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)

    def test_same_doi_entries_share_one_work_without_changing_paper_ids(self):
        self.store.put_papers([
            {'id': 'z:1:AAA', 'title': 'Sampling Method', 'kind': 'library', 'doi': 'https://doi.org/10.1234/ABC', 'year': 2020},
            {'id': 'z:1:BBB', 'title': 'Sampling Method (preprint)', 'kind': 'library', 'doi': '10.1234/abc', 'year': 2020},
        ])
        stats = rebuild(self.store)
        self.assertEqual(stats['multi_paper_works'], 1)
        first, second = work_of(self.store, 'z:1:AAA'), work_of(self.store, 'z:1:BBB')
        self.assertEqual(first['work_id'], 'doi:10.1234/abc')
        self.assertEqual(second['work_id'], first['work_id'])
        self.assertEqual(first['basis'], 'doi')
        self.assertEqual(first['confidence'], 'high')
        self.assertEqual(first['evidence']['doi'], '10.1234/abc')
        self.assertEqual(sorted(work_papers(self.store, first['work_id'])), ['z:1:AAA', 'z:1:BBB'])
        self.assertEqual(sorted(self.store.papers()), ['z:1:AAA', 'z:1:BBB'])

    def test_no_doi_entry_joins_unique_doi_work_by_title_and_year(self):
        self.store.put_papers([
            {'id': 'z:1:AAA', 'title': 'Coverage Path Planning: A Survey', 'kind': 'library', 'doi': '10.1234/cpp', 'year': 2021},
            {'id': 'z:1:CCC', 'title': 'Coverage path planning — a survey', 'kind': 'external', 'year': 2021},
        ])
        stats = rebuild(self.store)
        self.assertEqual(stats['multi_paper_works'], 1)
        linked = work_of(self.store, 'z:1:CCC')
        self.assertEqual(linked['work_id'], 'doi:10.1234/cpp')
        self.assertEqual(linked['basis'], 'title+year')
        self.assertEqual(linked['confidence'], 'medium')
        self.assertEqual(linked['evidence']['matched_work'], 'doi:10.1234/cpp')

    def test_unique_title_only_entry_gets_own_title_work(self):
        self.store.put_papers([{'id': 'z:1:DDD', 'title': 'Unique Underwater Study', 'kind': 'library', 'year': 2019}])
        rebuild(self.store)
        info = work_of(self.store, 'z:1:DDD')
        self.assertTrue(info['work_id'].startswith('work:'))
        self.assertEqual(info['basis'], 'title+year')
        self.assertEqual(normalize_title('Unique-Underwater Study!'), normalize_title('unique underwater study'))

    def test_ambiguous_title_match_enters_review_instead_of_silent_merge(self):
        self.store.put_papers([
            {'id': 'z:1:A1', 'title': 'Dual Publication', 'kind': 'library', 'doi': '10.1234/one', 'year': 2022},
            {'id': 'z:1:A2', 'title': 'Dual Publication', 'kind': 'library', 'doi': '10.1234/two', 'year': 2022},
            {'id': 'z:1:A3', 'title': 'Dual Publication', 'kind': 'external', 'year': 2022},
        ])
        stats = rebuild(self.store)
        self.assertEqual(stats['conflicts'], 1)
        info = work_of(self.store, 'z:1:A3')
        self.assertTrue(info['work_id'].startswith('work:'), '歧义时不得静默并入任一 DOI 作品')
        with self.store.db() as db:
            rows = [dict(r) for r in db.execute("SELECT * FROM metadata_conflicts WHERE paper_id='z:1:A3' AND field='work'")]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['status'], 'pending')
        self.assertEqual(sorted(json.loads(rows[0]['authoritative'])), ['doi:10.1234/one', 'doi:10.1234/two'])

    def test_rebuild_is_idempotent_and_bumps_paper_revision(self):
        self.store.put_papers([
            {'id': 'z:1:AAA', 'title': 'Same', 'kind': 'library', 'doi': '10.1234/same', 'year': 2020},
            {'id': 'z:1:BBB', 'title': 'Same', 'kind': 'library', 'doi': '10.1234/same', 'year': 2020},
        ])
        before = self.store.state('paper_revision', 0)
        first = rebuild(self.store)
        second = rebuild(self.store)
        self.assertEqual(first, second)
        self.assertGreater(self.store.state('paper_revision', 0), before)
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM works').fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM paper_works').fetchone()[0], 2)



    def test_version_types_and_ordering_within_work(self):
        """C03：同一作品的预印本/会议版/期刊版建立版本关系，各自结论保留。"""
        from kbapp.library.works import classify_version, versions_of
        self.assertEqual(classify_version({'title': 'A Method (Preprint)'})[0], 'preprint')
        self.assertEqual(classify_version({'title': 'A Method', 'venue': 'Proceedings of the IEEE ICRA Conference'})[0], 'conference')
        self.assertEqual(classify_version({'title': 'A Method', 'venue': 'Journal of Field Robotics'})[0], 'journal')
        self.assertEqual(classify_version({'title': 'A Method'})[0], 'unknown')
        self.store.put_papers([
            {'id': 'z:1:PRE', 'title': 'A Method (preprint)', 'kind': 'library', 'doi': '10.1234/m', 'year': 2021, 'abstract': 'preprint result 1'},
            {'id': 'z:1:CONF', 'title': 'A Method', 'kind': 'library', 'doi': '10.1234/m', 'year': 2022, 'venue': 'Proceedings of ICRA Conference', 'abstract': 'conference result 2'},
            {'id': 'z:1:JOUR', 'title': 'A Method', 'kind': 'library', 'doi': '10.1234/m', 'year': 2023, 'venue': 'Journal of Field Robotics', 'abstract': 'journal result 3'},
        ])
        rebuild(self.store)
        versions = versions_of(self.store, 'doi:10.1234/m')
        self.assertEqual([v['version'] for v in versions], ['preprint', 'conference', 'journal'])
        self.assertEqual(versions[1]['venue'], 'Proceedings of ICRA Conference')
        papers = self.store.papers()
        self.assertEqual({papers[v['paper_id']]['abstract'] for v in versions}, {'preprint result 1', 'conference result 2', 'journal result 3'})


if __name__ == '__main__':
    unittest.main()
