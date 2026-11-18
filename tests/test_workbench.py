"""验证审批、费用、原文证据、人工内容与版本发布等行为边界。"""

from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import pickle
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pymupdf
import pytest
from kbapp.core.chunking import chunk_paras, est_tokens
from kbapp.library.chunk import page_text
from kbapp.storage.store import Store
from kbapp.core.io import atomic, digest, jsonl
from kbapp.library.sources import approved_papers
from kbapp.workflows.notes import publish_notes
from kbapp.research.rules import validate_ai_relation
from kbapp.research.graph import sync_references, overview, timeline
from kbapp.integrations.cloud import complete
from kbapp.research.relations import ask
from kbapp.core.evidence_rules import fully_supported
from kbapp.library.network import fetch_approved
from kbapp.search.indexing import (
    Search,
    research_tokens,
    build_keyword_index,
    build_corpus,
    build_vectors,
)
from kbapp.search.ranking import BM25
from kbapp.workflows.status import status as workbench_status

os.environ['KB_NO_STARTUP_JOBS'] = '1'
os.environ['KB_NO_REFERENCE_WORKER'] = '1'

# 把 api_key 写进设置会走 DPAPI 密钥保护,该保护只在 Windows 提供(见 kbapp/core/secrets.py)。
# 依赖它的用例在非 Windows 上按平台如实跳过,而不是红灯。
DPAPI_REQUIRED = pytest.mark.skipif(
    os.name != 'nt',
    reason='DPAPI 密钥保护仅 Windows 提供,见 kbapp/core/secrets.py',
)

# 运行时身份判断在生产代码里本身就限定 Windows(见 kbapp/integrations/models.py 的
# _runtime_owned),断言该判断为真的用例只能在其被实现的平台上运行。
WINDOWS_ONLY = pytest.mark.skipif(
    os.name != 'nt',
    reason='运行时身份判断仅 Windows 实现,见 kbapp/integrations/models.py',
)


