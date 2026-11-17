"""向量任务发布统计与长块分片必须使用同一来源集合。"""
import json
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from kbapp.core.io import atomic, digest
from kbapp.storage.store import Store
from kbapp.workflows.vector_jobs import queue_vectors, run_vectors


class ReliabilityVectorJob(unittest.TestCase):
    def test_published_coverage_counts_split_sources_and_reuses_them(self):
        self.check_source(False)

    def test_document_child_survives_repeated_vector_publication(self):
        self.check_source(True)

    def check_source(self, document):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(temp)
            store.put_papers([{'id': 'p', 'kind': 'library', 'title': 'Paper'}])
            root = store.paths.indexes / 'versions/published'
            root.mkdir(parents=True)
            text = 'Long table evidence with all rows preserved. ' * 260
            parent = {'id': 'long', 'source_id': 'long', 'paper_id': 'p', 'kind': 'figure', 'text': text, 'text_hash': digest(text), 'title': 'Paper'}
            if document:
                parent['document_id'] = 'attachment'
            atomic(root / 'corpus.jsonl', json.dumps(parent) + '\n')
            atomic(root / 'corpus_meta.json', {'sources': 1, 'vector_ids': [], 'vector_count': 0, 'vector_pending': 1})
            store.set_state('active_version', 'published')
            def embed(model, texts, **kwargs):
                matrix = np.zeros((len(texts), 1024), dtype=np.float32)
                matrix[:, 0] = 1
                return matrix
            with patch('kbapp.integrations.model_identity.embedding_signature', return_value={'model': 'fixture', 'revision': 'known', 'dims': 1024}), patch('kbapp.search.indexing.embed_texts', side_effect=embed) as inference, patch('kbapp.workflows.vector_jobs.export_static'), patch('kbapp.library.documents.sources', return_value=[parent] if document else []):
                report = run_vectors(store, queue_vectors(store)['id'])
                meta = json.loads((store.active_dir() / 'corpus_meta.json').read_text(encoding='utf-8'))
                self.assertTrue(report['published'])
                self.assertGreater(meta['sources'], 1)
                self.assertEqual(report['coverage']['sources'], meta['sources'])
                calls = inference.call_count
                second = run_vectors(store, queue_vectors(store)['id'])
                self.assertTrue(second['published'])
                self.assertEqual(inference.call_count, calls)
                self.assertEqual(second['coverage']['sources'], meta['sources'])


if __name__ == '__main__':
    unittest.main()
