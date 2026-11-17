"""E01/E02：无过滤条件不携带全量行号掩码；有筛选查询的计划与结果边界回归。"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['KB_NO_STARTUP_JOBS'] = '1'
os.environ['KB_NO_REFERENCE_WORKER'] = '1'
from kbapp.storage.store import Store
from kbapp.search.indexing import Search, DiskBM25, build_keyword_index, research_tokens


def corpus_rows():
    rows = []
    for n in range(1, 11):
        pid = f'p{n % 2 + 1}'
        rows.append({'id': f'metadata:p{n % 2 + 1}', 'paper_id': pid, 'kind': 'metadata', 'title': f'Paper {n % 2 + 1}', 'text': f'Paper {n % 2 + 1} adaptive sampling'})
    for n in range(1, 21):
        pid = f'p{n % 2 + 1}'
        rows.append({'id': f'text:chunk{n}', 'paper_id': pid, 'kind': 'paper_text', 'title': f'Paper {n % 2 + 1}', 'text': f'Chunk {n} about adaptive sampling under static conditions with obstacle fields.'})
    return rows


class SearchMask(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.store.put_papers([{'id': 'p1', 'title': 'Paper 1', 'kind': 'library', 'year': 2021}, {'id': 'p2', 'title': 'Paper 2', 'kind': 'library', 'year': 2022}])
        self.index = self.store.paths.indexes / 'versions' / 'mask-test'
        self.index.mkdir(parents=True)
        self.rows = corpus_rows()
        (self.index / 'corpus.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in self.rows), encoding='utf-8')
        (self.index / 'corpus_meta.json').write_text(json.dumps({'embedding': {'revision': 'test'}, 'sources': len(self.rows), 'vector_ids': [], 'vector_count': 0, 'vector_pending': len(self.rows)}), encoding='utf-8')
        build_keyword_index(self.index, self.rows)
        self.store.set_state('active_version', 'mask-test')

    def bm(self):
        return DiskBM25(self.index / 'corpus-search.sqlite3')

    def test_full_mask_and_no_mask_return_identical_results(self):
        bm = self.bm()
        terms = research_tokens('adaptive sampling')
        masked = bm.search(terms, topk=100, allowed=set(range(len(self.rows))))
        unmasked = bm.search(terms, topk=100, allowed=None)
        self.assertEqual(masked, unmasked)

    def test_unfiltered_search_carries_no_rowid_mask(self):
        captured = []
        original = DiskBM25.search

        def spy(self, tokens, topk=100, allowed=None):
            captured.append(allowed)
            return original(self, tokens, topk, allowed)

        with patch.object(DiskBM25, 'search', spy):
            result = Search(self.store).search('adaptive sampling', k=6, vectors=False)
        self.assertTrue(captured)
        self.assertIsNone(captured[0], '无过滤条件时不应把全量 rowid 列表传给 FTS')
        self.assertTrue(result['results'])

    def test_filtered_search_keeps_mask_and_respects_scope(self):
        captured = []
        original = DiskBM25.search

        def spy(self, tokens, topk=100, allowed=None):
            captured.append(allowed)
            return original(self, tokens, topk, allowed)

        with patch.object(DiskBM25, 'search', spy):
            result = Search(self.store).search('adaptive sampling', k=6, kind='paper_text', vectors=False)
        self.assertTrue(captured)
        self.assertTrue(all(a is not None for a in captured))
        self.assertTrue(all(r['kind'] == 'paper_text' for r in result['results']))
        with patch.object(DiskBM25, 'search', spy):
            single = Search(self.store).search('adaptive sampling', k=6, paper='p1', vectors=False)
        self.assertTrue(single['results'])
        self.assertTrue(all(r['paper_id'] == 'p1' for r in single['results']))

    def test_invalid_document_rows_stay_excluded_without_filters(self):
        rows = self.rows + [{'id': 'text:doc-stale', 'paper_id': 'p1', 'kind': 'paper_text', 'title': 'Stale', 'document_id': 'doc-stale', 'text': 'Stale adaptive sampling block that must stay hidden.'}]
        (self.index / 'corpus.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')
        build_keyword_index(self.index, rows)
        result = Search(self.store).search('stale adaptive sampling', k=20, vectors=False)
        self.assertTrue(result['results'])
        self.assertFalse(any(r['id'] == 'text:doc-stale' for r in result['results']))

    def test_filtered_fts_plan_uses_match_not_rowid_probe(self):
        """E02：带掩码的 FTS 查询计划必须走 MATCH 索引，而不是逐 rowid 全表探测。"""
        db = sqlite3.connect(self.index / 'corpus-search.sqlite3')
        try:
            expression = ' OR '.join(f'"{t}"' for t in research_tokens('adaptive sampling'))
            params = [expression, json.dumps(list(range(len(self.rows)))), 100]
            plan = list(db.execute('EXPLAIN QUERY PLAN SELECT rowid,bm25(docs,1.0,4.0) AS score FROM docs WHERE docs MATCH ? AND rowid IN (SELECT value+1 FROM json_each(?)) ORDER BY score LIMIT ?', params))
            bare = list(db.execute('EXPLAIN QUERY PLAN SELECT rowid FROM docs WHERE rowid IN (SELECT value+1 FROM json_each(?))', [params[1]]))
        finally:
            db.close()
        detail = ' | '.join(str(r[-1]) for r in plan)
        self.assertRegex(detail, r'VIRTUAL TABLE INDEX \d+:=?M\d', 'MATCH 约束必须参与查询计划')
        self.assertIn('LIST SUBQUERY', detail)
        self.assertNotRegex(' | '.join(str(r[-1]) for r in bare), r'VIRTUAL TABLE INDEX \d+:=?M\d', '对照：无 MATCH 时会退化为逐 rowid 探测')


    def test_masked_results_equal_explicit_in_query(self):
        """批量取回过滤与旧 IN 掩码 SQL 结果一致（含稀疏掩码）。"""
        bm = self.bm()
        terms = research_tokens('adaptive sampling')
        for allowed in (set(range(0, len(self.rows), 2)), set(range(3, 12)), {len(self.rows) - 1}):
            db = sqlite3.connect(self.index / 'corpus-search.sqlite3')
            try:
                expression = ' OR '.join(f'"{t}"' for t in terms)
                expected = db.execute('SELECT rowid,bm25(docs,1.0,4.0) AS score FROM docs WHERE docs MATCH ? AND rowid IN (SELECT value+1 FROM json_each(?)) ORDER BY score LIMIT ?', [expression, json.dumps(sorted(allowed)), 6]).fetchall()
            finally:
                db.close()
            actual = bm.search(terms, topk=6, allowed=allowed)
            self.assertEqual([(r[0] - 1, round(-r[1], 9)) for r in expected], [(i, round(s, 9)) for i, s in actual], str(sorted(allowed)[:5]))

    def test_deep_allowed_row_is_found_after_batching(self):
        bm = self.bm()
        terms = research_tokens('adaptive sampling')
        deep = {len(self.rows) - 1}
        hits = bm.search(terms, topk=3, allowed=deep)
        self.assertTrue(hits)
        self.assertEqual(hits[0][0], len(self.rows) - 1)

    def test_papers_cache_follows_paper_revision(self):
        search = Search(self.store)
        first = search.papers_map()
        self.assertIs(first, search.papers_map())
        self.store.patch_paper('p1', {'title': 'Paper 1 renamed'})
        second = search.papers_map()
        self.assertIsNot(first, second)
        self.assertEqual(second['p1']['title'], 'Paper 1 renamed')

    def test_source_token_tracks_revision_and_recognized_files(self):
        search = Search(self.store)
        root = self.store.active_dir()
        token = search.source_token(root)
        self.assertEqual(token, search.source_token(root))
        with self.store.db() as db:
            db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?)', ('doc-token', 'p1', '', 'hash', 'unused', 1, '{}', '2026-11-03'))
        self.assertNotEqual(token, search.source_token(root))
        token = search.source_token(root)
        cache_dir = self.store.paths.cache / 'recognized_sources'
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / 'p1.json').write_text('{}', encoding='utf-8')
        self.assertNotEqual(token, search.source_token(root))



    def test_duplicate_content_is_collapsed_and_paper_level_limits_to_one(self):
        """E07/E08：同页同内容去重；论文级检索每篇只保留最高分段落。"""
        rows = self.rows + [{'id': 'text:dup1', 'paper_id': 'p1', 'kind': 'paper_text', 'title': 'Paper 1', 'page_start': 5, 'text': 'Chunk 1 about adaptive sampling under static conditions with obstacle fields.'}]
        (self.index / 'corpus.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')
        build_keyword_index(self.index, rows)
        result = Search(self.store).search('adaptive sampling', k=10, vectors=False)
        keys = [(r['paper_id'], r.get('page_start'), r.get('text')) for r in result['results']]
        self.assertEqual(len(keys), len(set(keys)), '同页同内容不得重复出现')
        paper_level = Search(self.store).search('adaptive sampling', k=10, vectors=False, level='paper')
        self.assertEqual(len({r['paper_id'] for r in paper_level['results']}), len(paper_level['results']))


if __name__ == '__main__':
    unittest.main()
