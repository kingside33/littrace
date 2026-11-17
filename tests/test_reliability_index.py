"""Isolated reliability regressions: real files/SQLite, no network or model execution."""

from contextlib import ExitStack, closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import kbapp.core.config as C
from kbapp.core.chunking import est_tokens
from kbapp.core.io import atomic, digest, jsonl, read_json
from kbapp.storage.store import Store
from kbapp.search import indexing
from kbapp.integrations import embedding
from kbapp.search import query as query_module


class ReliabilityIndex(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='index-regression-')
        self.addCleanup(self.temp.cleanup)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(C, 'EMBED_DIMS', 4))
        self.signature = {
            'model': C.EMBED_MODEL,
            'revision': 'mock-weights',
            'dims': 4,
            'version': 2,
        }
        self.stack.enter_context(
            patch(
                'kbapp.integrations.model_identity.embedding_signature', return_value=self.signature
            )
        )
        self.stack.enter_context(patch('kbapp.integrations.models.cancelled', return_value=False))
        self.stack.enter_context(
            patch(
                'kbapp.integrations.models.prepare_local',
                side_effect=AssertionError('Real model forbidden'),
            )
        )
        self.stack.enter_context(
            patch(
                'kbapp.integrations.embedding.http_post',
                side_effect=AssertionError('Network forbidden'),
            )
        )
        self.store = Store(self.temp.name)
        self.store.put_papers(
            [
                {'id': 'p', 'kind': 'library', 'title': 'Paper', 'citekey': 'Paper', 'year': 2020},
                {'id': 'other', 'kind': 'library', 'title': 'Other'},
            ]
        )
        self.root = self.store.paths.indexes / 'versions' / 'published'
        self.root.mkdir(parents=True)
        self.store.set_state('active_version', 'published')

    def source(self, identity, text='adaptive evidence', **fields):
        return dict(
            {
                'id': identity,
                'source_id': identity,
                'paper_id': 'p',
                'kind': 'paper_text',
                'origin': 'paper',
                'text': text,
                'text_hash': digest(text),
                'title': 'Paper',
                'page_start': 3,
                'page_end': 3,
            },
            **fields,
        )

    def publish(self, rows, vectors=None):
        atomic(self.root / 'corpus.jsonl', ''.join(json.dumps(r) + '\n' for r in rows))
        matrix = (
            np.zeros((len(rows), 4), np.float32)
            if vectors is None
            else np.asarray(vectors, np.float32)
        )
        np.save(self.root / 'corpus.npy', matrix)
        ready = [] if vectors is None else [r['id'] for r in rows]
        atomic(
            self.root / 'corpus_meta.json',
            {
                'embedding': self.signature,
                'sources': len(rows),
                'vector_ids': ready,
                'vector_count': len(ready),
                'vector_pending': len(rows) - len(ready),
            },
        )
        indexing.build_keyword_index(self.root, rows)

    def build(self, rows, embed, limit=None):
        target = self.root.parent / 'candidate'
        target.mkdir(exist_ok=True)
        with patch('kbapp.search.indexing.embed_texts', side_effect=embed):
            warnings = indexing.build_vectors(self.store, target, rows, limit=limit)
        return target, read_json(target / 'corpus_meta.json'), warnings

    def vectors(self, model, texts, **kwargs):
        return [[0.0, 1.0, 0.0, 0.0] for text in texts]

    def test_documents_and_active_sources_share_budget_and_provenance(self):
        long = '研究甲乙丙丁。' * 600
        sources = [
            self.source(
                'doc-long',
                long,
                document_id='document',
                source_id='original-source',
                hash='document-hash',
            ),
            self.source('ocr-long', long + '尾部标记', document_id='ocr-document'),
            self.source('doc-short', 'short evidence', document_id='document'),
        ]
        with (
            patch('kbapp.library.documents.sources', return_value=[sources[0], sources[2]]),
            patch('kbapp.library.recognition.active_sources', return_value=[sources[1]]),
            patch('kbapp.search.indexing.note_sources', return_value=[]),
            patch('kbapp.search.indexing.approved_papers', return_value=[]),
        ):
            rows = indexing.build_corpus(self.store, self.root, [], [])
        self.assertTrue(
            all(est_tokens(r['text']) <= C.CHUNK_MAX_TOKENS for r in rows),
            'documents and OCR must not bypass budget',
        )
        self.assertEqual(
            next(r for r in rows if r['id'] == 'doc-short')['text_hash'], sources[2]['text_hash']
        )
        parts = [r for r in rows if r.get('parent_id') == 'doc-long']
        self.assertGreater(len(parts), 1)
        self.assertEqual(
            len(parts), len({r['id'] for r in parts}), 'repeated pieces retain distinct identities'
        )
        for part in parts:
            self.assertEqual(
                (
                    part['source_id'],
                    part['document_id'],
                    part['hash'],
                    part['page_start'],
                    part['page_end'],
                ),
                ('original-source', 'document', 'document-hash', 3, 3),
            )
            self.assertEqual(part['source_text_hash'], digest(long))
            self.assertEqual(part['text_hash'], digest(part['text']))
        self.assertIn('尾部标记', ''.join(r['text'] for r in rows))

    def test_normal_legacy_metadata_ids_stay_unchanged(self):
        paper = {
            'id': 'p',
            'title': 'Paper',
            'citekey': 'Paper',
            'authors': [],
            'abstract': 'small abstract',
        }
        with (
            patch('kbapp.library.documents.sources', return_value=[]),
            patch('kbapp.library.recognition.active_sources', return_value=[]),
            patch('kbapp.search.indexing.note_sources', return_value=[]),
            patch('kbapp.search.indexing.approved_papers', return_value=[]),
        ):
            rows = indexing.build_corpus(self.store, self.root, [paper], [])
        self.assertEqual(rows[0]['id'], 'metadata:p:' + digest('Paper\nsmall abstract')[:16])

    def test_split_ids_resolve_identically_after_live_reload(self):
        source = self.source('doc', '文献安全出处。' * 600, document_id='d')
        with (
            patch('kbapp.library.documents.sources', return_value=[source]),
            patch('kbapp.library.recognition.active_sources', return_value=[]),
            patch('kbapp.search.indexing.note_sources', return_value=[]),
            patch('kbapp.search.indexing.approved_papers', return_value=[]),
        ):
            rows = indexing.build_corpus(self.store, self.root, [], [])
        self.publish(rows)
        engine = indexing.Search(self.store)
        self.addCleanup(
            lambda: (
                getattr(getattr(engine, 'mat', None), '_mmap', None) and engine.mat._mmap.close()
            )
        )
        engine.source_snapshot = ('snapshot', [source], self.store.papers())
        engine.load()
        self.assertGreater(len(rows), 1, 'long live sources need chunk identities')
        self.assertEqual(len(engine.corpus), len(rows))
        self.assertFalse(engine.invalid_documents)
        self.assertEqual(set(engine.by_id), {r['id'] for r in rows})
        self.assertEqual(set(engine.source_parts['doc']), set(engine.by_id))
        self.assertTrue(engine.search('文献安全', vectors=False)['results'])

    def test_changed_live_content_does_not_use_old_fts_or_vector(self):
        old = self.source('doc', 'obsolete needle', document_id='d')
        self.publish([old], [[1, 0, 0, 0]])
        live = self.source('doc', 'fresh evidence', document_id='d')
        engine = indexing.Search(self.store)
        self.addCleanup(
            lambda: (
                getattr(getattr(engine, 'mat', None), '_mmap', None) and engine.mat._mmap.close()
            )
        )
        engine.source_snapshot = ('changed', [live], self.store.papers())
        with patch(
            'kbapp.search.query.query_vector',
            return_value=(np.array([1, 0, 0, 0], np.float32), 'ready'),
        ) as inference:
            stale = engine.search('obsolete needle')
        self.assertEqual(stale['results'], [], 'old FTS/vector rows must not rank replacement text')
        inference.assert_not_called()
        self.assertEqual(
            engine.search('fresh evidence', vectors=False)['results'][0]['text'], live['text']
        )

    def test_legacy_oversized_vectors_invalidated_without_recomputing_normal(self):
        short = self.source('short', 'normal evidence')
        long = self.source('oversized', '超长全文尾巴' * 900)
        self.publish([short, long], [[1, 0, 0, 0], [0, 0, 1, 0]])
        rows = [short, long]
        calls = []

        def embed(model, texts, **kwargs):
            calls.extend(texts)
            return self.vectors(model, texts)

        target, meta, warnings = self.build(rows, embed)
        self.assertGreater(len(rows), 2, 'vector-only rebuild must migrate oversized rows too')
        self.assertNotIn(short['text'], calls)
        self.assertTrue(all(est_tokens(text) <= C.CHUNK_MAX_TOKENS for text in calls))
        self.assertNotIn('oversized', meta['vector_ids'])
        self.assertEqual(jsonl(target / 'corpus.jsonl'), rows)
        np.testing.assert_array_equal(np.load(target / 'corpus.npy')[0], [1, 0, 0, 0])
        self.assertEqual(
            meta['embedding'], self.signature, 'chunk policy must not change model namespace'
        )
        self.assertFalse(meta['embedding_policy']['truncate'])
        self.assertEqual(meta['vector_coverage']['short']['status'], 'legacy_budget_checked')
        self.assertTrue(
            all(meta['vector_coverage'][r['id']]['status'] == 'full_input' for r in rows[1:])
        )
        self.assertFalse(warnings)

    def test_failed_batch_isolates_one_bad_item_and_resume_reuses_successes(self):
        rows = [
            self.source('good-a', 'good alpha'),
            self.source('bad', 'bad context'),
            self.source('good-b', 'good beta'),
        ]
        self.publish([self.source('unrelated')])

        def flaky(model, texts, **kwargs):
            if 'bad context' in texts:
                raise ValueError('input length exceeds context length')
            return self.vectors(model, texts)

        target, meta, warnings = self.build(rows, flaky)
        self.assertEqual(
            set(meta['vector_ids']), {'good-a', 'good-b'}, 'one bad row must not stop the batch'
        )
        self.assertEqual(meta['vector_pending'], 1)
        self.assertEqual(meta['vector_coverage']['bad']['status'], 'failed')
        self.assertIn('context', meta['vector_coverage']['bad']['reason'])
        self.assertTrue(warnings)
        calls = []

        def recovered(model, texts, **kwargs):
            calls.extend(texts)
            return self.vectors(model, texts)

        _, meta, _ = self.build(rows, recovered)
        self.assertEqual(calls, ['bad context'])
        self.assertEqual(meta['vector_pending'], 0)

    def test_invalid_vector_row_does_not_discard_valid_siblings(self):
        rows = [self.source('valid', 'valid row'), self.source('zero', 'zero row')]
        self.publish([self.source('unrelated')])

        def embed(model, texts, **kwargs):
            return [[0, 0, 0, 0] if text == 'zero row' else [1, 0, 0, 0] for text in texts]

        _, meta, _ = self.build(rows, embed)
        self.assertEqual(meta['vector_ids'], ['valid'])

    def test_split_long_space_boundary_still_obeys_hard_budget(self):
        from kbapp.search.budget import split_source

        source = self.source('boundary', 'a' * (C.CHUNK_MAX_TOKENS * 3 + 2) + ' ' + 'b' * 100)
        rows = split_source(source)
        self.assertGreater(len(rows), 1)
        self.assertTrue(
            all(est_tokens(row['text']) <= C.CHUNK_MAX_TOKENS for row in rows),
            'split_long can advance one space beyond the token budget',
        )
        self.assertEqual(
            ''.join(row['text'] for row in rows).replace(' ', ''), source['text'].replace(' ', '')
        )
        self.assertEqual(split_source(rows[0]), [rows[0]], 'normalization must be idempotent')

    def test_normal_legacy_split_piece_vector_is_not_invalidated_by_parent_metadata(self):
        from kbapp.search.budget import split_source

        source = self.source('metadata-parent', '已有正常块。' * 500)
        rows = split_source(source, legacy_ids=True)
        old_rows = [
            {
                key: value
                for key, value in row.items()
                if key
                not in {'parent_id', 'source_text_hash', 'part_index', 'part_count', 'chunk_policy'}
            }
            for row in rows
        ]
        self.publish(old_rows, np.tile([1, 0, 0, 0], (len(rows), 1)))
        with patch(
            'kbapp.search.indexing.embed_texts',
            side_effect=AssertionError('existing normal split pieces must be reused'),
        ) as embed:
            target = self.root.parent / 'candidate'
            target.mkdir()
            indexing.build_vectors(self.store, target, rows)
        embed.assert_not_called()
        self.assertEqual(read_json(target / 'corpus_meta.json')['vector_pending'], 0)

    def test_legacy_oversized_non_document_source_is_split_in_live_view(self):
        source = self.source('legacy-long', '现场关键词。' * 500)
        self.publish([source], [[1, 0, 0, 0]])
        engine = indexing.Search(self.store)
        self.addCleanup(
            lambda: (
                getattr(getattr(engine, 'mat', None), '_mmap', None) and engine.mat._mmap.close()
            )
        )
        engine.source_snapshot = ('legacy', [], self.store.papers())
        result = engine.search('现场关键词', vectors=False)
        self.assertTrue(result['results'])
        self.assertTrue(
            all(est_tokens(row['text']) <= C.CHUNK_MAX_TOKENS for row in result['results'])
        )
        self.assertNotIn('legacy-long', engine.by_id)
        self.assertTrue(all(row['parent_id'] == 'legacy-long' for row in result['results']))

    def test_fts_failed_atomic_replace_resumes_completed_database(self):
        rows = [self.source('new', 'new replacement')]
        self.publish([self.source('old', 'old published')])
        before = (self.root / 'corpus-search.sqlite3').read_bytes()
        with patch.object(Path, 'replace', side_effect=PermissionError('published index is busy')):
            with self.assertRaises(PermissionError):
                indexing.build_keyword_index(self.root, rows)
        self.assertEqual((self.root / 'corpus-search.sqlite3').read_bytes(), before)
        with patch.object(
            indexing,
            'research_tokens',
            side_effect=AssertionError('completed FTS must not be rebuilt'),
        ):
            indexing.build_keyword_index(self.root, rows)
        self.assertTrue(
            indexing.DiskBM25(self.root / 'corpus-search.sqlite3').search(['replacement'])
        )

    def test_same_directory_resume_never_relabels_previous_matrix_rows(self):
        first = self.source('first', 'first unique evidence')
        second = self.source('second', 'second unique evidence')
        self.publish([first, second], [[1, 0, 0, 0], [0, 1, 0, 0]])
        rows = [second, first]
        with patch(
            'kbapp.search.indexing.embed_texts',
            side_effect=AssertionError('normal vectors must be reused'),
        ):
            indexing.build_vectors(self.store, self.root, rows)
        np.testing.assert_array_equal(
            np.load(self.root / 'corpus.npy'), [[0, 1, 0, 0], [1, 0, 0, 0]]
        )

    def test_query_budget_counts_instruction_prefix(self):
        query = 'a' * (C.CHUNK_MAX_TOKENS * 3)
        with patch(
            'kbapp.integrations.embedding.embed_texts', return_value=[[1, 0, 0, 0]]
        ) as embed:
            value, status = query_module.query_vector(self.store, query, wait=True)
        self.assertEqual((value, status), (None, 'too_long'))
        embed.assert_not_called()

    def test_embedding_never_requests_silent_truncation(self):
        with patch(
            'kbapp.integrations.embedding.http_post', return_value={'embeddings': [[1, 0, 0, 0]]}
        ) as post:
            embedding._embed_texts(C.EMBED_MODEL, ['short'])
        self.assertIs(post.call_args.args[1]['truncate'], False)

    def test_server_context_rejection_explains_failure_without_truncation(self):
        from io import BytesIO
        from urllib.error import HTTPError

        error = HTTPError(
            'http://mock.invalid/api/embed',
            400,
            'Bad Request',
            {},
            BytesIO(b'{"error":"input length exceeds the context length"}'),
        )
        with patch('kbapp.integrations.embedding.http_post', side_effect=error):
            with self.assertRaises(Exception) as raised:
                embedding._embed_texts(C.EMBED_MODEL, ['apparently short but tokenizer-dependent'])
        self.assertIsInstance(raised.exception, ValueError)
        self.assertIn('context length', str(raised.exception))

    def test_search_long_query_reports_explicit_keyword_fallback(self):
        self.publish([self.source('s', 'query evidence')], [[1, 0, 0, 0]])
        engine = indexing.Search(self.store)
        self.addCleanup(
            lambda: (
                getattr(getattr(engine, 'mat', None), '_mmap', None) and engine.mat._mmap.close()
            )
        )
        engine.source_snapshot = ('long-query', [], self.store.papers())
        with patch(
            'kbapp.integrations.embedding.embed_texts',
            side_effect=AssertionError('long query must not call model'),
        ):
            result = engine.search('超长问题' * 2000)
        self.assertEqual(result['vector_status'], 'too_long')
        self.assertIn('未截断', result['warning'])
        self.assertEqual(result['mode'], 'bm25')

    def test_oversized_embedding_rejected_before_network_or_model_slot(self):
        with patch(
            'kbapp.integrations.embedding.http_post', return_value={'embeddings': [[1, 0, 0, 0]]}
        ) as post:
            with self.assertRaisesRegex(ValueError, '(budget|预算)'):
                embedding._embed_texts(C.EMBED_MODEL, ['超长内容' * 2000])
        post.assert_not_called()
        with patch('kbapp.integrations.scheduling.model_slot') as slot:
            with self.assertRaisesRegex(ValueError, '(budget|预算)'):
                embedding.embed_texts(C.EMBED_MODEL, ['超长内容' * 2000], store=self.store)
        slot.assert_not_called()

    def test_long_query_never_uses_previously_truncated_cache(self):
        query = '超长检索' * 2000
        signature = json.dumps(
            [self.signature, query_module.INSTRUCTION_VERSION],
            sort_keys=True,
            separators=(',', ':'),
        )
        key = digest(signature + '\n' + query)
        with self.store.db() as db:
            db.execute(
                'INSERT INTO query_vectors VALUES(?,?,?,?,?,?)',
                (key, signature, '[1,0,0,0]', 'ready', '', 'today'),
            )
        with patch(
            'kbapp.integrations.embedding.embed_texts', return_value=[[1, 0, 0, 0]]
        ) as embed:
            value, status = query_module.query_vector(self.store, query, wait=True)
        self.assertIsNone(value, 'oversized cached query was possibly truncated')
        self.assertEqual(status, 'too_long')
        embed.assert_not_called()

    def interrupted_fts(self, rows):
        original = indexing.research_tokens

        def fail(text):
            if text == rows[501]['text']:
                raise RuntimeError('simulated interruption')
            return original(text)

        with patch.object(indexing, 'research_tokens', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'simulated interruption'):
                indexing.build_keyword_index(self.root, rows)

    def test_fts_checkpoint_commits_batches_and_resumes_without_retokenizing(self):
        rows = [self.source(str(i), f'recordnumber{i} adaptive evidence') for i in range(1005)]
        self.publish([self.source('old', 'publishedold')])
        before = (self.root / 'corpus-search.sqlite3').read_bytes()
        self.interrupted_fts(rows)
        self.assertEqual((self.root / 'corpus-search.sqlite3').read_bytes(), before)
        tmp = self.root / 'corpus-search.building.sqlite3'
        with closing(sqlite3.connect(tmp)) as db:
            self.assertEqual(
                db.execute('SELECT COUNT(*) FROM docs').fetchone()[0],
                500,
                'committed prefix must survive failure',
            )
            self.assertEqual(
                db.execute('SELECT last_rowid FROM build_checkpoint').fetchone()[0], 500
            )
        calls = []
        original = indexing.research_tokens

        def track(text):
            calls.append(text)
            return original(text)

        with patch.object(indexing, 'research_tokens', side_effect=track):
            indexing.build_keyword_index(self.root, rows)
        self.assertNotIn(rows[0]['text'], calls)
        self.assertEqual(sum(text.startswith('recordnumber') for text in calls), 505)
        self.assertFalse(tmp.exists())
        with closing(sqlite3.connect(self.root / 'corpus-search.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM docs').fetchone()[0], 1005)
            self.assertEqual(
                db.execute('SELECT last_rowid,complete FROM build_checkpoint').fetchone(), (1005, 1)
            )
        self.assertEqual(
            indexing.DiskBM25(self.root / 'corpus-search.sqlite3').search(['recordnumber1004'])[0][
                0
            ],
            1004,
        )

    def test_fts_resume_fingerprint_covers_content_and_token_strategy(self):
        rows = [self.source(str(i), f'itemnumber{i} adaptive') for i in range(1005)]
        for change in ('content', 'strategy'):
            self.interrupted_fts(rows)
            if change == 'content':
                rows[0] = dict(rows[0], text='replacementmarker')
            calls = []
            original = indexing.research_tokens

            def track(text):
                calls.append(text)
                return original(text)

            with (
                patch.object(indexing, 'research_tokens', side_effect=track),
                patch.object(
                    indexing,
                    'KEYWORD_POLICY_VERSION',
                    'changed-strategy'
                    if change == 'strategy'
                    else getattr(indexing, 'KEYWORD_POLICY_VERSION', 'missing'),
                    create=True,
                ),
            ):
                indexing.build_keyword_index(self.root, rows)
            self.assertIn(rows[0]['text'], calls)
            with closing(sqlite3.connect(self.root / 'corpus-search.sqlite3')) as db:
                self.assertTrue(
                    db.execute(
                        "SELECT name FROM sqlite_master WHERE name='build_checkpoint'"
                    ).fetchone(),
                    'strategy and corpus fingerprint must be persisted',
                )

    def test_microprofile_warm_unfiltered_search_reuses_valid_scope_and_skips_vectors(self):
        import cProfile
        import pstats
        from io import StringIO

        class CountedList(list):
            visits = 0

            def __iter__(self):
                for value in super().__iter__():
                    self.visits += 1
                    yield value

        rows = [
            self.source(f'normal-{i}', f'xy evidence {i}', paper_id='p' if i % 2 else 'other')
            for i in range(2500)
        ]
        rows += [self.source('invalid-document', 'xy invalid evidence', document_id='missing')]
        rows += [self.source('long-parent', '超长安全。' * 500)]
        self.publish(rows, np.tile([1, 0, 0, 0], (len(rows), 1)))
        engine = indexing.Search(self.store)
        self.addCleanup(
            lambda: (
                getattr(getattr(engine, 'mat', None), '_mmap', None) and engine.mat._mmap.close()
            )
        )
        papers = self.store.papers()
        engine.source_snapshot = ('profile-1', [], papers)
        expected = engine.search('xy', vectors=False)
        self.assertTrue(engine.invalid_rows)
        engine.corpus = CountedList(engine.corpus)
        ids = CountedList(engine.meta['vector_ids'])
        engine.meta['vector_ids'] = ids
        scopes = []
        original = indexing.DiskBM25.search

        def capture(bm, terms, topk=100, allowed=None):
            scopes.append(allowed)
            return original(bm, terms, topk, allowed)

        profile = cProfile.Profile()
        with patch.object(indexing.DiskBM25, 'search', capture):
            profile.enable()
            found = engine.search('xy', vectors=False)
            repeated = engine.search('xy', vectors=False)
            profile.disable()
        stream = StringIO()
        pstats.Stats(profile, stream=stream).strip_dirs().sort_stats('cumulative').print_stats(12)
        print('WARM-SCOPE MICROPROFILE\n' + stream.getvalue())
        print(
            f'WARM-SCOPE COUNTS corpus_iter={engine.corpus.visits} vector_id_iter={ids.visits} first_scope={type(scopes[0]).__name__} filter_ms={repeated["timings_ms"]["filter"]}'
        )
        self.assertEqual(found['results'], expected['results'])
        self.assertEqual(repeated['results'], expected['results'])
        self.assertFalse(
            any(r['id'] in {'invalid-document', 'long-parent'} for r in found['results'])
        )
        self.assertEqual(
            engine.corpus.visits,
            0,
            'no user filter: warm search must reuse revision-bound valid row mask',
        )
        self.assertEqual(ids.visits, 0, 'disabled vectors must not materialize vector IDs or rows')
        self.assertIsNone(
            scopes[0], 'unfiltered FTS uses sparse exclusions instead of passing all valid rowids'
        )
        # A changed live snapshot must invalidate the cached scope, not trust it.
        live = self.source('invalid-document', 'xy now valid evidence', document_id='missing')
        engine.mat._mmap.close()
        engine.source_snapshot = ('profile-2', [live], papers)
        engine.load()
        self.assertIn('invalid-document', engine.by_id)
        self.assertTrue(engine.search('xy now valid', document='missing', vectors=False)['results'])

    def test_budget_fast_path_matches_estimator_without_python_character_scan(self):
        import cProfile
        import pstats
        from io import StringIO

        rng = np.random.default_rng(7)
        texts = ['', 'a' * 2306, 'a' * 2307, '研' * 512, '研' * 513, 'é' * 2306, '😀' * 2306]
        texts += [
            ''.join(rng.choice(list('a研。😀_ '), size=int(n)))
            for n in rng.integers(0, 2600, size=120)
        ]
        expected = [est_tokens(text) <= C.CHUNK_MAX_TOKENS for text in texts]
        profile = cProfile.Profile()
        with patch.object(embedding, 'est_tokens', wraps=est_tokens) as estimate:
            profile.enable()
            actual = [embedding.within_budget(text) for text in texts]
            profile.disable()
        stream = StringIO()
        pstats.Stats(profile, stream=stream).strip_dirs().sort_stats('cumulative').print_stats(6)
        print('BUDGET MICROPROFILE\n' + stream.getvalue())
        self.assertEqual(actual, expected)
        self.assertEqual(
            estimate.call_count,
            0,
            'cold load must avoid per-character Python estimator for every source',
        )

    def test_vector_topk_matches_full_sort_with_deterministic_boundary_ties(self):
        self.assertTrue(hasattr(indexing, 'vector_topk'), 'exact partial top-k helper is missing')
        rng = np.random.default_rng(42)
        scores = rng.integers(-3, 8, size=5000).astype(np.float32)
        scores[17] = np.nan
        for allowed in (set(range(5000)), set(range(1301, 5000, 17)), {4999}, set()):
            for k in (0, 1, 7, 100, 6000):
                expected = sorted(
                    (i for i in allowed if scores[i] > 0), key=lambda i: (-float(scores[i]), i)
                )[:k]
                self.assertEqual(indexing.vector_topk(scores, allowed, k), expected)

    def test_search_timings_and_empty_or_sparse_scope_never_leak(self):
        rows = [
            self.source(f's{i}', f'adaptive evidence {i}', paper_id='p' if i == 999 else 'other')
            for i in range(1000)
        ]
        matrix = np.tile([1, 0, 0, 0], (1000, 1)).astype(np.float32)
        self.publish(rows, matrix)
        engine = indexing.Search(self.store)
        self.addCleanup(
            lambda: (
                getattr(getattr(engine, 'mat', None), '_mmap', None) and engine.mat._mmap.close()
            )
        )
        engine.source_snapshot = ('timing', [], self.store.papers())
        with patch(
            'kbapp.search.query.query_vector',
            return_value=(np.array([1, 0, 0, 0], np.float32), 'ready'),
        ):
            found = engine.search('adaptive', paper_ids={'p'})
            empty = engine.search('adaptive', paper_ids=set())
        self.assertTrue(found['results'])
        self.assertTrue(all(r['paper_id'] == 'p' for r in found['results']))
        self.assertEqual(empty['results'], [], 'explicit empty scope must not mean all papers')
        self.assertIn('timings_ms', found)
        required = {'load', 'filter', 'fts', 'query_vector', 'dot', 'ranking', 'total'}
        self.assertTrue(required.issubset(found['timings_ms']))
        self.assertTrue(all(value >= 0 for value in found['timings_ms'].values()))
        self.assertAlmostEqual(
            sum(v for key, v in found['timings_ms'].items() if key != 'total'),
            found['timings_ms']['total'],
            delta=0.05,
        )


if __name__ == '__main__':
    unittest.main()
