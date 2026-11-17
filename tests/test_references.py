'自动参考文献的缓存、身份匹配、失败保留及来源口径。'
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['KB_NO_STARTUP_JOBS'] = '1'
os.environ['KB_NO_REFERENCE_WORKER'] = '1'
from kbapp.storage.store import Store
from kbapp.core.io import atomic
from kbapp.workflows.references import citation_info, enqueue, view, collect, work_one, entry, attach_citations, recover_jobs
from kbapp.integrations.openalex import OpenAlex, map_work

def work(wid, title='A paper', doi=None, refs=(), count=12):
    return {'id': 'https://openalex.org/' + wid, 'title': title, 'doi': 'https://doi.org/' + doi if doi else None, 'publication_year': 2020, 'referenced_works': ['https://openalex.org/' + x for x in refs], 'cited_by_count': count}

class References(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name))
        self.paper = {'id': 'p', 'kind': 'library', 'title': 'Original title', 'doi': '10.1234/main', 'year': 2020, 'citekey': 'Original', 'card': 'note.md', 'read': '人工在读', 'directions': ['02']}
        self.store.put_papers([self.paper])
        atomic(self.store.base / 'note.md', '人工正文')

    def tearDown(self):
        self.tmp.cleanup()

    def fetch(self, path, params=None):
        if path.startswith('works/'):
            return work('W1', doi='10.1234/main', refs=['W2'], count=0)
        return {'results': [work('W2', 'Referenced paper', doi='10.1234/ref', count=234)]}

    def test_unknown_zero_and_legacy_are_distinct(self):
        self.assertIsNone(citation_info({})['citation_count'])
        self.assertIsNone(citation_info({'cited_by': True})['citation_count'])
        self.assertEqual(citation_info({'cited_by': 0})['citation_count'], 0)
        self.assertEqual(citation_info({'cited_by': 12, 'openalex_id': 'W1'})['citation_source'], 'OpenAlex 缓存')
        self.assertIsNone(map_work({'id': 'https://openalex.org/W1'})['cited_by'])

    def test_automatic_queue_deduplicates_and_fresh_cache_skips_network(self):
        enqueue(self.store, 'p')
        enqueue(self.store, 'p')
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reference_jobs').fetchone()[0], 1)
        with patch.object(OpenAlex, 'get', side_effect=self.fetch):
            self.assertTrue(work_one(self.store))
        with patch.object(OpenAlex, 'get', side_effect=AssertionError('fresh cache must not fetch')):
            self.assertEqual(enqueue(self.store, 'p')['status'], 'complete')
            self.assertFalse(work_one(self.store))
        result = view(self.store, 'p', auto=False)
        self.assertEqual(result['citation']['citation_count'], 0)
        self.assertEqual(result['items'][0]['citation_count'], 234)
        self.assertEqual(result['items'][0]['paper_id'], 'oa:W2')

    def test_metadata_merge_keeps_library_identity_manual_fields_and_body(self):
        target = {'id': 'library-ref', 'kind': 'library', 'title': 'Hand title', 'doi': '10.1234/ref', 'directions': ['04'], 'read': '已读', 'citekey': 'HandKey'}
        self.store.put_papers([target])
        with patch.object(OpenAlex, 'get', side_effect=self.fetch):
            collect(self.store, 'p')
        paper = self.store.papers()['p']
        ref = self.store.papers()['library-ref']
        self.assertEqual((paper['title'], paper['read'], paper['directions']), ('Original title', '人工在读', ['02']))
        self.assertEqual((ref['title'], ref['citekey'], ref['citation_count']), ('Hand title', 'HandKey', 234))
        self.assertNotIn('oa:W2', self.store.papers())
        self.assertEqual((self.store.base / 'note.md').read_text(encoding='utf-8'), '人工正文')

    def test_failed_refresh_retains_published_list_and_count(self):
        with patch.object(OpenAlex, 'get', side_effect=self.fetch):
            collect(self.store, 'p')
        before = view(self.store, 'p', auto=False)
        with patch.object(OpenAlex, 'get', side_effect=ValueError('来源暂不可用')):
            result = collect(self.store, 'p', force=True)
        self.assertEqual(result['status'], 'partial')
        self.assertTrue(result['gaps'])
        after = view(self.store, 'p', auto=False)
        self.assertEqual(after['items'][0]['paper_id'], before['items'][0]['paper_id'])
        self.assertEqual(after['citation']['citation_count'], 0)

    def test_pdf_change_invalidates_list_fingerprint(self):
        import pymupdf
        path = self.store.base / 'p.pdf'
        with pymupdf.open() as doc:
            doc.new_page()
            doc.save(path)
        self.store.patch_paper('p', {'pdf': 'p.pdf'})
        with patch.object(OpenAlex, 'get', side_effect=self.fetch):
            enqueue(self.store, 'p')
            work_one(self.store)
        self.assertFalse(view(self.store, 'p', auto=False)['stale'])
        with path.open('ab') as f:
            f.write(b'\n% changed')
        self.assertTrue(view(self.store, 'p', auto=False)['stale'])
        self.assertEqual(enqueue(self.store, 'p')['status'], 'queued')

    def test_title_similarity_without_exact_year_is_not_a_match(self):
        other = {'id': 'b', 'title': 'Long exact reference title', 'year': 2021, 'kind': 'library', 'citation_count': 9}
        self.store.put_papers([other])
        row = entry({'title': other['title'], 'year': 2020}, self.store.papers())
        self.assertIsNone(row['paper_id'])
        self.assertIsNone(row['citation_count'])
        row = entry({'title': other['title'], 'year': 2021}, self.store.papers())
        self.assertEqual(row['paper_id'], 'b')

    def test_wrong_doi_is_not_ingested(self):
        with patch.object(OpenAlex, 'get', return_value=work('W99', doi='10.1234/wrong')), patch('kbapp.workflows.references.crossref', side_effect=ValueError('unavailable')):
            with self.assertRaises(ValueError):
                collect(self.store, 'p')
        self.assertNotIn('oa:W99', self.store.papers())
        self.assertIsNone(citation_info(self.store.papers()['p'])['citation_count'])

    def test_manual_rejection_remains_rejected_and_citation_is_not_method_evidence(self):
        self.store.put_papers([{'id': 'b', 'kind': 'external', 'title': 'ref', 'openalex_id': 'W2'}])
        self.store.relation({'src': 'p', 'dst': 'b', 'kind': 'cites', 'status': 'rejected'}, manual=True)
        with patch.object(OpenAlex, 'get', side_effect=self.fetch):
            collect(self.store, 'p')
        rel = self.store.relations()[0]
        self.assertEqual(rel['status'], 'rejected')
        self.assertEqual(rel['manual'], 1)
        self.assertFalse(any((r['kind'] != 'cites' for r in self.store.relations())))
        self.assertEqual(view(self.store, 'p', auto=False)['items'][0]['association_status'], 'manual_rejected')

    def test_anonymous_openalex_does_not_send_authorization(self):
        seen = []

        def open_url(request, timeout):
            seen.append(dict(request.header_items()))
            response = io.BytesIO(b'{"results":[]}')
            response.headers = {}
            return response
        with patch('kbapp.integrations.openalex.urlopen', side_effect=open_url), patch('kbapp.library.network.time.sleep'):
            OpenAlex(self.store, allow_anonymous=True).get('works', {'search': 'test'})
        self.assertNotIn('Authorization', seen[0])

    def test_paper_detail_queues_without_synchronous_network_and_refresh_needs_token(self):
        from fastapi.testclient import TestClient
        from support import service_for
        service = service_for(self.store)
        with patch.object(service, 'store', self.store), patch('kbapp.workflows.references.collect', side_effect=AssertionError('GET must not collect inline')):
            with TestClient(service.app) as client:
                result = client.get('/api/paper', params={'paper': 'p'})
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.json()['references']['job']['status'], 'queued')
                self.assertEqual(client.post('/api/references/refresh', json={'paper_id': 'p'}).status_code, 403)
                token = client.get('/api/session').json()['token']
                result = client.post('/api/references/refresh', json={'paper_id': 'p'}, headers={'x-kb-token': token})
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.json()['job']['status'], 'queued')

    def test_search_citation_metrics_use_current_paper_not_index_snapshot(self):
        self.store.patch_paper('p', {'citation_count': 0, 'citation_source': 'OpenAlex'})
        data = attach_citations(self.store, {'papers': [{'id': 'p'}], 'results': [{'id': 'chunk', 'paper_id': 'p'}]})
        self.assertEqual(data['papers'][0]['citation_count'], 0)
        self.assertEqual(data['results'][0]['citation_count'], 0)

    def test_interrupted_reference_job_recovers_without_erasing_published_list(self):
        with patch.object(OpenAlex, 'get', side_effect=self.fetch):
            enqueue(self.store, 'p')
            work_one(self.store)
        with self.store.db() as db:
            db.execute("UPDATE reference_jobs SET status='running'")
        recover_jobs(self.store)
        result = view(self.store, 'p', auto=False)
        self.assertEqual(result['job']['status'], 'queued')
        self.assertEqual(result['items'][0]['citation_count'], 234)

    def test_crossref_fallback_keeps_its_source_and_real_zero(self):
        self.store.put_papers([{'id': 'b', 'kind': 'library', 'title': 'Reference method', 'doi': '10.1234/ref', 'citation_count': 37}])
        response = {'DOI': '10.1234/main', 'is-referenced-by-count': 0, 'reference': [{'DOI': '10.1234/ref', 'article-title': 'Reference method', 'year': '2020'}]}
        with patch.object(OpenAlex, 'get', side_effect=ValueError('offline')), patch('kbapp.workflows.references.crossref', return_value=response):
            result = collect(self.store, 'p')
        self.assertEqual(result['sources'], ['Crossref'])
        self.assertEqual(result['items'][0]['paper_id'], 'b')
        self.assertEqual(citation_info(self.store.papers()['p'])['citation_count'], 0)
        self.assertEqual(citation_info(self.store.papers()['p'])['citation_source'], 'Crossref')

    def test_pdf_fallback_extracts_literal_references_without_a_model(self):
        import pymupdf
        path = self.store.base / 'refs.pdf'
        text = 'References\n' + '\n'.join((f'[{i}] A. Author, "Reference method number {i}", Robotics Journal, 2020.' for i in range(1, 7)))
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text((45, 50), text, fontsize=10)
            doc.save(path)
        self.store.patch_paper('p', {'pdf': 'refs.pdf', 'doi': None})
        self.store.put_papers([{'id': 'b', 'kind': 'library', 'title': 'Reference method number 1', 'year': 2020, 'citation_count': 0}])
        with patch.object(OpenAlex, 'get', side_effect=ValueError('offline')):
            result = collect(self.store, 'p')
        row = next((x for x in result['items'] if x.get('paper_id') == 'b'))
        self.assertEqual(row['origin'], 'pdf_reference')
        self.assertEqual(row['ref_index'], 1)
        self.assertIn('Robotics Journal', row['raw'])
        self.assertEqual(row['citation_count'], 0)

    def test_index_publication_keeps_citation_refresh_completed_during_build(self):
        from kbapp.workflows.update import run_update
        snapshot = dict(self.paper, citation_count=10, citation_updated='2020-01-01T00:00:00+08:00')

        def build(store, index, corpus, progress, limit):
            store.patch_paper('p', {'citation_count': 0, 'citation_source': 'OpenAlex', 'citation_updated': '2030-01-01T00:00:00+08:00'})
            atomic(index / 'corpus_meta.json', {'sources': 0, 'vector_count': 0, 'vector_pending': 0})
            return []
        with patch('kbapp.workflows.update.sync_library', return_value=([snapshot], [], [])), patch('kbapp.workflows.update.build_corpus', return_value=[]), patch('kbapp.workflows.update.build_vectors', side_effect=build), patch('kbapp.workflows.update.publish_notes'), patch('kbapp.workflows.export.export_static'), patch('kbapp.workflows.reports.publish_lineage'), patch('kbapp.workflows.reports.publish_audit'):
            run_update(self.store, bootstrap=True)
        self.assertEqual(self.store.papers()['p']['citation_count'], 0)
        self.assertEqual(self.store.papers()['p']['citation_updated'], '2030-01-01T00:00:00+08:00')
if __name__ == '__main__':
    unittest.main()
