"""独立向量补齐与完整更新的行为边界。"""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import kbapp.core.config as C
from kbapp.storage.store import Store
from kbapp.core.io import atomic, digest
from kbapp.search.indexing import build_vectors, Search
from kbapp.workflows.vector_jobs import queue_vectors, run_vectors, recover_vectors
from kbapp.workflows.status import status as workbench_status
os.environ['KB_NO_STARTUP_JOBS'] = '1'
os.environ['KB_NO_REFERENCE_WORKER'] = '1'

class VectorJobs(unittest.TestCase):

    def setUp(self):
        model = patch('kbapp.integrations.model_identity.embedding_signature', return_value={'model': 'fixture-embedding', 'revision': 'fixture-weights', 'dims': 1024, 'version': 2})
        model.start()
        self.addCleanup(model.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.store.put_papers([{'id': 'p', 'kind': 'library', 'title': 'Paper', 'year': 2020}])
        self.source = {'id': 'old', 'source_id': 'old', 'kind': 'paper_text', 'paper_id': 'p', 'text': 'Original evidence', 'text_hash': digest('Original evidence'), 'title': 'Paper'}
        self.root = self.store.paths.indexes / 'versions/published'
        self.root.mkdir(parents=True)
        atomic(self.root / 'corpus.jsonl', json.dumps(self.source) + '\n')
        atomic(self.root / 'corpus_meta.json', {'sources': 1, 'vector_count': 1, 'vector_pending': 0, 'vector_ids': ['old'], 'embedding': __import__('kbapp.integrations.model_identity', fromlist=['embedding_signature']).embedding_signature()})
        self.matrix = np.zeros((1, C.EMBED_DIMS), np.float32)
        self.matrix[0, 0] = 1
        np.save(self.root / 'corpus.npy', self.matrix)
        atomic(self.root / 'chunk_cache.json', {'manual': 'preserve extraction cache'})
        self.store.set_state('active_version', 'published')
        self.store.set_state('coverage', {'sources': 1, 'vector_count': 1, 'vector_pending': 0})

    def extra(self, identity):
        text = 'New evidence ' + identity
        return dict(self.source, id=identity, source_id=identity, text=text, text_hash=digest(text))

    def embedding(self, model, texts, **kwargs):
        self.assertNotIn(self.source['text'], texts)
        matrix = np.zeros((len(texts), C.EMBED_DIMS), np.float32)
        matrix[:, 1] = 1
        return matrix

    def test_only_missing_vectors_are_computed_and_source_caches_are_preserved(self):
        extra = self.extra('novelterm')
        with ExitStack() as stack:
            for target in ('kbapp.workflows.update.sync_library', 'kbapp.workflows.extraction.quality_ocr', 'kbapp.workflows.extraction.extract_stage', 'kbapp.library.network.expand', 'kbapp.library.network.fetch_approved', 'kbapp.integrations.cloud.complete'):
                stack.enter_context(patch(target, side_effect=AssertionError('vector indexing must not perform ' + target)))
            stack.enter_context(patch('kbapp.workflows.vector_jobs.active_sources', return_value=[extra]))
            stack.enter_context(patch('kbapp.workflows.vector_jobs.export_static'))
            inference = stack.enter_context(patch('kbapp.search.indexing.embed_texts', side_effect=self.embedding))
            queued = queue_vectors(self.store)
            report = run_vectors(self.store, queued['id'])
        self.assertTrue(report['published'])
        self.assertEqual(inference.call_count, 1)
        self.assertEqual(inference.call_args.args[1], [extra['text']])
        published = self.store.active_dir()
        np.testing.assert_array_equal(np.load(published / 'corpus.npy')[0], self.matrix[0])
        self.assertEqual(json.loads((published / 'chunk_cache.json').read_text())['manual'], 'preserve extraction cache')
        self.assertTrue((self.root / 'corpus.npy').is_file())
        found = Search(self.store).search('novelterm', vectors=False)
        self.assertIn('novelterm', [r['id'] for r in found['results']])
        self.assertEqual(workbench_status(self.store)['index_health']['vector_count'], 2)

    def test_incomplete_build_preserves_published_version_and_next_run_reuses_finished_cache(self):
        extras = [self.extra('one'), self.extra('two')]
        with patch('kbapp.workflows.vector_jobs.active_sources', return_value=extras), patch('kbapp.workflows.vector_jobs.export_static'), patch('kbapp.search.indexing.embed_texts', side_effect=self.embedding) as inference:
            first = queue_vectors(self.store)
            with patch('kbapp.workflows.vector_jobs.build_vectors', side_effect=lambda store, index, rows, progress: build_vectors(store, index, rows, progress, limit=1)):
                report = run_vectors(self.store, first['id'])
            self.assertFalse(report['published'])
            self.assertEqual(self.store.state('active_version'), 'published')
            self.assertEqual(workbench_status(self.store)['index_health']['vector_count'], 1)
            second = queue_vectors(self.store)
            report = run_vectors(self.store, second['id'])
        self.assertTrue(report['published'])
        self.assertEqual(inference.call_count, 2)
        self.assertEqual([call.args[1] for call in inference.call_args_list], [[extras[0]['text']], [extras[1]['text']]])
        self.assertEqual(workbench_status(self.store)['index_health']['vector_count'], 3)

    def test_failed_or_zero_vectors_do_not_replace_the_published_version(self):
        with patch('kbapp.workflows.vector_jobs.active_sources', return_value=[self.extra('new')]), patch('kbapp.search.indexing.embed_texts', return_value=np.zeros((1, C.EMBED_DIMS), np.float32)), patch('kbapp.workflows.vector_jobs.export_static') as export:
            job = queue_vectors(self.store)
            report = run_vectors(self.store, job['id'])
            self.assertFalse(report['published'])
            export.assert_not_called()
        self.assertEqual(self.store.state('active_version'), 'published')
        with patch('kbapp.workflows.vector_jobs.build_vectors', side_effect=RuntimeError('disk unavailable')):
            job = queue_vectors(self.store)
            with self.assertRaisesRegex(RuntimeError, 'disk unavailable'):
                run_vectors(self.store, job['id'])
        self.assertEqual(self.store.state('active_version'), 'published')
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT status FROM jobs WHERE id=?', (job['id'],)).fetchone()[0], 'failed')

    def test_valid_document_ocr_sources_survive_document_source_filter(self):
        recognized=dict(self.extra('recognized'),document_id='verified-document',origin='paper_ocr')
        with patch('kbapp.workflows.vector_jobs.active_sources',return_value=[recognized]),patch('kbapp.library.documents.sources',return_value=[]),patch('kbapp.workflows.vector_jobs.export_static'),patch('kbapp.search.indexing.embed_texts',side_effect=self.embedding) as inference:
            job=queue_vectors(self.store)
            result=run_vectors(self.store,job['id'])
        self.assertTrue(result['published'])
        self.assertEqual(inference.call_args.args[1],[recognized['text']])
        meta=json.loads((self.store.active_dir()/'corpus_meta.json').read_text())
        self.assertIn('recognized',meta['vector_ids'])
        self.assertEqual(meta['vector_pending'],0)

    def test_full_update_and_duplicate_vector_requests_do_not_start_another_job(self):
        with self.store.db() as db:
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)', ('full', 'update', 'running', os.getpid(), '{}', 'chunk.py', 'today', None, None))
        queued = queue_vectors(self.store)
        self.assertEqual((queued['state'], queued['kind'], queued['id']), ('running', 'update', 'full'))
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='complete' WHERE id='full'")
        first = queue_vectors(self.store)
        second = queue_vectors(self.store)
        self.assertEqual(second['state'], 'running')
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(workbench_status(self.store)['indexing']['kind'], 'vectors')

    def test_interrupted_process_is_recoverable_without_starting_models(self):
        queued = queue_vectors(self.store)
        with self.store.db() as db:
            db.execute('UPDATE jobs SET pid=? WHERE id=?', (99999999, queued['id']))
        target = 'kbapp.core.win_runtime.process_info' if os.name == 'nt' else 'kbapp.workflows.vector_jobs.os.kill'
        options = {'return_value': None} if os.name == 'nt' else {'side_effect': ProcessLookupError}
        with patch(target, **options), patch('kbapp.search.indexing.embed_texts', side_effect=AssertionError('recovery must not start models')):
            recover_vectors(self.store)
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT status FROM jobs WHERE id=?', (queued['id'],)).fetchone()[0], 'interrupted')
        self.assertEqual(queue_vectors(self.store)['state'], 'started')

    def test_jobs_api_uses_distinct_actions_and_requires_session_token(self):
        from fastapi.testclient import TestClient
        from support import service_for
        service = service_for(self.store)
        with patch.object(service, 'store', self.store), patch.object(service, 'start_vectors', return_value={'kind': 'vectors'}) as vectors, patch.object(service, 'start_update', return_value={'kind': 'update'}) as full:
            client = TestClient(service.app)
            self.assertEqual(client.post('/api/jobs', json={'action': 'vectors'}).status_code, 403)
            vectors.assert_not_called()
            headers = {'X-KB-Token': service.TOKEN}
            self.assertEqual(client.post('/api/jobs', headers=headers, json={'action': 'vectors'}).json()['kind'], 'vectors')
            self.assertEqual(client.post('/api/jobs', headers=headers, json={'action': 'update'}).json()['kind'], 'update')
            vectors.assert_called_once()
            full.assert_called_once()
if __name__ == '__main__':
    unittest.main()
