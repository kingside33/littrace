"""Isolated reliability regressions: real PDFs/SQLite, model boundaries only mocked."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf
from kbapp.core.io import digest, now
from kbapp.library import documents
from kbapp.library.evidence_sources import refresh_sources
from kbapp.research import evidence, lineage, lineage_material, review, routing, prompts
from kbapp.storage.store import Store


class ReliabilityEvidence(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name, data_dir=Path(self.temp.name) / '_index')
        self.store.save_settings({'model': 'cloud-one', 'api_base': 'https://models.invalid/v1',
                                  'local_text_model': 'local-one', 'relation_extractor': 'local'})
        self.weight = 'weights-one'
        self.calls = []
        offline = patch('socket.socket.connect', side_effect=OSError('All network disabled in evidence tests'))
        offline.start()
        self.addCleanup(offline.stop)
        for name, replacement in (
            ('kbapp.integrations.model_identity.signature', self.identity),
            ('kbapp.integrations.local_model.complete_local', self.local_model),
            ('kbapp.integrations.cloud.complete', self.cloud_model),
        ):
            mock = patch(name, side_effect=replacement)
            mock.start()
            self.addCleanup(mock.stop)
        self.a = dict(id='a', kind='library', title='New method', abstract='Adaptive sampling improves Target.',
                      year=2024, authors=['New Author'], pdf='a-main.pdf')
        self.b = dict(id='b', kind='library', title='Target algorithm', abstract='Fixed sampling for planning.',
                      year=2020, authors=['Earlier Author'], pdf='b.pdf')
        self.store.put_papers([self.a, self.b])
        self.main = self.make_document(self.a, 'a-main.pdf', 'MAIN', 'PRIMARYONLY The primary paper uses an unrelated method.')
        self.supp = self.make_document(self.a, 'a-supp.pdf', 'SUPP', 'SUPPLEMENTONLY We improve Target [1] by adding adaptive sampling.')
        self.target = self.make_document(self.b, 'b.pdf', 'TARGET', 'The Target algorithm uses fixed sampling for planning.')
        self.source = next(s for s in documents.sources(self.store) if s['document_id'] == self.supp['id'] and s['page_start'] == 2)
        self.target_source = next(s for s in documents.sources(self.store) if s['document_id'] == self.target['id'] and s['page_start'] == 2)
        self.relation = dict(src='a', dst='b', kind='improves', explanation='Improves Target',
                             previous_problem='Fixed sampling', change='Adaptive sampling', conditions='Static maps', evidence=[])
        self.store.relation(dict(src='a', dst='b', kind='cites', status='confirmed', level='citation', evidence=[{'ref_index': '1'}]))

    def identity(self, model, url, api='ollama', **kwargs):
        return dict(model=model, endpoint=url, api=api, revision=self.weight)

    def make_pdf(self, path, text):
        with pymupdf.open() as pdf:
            for n in range(1, 4):
                page = pdf.new_page()
                page.insert_textbox((50, 50, 520, 400), text + '\nPage context number ' + str(n) + ' contains complete original methodological details.')
            pdf.save(path)

    def make_document(self, paper, filename, attachment, text):
        path = self.store.base / filename
        self.make_pdf(path, text)
        doc = documents.register(self.store, paper, path, attachment)
        result = documents.process(self.store, doc)
        self.assertEqual(result['failed'], 0)
        return doc

    def response(self, origin, store, prompt, purpose, **kwargs):
        data = json.loads(prompt.split('\n', 1)[1])
        self.calls.append((origin, purpose, data))
        if purpose == 'lineage_classification':
            return dict(directions=['02'], project_relevance=True, reason='Sampling', quote=self.a['abstract'], methods=[])
        if purpose == 'abstract_lineage':
            return {'relation': None}
        if purpose == 'relation_material':
            refs = [{'sentence_id': s['sentences'][0]['id']} for s in data['sources'] if s['sentences']]
            return {'relation': dict(self.relation, evidence=refs)}
        claim = data['claims'][0]
        return {'reviews': [dict(id=claim['id'], decision='supported', supported=True, method_relation=True,
                                 unsupported_details=[], reason='The original evidence supports the relation.')], 'conflicts': []}

    def local_model(self, *args, **kwargs):
        return self.response('local', *args, **kwargs)

    def cloud_model(self, *args, **kwargs):
        return self.response('cloud', *args, **kwargs)

    def count(self, purpose, origin=None):
        return sum(p == purpose and (origin is None or o == origin) for o, p, _ in self.calls)

    def refreshed(self):
        fresh, gaps = refresh_sources(self.store, [self.source, self.target_source])
        self.assertFalse(gaps)
        return {s['id']: s for s in fresh}

    def prepared(self):
        sources = self.refreshed()
        refs = [{'source_id': s['id'], 'quote': s['text'].split('\n')[0]} for s in sources.values()]
        return dict(relation=dict(self.relation, evidence=refs), sources=sources, references=[],
                    verification_status='ready', input_fingerprint='prepared-fixture')

    def mutate(self, obj, name, value):
        old = getattr(obj, name)
        setattr(obj, name, value)
        self.addCleanup(setattr, obj, name, old)

    def test_refresh_carries_attachment_content_version(self):
        sources = self.refreshed()
        source = next(s for s in sources.values() if s['paper_id'] == 'a')
        self.assertEqual(source.get('sha256'), self.supp['sha256'])
        self.assertTrue(source.get('document_version'))
        ref = evidence.evidence_ref({'source_id': source['id'], 'quote': 'SUPPLEMENTONLY'}, sources)
        self.assertEqual(ref.get('document_id'), self.supp['id'])
        self.assertEqual(ref.get('sha256'), self.supp['sha256'])
        self.assertEqual(ref['page_start'], 2)

    def test_expand_reads_original_attachment_not_primary(self):
        expanded = review.expand_contexts(self.store, [self.source], 1)[0]
        text = '\n'.join(p['text'] for p in expanded.get('supplementary_pages', []))
        self.assertIn('SUPPLEMENTONLY', text)
        self.assertNotIn('PRIMARYONLY', text)
        self.assertEqual(expanded.get('sha256'), self.supp['sha256'])

    def test_missing_attachment_never_expands_primary(self):
        Path(self.supp['path']).unlink()
        expanded = review.expand_contexts(self.store, [self.source], 1)[0]
        self.assertFalse(expanded.get('supplementary_pages'))
        self.assertEqual(expanded.get('context_status'), 'source_unavailable')

    def test_changed_attachment_never_expands_old_version(self):
        self.make_pdf(Path(self.supp['path']), 'REPLACED Attachment content is a new incompatible version.')
        expanded = review.expand_contexts(self.store, [self.source], 1)[0]
        self.assertFalse(expanded.get('supplementary_pages'))
        self.assertEqual(expanded.get('context_status'), 'source_unavailable')

    def test_refresh_rejects_stale_snapshot_and_changed_pdf(self):
        frozen = lineage_material.snapshot(self.store)
        self.make_pdf(Path(self.supp['path']), 'REPLACED Attachment content is a new incompatible version.')
        fresh, gaps = refresh_sources(self.store, [self.source], snapshot=frozen)
        self.assertEqual(fresh, [])
        self.assertTrue(gaps)

    def test_review_contexts_keep_document_and_version(self):
        material = self.prepared()
        result = review.review_relation(self.store, material['relation'], material['sources'], self.b, [], self.cloud_model, self.local_model)
        self.assertTrue(result['accepted'])
        contexts = next(data['original_contexts'] for origin, purpose, data in self.calls if origin == 'cloud')
        own = next(s for s in contexts if s['paper_id'] == 'a')
        self.assertEqual(own.get('document_id'), self.supp['id'])
        self.assertEqual(own.get('sha256'), self.supp['sha256'])
        self.assertEqual(own['page_start'], 2)
        self.assertIn('核心方法关系', result['cloud_route'])

    def test_direct_review_case_rejects_missing_attachment_before_models(self):
        source = self.source
        claim = dict(text='Supported methodological claim', evidence=[{'source_id': source['id'], 'quote': 'SUPPLEMENTONLY'}])
        Path(self.supp['path']).unlink()
        with self.assertRaises(ValueError):
            review.review_case(self.store, claim, {source['id']: source}, self.cloud_model, self.local_model)
        self.assertEqual(self.calls, [])

    def test_review_raw_attachment_enriches_context_version(self):
        source = self.source
        claim = dict(text='Supported methodological claim', evidence=[{'source_id': source['id'], 'quote': 'SUPPLEMENTONLY'}])
        review.review_case(self.store, claim, {source['id']: source}, self.cloud_model, self.local_model)
        contexts = next(data['original_contexts'] for origin, purpose, data in self.calls if origin == 'cloud')
        self.assertEqual(contexts[0].get('sha256'), self.supp['sha256'])

    def test_cached_assessment_rejects_missing_legacy_prepared_attachment(self):
        material = self.prepared()
        lineage_material.assess(self.store, material, 'a|b')
        self.calls.clear()
        Path(self.supp['path']).unlink()
        with self.assertRaises((ValueError, review.ReviewPending)):
            lineage_material.assess(self.store, material, 'a|b')
        self.assertEqual(self.calls, [])

    def test_coordinate_lookup_resolves_attachment_with_store(self):
        # The optional store argument is checked before use so RED is an assertion, not a TypeError.
        import inspect
        self.assertIn('store', inspect.signature(evidence.evidence_ref).parameters)
        ref = evidence.evidence_ref({'source_id': self.source['id'], 'quote': 'SUPPLEMENTONLY'},
                                    {self.source['id']: self.source}, pdf_path=self.main['path'], store=self.store)
        self.assertTrue(ref['bbox'])
        self.assertEqual(ref['document_id'], self.supp['id'])
        self.assertEqual(ref['sha256'], self.supp['sha256'])
        Path(self.supp['path']).unlink()
        with self.assertRaises(ValueError):
            evidence.evidence_ref({'source_id': self.source['id'], 'quote': 'SUPPLEMENTONLY'},
                                  {self.source['id']: self.source}, pdf_path=self.main['path'], store=self.store)

    def test_legacy_source_without_document_id_still_expands(self):
        legacy = dict(self.source, id='legacy', text='PRIMARYONLY', document_id=None)
        expanded = review.expand_contexts(self.store, [legacy], 1)[0]
        self.assertIn('PRIMARYONLY', expanded['supplementary_pages'][0]['text'])

    def assert_assessment_change(self, change):
        material = self.prepared()
        first = lineage_material.assess(self.store, material, 'a|b')
        self.assertEqual(first['verification_status'], 'verified')
        self.assertEqual(lineage_material.assess(self.store, material, 'a|b')['id'], first['id'])
        change()
        second = lineage_material.assess(self.store, material, 'a|b')
        self.assertNotEqual(first['input_fingerprint'], second['input_fingerprint'])
        self.assertEqual(self.count('independent_claim_review'), 2)

    def test_assessment_invalidates_cloud_model(self):
        self.assert_assessment_change(lambda: self.store.save_settings({'model': 'cloud-two'}))

    def test_assessment_invalidates_cloud_endpoint(self):
        self.assert_assessment_change(lambda: self.store.save_settings({'api_base': 'https://other.invalid/v1'}))

    def test_assessment_invalidates_review_version(self):
        self.assert_assessment_change(lambda: self.mutate(review, 'VERSION', review.VERSION + '-new'))

    def test_assessment_invalidates_review_prompt(self):
        self.assert_assessment_change(lambda: self.mutate(review, 'INSTRUCTION', review.INSTRUCTION.rstrip() + ' Check carefully.\n'))

    def test_classification_invalidates_model_weights(self):
        first = lineage.classify_paper(self.store, self.a)
        self.weight = 'weights-two'
        second = lineage.classify_paper(self.store, self.a)
        self.assertNotEqual(first['fingerprint'], second['fingerprint'])
        self.assertEqual(self.count('lineage_classification'), 2)

    def test_classification_invalidates_prompt_and_reuses_unchanged(self):
        first = lineage.classify_paper(self.store, self.a)
        self.assertEqual(first['fingerprint'], lineage.classify_paper(self.store, self.a)['fingerprint'])
        original = prompts.TASKS['classification']
        prompts.TASKS['classification'] = dict(original, instruction=original['instruction'] + ' Check carefully.')
        self.addCleanup(prompts.TASKS.__setitem__, 'classification', original)
        second = lineage.classify_paper(self.store, self.a)
        self.assertNotEqual(first['fingerprint'], second['fingerprint'])

    def test_proposal_invalidates_weights_with_prepared_classification(self):
        context = lineage.classify_paper(self.store, self.a)
        first = lineage.propose_pair(self.store, context, self.b)
        self.weight = 'weights-two'
        second = lineage.propose_pair(self.store, context, self.b)
        self.assertNotEqual(first['input_fingerprint'], second['input_fingerprint'])

    def test_negative_proposal_invalidates_prompt(self):
        context = lineage.classify_paper(self.store, self.a)
        first = lineage.propose_pair(self.store, context, self.b)
        lineage.assess_pair(self.store, context, self.b, first)
        original = prompts.TASKS['abstract_lineage']
        prompts.TASKS['abstract_lineage'] = dict(original, instruction=original['instruction'] + ' Check carefully.')
        self.addCleanup(prompts.TASKS.__setitem__, 'abstract_lineage', original)
        second = lineage.propose_pair(self.store, context, self.b)
        self.assertNotIn('assessment', second)
        self.assertNotEqual(first['input_fingerprint'], second['input_fingerprint'])

    def test_gather_reuses_after_unrelated_document_change(self):
        first = lineage_material.gather(self.store, self.relation)
        other = dict(id='unrelated', kind='library', title='Other unrelated paper', year=2000, pdf='other.pdf')
        self.store.put_papers([other])
        self.make_document(other, 'other.pdf', 'OTHER', 'An unrelated paper discusses something else entirely.')
        second = lineage_material.gather(self.store, self.relation)
        self.assertEqual(first['input_fingerprint'], second['input_fingerprint'])
        self.assertEqual(self.count('relation_material'), 1)

    def test_gather_invalidates_endpoint_abstract_change(self):
        first = lineage_material.gather(self.store, self.relation)
        self.store.patch_paper('b', {'abstract': 'A revised abstract changes the target method scope.'})
        second = lineage_material.gather(self.store, self.relation)
        self.assertNotEqual(first['input_fingerprint'], second['input_fingerprint'])

    def test_gather_invalidates_fulltext_reference_and_vocabulary(self):
        first = lineage_material.gather(self.store, self.relation)
        with self.store.db() as db:
            db.execute('UPDATE document_chunks SET text=text || ? WHERE document_id=?', (' Additional original text.', self.target['id']))
        second = lineage_material.gather(self.store, self.relation)
        self.assertNotEqual(first['input_fingerprint'], second['input_fingerprint'])
        with self.store.db() as db:
            db.execute('INSERT INTO document_references VALUES(?,?,?,?,?,?,?)', ('ref-new', self.supp['id'], 2, '1', 'Target algorithm', 'b', '{}'))
            db.execute('INSERT INTO citation_contexts VALUES(?,?,?,?,?,?,?)', ('ctx-new', 'ref-new', self.supp['id'], 2, 'Method', 'We improve Target [1].', 'unknown'))
        third = lineage_material.gather(self.store, self.relation)
        self.assertNotEqual(second['input_fingerprint'], third['input_fingerprint'])
        with self.store.db() as db:
            db.execute('INSERT INTO method_concepts VALUES(?,?,?,?,0,?)', ('method:new', 'Target', '["Target method"]', '{}', now()))
        fourth = lineage_material.gather(self.store, self.relation)
        self.assertNotEqual(third['input_fingerprint'], fourth['input_fingerprint'])

    def test_cached_local_rejection_has_origin_and_persistent_audit_queue(self):
        subject = next('audit-' + str(i) for i in range(1000) if int(digest('audit-' + str(i))[:4], 16) % 100 < 10)
        claim = dict(id='claim', text='A factual claim', evidence=[])
        def reject(store, prompt, purpose, **kwargs):
            return {'reviews': [dict(id='claim', decision='insufficient', supported=False, method_relation=False,
                                     unsupported_details=['Missing context'], reason='Insufficient original evidence')], 'conflicts': []}
        review.cached_review(self.store, subject, [self.source], claim, reject, True, False, lambda: False)
        with self.store.db() as db:
            attempt = dict(db.execute('SELECT * FROM verification_attempts').fetchone())
        self.assertEqual(json.loads(attempt['payload']).get('origin'), 'local')
        self.assertEqual(len(routing.sample_for_audit(self.store, rate=1)), 1)
        with self.store.db() as db:
            queued = [json.loads(r[0]) for r in db.execute("SELECT payload FROM research_artifacts WHERE type='LocalReviewAudit'")]
        self.assertEqual(len(queued), 1)
        self.assertEqual(queued[0]['verification_status'], 'pending')
        self.assertEqual(queued[0]['cache_key'], attempt['cache_key'])
        self.assertEqual(queued[0]['contexts'][0]['document_id'], self.supp['id'])
        review.cached_review(self.store, subject, [self.source], claim, reject, True, False, lambda: False)
        with self.store.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM research_artifacts WHERE type='LocalReviewAudit'").fetchone()[0], 1)
        self.assertEqual(self.count('independent_claim_review'), 0)

    def test_audit_recovers_hashed_legacy_origin_but_excludes_cloud_unknown_and_errors(self):
        verdict = dict(supported=False, decision='insufficient', reason='Missing evidence')
        def insert(key, subject, fp, payload, status='complete'):
            with self.store.db() as db:
                db.execute('INSERT INTO verification_attempts VALUES(?,?,?,?,?,?)', (key, subject, fp, status, json.dumps(payload), now()))
        insert(digest('local:old-fp'), 'old-local', 'old-fp', {'verdict': verdict})
        insert('local:prefix', 'prefix-local', 'prefix-fp', {'verdict': verdict})
        insert(digest('cloud:cloud-fp'), 'cloud', 'cloud-fp', {'verdict': verdict})
        insert('unknown-hash', 'unknown', 'unknown-fp', {'verdict': verdict})
        insert(digest('local:failed'), 'failed', 'failed', {'origin': 'local', 'verdict': dict(verdict, execution_error=True)}, 'local_failed')
        self.assertEqual({r['subject'] for r in routing.sample_for_audit(self.store, rate=1)}, {'old-local', 'prefix-local'})

    def test_split_document_refresh_preserves_child_and_rejects_changed_parent(self):
        from kbapp.search.budget import split_source
        text = ' '.join('Original methodological sentence number %d describes complete sampling evidence.' % n for n in range(500))
        with self.store.db() as db:
            db.execute('UPDATE document_chunks SET text=? WHERE id=?', (text, self.source['id']))
        parent = next(s for s in documents.sources(self.store) if s['id'] == self.source['id'])
        child = split_source(parent)[1]
        self.assertEqual(child['parent_id'], parent['id'])
        fresh, gaps = refresh_sources(self.store, [child])
        self.assertFalse(gaps)
        self.assertEqual(len(fresh), 1)
        self.assertEqual(fresh[0]['text'], child['text'])
        self.assertEqual(fresh[0]['retrieved_source_id'], child['id'])
        self.assertEqual(fresh[0]['parent_id'], parent['id'])
        self.assertEqual(fresh[0]['sha256'], self.supp['sha256'])
        ref = evidence.evidence_ref({'source_id': fresh[0]['id'], 'quote': child['text'][:80]}, {fresh[0]['id']: fresh[0]})
        self.assertEqual(ref['document_id'], self.supp['id'])
        with self.store.db() as db:
            db.execute('UPDATE document_chunks SET text=text || ? WHERE id=?', (' Changed parent.', parent['id']))
        fresh, gaps = refresh_sources(self.store, [child])
        self.assertFalse(fresh)
        self.assertTrue(gaps)

    def test_missing_legacy_pdf_does_not_create_new_evidence_round(self):
        self.store.patch_paper('a', {'pdf': None})
        source = dict(id='legacy-no-file', paper_id='a', kind='paper_text', text='Original methodological evidence.', page_start=1)
        claim = dict(text=source['text'], evidence=[{'source_id': source['id'], 'quote': source['text']}])
        calls = []
        def insufficient(store, prompt, purpose, **kwargs):
            calls.append(purpose)
            item = json.loads(prompt.split('\n', 1)[1])['claims'][0]
            return {'reviews': [dict(id=item['id'], decision='insufficient', supported=False, method_relation=False,
                                     unsupported_details=['No further evidence'], reason='Insufficient context')], 'conflicts': []}
        first = review.review_case(self.store, claim, {source['id']: source}, insufficient, insufficient)
        review.review_case(self.store, claim, {source['id']: source}, insufficient, insufficient)
        self.assertEqual(len(calls), 2)
        self.assertEqual(first['supplement_rounds'], 0)

    def test_failed_expansion_preserves_initial_local_verdict_without_recalling_model(self):
        self.store.patch_paper('a', {'pdf': None})
        source = dict(id='legacy-no-file', paper_id='a', kind='paper_text', text='Original methodological evidence.', page_start=1)
        claim = dict(text=source['text'], kind='improves', evidence=[{'source_id': source['id'], 'quote': source['text']}])
        initial = dict(decision='insufficient', supported=False, method_relation=True, unsupported_details=[], reason='Needs independent review')
        result = review.review_case(self.store, claim, {source['id']: source}, self.cloud_model, self.local_model,
                                    relation=True, initial_local=initial)
        self.assertTrue(result['accepted'])
        self.assertEqual(self.count('understanding_verify'), 0)
        self.assertEqual(self.count('independent_claim_review'), 1)

    def test_overflow_handoff_then_local_call_does_not_capture_temporary_estimator(self):
        # Existing integration cases in a clean interpreter reproduce the import-order regression.
        import os
        import subprocess
        import sys
        script = '''import io, os, socket, sys, unittest
from pathlib import Path
socket.socket.connect = lambda *a, **k: (_ for _ in ()).throw(OSError("network disabled"))
sys.dont_write_bytecode = True
root = Path(sys.argv[1])
sys.path[:0] = [str(root), str(root / "tests")]
suite = unittest.defaultTestLoader.loadTestsFromNames([
 "test_agent_flow.AgentFlow.test_local_context_overflow_handoff_does_not_loop",
 "test_workbench.Cases.test_local_reading_caches_completed_calls_and_retains_usage"])
stream = io.StringIO()
result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
print(stream.getvalue())
sys.exit(not result.wasSuccessful())
'''
        result = subprocess.run([sys.executable, '-I', '-X', 'utf8', '-c', script, str(Path(__file__).resolve().parents[1])],
                                cwd=self.temp.name, env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'), capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_dependency_layers_remain_acyclic(self):
        from kbapp.workflows.diagnostics import architecture
        report = architecture()
        self.assertTrue(report['ok'], report)


if __name__ == '__main__':
    unittest.main()
