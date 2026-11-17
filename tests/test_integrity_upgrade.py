"""合成边界回归；真实 PDF/模型验收另存 evaluation，不冒充真实样本。"""
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pymupdf
from kbapp.storage.store import Store
from kbapp.library.sources import identity
from kbapp.library import documents as documents
from kbapp.workflows.status import status as workbench_status
os.environ['KB_NO_STARTUP_JOBS'] = '1'
os.environ['KB_NO_REFERENCE_WORKER'] = '1'

class Integrity(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)
        self.paper = {'id': 'z:1:ITEM0001', 'kind': 'library', 'title': 'Original title', 'metadata_source': 'zotero', 'doi': '10.1234/test', 'citekey': 'Test', 'pdf': str(Path(self.tmp.name) / 'paper.pdf')}
        self.store.put_papers([self.paper])

    def pdf(self):
        path = Path(self.paper['pdf'])
        doc = pymupdf.open()
        for text in ('This is the native method and its assumptions. ' + 'sampling ' * 20, 'References\n[1] Earlier method. doi:10.1234/earlier'):
            page = doc.new_page()
            page.insert_textbox((50, 50, 500, 750), text)
        doc.save(path)
        doc.close()
        return documents.register(self.store, self.paper, path, 'ATTACH01')

    def test_library_key_is_distinct_from_citation_key(self):
        self.assertNotEqual(identity({'zotero_key': 'ABCD1234', 'library_id': 1}), identity({'zotero_key': 'ABCD1234', 'library_id': 2}))

    def test_zotero_conflict_cannot_overwrite_authoritative_metadata(self):
        result = self.store.patch_paper(self.paper['id'], {'title': 'AI title', 'citation_count': 5})
        self.assertEqual(result['title'], 'Original title')
        self.assertEqual(result['citation_count'], 5)
        self.store.patch_paper(self.paper['id'], {'title': 'AI title'})
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM metadata_conflicts').fetchone()[0], 1)

    def test_migration_is_idempotent_and_fk_rejects_orphan(self):
        Store(self.tmp.name)
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM schema_migrations').fetchone()[0], 1)
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute('INSERT INTO document_sections VALUES(?,?,?,?)', ('orphan', 'missing', 'Title', 1))
        backup = next(self.store.paths.backups.glob('pre-schema-*.sqlite3'))
        with closing(sqlite3.connect(backup)) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_same_document_is_idempotent_and_two_attachments_are_distinct(self):
        first = self.pdf()
        again = documents.register(self.store, self.paper, self.paper['pdf'], 'ATTACH01')
        other = documents.register(self.store, self.paper, self.paper['pdf'], 'ATTACH02')
        self.assertEqual(first['id'], again['id'])
        self.assertNotEqual(first['id'], other['id'])
        self.assertEqual(first['sha256'], other['sha256'])

    def test_failed_page_preserves_following_page_and_single_page_resume(self):
        doc = self.pdf()
        original = documents.classify

        def broken(page):
            if page.number == 0:
                raise ValueError('page fault')
            return original(page)
        with patch('kbapp.library.documents.classify', side_effect=broken):
            result = documents.process(self.store, doc)
        self.assertEqual(result['failed'], 1)
        self.assertEqual(documents.page_record(self.store, doc['id'], 2)['status'], 'complete')
        documents.process(self.store, doc, [1])
        self.assertEqual(documents.page_record(self.store, doc['id'], 1)['status'], 'complete')

    def test_changed_file_cannot_be_read_as_old_evidence(self):
        doc = self.pdf()
        documents.process(self.store, doc)
        Path(self.paper['pdf']).write_bytes(b'changed')
        self.assertEqual(documents.sources(self.store), [])
        with self.assertRaisesRegex(ValueError, 'PDF已变化'):
            documents.process(self.store, doc)

    def test_native_and_scanned_with_text_are_not_confused(self):
        doc = pymupdf.open()
        p = doc.new_page()
        p.insert_text((30, 30), 'Header text only, enough characters to fool old OCR detection')
        pix = pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 100, 100), False)
        pix.clear_with(120)
        p.insert_image((0, 60, 595, 842), pixmap=pix)
        self.assertEqual(documents.classify(p)['classification'], 'MIXED')
        doc.close()

    def test_native_parse_sources_have_exact_attachment_and_page(self):
        doc = self.pdf()
        documents.process(self.store, doc)
        sources = documents.sources(self.store)
        self.assertTrue(sources)
        self.assertTrue(all((s['document_id'] == doc['id'] and '#page=' in s['url'] for s in sources)))

    def test_unilateral_relation_is_insufficient_without_model_calls(self):
        from kbapp.research.review import review_relation
        source = {'id': 's', 'paper_id': self.paper['id'], 'kind': 'paper_text', 'text': 'We improve sampling.', 'page_start': 1}
        item = {'src': self.paper['id'], 'dst': 'other', 'kind': 'improves', 'evidence': [{'source_id': 's', 'quote': source['text']}]}

        def forbidden(*a, **kw):
            raise AssertionError('insufficient evidence must not call model')
        result = review_relation(self.store, item, {'s': source}, {'id': 'other'}, {}, forbidden, forbidden)
        self.assertEqual(result['status'], 'insufficient')
        self.assertFalse(result['accepted'])

    def test_wrong_paper_and_fabricated_relation_quotes_are_rejected(self):
        from kbapp.research.rules import validate_ai_relation
        item = {'src': self.paper['id'], 'dst': 'other', 'kind': 'improves', 'evidence': [{'source_id': 's', 'quote': 'Literal quote.'}]}
        source = {'id': 's', 'paper_id': 'third', 'kind': 'paper_text', 'text': 'Literal quote.'}
        with self.assertRaises(ValueError):
            validate_ai_relation(item, {'s': source}, self.store.papers())

    def test_doi_normalization_preserves_distinct_zotero_items(self):
        from kbapp.core.metadata import normalize_doi
        self.assertEqual(normalize_doi('https://doi.org/10.1234/ABC'), '10.1234/abc')
        other = dict(self.paper, id='z:2:ITEM0001')
        self.store.put_papers([other])
        self.assertEqual(len(self.store.papers()), 2)
        with self.assertRaisesRegex(ValueError, '多个独立条目'):
            self.store.resolve('10.1234/test')

    def test_page_api_and_review_survive_reprocessing(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from kbapp.web.document_api import install
        doc = self.pdf()
        documents.process(self.store, doc)
        app = FastAPI()
        install(app, self.store)
        with TestClient(app) as client:
            base = '/api/documents/' + doc['id']
            page = client.get(base + '/pages/1').json()
            block = page['blocks'][0]
            self.assertEqual(client.get(base + '/file').status_code, 200)
            self.assertEqual(client.get(base + '/pages/1/image').headers['content-type'], 'image/png')
            self.assertEqual(client.get(base + '/pages/999/image').status_code, 404)
            result = client.post(base + '/blocks/' + block['id'] + '/review', json={'status': 'confirmed', 'note': 'test review'})
            self.assertEqual(result.status_code, 200)
            documents.process(self.store, doc, [1], force=True)
            changed = client.get(base + '/pages/1').json()
            self.assertEqual(changed['blocks'][0]['payload']['review_status'], 'confirmed')
            self.assertTrue(changed['review_history'])

    def test_model_revision_is_part_of_signature(self):
        from kbapp.integrations.model_identity import signature
        import io
        with patch('kbapp.integrations.model_identity.urlopen', return_value=io.BytesIO(b'{"models":[{"name":"m","digest":"rev1"}]}')):
            first = signature('m', 'http://test', refresh=True)
        with patch('kbapp.integrations.model_identity.urlopen', return_value=io.BytesIO(b'{"models":[{"name":"m","digest":"rev2"}]}')):
            second = signature('m', 'http://test', refresh=True)
        self.assertNotEqual(first, second)

    def test_crashed_document_job_recovers_without_model_or_data_loss(self):
        from kbapp.web.document_api import recover_documents
        doc = self.pdf()
        documents.process(self.store, doc)
        with self.store.db() as db:
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)', ('dead', 'documents', 'running', None, '{}', 'working', 'test', None, None))
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)', ('alive', 'documents', 'running', os.getpid(), '{}', 'working', 'test', None, None))
        recover_documents(self.store)
        with self.store.db() as db:
            jobs = {r['id']: r['status'] for r in db.execute('SELECT * FROM jobs')}
        self.assertEqual(jobs, {'dead': 'interrupted', 'alive': 'running'})
        self.assertEqual(documents.page_record(self.store, doc['id'], 1)['status'], 'complete')

    def test_failed_reparse_excludes_old_page_from_current_search_sources(self):
        doc = self.pdf()
        documents.process(self.store, doc)
        with patch('kbapp.library.documents.classify', side_effect=ValueError('page failed')):
            documents.process(self.store, doc, [1], force=True)
        # D02：失败页不进入来源；本 PDF 仅剩参考文献页，参考文献不作为证据来源
        self.assertFalse(any((s['page_start'] == 1 for s in documents.sources(self.store))))
        self.assertEqual(documents.sources(self.store), [])

    def test_legacy_chunker_continues_after_bad_page(self):
        import kbapp.library.chunk as chunk
        self.pdf()
        original = chunk.page_text

        def broken(page):
            if page.number == 0:
                raise ValueError('bad page')
            return original(page)
        with patch('kbapp.library.chunk.page_text', side_effect=broken), self.assertWarns(RuntimeWarning):
            blocks = chunk.extract_pdf(self.paper['pdf'])
        self.assertTrue(blocks)
        self.assertTrue(all((b[0] == 2 for b in blocks)))

    def test_unknown_embedding_revision_cannot_reuse_query_vectors(self):
        from kbapp.search.query import query_vector
        with patch('kbapp.integrations.model_identity.embedding_signature', return_value={'revision': 'unknown'}):
            vector, status = query_vector(self.store, 'sampling', wait=True)
        self.assertIsNone(vector)
        self.assertEqual(status, 'unavailable')

    def test_relation_evidence_update_does_not_replace_fk_parent(self):
        self.store.put_papers([dict(self.paper, id='other')])
        item = {'src': self.paper['id'], 'dst': 'other', 'kind': 'similar', 'status': 'candidate', 'level': 'fulltext', 'evidence': [{'paper_id': self.paper['id'], 'source_id': 's', 'quote': 'Exact passage.'}]}
        rid = self.store.relation(item)
        self.store.relation(dict(item, explanation='Updated explanation'))
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM relation_evidence WHERE relation_id=?', (rid,)).fetchone()[0], 1)
            self.assertEqual(list(db.execute('PRAGMA foreign_key_check')), [])

    def test_legacy_seed_audit_keeps_history_and_human_confirmation(self):
        from kbapp.research.verified import audit_legacy_seeds
        self.store.put_papers([dict(self.paper, id='other')])
        item = {'src': self.paper['id'], 'dst': 'other', 'kind': 'similar', 'status': 'confirmed', 'level': 'fulltext', 'reviewed_by': 'Codex 原文复核，2026-10-31'}
        automatic = self.store.relation(item)
        human = self.store.relation(dict(item, kind='compares'), manual=True)
        audit_legacy_seeds(self.store)
        audit_legacy_seeds(self.store)
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT status FROM relations WHERE id=?', (automatic,)).fetchone()[0], 'candidate')
            self.assertEqual(db.execute('SELECT status FROM relations WHERE id=?', (human,)).fetchone()[0], 'confirmed')
            self.assertEqual(db.execute('SELECT count(*) FROM relation_history WHERE relation_id=?', (automatic,)).fetchone()[0], 1)

    def test_lineage_export_accepts_null_change_and_preserves_user_notes(self):
        from kbapp.workflows.reports import publish_lineage
        folder = self.store.base / '_知识库/50_谱系'
        folder.mkdir(parents=True)
        path = folder / '00_测试.md'
        path.write_text('用户已有笔记', encoding='utf-8')
        data = [{'name': 'Test', 'nodes': [dict(self.paper, directions=['00'])], 'edges': [{'kind': 'improves', 'explanation': 'Evidence-backed description', 'change': None, 'evidence': []}]}]
        with patch('kbapp.workflows.reports.chains', return_value=data):
            publish_lineage(self.store)
            publish_lineage(self.store)
        text = path.read_text(encoding='utf-8')
        self.assertEqual(text.count('用户已有笔记'), 1)
        self.assertIn('Evidence-backed description', text)
        self.assertNotIn('None', text)

    def test_status_distinguishes_saved_vectors_from_compatible_vectors(self):
        from kbapp.core.io import atomic
        root = self.store.paths.indexes / 'versions/saved'
        root.mkdir(parents=True)
        atomic(root / 'corpus.jsonl', json.dumps({'id': 'one', 'kind': 'metadata'}) + '\n')
        atomic(root / 'corpus_meta.json', {'sources': 1, 'vector_count': 1, 'vector_ids': ['one'], 'embedding': {'version': 1}})
        self.store.set_state('active_version', 'saved')
        current = {'model': 'test', 'revision': 'actual-weights', 'dims': 1024, 'version': 2}
        with patch('kbapp.integrations.model_identity.embedding_signature', return_value=current):
            health = workbench_status(self.store)['index_health']
            self.assertEqual(health['vector_count'], 1)
            self.assertEqual(health['usable_vector_count'], 0)
            self.assertFalse(health['embedding_compatible'])
            self.assertTrue(health['vector_warning'])
            atomic(root / 'corpus_meta.json', {'sources': 1, 'vector_count': 1, 'vector_ids': ['one'], 'embedding': current})
            self.assertEqual(workbench_status(self.store)['index_health']['usable_vector_count'], 1)