class Cases(unittest.TestCase):
    def setUp(self):
        model = patch(
            'kbapp.integrations.model_identity.embedding_signature',
            return_value={
                'model': 'fixture-embedding',
                'revision': 'fixture-weights',
                'dims': 1024,
                'version': 2,
            },
        )
        model.start()
        self.addCleanup(model.stop)
        preparation = patch('kbapp.integrations.models.prepare_local')
        preparation.start()
        self.addCleanup(preparation.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.store = Store(self.base)
        self.paper = {
            'id': 'z:1:TEST0001',
            'kind': 'library',
            'citekey': 'Example2020',
            'title': 'Informed RRT*',
            'year': 2020,
            'authors': ['Example, A'],
            'directions': ['02'],
            'primary_direction': '02',
            'card': '_知识库/10_文献/02_研究方向二/Example2020.md',
            'zotero_key': 'TEST0001',
            'attachment_key': 'PDF00001',
            'abstract': 'Test abstract',
            'pdf': None,
            'project': 'project',
            'project_stages': ['阶段二'],
        }
        self.external = {
            'id': 'oa:W123',
            'kind': 'external',
            'title': 'Earlier RRT',
            'year': 2010,
            'abstract': 'Earlier paper',
            'openalex_id': 'W123',
            'oa_url': 'https://example.org/paper.pdf',
        }
        self.store.put_papers([self.paper, self.external])

    def tearDown(self):
        self.temp.cleanup()

    def configure(self, budget=1):
        return self.store.save_settings(
            {
                'api_base': 'https://example.org/v1',
                'model': 'test-model',
                'api_key': 'private-test-key',
                'input_price': 1,
                'output_price': 1,
                'daily_budget': budget,
                'cloud_enabled': True,
            }
        )

    def corpus(self):
        root = self.store.paths.indexes / 'versions/test'
        root.mkdir(parents=True)
        docs = [
            {
                'id': 'source-a',
                'source_id': 'a',
                'paper_id': self.paper['id'],
                'kind': 'paper_text',
                'origin': 'paper',
                'text': 'We extend the RRT algorithm [7] with informed sampling.',
                'title': 'Informed RRT*',
                'url': 'zotero://example',
                'text_hash': 'a',
                'source_label': '论文原文',
            },
            {
                'id': 'source-b',
                'source_id': 'b',
                'paper_id': self.external['id'],
                'kind': 'external_abstract',
                'origin': 'bibliographic',
                'text': 'Earlier method with a different constraint.',
                'title': 'Earlier RRT',
                'url': 'https://example.org',
                'text_hash': 'b',
                'source_label': '库外摘要',
            },
        ]
        atomic(root / 'corpus.jsonl', ''.join((json.dumps(r) + '\n' for r in docs)))
        atomic(
            root / 'corpus_meta.json',
            {'sources': 2, 'vector_count': 0, 'vector_pending': 2, 'vector_ids': []},
        )
        with (root / 'corpus_bm25.pkl').open('wb') as f:
            pickle.dump(BM25([research_tokens(r['text'] + ' ' + r['title']) for r in docs]), f)
        np.save(root / 'corpus.npy', np.zeros((2, 1024), np.float32))
        self.store.set_state('active_version', 'test')
        return docs

    def bilateral_corpus(self):
        docs = self.corpus()
        docs.append(
            dict(
                docs[1],
                id='target-fulltext',
                source_id='target-fulltext',
                kind='paper_text',
                origin='paper',
                page_start=1,
                page_end=1,
                text_hash='target-fulltext',
            )
        )
        atomic(
            self.store.active_dir() / 'corpus.jsonl', ''.join((json.dumps(r) + '\n' for r in docs))
        )
        return docs

    def test_semantic_passage_can_recall_paper_outside_top_twenty_metadata_hits(self):
        docs = []
        papers = []
        for i in range(25):
            pid = f'noise-{i}'
            papers.append(
                {'id': pid, 'kind': 'library', 'title': 'navigation baseline', 'year': 2020}
            )
            docs.append(
                {
                    'id': pid,
                    'paper_id': pid,
                    'source_id': pid,
                    'kind': 'metadata',
                    'title': 'navigation baseline',
                    'text': 'navigation navigation baseline',
                }
            )
        docs.append(
            {
                'id': 'semantic',
                'source_id': 'semantic',
                'paper_id': self.paper['id'],
                'kind': 'paper_text',
                'title': 'Unrelated title',
                'text': 'A robot can pass through narrow spaces by changing its morphology.',
            }
        )
        self.store.put_papers(papers)
        root = self.store.paths.indexes / 'versions/semantic'
        root.mkdir(parents=True)
        atomic(root / 'corpus.jsonl', ''.join((json.dumps(row) + '\n' for row in docs)))
        atomic(
            root / 'corpus_meta.json',
            {
                'sources': len(docs),
                'vector_count': len(docs),
                'vector_pending': 0,
                'vector_ids': [r['id'] for r in docs],
                'embedding': __import__(
                    'kbapp.integrations.model_identity', fromlist=['embedding_signature']
                ).embedding_signature(),
            },
        )
        with (root / 'corpus_bm25.pkl').open('wb') as stream:
            pickle.dump(BM25([research_tokens(r['text']) for r in docs]), stream)
        matrix = np.zeros((len(docs), 1024), np.float32)
        matrix[:-1, 1] = 1
        matrix[-1, 0] = 1
        np.save(root / 'corpus.npy', matrix)
        self.store.set_state('active_version', 'semantic')
        vector = np.zeros(1024, np.float32)
        vector[0] = 1
        with patch('kbapp.search.query.query_vector', return_value=(vector, 'cached')):
            data = Search(self.store).search('navigation', wait_vectors=True, k=50)
        result = next((row for row in data['results'] if row['id'] == 'semantic'))
        self.assertEqual(result['retrieval']['vector_rank'], 1)
        self.assertIsNone(result['retrieval']['keyword_rank'])
        self.assertEqual(data['mode'], 'bm25+vector(rrf)')
        self.assertLessEqual(len(data['papers']), 20)
        with patch('kbapp.search.query.query_vector', side_effect=AssertionError('outside filter')):
            scoped = Search(self.store).search('navigation', paper='noise-0', vectors=False)
        self.assertEqual({r['paper_id'] for r in scoped['results']}, {'noise-0'})

    def test_embedding_requests_auto_load_with_explicit_five_minute_idle_timeout(self):
        from kbapp.integrations.embedding import _embed_texts
        import kbapp.core.config as C

        with patch(
            'kbapp.integrations.embedding.http_post', return_value={'embeddings': [[1.0]]}
        ) as request:
            _embed_texts(C.EMBED_MODEL, ['question'])
        payload = request.call_args.args[1]
        self.assertEqual(payload['keep_alive'], '5m')
        self.assertFalse(payload['truncate'])  # 输入先按预算切分，服务端禁止静默截断。
        self.assertEqual(payload['dimensions'], 1024)

    def test_new_search_resumes_embedding_but_poll_and_keyword_search_keep_manual_stop(self):
        from support import service_for

        service = service_for(self.store)
        from kbapp.integrations.models import cancelled, set_cancelled

        with (
            patch.object(service, 'store', self.store),
            patch.object(service.search_engine, 'search', return_value={'results': []}) as engine,
        ):
            set_cancelled(self.store, ['embedding', 'text'], True)
            service.search('new question', vectors=False)
            self.assertTrue(cancelled(self.store, 'embedding'))
            service.search('new question', resume_model=False)
            self.assertTrue(cancelled(self.store, 'embedding'))
            service.search('new question')
            self.assertFalse(cancelled(self.store, 'embedding'))
            self.assertTrue(cancelled(self.store, 'text'))
            self.assertTrue(engine.call_args.kwargs['wait_vectors'])

    def test_jsonl_unicode_separators_are_content(self):
        p = self.base / 'input.jsonl'
        atomic(p, json.dumps({'text': 'a\u2028b\x85c'}, ensure_ascii=False) + '\n')
        self.assertEqual(jsonl(p)[0]['text'], 'a\u2028b\x85c')

    def test_status_includes_new_recognized_sources_and_invalidates_changed_files_and_pdfs(self):
        from kbapp.library.recognition import pdf_fingerprint

        docs = self.corpus()
        root = self.store.active_dir()
        atomic(
            root / 'corpus_meta.json',
            {
                'sources': 2,
                'vector_ids': ['source-a'],
                'vector_count': 1,
                'embedding': {'model': 'test', 'dims': 1024},
            },
        )
        self.store.set_state('coverage', {'sources': 2, 'vector_count': 1, 'vector_pending': 1})
        path = self.base / 'paper.pdf'
        pdf = pymupdf.open()
        pdf.new_page()
        pdf.save(path)
        pdf.close()
        self.store.put_papers([dict(self.paper, pdf=str(path))])
        record = {
            'paper_id': self.paper['id'],
            'pdf_hash': pdf_fingerprint(path),
            'sources': [docs[0], dict(docs[0], id='new-one')],
        }
        file = self.store.paths.cache / 'recognized_sources/paper.json'
        atomic(file, record)
        with (
            patch('numpy.load', side_effect=AssertionError('status must not load vectors')),
            patch(
                'kbapp.integrations.embedding.embed_texts',
                side_effect=AssertionError('status must not start models'),
            ),
        ):
            status = workbench_status(self.store)
            health = status['index_health']
            self.assertEqual(status['coverage']['sources'], 2)
            self.assertEqual(
                (
                    health['sources'],
                    health['vector_count'],
                    health['vector_pending'],
                    health['live_vector_pending'],
                ),
                (3, 1, 2, 1),
            )
            row = next((r for r in health['by_kind'] if r['kind'] == 'paper_text'))
            self.assertEqual((row['sources'], row['vectors'], row['live_pending']), (2, 1, 1))
            record['sources'].append(dict(docs[0], id='new-two'))
            atomic(file, record)
            self.assertEqual(workbench_status(self.store)['index_health']['live_vector_pending'], 2)
            path.write_bytes(path.read_bytes() + b'\n% PDF changed')
            self.assertEqual(workbench_status(self.store)['index_health']['live_vector_pending'], 0)
            file.unlink()
            self.assertEqual(workbench_status(self.store)['index_health']['sources'], 2)

    def test_status_reloads_published_metadata_without_restart_and_counts_query_cache_separately(
        self,
    ):
        self.corpus()
        root = self.store.active_dir()
        workbench_status(self.store)
        atomic(
            root / 'corpus_meta.json',
            {'sources': 2, 'vector_ids': ['source-a', 'source-b'], 'vector_count': 2},
        )
        with self.store.db() as db:
            db.execute(
                'INSERT INTO query_vectors VALUES(?,?,?,?,?,?)',
                ('query', 'signature', '[]', 'ready', '', 'today'),
            )
        status = workbench_status(self.store)
        self.assertEqual(status['index_health']['vector_count'], 2)
        self.assertEqual(status['query_vectors'], {'ready': 1})
        self.assertEqual(status['index_health']['sources'], 2)
        other = self.store.paths.indexes / 'versions/next'
        other.mkdir(parents=True)
        atomic(other / 'corpus.jsonl', json.dumps({'id': 'next', 'kind': 'metadata'}) + '\n')
        atomic(
            other / 'corpus_meta.json', {'vector_ids': ['next'], 'sources': 1, 'vector_count': 1}
        )
        self.store.set_state('active_version', 'next')
        status = workbench_status(self.store)
        self.assertEqual(status['index_health']['version'], 'next')
        self.assertEqual(
            (status['index_health']['sources'], status['index_health']['vector_count']), (1, 1)
        )

    def test_batch_completion_is_persisted_before_publication_and_overrides_old_progress_text(self):
        self.corpus()
        root = self.store.paths.indexes / 'versions/batch'
        root.mkdir(parents=True)
        with self.store.db() as db:
            db.execute(
                'INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)',
                (
                    'batch',
                    'update',
                    'running',
                    123,
                    '{}',
                    'Qwen 向量化 0/2；复用 0',
                    'today',
                    None,
                    None,
                ),
            )
        records = [
            {'id': str(i), 'text': text, 'text_hash': digest(text), 'kind': 'paper_text'}
            for i, text in enumerate(('first', 'second'))
        ]
        observed = []

        def progress(message):
            observed.append(workbench_status(self.store)['indexing'])

        response = np.zeros((2, 1024), np.float32)
        response[:, 0] = 1
        with patch('kbapp.search.indexing.embed_texts', return_value=response):
            build_vectors(self.store, root, records, progress)
        self.assertEqual([r['new_completed'] for r in observed], [0, 2])
        status = workbench_status(self.store)
        self.assertEqual(status['indexing']['phase'], 'awaiting_publication')
        self.assertEqual(status['indexing']['completed'], 2)
        self.assertEqual(status['indexing']['cached_unpublished'], 2)
        self.assertEqual(status['indexing']['by_kind'], {'paper_text': 2})
        self.assertEqual(status['index_health']['vector_count'], 0)
        self.assertEqual(self.store.state('active_version'), 'test')

    def test_live_vector_progress_does_not_claim_published_coverage(self):
        self.store.set_state('coverage', {'vector_count': 3, 'sources': 40})
        with self.store.db() as db:
            db.execute(
                'INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)',
                (
                    'vector-job',
                    'update',
                    'running',
                    123,
                    '{}',
                    'Qwen 向量化 16/37；复用 3',
                    '2026-10-31',
                    None,
                    None,
                ),
            )
        status = workbench_status(self.store)
        self.assertEqual(status['coverage']['vector_count'], 3)
        self.assertEqual(status['indexing']['completed'], 19)
        self.assertEqual(status['indexing']['pending'], 21)

    def test_identical_text_is_embedded_once_and_reordered_rows_keep_their_vectors(self):
        root = self.store.paths.archive / 'legacy/unpublished'
        root.mkdir(parents=True)
        records = [
            {'id': str(i), 'text': text, 'text_hash': digest(text), 'kind': 'paper_text'}
            for i, text in enumerate(('same text', 'different text', 'same text'))
        ]
        response = np.zeros((2, 1024), np.float32)
        response[0, 0] = 1
        response[1, 1] = 1
        with patch('kbapp.search.indexing.embed_texts', return_value=response) as inference:
            build_vectors(self.store, root, records)
            self.assertEqual(inference.call_count, 1)
            self.assertEqual(len(inference.call_args.args[1]), 2)
            matrix = np.load(root / 'corpus.npy')
            np.testing.assert_array_equal(matrix[0], matrix[2])
            self.assertFalse(np.array_equal(matrix[0], matrix[1]))
            build_vectors(self.store, root, [records[1], records[0], records[2]])
            self.assertEqual(inference.call_count, 1)
            reordered = np.load(root / 'corpus.npy')
            np.testing.assert_array_equal(reordered[0], matrix[1])
            np.testing.assert_array_equal(reordered[1], matrix[0])
            self.assertEqual(
                json.loads((root / 'corpus_meta.json').read_text(encoding='utf-8'))['vector_count'],
                3,
            )

    def test_failed_vector_resume_preserves_previous_published_version(self):
        self.corpus()
        version = 'a' * 20
        root = self.store.paths.indexes / 'versions' / version
        root.mkdir(parents=True)
        atomic(
            root / 'corpus.jsonl',
            json.dumps({'id': 'staged', 'kind': 'paper_text', 'text': 'original'}) + '\n',
        )
        with self.store.db() as db:
            db.execute(
                'INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)',
                (version, 'update', 'interrupted', 123, '{}', 'paused', '2026-10-31', None, None),
            )
        from kbapp.workflows.resume import resume

        with (
            patch('kbapp.workflows.resume.Store', return_value=self.store),
            patch(
                'kbapp.workflows.resume.build_vectors', side_effect=RuntimeError('model offline')
            ),
        ):
            with self.assertRaises(RuntimeError):
                resume(version)
        self.assertEqual(self.store.state('active_version'), 'test')
        self.assertTrue((root / 'corpus.jsonl').exists())

    def test_local_reading_waits_for_model_slot_and_never_truncates_input(self):
        from kbapp.integrations.local_model import complete_local

        with self.store.db() as db:
            db.execute(
                'INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)',
                (
                    'vector-job',
                    'update',
                    'running',
                    123,
                    '{}',
                    'Qwen 向量化 16/37；复用 3',
                    '2026-10-31',
                    None,
                    None,
                ),
            )
        with (
            patch('kbapp.integrations.local_model.post') as request,
            patch('kbapp.integrations.scheduling.model_slot', side_effect=ValueError('queue busy')),
        ):
            with self.assertRaisesRegex(ValueError, 'queue busy'):
                complete_local(self.store, 'test', 'test')
            request.assert_not_called()
        with self.store.db() as db:
            db.execute("UPDATE jobs SET status='complete'")
        with patch('kbapp.integrations.local_model.post') as request:
            with self.assertRaisesRegex(ValueError, '禁止静默截断'):
                complete_local(self.store, '超长原文' * 5000, 'test')
            request.assert_not_called()

    def test_local_reading_caches_completed_calls_and_retains_usage(self):
        from kbapp.integrations.local_model import complete_local

        def answer(path, payload, timeout):
            if path == '/api/generate':
                return {}
            self.assertIs(payload['truncate'], False)
            self.assertIs(payload['shift'], False)
            return {
                'done_reason': 'stop',
                'message': {'content': '{"relations":[]}'},
                'prompt_eval_count': 80,
                'eval_count': 5,
            }

        with (
            patch('kbapp.integrations.local_model.post', side_effect=answer) as request,
            patch(
                'kbapp.integrations.local_model.text_request',
                side_effect=lambda store, path, payload, timeout: answer(path, payload, timeout),
            ) as inference,
        ):
            self.assertEqual(complete_local(self.store, 'test', 'test'), {'relations': []})
            count = request.call_count
            self.assertEqual(complete_local(self.store, 'test', 'test'), {'relations': []})
            self.assertEqual(request.call_count, count)
            self.assertEqual(inference.call_count, 1)
        record = json.loads(
            next((self.store.paths.cache / 'local_reading').glob('*.json')).read_text(
                encoding='utf-8'
            )
        )
        self.assertEqual(record['input_tokens'], 80)
        self.assertEqual(record['output_tokens'], 5)

    def test_stopped_local_model_does_not_restart_itself_or_lose_cached_files(self):
        from kbapp.integrations.models import set_cancelled
        from kbapp.integrations.local_model import complete_local

        set_cancelled(self.store, ['text'], True)
        with patch('kbapp.integrations.local_model.text_request') as request:
            with self.assertRaisesRegex(ValueError, '已停止'):
                complete_local(self.store, 'test', 'test')
            request.assert_not_called()

    def test_malformed_cached_local_relation_is_retried_and_never_accepted(self):
        from kbapp.integrations.local_model import complete_local
        from kbapp.core.io import atomic, digest

        cfg = self.store.settings()
        key = digest(
            cfg['local_text_model']
            + str(cfg['local_text_context'])
            + 'format test'
            + '1536'
            + 'local-verifier-v3-glm-text'
        )
        atomic(
            self.store.paths.cache / 'local_reading' / (key + '.json'),
            {'result': {'relations': ['broken']}},
        )
        bad = {'done_reason': 'stop', 'message': {'content': '{"relations":["broken"]}'}}
        good = {
            'done_reason': 'stop',
            'message': {'content': '{"relations":[],"fulltext_requests":[]}'},
        }
        with (
            patch('kbapp.integrations.local_model.post'),
            patch(
                'kbapp.integrations.local_model.text_request', side_effect=[bad, bad]
            ) as inference,
        ):
            with self.assertRaisesRegex(ValueError, '格式错误'):
                complete_local(self.store, 'format test', 'relation_verification:1')
            self.assertEqual(inference.call_count, 2)
        with (
            patch('kbapp.integrations.local_model.post'),
            patch('kbapp.integrations.local_model.text_request', return_value=good) as inference,
        ):
            self.assertEqual(
                complete_local(self.store, 'format test', 'relation_verification:1'),
                {'relations': [], 'fulltext_requests': []},
            )
            self.assertEqual(inference.call_count, 1)
            self.assertEqual(
                inference.call_args.args[2]['format']['properties']['relations']['items']['type'],
                'object',
            )

    def test_local_repeat_abort_retries_with_different_sampling(self):
        from kbapp.integrations.local_model import complete_local

        answer = {'done_reason': 'stop', 'message': {'content': '{"relations":[]}'}}
        with (
            patch('kbapp.integrations.local_model.post'),
            patch(
                'kbapp.integrations.local_model.text_request',
                side_effect=[ValueError('prediction aborted, token repeat limit reached'), answer],
            ) as inference,
        ):
            self.assertEqual(
                complete_local(self.store, 'repeat test', 'relation_verification:1'),
                {'relations': []},
            )
            self.assertEqual(inference.call_args_list[1].args[2]['options']['temperature'], 0.7)

    def test_local_truncated_output_is_retried_within_context_budget(self):
        from kbapp.integrations.local_model import complete_local

        truncated = {'done_reason': 'length', 'message': {'content': '{"relations":['}}
        answer = {
            'done_reason': 'stop',
            'message': {'content': '{"relations":[],"fulltext_requests":[]}'},
        }
        with (
            patch('kbapp.integrations.local_model.post'),
            patch(
                'kbapp.integrations.local_model.text_request', side_effect=[truncated, answer]
            ) as inference,
        ):
            self.assertEqual(
                complete_local(self.store, 'length test', 'relation_verification:1'),
                {'relations': [], 'fulltext_requests': []},
            )
            self.assertGreater(inference.call_args_list[1].args[2]['options']['num_predict'], 1536)

    def test_ocr_repeat_abort_requests_region_split_without_caching(self):
        import io
        from urllib.error import HTTPError
        from kbapp.integrations.ocr import GLMOCR, OCRRepeatLimitError

        err = HTTPError(
            'http://localhost',
            500,
            'failure',
            {},
            io.BytesIO(b'{"error":"prediction aborted, token repeat limit reached"}'),
        )
        client = GLMOCR()
        client.api = 'ollama'
        client.cache_dir = str(self.store.paths.archive / 'legacy/ocr-test-cache')
        with patch.object(client, '_post', side_effect=err):
            with self.assertRaises(OCRRepeatLimitError):
                client.image_to_text(b'image', mode='page', use_cache=False)

    def test_model_stop_never_terminates_unowned_process(self):
        from kbapp.integrations.models import stop_text_runtime

        self.store.set_state(
            'local_text_runtime', {'pid': 123, 'created': 'old', 'backend': 'native'}
        )
        with (
            patch('kbapp.integrations.models._runtime_owned', return_value=False),
            patch('kbapp.integrations.models.subprocess.run') as process,
        ):
            stop_text_runtime(self.store)
            process.assert_not_called()
        self.assertEqual(self.store.state('local_model_state')['state'], 'stopped')

    def test_native_text_adapter_preserves_output_usage_and_explicit_input_limits(self):
        from kbapp.integrations.models import text_request

        payload = {
            'model': 'local-model',
            'messages': [{'role': 'user', 'content': 'source'}],
            'options': {'num_predict': 20},
        }
        raw = {
            'choices': [{'message': {'content': '{"relations":[]}'}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 7, 'completion_tokens': 5},
        }
        with (
            patch(
                'kbapp.integrations.models.ensure_text_runtime',
                return_value='http://127.0.0.1:11435',
            ),
            patch('kbapp.integrations.models.request', return_value=raw) as request,
        ):
            result = text_request(self.store, '/api/chat', payload)
            sent = request.call_args.args[2]
            self.assertIs(sent['truncate'], False)
            self.assertIs(sent['context_shift'], False)
            self.assertEqual(sent['messages'], payload['messages'])
            self.assertEqual(result['prompt_eval_count'], 7)
            self.assertEqual(result['eval_count'], 5)

    def test_gemma_uses_ollama_without_native_runtime_or_automatic_download(self):
        from kbapp.integrations.models import ensure_text_runtime, text_request, stop_text_runtime
        from kbapp.core.config import OLLAMA

        self.store.save_settings({'local_text_model': 'gemma4:e4b'})
        with (
            patch('kbapp.integrations.models.request', return_value={'models': []}) as request,
            patch('kbapp.integrations.models.subprocess.Popen') as spawn,
        ):
            with self.assertRaisesRegex(ValueError, '尚未下载完成'):
                ensure_text_runtime(self.store)
            spawn.assert_not_called()
            self.assertEqual(request.call_args.args[1], '/api/tags')
        with (
            patch(
                'kbapp.integrations.models.request',
                return_value={'models': [{'name': 'gemma4:e4b'}]},
            ),
            patch('kbapp.integrations.models.subprocess.Popen') as spawn,
        ):
            self.assertEqual(ensure_text_runtime(self.store), OLLAMA)
            spawn.assert_not_called()
        payload = {
            'model': 'gemma4:e4b',
            'think': False,
            'format': 'json',
            'truncate': False,
            'options': {'num_ctx': 4096},
        }
        with (
            patch('kbapp.integrations.models.ensure_text_runtime', return_value=OLLAMA),
            patch(
                'kbapp.integrations.models.ollama_chat', return_value={'message': {'content': '{}'}}
            ) as chat,
        ):
            text_request(self.store, '/api/chat', payload)
            self.assertEqual(chat.call_args.args[1], payload)
        with (
            patch('kbapp.integrations.models.request') as request,
            patch('kbapp.integrations.models.terminate_owned') as terminate,
        ):
            stop_text_runtime(self.store)
            terminate.assert_not_called()
            self.assertEqual(request.call_args.args[2], {'model': 'gemma4:e4b', 'keep_alive': 0})

    def test_ollama_stream_only_caches_complete_output_and_can_be_cancelled(self):
        from kbapp.integrations.models import ollama_chat

        stream = [
            {'message': {'content': '{"relations":'}},
            {
                'message': {'content': '[]}'},
                'done': True,
                'done_reason': 'stop',
                'prompt_eval_count': 20,
                'eval_count': 7,
            },
        ]
        raw = ''.join((json.dumps(row) + '\n' for row in stream)).encode()
        with patch('kbapp.integrations.models.urlopen', return_value=io.BytesIO(raw)):
            result = ollama_chat(self.store, {'model': 'gemma4:e4b'}, 30)
            self.assertEqual(result['message']['content'], '{"relations":[]}')
            self.assertEqual(result['prompt_eval_count'], 20)
        with patch(
            'kbapp.integrations.models.urlopen',
            return_value=io.BytesIO(json.dumps(stream[0]).encode()),
        ):
            with self.assertRaisesRegex(ValueError, '响应中断'):
                ollama_chat(self.store, {}, 30)
        with (
            patch('kbapp.integrations.models.urlopen', return_value=io.BytesIO(raw)),
            patch('kbapp.integrations.models.cancelled', return_value=True),
        ):
            with self.assertRaisesRegex(ValueError, '已停止'):
                ollama_chat(self.store, {}, 30)

    def test_transient_windows_file_lock_is_retried_without_losing_artifact(self):
        p = self.base / 'retry.jsonl'
        atomic(p, '{"text":"original"}\n')
        original = Path.open
        calls = 0

        def sometimes_locked(path, *args, **kwargs):
            nonlocal calls
            if path == p and calls == 0:
                calls += 1
                raise PermissionError('temporary lock')
            return original(path, *args, **kwargs)

        with patch.object(Path, 'open', sometimes_locked), patch('kbapp.storage.store.time.sleep'):
            self.assertEqual(jsonl(p)[0]['text'], 'original')

    def test_approval_required_before_any_network_fetch(self):
        self.store.request_fulltext('oa:W123', 'important', 'verify method')
        with patch('kbapp.library.network.build_opener') as opener:
            result = fetch_approved(self.store)
            opener.assert_not_called()
        self.assertEqual(result['downloaded'], [])

    def test_rejection_persists_across_repeated_request(self):
        self.store.request_fulltext('oa:W123', 'first', 'q')
        self.store.decide(['oa:W123'], 'rejected')
        self.store.request_fulltext('oa:W123', 'changed', 'q2')
        self.assertEqual(self.store.approvals()[0]['status'], 'rejected')

    def test_only_downloaded_approved_pdf_enters_unified_text_and_figure_sources(self):
        root = self.store.paths.indexes / 'versions/approved'
        root.mkdir(parents=True)
        pdf = self.base / 'approved.pdf'
        doc = pymupdf.open()
        doc.new_page()
        doc.save(pdf)
        doc.close()
        self.store.request_fulltext('oa:W123', 'important', 'verify')
        self.assertEqual(approved_papers(self.store), [])
        with self.store.db() as db:
            db.execute(
                "UPDATE approvals SET status='downloaded',filepath=? WHERE paper_id='oa:W123'",
                (str(pdf),),
            )
        p = approved_papers(self.store)[0]
        atomic(
            root / 'chunks.jsonl',
            json.dumps(
                {
                    'citekey': p['citekey'],
                    'chunk_id': p['citekey'] + '#001',
                    'page_start': 1,
                    'page_end': 1,
                    'text': 'Approved paper evidence.',
                }
            )
            + '\n',
        )
        atomic(
            root / 'figures.jsonl',
            json.dumps(
                {
                    'citekey': p['citekey'],
                    'id': 'fig-1',
                    'page': 1,
                    'caption': 'Approved figure caption.',
                    'file': 'image.png',
                }
            )
            + '\n',
        )
        rows = build_corpus(self.store, root, [self.paper], [])
        kinds = {r['kind'] for r in rows if r.get('paper_id') == 'oa:W123'}
        self.assertIn('paper_text', kinds)
        self.assertIn('figure', kinds)

    def test_new_reference_exact_match_keeps_reference_index(self):
        root = self.store.paths.indexes / 'versions/references'
        root.mkdir(parents=True)
        self.external['doi'] = '10.1000/example'
        self.store.put_papers([self.external])
        atomic(
            root / 'references.jsonl',
            json.dumps(
                {
                    'src_citekey': self.paper['citekey'],
                    'doi': '10.1000/example',
                    'raw': 'Earlier paper [7]',
                    'ref_index': 7,
                }
            )
            + '\n',
        )
        self.assertEqual(sync_references(self.store, root, [self.paper]), 1)
        row = self.store.relations()[0]
        self.assertEqual(row['dst'], self.external['id'])
        self.assertEqual(row['evidence'][0]['ref_index'], 7)

    def test_approved_download_is_verified_pdf(self):
        self.store.request_fulltext('oa:W123', 'first', 'q')
        self.store.decide(['oa:W123'], 'approved')
        doc = pymupdf.open()
        doc.new_page()
        data = doc.tobytes()
        doc.close()

        class Opener:
            def open(self, *args, **kwargs):
                return io.BytesIO(data)

        with (
            patch('kbapp.library.network.public_url', lambda x: x),
            patch('kbapp.library.network.build_opener', return_value=Opener()),
        ):
            result = fetch_approved(self.store)
        self.assertEqual(result['downloaded'], ['oa:W123'])
        self.assertTrue(Path(self.store.approvals()[0]['filepath']).is_file())

    def test_revoke_during_download_prevents_publication(self):
        self.store.request_fulltext('oa:W123', 'first', 'q')
        self.store.decide(['oa:W123'], 'approved')
        doc = pymupdf.open()
        doc.new_page()
        data = doc.tobytes()
        doc.close()
        store = self.store

        class Opener:
            def open(self, *args, **kwargs):
                store.decide(['oa:W123'], 'rejected')
                return io.BytesIO(data)

        with (
            patch('kbapp.library.network.public_url', lambda x: x),
            patch('kbapp.library.network.build_opener', return_value=Opener()),
        ):
            result = fetch_approved(self.store)
        self.assertEqual(result['downloaded'], [])
        self.assertFalse(list((self.store.paths.files / 'external-pdfs').glob('*.pdf')))

    def test_disabled_cloud_cannot_call_provider(self):
        with patch('kbapp.integrations.cloud.urlopen') as http:
            with self.assertRaisesRegex(ValueError, '配置'):
                complete(self.store, 'prompt', 'test')
            http.assert_not_called()

    @DPAPI_REQUIRED
    def test_key_not_exposed_and_budget_configuration_required(self):
        with self.assertRaises(ValueError):
            self.store.save_settings({'cloud_enabled': True})
        self.configure()
        self.assertIs(self.store.settings(public=True)['api_key'], True)
        raw = (self.store.paths.state / 'settings.json').read_text(encoding='utf-8')
        self.assertNotIn('private-test-key', raw)
        self.store.save_settings({'api_key': None})
        self.assertEqual(self.store.settings()['api_key'], 'private-test-key')

    @DPAPI_REQUIRED
    def test_budget_reservations_are_atomic(self):
        self.configure()

        def reserve(_):
            try:
                self.store.reserve(0.6, 'test')
                return True
            except ValueError:
                return False

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reserve, range(2)))
        self.assertEqual(sum(results), 1)

    @DPAPI_REQUIRED
    def test_explicit_unlimited_mode_records_tokens_without_claiming_zero_cost(self):
        self.store.save_settings(
            {
                'api_base': 'https://example.org/v1',
                'model': 'test',
                'api_key': 'test-private',
                'budget_mode': 'unlimited',
                'cloud_enabled': True,
            }
        )
        payload = {
            'choices': [{'message': {'content': '{"ok":true}'}}],
            'usage': {'prompt_tokens': 123, 'completion_tokens': 45},
        }
        with patch(
            'kbapp.integrations.cloud.urlopen',
            return_value=io.BytesIO(json.dumps(payload).encode()),
        ):
            self.assertTrue(complete(self.store, 'test', 'unlimited')['ok'])
        status = workbench_status(self.store)
        self.assertFalse(status['today_cost_known'])
        self.assertEqual(status['today_input_tokens'], 123)
        self.assertEqual(status['today_output_tokens'], 45)

    @DPAPI_REQUIRED
    def test_reasoning_only_length_stop_is_recorded_without_retry(self):
        self.store.save_settings(
            {
                'api_base': 'https://example.org/v1',
                'model': 'DeepSeek-V4.1-Flash',
                'api_key': 'test-private',
                'budget_mode': 'unlimited',
                'cloud_enabled': True,
            }
        )
        payload = {
            'choices': [{'message': {'content': None}, 'finish_reason': 'length'}],
            'usage': {'prompt_tokens': 20, 'completion_tokens': 3000},
        }
        with patch(
            'kbapp.integrations.cloud.urlopen',
            return_value=io.BytesIO(json.dumps(payload).encode()),
        ) as http:
            with self.assertRaisesRegex(ValueError, '长度上限'):
                complete(self.store, 'test', 'structured')
            self.assertEqual(http.call_count, 1)
            request = json.loads(http.call_args.args[0].data)
            self.assertEqual(request['thinking']['type'], 'disabled')
        self.assertEqual(workbench_status(self.store)['today_output_tokens'], 3000)

    @DPAPI_REQUIRED
    def test_independent_review_retries_only_definite_output_limit_and_caches_result(self):
        from kbapp.research.relations import review_relations
        from kbapp.integrations.cloud import CloudOutputLimitError

        self.configure()
        paper = self.paper
        target = self.external
        docs = self.bilateral_corpus()
        calls = []
        item = {
            'src': paper['id'],
            'dst': target['id'],
            'kind': 'improves',
            'review_index': 0,
            'evidence': [
                {'source_id': 'source-a', 'quote': docs[0]['text']},
                {'source_id': 'target-fulltext', 'quote': docs[-1]['text']},
            ],
            'local_review_verdict': {
                'supported': True,
                'method_relation': True,
                'unsupported_details': [],
                'reason': '原文支持',
            },
        }

        def response(store, prompt, purpose, **kw):
            calls.append(kw)
            if len(calls) == 1:
                raise CloudOutputLimitError('长度上限')
            c = json.loads(prompt.split('\n', 1)[1])['claims'][0]
            return {
                'reviews': [
                    {
                        'id': c['id'],
                        'supported': False,
                        'method_relation': False,
                        'unsupported_details': ['缺少依据'],
                        'reason': '证据不足',
                    }
                ]
            }

        with patch('kbapp.research.relations.complete', side_effect=response):
            result = review_relations(
                self.store,
                paper,
                {target['id']: target},
                {},
                [item],
                lambda _: None,
                {d['id']: d for d in docs},
            )
            again = review_relations(
                self.store,
                paper,
                {target['id']: target},
                {},
                [item],
                lambda _: None,
                {d['id']: d for d in docs},
            )
        self.assertFalse(result[0]['supported'])
        self.assertEqual(again, result)
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[0]['thinking'])
        self.assertFalse(calls[1]['thinking'])

    @DPAPI_REQUIRED
    def test_review_resume_reuses_completed_relations_and_does_not_retry_timeout(self):
        from kbapp.research.relations import review_relations
        from kbapp.research.review import retry_deferred

        self.configure()
        paper = self.paper
        target = self.external
        docs = self.bilateral_corpus()
        calls = []
        items = [
            {
                'src': paper['id'],
                'dst': target['id'],
                'kind': kind,
                'review_index': n,
                'evidence': [
                    {'source_id': 'source-a', 'quote': docs[0]['text']},
                    {'source_id': 'target-fulltext', 'quote': docs[-1]['text']},
                ],
                'local_review_verdict': {
                    'supported': True,
                    'method_relation': True,
                    'unsupported_details': [],
                    'reason': '原文支持',
                },
            }
            for n, kind in enumerate(('inherits', 'improves'))
        ]

        def response(store, prompt, purpose, **kw):
            calls.append(1)
            if len(calls) == 2:
                raise TimeoutError()
            c = json.loads(prompt.split('\n', 1)[1])['claims'][0]
            return {
                'reviews': [
                    {
                        'id': c['id'],
                        'supported': False,
                        'method_relation': False,
                        'unsupported_details': [],
                        'reason': '该关系未获支持',
                    }
                ]
            }

        with patch('kbapp.research.relations.complete', side_effect=response):
            with self.assertRaisesRegex(ValueError, '云端调用未完成'):
                review_relations(
                    self.store,
                    paper,
                    {target['id']: target},
                    {},
                    items,
                    lambda _: None,
                    {d['id']: d for d in docs},
                )
            self.assertEqual(len(calls), 2)
            retry_deferred(self.store, '')
            result = review_relations(
                self.store,
                paper,
                {target['id']: target},
                {},
                items,
                lambda _: None,
                {d['id']: d for d in docs},
            )
            self.assertEqual(len(calls), 3)
            self.assertEqual(set(result), {0, 1})

    def test_all_directions_visible_and_old_citation_path_not_promoted(self):
        self.store.relation(
            {
                'src': self.paper['id'],
                'dst': self.external['id'],
                'kind': 'cites',
                'status': 'confirmed',
            }
        )
        atomic(
            self.store.paths.archive / 'legacy/citation/backbone.json',
            {'directions': {'02': {'chains': [{'nodes': ['W123', self.paper['citekey']]}]}}},
        )
        stats = overview(self.store)
        self.assertEqual(len(stats['directions']), 11)
        self.assertEqual(stats['confirmed'], 0)
        data = timeline(self.store, direction='02', project=True)
        self.assertEqual(data['groups'][0]['paths'][0]['kind'], 'citation')
        self.assertTrue(all((e['kind'] == 'cites' for e in data['groups'][0]['paths'][0]['edges'])))

    @DPAPI_REQUIRED
    def test_failed_provider_does_not_retry_or_release_uncertain_cost(self):
        self.configure(0.001)
        with patch('kbapp.integrations.cloud.urlopen', side_effect=TimeoutError()) as http:
            with self.assertRaises(ValueError):
                complete(self.store, 'prompt', 'test', max_tokens=10)
            self.assertEqual(http.call_count, 0)
        self.configure(1)
        with patch('kbapp.integrations.cloud.urlopen', side_effect=TimeoutError()) as http:
            with self.assertRaises(ValueError):
                complete(self.store, 'prompt', 'test', max_tokens=10)
            self.assertEqual(http.call_count, 1)
        with self.store.db() as db:
            row = db.execute('SELECT reserved,actual,status FROM usage').fetchone()
        self.assertGreater(row['reserved'], 0)
        self.assertIsNone(row['actual'])

    def test_fabricated_evidence_is_rejected(self):
        docs = self.corpus()
        r = {
            'src': self.paper['id'],
            'dst': 'oa:W123',
            'kind': 'inherits',
            'evidence': [{'source_id': 'source-a', 'quote': 'fabricated'}],
        }
        with self.assertRaises(ValueError):
            validate_ai_relation(r, {s['id']: s for s in docs}, self.store.papers())

    def test_abstract_evidence_remains_candidate(self):
        docs = self.corpus()
        docs[1]['text'] = 'We extend earlier work.'
        r = {
            'src': self.paper['id'],
            'dst': 'oa:W123',
            'kind': 'inherits',
            'citation_verified': True,
            'explanation': '说明',
            'previous_problem': '问题',
            'change': '改进',
            'evidence': [{'source_id': 'source-b', 'quote': docs[1]['text']}],
        }
        self.assertEqual(
            validate_ai_relation(r, {s['id']: s for s in docs}, self.store.papers())['status'],
            'candidate',
        )

    def test_fulltext_must_belong_to_citing_paper(self):
        docs = self.corpus()
        docs[0]['paper_id'] = 'oa:W123'
        r = {
            'src': self.paper['id'],
            'dst': 'oa:W123',
            'kind': 'inherits',
            'citation_verified': True,
            'explanation': '说明',
            'previous_problem': '问题',
            'change': '改进',
            'evidence': [{'source_id': 'source-a', 'quote': docs[0]['text']}],
        }
        self.assertEqual(
            validate_ai_relation(r, {s['id']: s for s in docs}, self.store.papers())['status'],
            'candidate',
        )

    def test_strong_evidence_and_same_year_relationship(self):
        self.external['year'] = self.paper['year']
        self.store.put_papers([self.external])
        docs = self.corpus()
        r = {
            'src': self.paper['id'],
            'dst': 'oa:W123',
            'kind': 'inherits',
            'citation_verified': True,
            'explanation': '说明',
            'previous_problem': '问题',
            'change': '改进',
            'evidence': [{'source_id': 'source-a', 'quote': docs[0]['text']}],
        }
        citations = [
            {
                'src': self.paper['id'],
                'dst': 'oa:W123',
                'kind': 'cites',
                'status': 'confirmed',
                'evidence': [{'ref_index': 7}],
            }
        ]
        self.assertEqual(
            validate_ai_relation(r, {s['id']: s for s in docs}, self.store.papers(), citations)[
                'status'
            ],
            'confirmed',
        )

    def test_unrelated_citation_or_model_claim_cannot_confirm(self):
        docs = self.corpus()
        r = {
            'src': self.paper['id'],
            'dst': 'oa:W123',
            'kind': 'inherits',
            'citation_verified': True,
            'explanation': '说明',
            'previous_problem': '问题',
            'change': '改进',
            'evidence': [{'source_id': 'source-a', 'quote': docs[0]['text']}],
        }
        citations = [
            {
                'src': self.paper['id'],
                'dst': 'oa:W123',
                'kind': 'cites',
                'status': 'confirmed',
                'evidence': [{'ref_index': 8}],
            }
        ]
        self.assertEqual(
            validate_ai_relation(
                dict(r), {s['id']: s for s in docs}, self.store.papers(), citations
            )['status'],
            'candidate',
        )
        docs[0]['text'] = 'Earlier work [8] is a baseline. We extend a different algorithm [7].'
        r['evidence'][0]['quote'] = docs[0]['text']
        self.assertEqual(
            validate_ai_relation(r, {s['id']: s for s in docs}, self.store.papers(), citations)[
                'status'
            ],
            'candidate',
        )

    def test_adjacent_original_context_requires_separate_review(self):
        docs = self.corpus()
        docs[0]['text'] = (
            'The prior method [7] cannot handle changing constraints. In this work, a similar strategy is developed for inspection.'
        )
        r = {
            'src': self.paper['id'],
            'dst': 'oa:W123',
            'kind': 'transfers',
            'explanation': '应用迁移',
            'previous_problem': '约束',
            'change': '巡检',
            'evidence': [
                {
                    'source_id': 'source-a',
                    'quote': 'In this work, a similar strategy is developed for inspection.',
                }
            ],
        }
        cites = [
            {
                'src': self.paper['id'],
                'dst': 'oa:W123',
                'kind': 'cites',
                'status': 'confirmed',
                'evidence': [{'ref_index': 7}],
            }
        ]
        found = validate_ai_relation(r, {s['id']: s for s in docs}, self.store.papers(), cites)
        self.assertEqual(found['status'], 'candidate')
        self.assertTrue(found['eligible_for_review'])
        self.assertIn('[7]', found['review_context'][0]['quote'])

    def test_partially_supported_claim_does_not_enter_formal_graph(self):
        self.assertFalse(
            fully_supported(
                {
                    'supported': True,
                    'reason': '核心关系有依据，但额外设置未在给定证据中出现，不能据此核实该细节。',
                }
            )
        )
        self.assertTrue(
            fully_supported(
                {'supported': True, 'reason': '引用编号和原文对应，未发现引用编号错配或条件夸大。'}
            )
        )

    def test_disk_bm25_starred_algorithms_and_filters(self):
        docs = self.corpus()
        build_keyword_index(self.store.active_dir(), docs)
        engine = Search(self.store)
        data = engine.search('Informed RRT*', vectors=False)
        self.assertEqual(data['results'][0]['paper_id'], self.paper['id'])
        data = engine.search('method', direction='02', vectors=False)
        self.assertTrue(all((r['paper_id'] == self.paper['id'] for r in data['results'])))
        self.assertFalse(engine.search('RRT', year='1999,2000', vectors=False)['results'])

    def test_manual_rejection_survives_reimport(self):
        r = {'src': self.paper['id'], 'dst': 'oa:W123', 'kind': 'inherits', 'status': 'rejected'}
        self.store.relation(r, manual=True)
        self.store.relation(dict(r, status='confirmed'))
        self.assertEqual(self.store.relations()[0]['status'], 'rejected')

    def test_chunk_upper_bound_and_decimal_integrity(self):
        text = 'Value 0.12345 and DOI 10.1000/test are unchanged. ' * 300 + '中文公式与约束，' * 500
        chunks = chunk_paras([(1, text)], 512, 64)
        self.assertTrue(all((est_tokens(t[0]) <= 768 for t in chunks)))
        self.assertIn('0.12345', '\n'.join((t[0] for t in chunks)))
        self.assertNotIn('0.\n12345', '\n'.join((t[0] for t in chunks)))
        self.assertGreater(len(chunks), 1)

    def test_short_final_evidence_is_not_dropped_and_overlap_keeps_words(self):
        self.assertEqual(
            chunk_paras([(1, 'A short but important result.')], 512, 64)[0][0],
            'A short but important result.',
        )
        chunks = chunk_paras([(1, 'word boundary evidence ' * 200)], 512, 64)
        self.assertTrue(all((c[0].startswith(('word', 'boundary', 'evidence')) for c in chunks)))
        self.assertTrue(all((c[1] == est_tokens(c[0]) for c in chunks)))
        self.assertTrue(all((c[0].strip() for c in chunks)))

    def test_chunk_version_change_rebuilds_once_then_reuses(self):
        import kbapp.workflows.tools.chunk as chunk
        import contextlib

        root = self.store.paths.archive / 'legacy/version-check'
        root.mkdir(parents=True)
        pdf = self.base / 'source.pdf'
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((50, 80), 'Actual original evidence is retained.')
        doc.save(pdf)
        doc.close()
        p = dict(self.paper, pdf=str(pdf))
        atomic(root / 'papers_all.jsonl', json.dumps(p) + '\n')
        atomic(
            root / 'chunks.jsonl',
            json.dumps(
                {
                    'citekey': p['citekey'],
                    'chunk_id': p['citekey'] + '#001',
                    'section': 'Front',
                    'tokens': 10,
                    'text': 'Obsolete chunk.',
                }
            )
            + '\n',
        )
        atomic(root / 'chunk_cache.json', {'_version': -1, 'papers': {}})
        args = ['chunk.py', '--base', str(self.base), '--index', str(root), '--quiet']
        with patch.object(sys, 'argv', args), contextlib.redirect_stdout(io.StringIO()):
            chunk.main()
        self.assertNotIn('Obsolete chunk.', (root / 'chunks.jsonl').read_text(encoding='utf-8'))
        self.assertIn(
            'Actual original evidence', (root / 'chunks.jsonl').read_text(encoding='utf-8')
        )
        with patch.object(sys, 'argv', args), contextlib.redirect_stdout(io.StringIO()):
            chunk.main()
        report = json.loads((root / 'chunk_report.json').read_text(encoding='utf-8'))
        self.assertEqual(report['reused'], 1)
        self.assertEqual(report['rebuilt'], 0)

    def test_two_column_reading_order(self):
        doc = pymupdf.open()
        page = doc.new_page()
        for i in range(3):
            page.insert_text((50, 100 + 60 * i), f'Left paragraph {i} with some text.')
            page.insert_text((330, 100 + 60 * i), f'Right paragraph {i} with some text.')
        text = page_text(page)
        self.assertLess(text.index('Left paragraph 2'), text.index('Right paragraph 0'))
        doc.close()

    def test_blank_scan_is_not_sent_for_ocr(self):
        from kbapp.library.page_ocr import blank_page

        doc = pymupdf.open()
        page = doc.new_page()
        self.assertTrue(blank_page(page))
        page.insert_text((50, 80), 'This page contains real text requiring recognition.')
        self.assertFalse(blank_page(page))
        doc.close()

    def test_stopping_ocr_keeps_completed_cache_in_next_version(self):
        from kbapp.workflows.extraction import quality_ocr
        from kbapp.integrations.models import set_cancelled

        cached = self.store.paths.cache / 'ocr_pages' / self.paper['citekey'] / 'p0001.txt'
        atomic(cached, 'Already recognized original evidence.')
        index = self.store.paths.indexes / 'versions/next'
        index.mkdir(parents=True)
        set_cancelled(self.store, ['ocr'], True)
        with patch('kbapp.integrations.ocr.GLMOCR.available', return_value=True):
            warnings = quality_ocr(self.store, index, [self.paper], lambda message: None)
        self.assertTrue(warnings)
        self.assertEqual(
            (index / 'ocr_pages' / self.paper['citekey'] / 'p0001.txt').read_text(encoding='utf-8'),
            cached.read_text(encoding='utf-8'),
        )

    def test_manual_notes_fields_and_path_survive_refresh(self):
        path = self.base / self.paper['card']
        path.parent.mkdir(parents=True)
        original = '---\ntitle: 人工修订题名\nrating: 5\n---\n# 自由正文\n不要覆盖此处。\n\n## 我的笔记\n自定义理解。\n'
        atomic(path, original)
        publish_notes(self.store, [self.paper])
        publish_notes(self.store, [dict(self.paper, title='Later metadata')])
        second = path.read_text(encoding='utf-8')
        self.assertIn('不要覆盖此处。', second)
        self.assertIn('自定义理解。', second)
        self.assertIn('title: 人工修订题名', second)
        self.assertIn('rating: 5', second)
        self.assertEqual(second.count('KB:AUTO:START'), 1)

    def test_metadata_without_pdf_and_notes_are_searchable(self):
        self.corpus()
        data = Search(self.store).search('Earlier method', vectors=False)
        self.assertTrue(any((r['kind'] == 'external_abstract' for r in data['results'])))
        data = Search(self.store).search('RRT', direction='02', vectors=False)
        self.assertTrue(all((r['paper_id'] == self.paper['id'] for r in data['results'])))

    @DPAPI_REQUIRED
    def test_qa_citations_must_be_exact_and_numbered(self):
        self.configure()
        docs = self.corpus()
        found = {
            'results': docs,
            'subquestions': ['RRT'],
            'process': [{'mode': 'bm25+vector(rrf)'}],
            'reranked': 2,
        }

        def reviewer(store, prompt, purpose, **kwargs):
            if purpose == 'interactive_answer':
                return {
                    'claims': [
                        {
                            'text': '本文扩展RRT',
                            'evidence': [{'source_id': 'source-a', 'quote': docs[0]['text']}],
                        }
                    ]
                }
            payload = json.loads(prompt.split('\n', 1)[1])
            return {
                'reviews': [
                    {
                        'id': c['id'],
                        'supported': True,
                        'unsupported_details': [],
                        'reason': '原句明确支持扩展关系',
                    }
                    for c in payload['claims']
                ]
            }

        with (
            patch('kbapp.research.retrieval.evidence_search', return_value=found),
            patch('kbapp.research.qa.refresh_sources', return_value=(docs, [])),
            patch('kbapp.integrations.local_model.complete_local', side_effect=reviewer),
            patch('kbapp.integrations.cloud.complete', side_effect=reviewer),
        ):
            result = ask(self.store, 'RRT')
            self.assertEqual(len(result['citations']), 1)
            self.assertIn('[1]', result['answer'])

        def fabricated(store, prompt, purpose, **kwargs):
            return {
                'claims': [
                    {
                        'text': '编造结论',
                        'evidence': [{'source_id': 'source-a', 'quote': 'fabricated'}],
                    }
                ]
            }

        with (
            patch('kbapp.research.retrieval.evidence_search', return_value=found),
            patch('kbapp.research.qa.refresh_sources', return_value=(docs, [])),
            patch('kbapp.integrations.cloud.complete', side_effect=fabricated),
        ):
            result = ask(self.store, 'RRT')
            self.assertEqual(result['citations'], [])
            self.assertNotIn('编造结论', result['answer'])

    def test_failed_build_keeps_previous_version(self):
        self.corpus()
        with (
            patch('kbapp.workflows.update.sync_library', return_value=([self.paper], [], [])),
            patch('kbapp.workflows.update.build_corpus', return_value=[]),
            patch('kbapp.workflows.update.build_vectors', side_effect=RuntimeError('interrupted')),
        ):
            from kbapp.workflows.update import run_update

            with self.assertRaises(RuntimeError):
                run_update(self.store, bootstrap=True)
        self.assertEqual(self.store.state('active_version'), 'test')
        self.assertTrue((self.store.active_dir() / 'corpus.jsonl').is_file())

    @DPAPI_REQUIRED
    def test_verification_respects_scope_and_reuses_completed_paper(self):
        from kbapp.research.relations import analyze_relations

        self.configure()
        self.corpus()
        self.store.relation(
            {
                'src': self.paper['id'],
                'dst': self.external['id'],
                'kind': 'cites',
                'status': 'confirmed',
            }
        )
        states = []
        with patch('kbapp.research.relations.complete', return_value={'relations': []}) as model:
            result = analyze_relations(
                self.store, filters={'direction': '03'}, on_status=states.append
            )
            self.assertEqual(result['total'], 0)
            model.assert_not_called()
            result = analyze_relations(
                self.store,
                filters={'project': True, 'direction': '02', 'q': 'informed'},
                on_status=states.append,
            )
            self.assertEqual(result['processed'], 1)
            self.assertEqual(result['completed'], 1)
            result = analyze_relations(self.store, filters={'direction': '02'})
            self.assertEqual(result['reused'], 1)
            self.assertEqual(result['processed'], 0)
            self.assertEqual(model.call_count, 1)
        self.assertEqual(self.store.state('active_version'), 'test')

    @DPAPI_REQUIRED
    def test_local_verification_prepares_only_next_batch_and_reuses_cache_after_restart(self):
        from kbapp.research.relations import analyze_relations

        self.configure()
        docs = self.corpus()
        self.store.save_settings({'relation_extractor': 'local', 'cloud_requests_per_run': 1})
        for n in range(1, 4):
            paper = dict(
                self.paper, id=f'z:1:TEST000{n}', citekey=f'Example202{n}', title=f'Paper {n}'
            )
            self.store.put_papers([paper])
            self.store.relation(
                {
                    'src': paper['id'],
                    'dst': self.external['id'],
                    'kind': 'cites',
                    'status': 'confirmed',
                }
            )
            if n > 1:
                docs.append(
                    dict(docs[0], id=f'source-{n}', paper_id=paper['id'], text_hash=f'hash-{n}')
                )
        atomic(
            self.store.active_dir() / 'corpus.jsonl',
            ''.join((json.dumps(row) + '\n' for row in docs)),
        )
        with (
            patch('kbapp.library.recognition.prepare_relation_sources') as ocr,
            patch(
                'kbapp.research.local_verification.verify_relations', return_value={'relations': []}
            ) as local,
            patch('kbapp.integrations.models.release_local'),
        ):
            first = analyze_relations(self.store, completed_ids=set())
            self.assertEqual(
                (first['processed'], first['completed'], first['pending'], first['total']),
                (1, 1, 2, 3),
            )
            self.assertEqual(len(ocr.call_args.args[1]), 1)
            second = analyze_relations(self.store, completed_ids=set(first['batch_completed_ids']))
            self.assertEqual(
                (second['processed'], second['completed'], second['pending']), (1, 2, 1)
            )
            self.assertNotEqual(first['batch_completed_ids'], second['batch_completed_ids'])
            after_restart = analyze_relations(self.store, completed_ids=set())
            self.assertEqual(
                (after_restart['processed'], after_restart['reused'], after_restart['pending']),
                (0, 1, 2),
            )
            self.assertEqual(local.call_count, 2)

    def test_glm_recognition_cache_and_pdf_change_preserve_source_identity(self):
        from kbapp.library.recognition import recognize_sources, active_sources
        from unittest.mock import Mock

        pdf = self.base / 'paper.pdf'
        document = pymupdf.open()
        page = document.new_page()
        page.insert_text((50, 80), 'We extend the earlier algorithm [7].')
        document.save(pdf)
        document.close()
        paper = dict(self.paper, pdf='paper.pdf')
        self.store.put_papers([paper])
        source = {'paper_id': paper['id'], 'kind': 'paper_text', 'page_start': 1, 'page_end': 1}
        client = Mock(model='glm-ocr:latest', url='http://127.0.0.1:11434', api='ollama')
        client.available.return_value = True
        client.page_to_text.return_value = 'We extend the earlier algorithm [7].'
        first = recognize_sources(self.store, paper, [source], lambda text: None, client=client)
        second = recognize_sources(self.store, paper, [source], lambda text: None, client=client)
        self.assertEqual(client.page_to_text.call_count, 1)
        self.assertEqual(first[0]['id'], second[0]['id'])
        self.assertEqual(first[0]['ocr_provenance']['model'], 'glm-ocr:latest')
        self.assertEqual(first[0]['page_start'], 1)
        self.assertIn(first[0]['id'], {s['id'] for s in active_sources(self.store)})
        from kbapp.library.chunk import read_ocr_page

        self.assertEqual(
            read_ocr_page(self.store.paths.cache, paper['citekey'], 1, pdf_path=pdf),
            client.page_to_text.return_value,
        )
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((50, 80), 'Changed attachment and different original text.')
        doc.save(self.base / 'replacement.pdf')
        doc.close()
        os.replace(self.base / 'replacement.pdf', pdf)
        self.assertFalse(active_sources(self.store))
        self.assertEqual(
            read_ocr_page(self.store.paths.cache, paper['citekey'], 1, pdf_path=pdf), ''
        )
        third = recognize_sources(self.store, paper, [source], lambda text: None, client=client)
        self.assertNotEqual(first[0]['id'], third[0]['id'])
        self.assertEqual(client.page_to_text.call_count, 2)

    def test_recognition_never_reads_unapproved_external_pdf(self):
        from kbapp.library.recognition import recognize_sources
        from unittest.mock import Mock

        client = Mock(model='glm-ocr:latest', url='http://127.0.0.1:11434', api='ollama')
        forged = dict(self.external, pdf=str(self.base / 'anything.pdf'))
        metadata = {'paper_id': forged['id'], 'kind': 'external_abstract', 'text': 'Abstract only.'}
        self.assertEqual(
            recognize_sources(self.store, forged, [metadata], lambda text: None, client=client),
            [metadata],
        )
        client.page_to_text.assert_not_called()

    def test_ocr_evidence_stays_valid_and_enters_incremental_index_with_same_id(self):
        from kbapp.library.recognition import recognize_sources
        from kbapp.research.verified import mark_stale
        from unittest.mock import Mock

        self.corpus()
        pdf = self.base / 'paper.pdf'
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((50, 80), 'Original evidence is retained.')
        doc.save(pdf)
        doc.close()
        paper = dict(self.paper, pdf='paper.pdf')
        self.store.put_papers([paper])
        client = Mock(model='glm-ocr:latest', url='http://127.0.0.1:11434', api='ollama')
        client.available.return_value = True
        client.page_to_text.return_value = 'We extend the earlier algorithm [7].'
        sources = recognize_sources(
            self.store,
            paper,
            [{'paper_id': paper['id'], 'kind': 'paper_text', 'page_start': 1, 'page_end': 1}],
            lambda text: None,
            client=client,
        )
        relation = {
            'src': paper['id'],
            'dst': self.external['id'],
            'kind': 'improves',
            'status': 'confirmed',
            'level': 'fulltext',
            'evidence': [{'source_id': sources[0]['id'], 'quote': sources[0]['text']}],
        }
        self.store.relation(relation)
        mark_stale(self.store, self.store.active_dir())
        self.assertEqual(self.store.relations()[0]['status'], 'confirmed')
        corpus = build_corpus(self.store, self.store.active_dir(), [paper], [])
        found = next((s for s in corpus if s['id'] == sources[0]['id']))
        self.assertEqual(found['text'], sources[0]['text'])
        self.assertEqual(found['origin'], 'paper_ocr')

    def test_ocr_truncated_output_is_not_saved_as_complete_recognition(self):
        from kbapp.integrations.ocr import GLMOCR, GLMOCRError

        client = GLMOCR()
        client.cache_dir = str(self.base / 'recognition-cache')
        with (
            patch.object(
                client,
                '_post',
                return_value={'done_reason': 'length', 'message': {'content': 'incomplete'}},
            ),
            patch('kbapp.integrations.ocr.time.sleep'),
        ):
            with self.assertRaisesRegex(GLMOCRError, '长度上限'):
                client.page_to_text(b'original-page-image')
        self.assertFalse(list(Path(client.cache_dir).glob('*.json')))

    def test_two_column_ocr_regions_keep_short_trailing_formula(self):
        from kbapp.library.page_ocr import layout_regions

        doc = pymupdf.open()
        page = doc.new_page(width=612, height=792)
        for x in (50, 320):
            for y in (120, 220):
                page.insert_textbox(
                    pymupdf.Rect(x, y, x + 240, y + 70),
                    'Original method evidence with a long paragraph. ' * 4,
                    fontsize=10,
                )
        page.insert_text((350, 700), 'x = 7')
        regions = layout_regions(page)
        formula = next((pymupdf.Rect(b[:4]) for b in page.get_text('blocks') if 'x = 7' in b[4]))
        self.assertGreater(len(regions), 1)
        self.assertTrue(any((r.contains(formula) for r in regions)))
        self.assertTrue(
            all(
                (
                    any((r.contains(pymupdf.Rect(b[:4])) for r in regions))
                    for b in page.get_text('blocks')
                    if b[6] == 0
                )
            )
        )
        self.assertTrue(all((r.width < page.rect.width * 0.6 for r in regions)))
        doc.close()

    def test_short_line_columns_cover_the_figures_between_their_text(self):
        from kbapp.library.page_ocr import layout_regions

        doc = pymupdf.open()
        page = doc.new_page(width=612, height=792)
        for x in (50, 320):
            for y in (110, 610):
                page.insert_text(
                    (x, y), 'Separate evidence lines from the original paper.', fontsize=9
                )
        figure = pymupdf.Rect(340, 250, 520, 470)
        page.draw_rect(figure)
        regions = layout_regions(page)
        self.assertGreaterEqual(len(regions), 2)
        self.assertTrue(any((region.contains(figure) for region in regions)))
        self.assertTrue(
            all(
                (
                    any((r.contains(pymupdf.Rect(b[:4])) for r in regions))
                    for b in page.get_text('blocks')
                    if b[6] == 0
                )
            )
        )
        doc.close()

    def test_ocr_output_limit_splits_and_retries_without_hiding_other_failures(self):
        from unittest.mock import Mock
        from kbapp.integrations.ocr import OCRLengthLimitError
        from kbapp.library.page_ocr import recognize_page

        doc = pymupdf.open()
        page = doc.new_page(width=400, height=600)
        page.insert_text((60, 180), 'First text line before the split.')
        page.insert_text((60, 420), 'Second text line after the split.')
        client = Mock()
        client.page_to_text.side_effect = [
            OCRLengthLimitError('长度上限'),
            'first part',
            'second part',
        ]
        with patch('kbapp.library.page_ocr.layout_regions', return_value=[page.rect]):
            text, regions = recognize_page(client, page, lambda _: None)
        self.assertEqual(text, 'first part\n\nsecond part')
        self.assertEqual(len(regions), 2)
        self.assertEqual(client.page_to_text.call_count, 3)
        self.assertLessEqual(regions[0][3], regions[1][1])
        self.assertEqual(regions[0][1], 0)
        self.assertEqual(regions[1][3], 600)
        client.page_to_text.reset_mock(side_effect=True)
        client.page_to_text.side_effect = RuntimeError('offline')
        with patch('kbapp.library.page_ocr.layout_regions', return_value=[page.rect]):
            with self.assertRaisesRegex(RuntimeError, 'offline'):
                recognize_page(client, page, lambda _: None)
        self.assertEqual(client.page_to_text.call_count, 1)
        doc.close()

    @DPAPI_REQUIRED
    def test_uncertain_local_candidates_reach_cloud_and_cloud_support_is_required(self):
        from kbapp.research.relations import analyze_relations

        self.configure()
        docs = self.bilateral_corpus()
        docs[0]['ocr_provenance'] = {'model': 'glm-ocr:latest', 'page': 1, 'pdf_hash': 'test'}
        atomic(
            self.store.active_dir() / 'corpus.jsonl',
            ''.join((json.dumps(row) + '\n' for row in docs)),
        )
        self.store.relation(
            {
                'src': self.paper['id'],
                'dst': self.external['id'],
                'kind': 'cites',
                'status': 'confirmed',
                'evidence': [{'ref_index': 7}],
            }
        )
        for local_pass, cloud_pass in ((False, True), (True, False), (True, True)):
            with self.subTest(local=local_pass, cloud=cloud_pass):
                with self.store.db() as db:
                    db.execute(
                        "DELETE FROM relation_evidence WHERE relation_id IN (SELECT id FROM relations WHERE kind='improves')"
                    )
                    db.execute("DELETE FROM relations WHERE kind='improves'")
                self.store.save_settings(
                    {'relation_extractor': 'local', 'model': f'test-{local_pass}-{cloud_pass}'}
                )
                item = {
                    'src': self.paper['id'],
                    'dst': self.external['id'],
                    'kind': 'improves',
                    'explanation': '改进前作',
                    'previous_problem': '采样效率',
                    'change': '知情采样',
                    'evidence': [
                        {'source_id': 'source-a', 'quote': docs[0]['text']},
                        {'source_id': 'target-fulltext', 'quote': docs[-1]['text']},
                    ],
                    'local_review_verdict': {
                        'supported': local_pass,
                        'method_relation': True,
                        'unsupported_details': [],
                        'reason': '给定原句明确支持方法改进。' if local_pass else '给定证据不足。',
                    },
                }
                review = {
                    'reviews': [
                        {
                            'review_index': 0,
                            'supported': cloud_pass,
                            'method_relation': True,
                            'unsupported_details': [],
                            'reason': '原文与引用编号一致。' if cloud_pass else '给定证据不足。',
                        }
                    ]
                }

                def cloud_response(store, prompt, purpose, **kw):
                    result = json.loads(json.dumps(review))
                    result['reviews'][0]['id'] = json.loads(prompt.split('\n', 1)[1])['claims'][0][
                        'id'
                    ]
                    return result

                with (
                    patch('kbapp.library.recognition.prepare_relation_sources') as ocr,
                    patch(
                        'kbapp.research.local_verification.verify_relations',
                        return_value={'relations': [item]},
                    ) as local,
                    patch('kbapp.research.relations.complete', side_effect=cloud_response) as cloud,
                    patch('kbapp.integrations.models.release_local'),
                ):
                    result = analyze_relations(self.store)
                self.assertEqual(result['processed'], 1)
                ocr.assert_called_once()
                local.assert_called_once()
                self.assertEqual(cloud.call_count, 1)
                found = next((r for r in self.store.relations() if r['kind'] == 'improves'))
                self.assertEqual(found['status'], 'confirmed' if cloud_pass else 'candidate')
                self.assertEqual(
                    found['local_reviewed_by'], self.store.settings()['local_text_model']
                )
                self.assertEqual(found['extracted_by'], 'GLM-OCR')

    def test_ocr_whitespace_quote_is_saved_as_original_text_without_paraphrase_matching(self):
        docs = self.corpus()
        docs[0]['text'] = 'We extend\nthe RRT algorithm [7] with informed sampling.'
        item = {
            'src': self.paper['id'],
            'dst': self.external['id'],
            'kind': 'improves',
            'evidence': [
                {
                    'source_id': 'source-a',
                    'quote': 'We extend the RRT algorithm [7] with informed sampling.',
                }
            ],
        }
        result = validate_ai_relation(item, {s['id']: s for s in docs}, self.store.papers())
        self.assertEqual(result['evidence'][0]['quote'], docs[0]['text'])
        item['evidence'][0]['quote'] = 'We replace the RRT algorithm [7] with informed sampling.'
        with self.assertRaises(ValueError):
            validate_ai_relation(item, {s['id']: s for s in docs}, self.store.papers())

    @DPAPI_REQUIRED
    def test_late_method_evidence_is_reviewed_before_earlier_abstract_similarity(self):
        from kbapp.research.relations import analyze_relations

        self.configure()
        docs = self.bilateral_corpus()
        self.store.save_settings({'relation_extractor': 'local'})
        self.store.relation(
            {
                'src': self.paper['id'],
                'dst': self.external['id'],
                'kind': 'cites',
                'status': 'confirmed',
                'level': 'citation',
                'evidence': [{'ref_index': 7}],
            }
        )
        method = {
            'src': self.paper['id'],
            'dst': self.external['id'],
            'kind': 'improves',
            'explanation': '改进前作',
            'previous_problem': '采样效率',
            'change': '知情采样',
            'evidence': [
                {'source_id': 'source-a', 'quote': docs[0]['text']},
                {'source_id': 'target-fulltext', 'quote': docs[-1]['text']},
            ],
            'local_review_verdict': {
                'supported': True,
                'method_relation': True,
                'unsupported_details': [],
                'reason': '原文明确支持改进。',
            },
        }
        weak = dict(
            method, kind='similar', evidence=[{'source_id': 'source-b', 'quote': docs[1]['text']}]
        )
        review = {
            'reviews': [
                {
                    'review_index': 0,
                    'supported': True,
                    'method_relation': True,
                    'unsupported_details': [],
                    'reason': '原文与引用编号一致。',
                }
            ]
        }

        def cloud_response(store, prompt, purpose, **kw):
            result = json.loads(json.dumps(review))
            result['reviews'][0]['id'] = json.loads(prompt.split('\n', 1)[1])['claims'][0]['id']
            return result

        with (
            patch('kbapp.library.recognition.prepare_relation_sources'),
            patch(
                'kbapp.research.local_verification.verify_relations',
                return_value={'relations': [dict(weak) for _ in range(4)] + [method]},
            ),
            patch('kbapp.research.relations.complete', side_effect=cloud_response) as cloud,
            patch('kbapp.integrations.models.release_local'),
        ):
            result = analyze_relations(self.store)
        self.assertEqual(result['processed'], 1)
        cloud.assert_called_once()
        self.assertEqual(
            next((r for r in self.store.relations() if r['kind'] == 'improves'))['status'],
            'confirmed',
        )
        self.assertEqual(
            next((r for r in self.store.relations() if r['kind'] == 'similar'))['status'],
            'candidate',
        )

    def test_weaker_candidate_cannot_replace_confirmed_evidence_but_stale_source_can(self):
        relation = {
            'src': self.paper['id'],
            'dst': self.external['id'],
            'kind': 'improves',
            'status': 'confirmed',
            'level': 'fulltext',
            'explanation': '已核原文结论',
            'evidence': [{'source_id': 'original', 'quote': 'Original verified evidence.'}],
        }
        rid = self.store.relation(relation)
        weaker = dict(
            relation,
            status='candidate',
            explanation='新片段依据不足',
            evidence=[{'source_id': 'new', 'quote': 'A weaker proposal.'}],
        )
        self.store.relation(weaker)
        found = self.store.relations()[0]
        self.assertEqual(found['status'], 'confirmed')
        self.assertEqual(found['evidence'], relation['evidence'])
        with self.store.db() as db:
            history = json.loads(
                db.execute(
                    'SELECT payload FROM relation_history WHERE relation_id=?', (rid,)
                ).fetchone()[0]
            )
        self.assertEqual(history['history_event'], 'automatic_candidate_not_applied')
        self.assertEqual(history['evidence'], weaker['evidence'])
        self.store.relation(dict(weaker, evidence_stale=True))
        self.assertEqual(self.store.relations()[0]['status'], 'candidate')
        self.store.relation(dict(relation, status='rejected'), manual=True)
        self.store.relation(relation)
        self.assertEqual(self.store.relations()[0]['status'], 'rejected')

    @WINDOWS_ONLY
    def test_runtime_identity_uses_process_time_and_port_owner_without_powershell(self):
        from kbapp.integrations.models import _runtime_owned

        record = {
            'pid': 42,
            'created_ticks': 123,
            'backend': 'native',
            'executable': 'models\\llama-server.exe',
        }
        with (
            patch('kbapp.integrations.models.process_info', return_value=dict(record)),
            patch('kbapp.integrations.models.listener_pid', return_value=42),
            patch('kbapp.integrations.models.subprocess.run') as shell,
        ):
            self.assertTrue(_runtime_owned(record))
            self.assertFalse(_runtime_owned(dict(record, created_ticks=124)))
            shell.assert_not_called()
        with (
            patch('kbapp.integrations.models.process_info', return_value=dict(record)),
            patch('kbapp.integrations.models.listener_pid', return_value=999),
        ):
            self.assertFalse(_runtime_owned(record))

    def test_stop_rechecks_creation_time_before_terminating_reused_pid(self):
        from kbapp.integrations.models import stop_text_runtime

        self.store.set_state(
            'local_text_runtime', {'pid': 42, 'created_ticks': 123, 'backend': 'native'}
        )
        with (
            patch('kbapp.integrations.models._runtime_owned', return_value=True),
            patch('kbapp.integrations.models.process_info', return_value={'created_ticks': 124}),
            patch('kbapp.integrations.models.terminate_owned') as terminate,
        ):
            with self.assertRaisesRegex(ValueError, '身份已变化'):
                stop_text_runtime(self.store)
            terminate.assert_not_called()
        self.assertEqual(self.store.state('local_text_runtime')['created_ticks'], 123)

    @DPAPI_REQUIRED
    def test_unavailable_glm_does_not_fall_back_to_35b_ocr(self):
        from kbapp.research.relations import analyze_relations

        self.configure()
        self.corpus()
        self.store.save_settings({'relation_extractor': 'local'})
        self.store.relation(
            {
                'src': self.paper['id'],
                'dst': self.external['id'],
                'kind': 'cites',
                'status': 'confirmed',
            }
        )
        with (
            patch(
                'kbapp.library.recognition.prepare_relation_sources',
                side_effect=ValueError('GLM-OCR unavailable'),
            ),
            patch('kbapp.research.local_verification.verify_relations') as local,
            patch('kbapp.research.relations.complete') as cloud,
            patch('kbapp.integrations.models.release_local'),
        ):
            result = analyze_relations(self.store)
        self.assertEqual(result['state'], 'deferred')
        local.assert_not_called()
        cloud.assert_not_called()
        self.assertEqual(self.store.state('active_version'), 'test')

    def test_ocr_evidence_provenance_keeps_page_without_sending_local_file_paths(self):
        docs = self.corpus()
        docs[0]['ocr_provenance'] = {
            'model': 'glm-ocr:latest',
            'page': 1,
            'pdf_hash': 'hash',
            'pdf': 'private/path.pdf',
            'cache': 'private/cache',
        }
        item = {
            'src': self.paper['id'],
            'dst': self.external['id'],
            'kind': 'improves',
            'evidence': [{'source_id': 'source-a', 'quote': docs[0]['text']}],
        }
        relation = validate_ai_relation(item, {s['id']: s for s in docs}, self.store.papers())
        self.assertEqual(
            relation['evidence'][0]['ocr_provenance'],
            {'model': 'glm-ocr:latest', 'page': 1, 'pdf_hash': 'hash'},
        )

    @DPAPI_REQUIRED
    def test_verification_stop_before_call_preserves_index_and_makes_no_cloud_request(self):
        from kbapp.research.relations import analyze_relations

        self.configure()
        self.corpus()
        self.store.relation(
            {
                'src': self.paper['id'],
                'dst': self.external['id'],
                'kind': 'cites',
                'status': 'confirmed',
            }
        )
        with patch('kbapp.research.relations.complete') as model:
            result = analyze_relations(self.store, should_stop=lambda: True)
        self.assertEqual(result['state'], 'interrupted')
        model.assert_not_called()
        self.assertEqual(result['processed'], 0)
        self.assertEqual(self.store.state('active_version'), 'test')

    @DPAPI_REQUIRED
    def test_web_verification_stops_async_job_without_losing_published_index(self):
        import threading
        from kbapp.workflows import verification as verification

        self.configure()
        self.corpus()
        entered = threading.Event()
        finish = threading.Event()

        def analyze(store, progress, **options):
            options['on_status'](
                {'processed': 0, 'reused': 1, 'completed': 1, 'total': 2, 'pending': 1}
            )
            entered.set()
            finish.wait(5)
            self.assertTrue(options['should_stop']())
            return {'state': 'interrupted', 'processed': 0}

        with (
            patch('kbapp.workflows.verification.analyze_relations', side_effect=analyze),
            patch('kbapp.workflows.verification.publish_lineage'),
            patch('kbapp.workflows.verification.publish_audit'),
            patch('kbapp.workflows.verification.export_static'),
        ):
            response = verification.control_verification(
                self.store, {'action': 'start', 'filters': {'direction': '02'}}
            )
            try:
                self.assertTrue(entered.wait(5))
                self.assertEqual(response['state'], 'started')
                self.assertEqual(
                    verification.control_verification(self.store, {'action': 'stop'})['state'],
                    'stopping',
                )
            finally:
                finish.set()
                verification._thread.join(5)
        state = self.store.state('analysis_state')
        self.assertEqual(state['state'], 'interrupted')
        self.assertEqual(state['completed'], 1)
        self.assertEqual(self.store.state('active_version'), 'test')

    @DPAPI_REQUIRED
    def test_web_verification_continues_batches_and_counts_cache_once(self):
        from kbapp.workflows.verification import run_verification

        self.configure()
        self.corpus()
        calls = []

        def analyze(store, progress, **options):
            n = len(calls)
            calls.append(n)
            self.assertEqual(len(options['completed_ids']), n)
            result = {
                'state': 'complete',
                'processed': 1,
                'reused': 0,
                'completed': n + 1,
                'total': 2,
                'pending': 1 - n,
                'batch_completed_ids': [f'paper-{n}'],
            }
            options['on_status'](result)
            return result

        with (
            patch('kbapp.workflows.verification.analyze_relations', side_effect=analyze),
            patch('kbapp.workflows.verification.publish_lineage'),
            patch('kbapp.workflows.verification.publish_audit'),
            patch('kbapp.workflows.verification.export_static'),
        ):
            result = run_verification(self.store, 'test-verification', {})
        self.assertEqual(len(calls), 2)
        self.assertEqual(result['state'], 'complete')
        self.assertEqual(result['processed'], 2)
        self.assertEqual(result['reused'], 0)
        self.assertEqual(result['completed'], 2)

    def test_interrupted_verification_recovers_after_service_restart_and_keeps_evidence(self):
        from kbapp.workflows.verification import recover_verification
        from kbapp.core.locks import update_lock

        self.corpus()
        self.store.set_state(
            'analysis_state', {'job': 'old', 'state': 'running', 'completed': 3, 'processed': 2}
        )
        self.store.set_state('recognition_state', {'state': 'running'})
        with update_lock(self.store.root, 'workbench-analysis.lock'):
            recover_verification(self.store)
            self.assertEqual(self.store.state('analysis_state')['state'], 'running')
        recover_verification(self.store)
        state = self.store.state('analysis_state')
        self.assertEqual(state['state'], 'interrupted')
        self.assertEqual(state['completed'], 3)
        self.assertEqual(self.store.state('recognition_state')['state'], 'stopped')
        self.assertEqual(self.store.state('active_version'), 'test')

    def test_api_session_origin_approval_and_settings(self):
        from fastapi.testclient import TestClient
        from support import service_for

        service = service_for(self.store)
        old = service.store
        service.store = self.store
        try:
            with TestClient(service.app) as client:
                self.assertEqual(client.post('/api/settings', json={}).status_code, 403)
                self.assertEqual(
                    client.post('/api/models', json={'action': 'stop', 'role': 'all'}).status_code,
                    403,
                )
                self.assertEqual(
                    client.post('/api/verification', json={'action': 'start'}).status_code, 403
                )
                self.assertEqual(
                    client.get(
                        '/api/session', headers={'Origin': 'https://unrelated.example'}
                    ).status_code,
                    403,
                )
                token = client.get('/api/session').json()['token']
                headers = {'X-KB-Token': token}
                self.assertEqual(
                    client.post(
                        '/api/approvals/request', json={'paper_id': 'oa:W123'}, headers=headers
                    ).status_code,
                    200,
                )
                self.assertEqual(client.get('/api/file?paper=oa%3AW123').status_code, 404)
                self.assertEqual(
                    client.post(
                        '/api/settings', json={'cloud_enabled': True}, headers=headers
                    ).status_code,
                    400,
                )
                self.assertEqual(
                    client.post(
                        '/api/graph/edges',
                        json={'ids': [self.paper['id'], 'oa:W123']},
                        headers=headers,
                    ).status_code,
                    200,
                )
                with patch(
                    'kbapp.integrations.models.model_status', return_value={'items': []}
                ) as status:
                    self.assertEqual(client.get('/api/models').json(), {'items': []})
                    status.assert_called_once_with(self.store)
                with patch(
                    'kbapp.workflows.model_control.start_control', return_value={'state': 'started'}
                ) as control:
                    self.assertEqual(
                        client.post(
                            '/api/models', json={'action': 'stop', 'role': 'text'}, headers=headers
                        ).json(),
                        {'state': 'started'},
                    )
                    control.assert_called_once_with(self.store, 'stop', 'text')
                with patch(
                    'kbapp.workflows.verification.control_verification',
                    return_value={'state': 'started'},
                ) as verify:
                    payload = {'action': 'start', 'filters': {'project': True, 'direction': '02'}}
                    self.assertEqual(
                        client.post('/api/verification', json=payload, headers=headers).json(),
                        {'state': 'started'},
                    )
                    verify.assert_called_once_with(self.store, payload)
        finally:
            service.store = old


if __name__ == '__main__':
    unittest.main(verbosity=2)
