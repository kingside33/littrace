'研究闭环的失效边界：原文、增量、人工作品及调度。'
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['KB_NO_STARTUP_JOBS'] = '1'
import pymupdf
from kbapp.storage.store import Store
from kbapp.core.io import atomic, digest
from kbapp.research.evidence import evidence_ref, valid_claim
from kbapp.research.review import review_claims
from kbapp.research.reading import sync_reading, note_status, read_paper, understanding_fingerprint
from kbapp.core.markers import START
from kbapp.research.viewmodels import scoped_nodes, display_relation
from kbapp.library.catalog import enriched_papers
from kbapp.workflows.agents import create_run, get_run, step, control_run
from kbapp.integrations.scheduling import model_slot
from kbapp.core.execution import priority

class Research(unittest.TestCase):

    def setUp(self):
        model = patch('kbapp.integrations.model_identity.embedding_signature', return_value={'model': 'fixture-embedding', 'revision': 'fixture-weights', 'dims': 1024, 'version': 2})
        model.start()
        self.addCleanup(model.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.store = Store(self.base)
        self.paper = {'id': 'p', 'kind': 'library', 'citekey': 'P', 'title': 'Method paper', 'year': 2020, 'project': 'project', 'directions': ['02'], 'card': '_知识库/10_文献/p.md'}
        self.store.put_papers([self.paper])
        atomic(self.base / self.paper['card'], '---\nread: 手写\n---\n# 历史精读\n人工正文\n')
        self.source = {'id': 's', 'kind': 'paper_text', 'paper_id': 'p', 'text': 'We improve sampling. The cost is 10 s against baseline A under static conditions.', 'page_start': 2, 'url': 'zotero://source'}

    def tearDown(self):
        self.tmp.cleanup()

    def result(self, text='已核结论'):
        return {'id': 'understanding', 'coverage': {'complete': False, 'total_pages': 2, 'read_pages': [1], 'missing_pages': [2]}, 'claims': [{'category': 'problem', 'text': text, 'evidence': [{'source_id': 's', 'quote': 'We improve sampling.', 'page_start': 1, 'url': 'zotero://source'}]}], 'gaps': ['p.2识别失败']}

    def test_manual_reading_edit_is_preserved_with_diff_and_idempotence(self):
        sync_reading(self.store, self.paper, self.result())
        path = self.base / self.paper['card']
        first = path.read_text(encoding='utf-8')
        sync_reading(self.store, self.paper, self.result())
        self.assertEqual(path.read_text(encoding='utf-8'), first)
        atomic(path, first.replace('已核结论', '人工修订的结论'))
        status = sync_reading(self.store, self.paper, self.result('新自动结论'))
        self.assertEqual(status['status'], 'conflict')
        self.assertIn('人工修订', path.read_text(encoding='utf-8'))
        self.assertIn('新自动结论', status['diff'])
        self.assertIn('read: 手写', path.read_text(encoding='utf-8'))
        self.assertIn('历史精读', path.read_text(encoding='utf-8'))

    def test_deleted_managed_block_is_an_edit_and_not_reintroduced(self):
        sync_reading(self.store, self.paper, self.result())
        path = self.base / self.paper['card']
        atomic(path, '用户删除了自动区块\n')
        self.assertEqual(sync_reading(self.store, self.paper, self.result())['status'], 'conflict')
        self.assertNotIn(START, path.read_text(encoding='utf-8'))

    def test_metadata_managed_block_also_preserves_manual_edits(self):
        from kbapp.workflows.notes import publish_notes
        publish_notes(self.store, [self.paper])
        path = self.base / self.paper['card']
        old = path.read_text(encoding='utf-8')
        atomic(path, old.replace('最新元数据：', '人工入口说明：'))
        publish_notes(self.store, [dict(self.paper, title='Changed metadata')])
        self.assertIn('人工入口说明：', path.read_text(encoding='utf-8'))
        status = note_status(self.store, self.paper)
        self.assertEqual(status['status'], 'conflict')
        self.assertIn('Changed metadata', status['diff'])

    def test_derived_reading_never_reenters_note_corpus(self):
        from kbapp.library.sources import note_sources
        sync_reading(self.store, self.paper, self.result('UNIQUE_GENERATED_EVIDENCE'))
        docs = note_sources(self.store, [self.paper])
        self.assertNotIn('UNIQUE_GENERATED_EVIDENCE', str(docs))

    def test_wrong_paper_fabricated_quote_and_generated_notes_are_blocked(self):
        for ref, pid, sources in [({'source_id': 's', 'quote': 'not in paper'}, 'p', {'s': self.source}), ({'source_id': 's', 'quote': 'We improve sampling.'}, 'other', {'s': self.source}), ({'source_id': 's', 'quote': 'We improve sampling.'}, 'p', {'s': dict(self.source, derived=True)})]:
            with self.assertRaises(ValueError):
                evidence_ref(ref, sources, pid)

    def test_layout_equivalent_quote_is_saved_as_literal_original(self):
        source = dict(self.source, text='The efﬁcient\n  algorithm improves sampling.')
        ref = evidence_ref({'source_id': 's', 'quote': 'The efficient algorithm improves sampling.'}, {'s': source})
        self.assertEqual(ref['quote'], source['text'])
        self.assertIn(ref['quote'], source['text'])
        with self.assertRaises(ValueError):
            evidence_ref({'source_id': 's', 'quote': 'The algorithm is globally optimal.'}, {'s': source})

    def test_cross_page_chunk_quote_opens_its_actual_page(self):
        source = dict(self.source, page_start=1, page_end=2, url='zotero://open-pdf/library/items/PDF?page=1', page_texts=[{'page': 1, 'text': 'Other introductory material.'}, {'page': 2, 'text': self.source['text']}])
        ref = evidence_ref({'source_id': 's', 'quote': 'We improve sampling.'}, {'s': source})
        self.assertEqual((ref['page_start'], ref['page_end']), (2, 2))
        self.assertIn('page=2', ref['url'])

    def test_pdf_line_hyphenation_preserves_original_characters_and_math_minus(self):
        from kbapp.core.text import locate_quote
        raw = 'This subset is sam-\npled directly.'
        self.assertEqual(locate_quote(raw, 'This subset is sampled directly.'), raw)
        self.assertIsNone(locate_quote('x-\ny = 1', 'xy = 1'))

    def test_personal_judgment_and_legacy_notes_cannot_be_used_as_paper_fact(self):
        note = dict(self.source, kind='obsidian_note', origin='legacy_mixed')
        claim = {'text': '笔记中的个人理解', 'claim_type': 'personal_judgment', 'evidence': [{'source_id': 's', 'quote': 'We improve sampling.'}]}
        self.assertEqual(valid_claim(claim, {'s': note})['evidence'][0]['evidence_level'], 'legacy_mixed')
        claim['claim_type'] = 'paper_fact'
        with self.assertRaises(ValueError):
            valid_claim(claim, {'s': note})

    def test_model_failure_during_reassessment_preserves_previous_verified_relation(self):
        from kbapp.research.lineage import reassess_relation
        self.store.put_papers([{'id': 'earlier', 'title': 'Earlier', 'kind': 'external'}])
        relation = {'src': 'p', 'dst': 'earlier', 'kind': 'improves', 'status': 'confirmed', 'level': 'fulltext', 'reviewed_by': 'original reviewer', 'evidence': [{'source_id': 's', 'quote': 'We improve sampling.'}, {'source_id': 'earlier-source', 'quote': 'Earlier algorithm.'}]}
        rid = self.store.relation(relation)
        earlier = dict(self.source, id='earlier-source', paper_id='earlier', text='Earlier algorithm.')
        prepared = dict(relation=relation,sources={'s':self.source,'earlier-source':earlier},references=[],verification_status='ready',input_fingerprint='prepared')
        with patch('kbapp.research.review.review_relation',side_effect=ValueError('云端调用未完成')):
            with self.assertRaisesRegex(ValueError, '云端调用未完成'):
                reassess_relation(self.store, rid, prepared=prepared)
        saved = next((r for r in self.store.relations() if r['id'] == rid))
        self.assertEqual(saved['status'], 'confirmed')
        self.assertFalse(saved.get('semantic_recheck_failed'))

    def test_partial_reading_task_is_not_reported_as_complete(self):
        run = create_run(self.store, {'kind': 'maintenance', 'paper_ids': ['p'], 'stages': ['understanding']}, start=False)
        with patch('kbapp.research.reading.read_paper', return_value={'verification_status': 'partial', 'coverage': {'complete': False}}):
            step(self.store, run['id'])
        step(self.store, run['id'])
        self.assertEqual(get_run(self.store, run['id'])['status'], 'partial')

    def test_bad_pdf_does_not_block_unrelated_maintenance_units(self):
        self.store.put_papers([dict(self.paper, id='p2', title='Another paper')])
        self.store.set_state('active_version', 'previous-index')
        run = create_run(self.store, {'kind': 'maintenance', 'paper_ids': ['p', 'p2'], 'stages': ['understanding']}, start=False)
        with patch('kbapp.research.reading.read_paper', side_effect=[ValueError('PDF没有可读取页面'), {'verification_status': 'verified'}]):
            step(self.store, run['id'])
            step(self.store, run['id'])
            step(self.store, run['id'])
        result = get_run(self.store, run['id'])
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['processed'], 2)
        self.assertEqual(result['completed'], 1)
        self.assertEqual(self.store.state('active_version'), 'previous-index')

    def test_numbers_require_baseline_conditions_units_and_exact_source(self):
        claim = {'text': '耗时为10 s', 'evidence': [{'source_id': 's', 'quote': self.source['text']}], 'numbers': []}
        with self.assertRaises(ValueError):
            valid_claim(claim, {'s': self.source})
        claim['numbers'] = [{'value': '10', 'metric': 'time', 'unit': 's', 'baseline': 'A', 'conditions': 'static', 'source_id': 's'}]
        self.assertEqual(valid_claim(claim, {'s': self.source})['numbers'][0]['value'], '10')
        claim['numbers'][0]['value'] = '99'
        with self.assertRaises(ValueError):
            valid_claim(claim, {'s': self.source})

    def test_true_excerpt_cannot_publish_semantically_unsupported_claim(self):
        claim = {'text': '该算法适用于任意动态场景', 'evidence': [{'source_id': 's', 'quote': self.source['text']}]}

        def unsupported(store, prompt, purpose, **kw):
            data = json.loads(prompt.split('\n', 1)[1])
            return {'reviews': [{'id': c['id'], 'supported': False, 'unsupported_details': ['原文仅静态条件'], 'reason': '不能支持动态场景'} for c in data['claims']]}
        accepted, rejected = review_claims(self.store, [claim], {'s': self.source}, unsupported)
        self.assertFalse(accepted)
        self.assertEqual(len(rejected), 1)

    def test_final_citation_limit_keeps_conflicting_claims_together(self):
        from kbapp.research.qa import limit_evidence
        claims = [{'id': str(i), 'text': str(i), 'evidence': [{'source_id': str(i), 'quote': 'original'}]} for i in range(17)]
        selected, gaps = limit_evidence(claims)
        self.assertEqual(len(selected), 16)
        self.assertTrue(gaps)
        first = dict(claims[0], conflicts=['opposed evidence'], evidence=[{'source_id': str(i), 'quote': 'original'} for i in range(9)])
        second = dict(claims[1], conflicts=['opposed evidence'], evidence=[{'source_id': str(i), 'quote': 'original'} for i in range(9, 18)])
        selected, gaps = limit_evidence([first, second])
        self.assertEqual(selected, [])
        self.assertTrue(gaps)

    def test_completed_claim_review_is_reused_until_original_context_changes(self):
        claim = {'text': '改进采样', 'evidence': [{'source_id': 's', 'quote': 'We improve sampling.'}]}
        calls = []

        def reviewer(store, prompt, purpose, **kwargs):
            calls.append(purpose)
            data = json.loads(prompt.split('\n', 1)[1])
            return {'reviews': [{'id': c['id'], 'supported': True, 'unsupported_details': [], 'reason': '原文明确支持'} for c in data['claims']]}
        review_claims(self.store, [claim], {'s': self.source}, reviewer)
        review_claims(self.store, [claim], {'s': self.source}, reviewer)
        self.assertEqual(len(calls), 1)
        review_claims(self.store, [claim], {'s': dict(self.source, text=self.source['text'] + ' Changed assumptions.')}, reviewer)
        self.assertEqual(len(calls), 2)

    def test_missing_fulltext_is_partial_and_never_overwrites_historical_notes(self):
        before = (self.base / self.paper['card']).read_text(encoding='utf-8')
        result = read_paper(self.store, self.paper)
        self.assertFalse(result['coverage']['complete'])
        self.assertEqual(result['verification_status'], 'partial')
        self.assertEqual(before, (self.base / self.paper['card']).read_text(encoding='utf-8'))

    def test_pdf_change_invalidates_understanding_fingerprint(self):
        path = self.base / 'p.pdf'
        doc = pymupdf.open()
        doc.new_page().insert_text((30, 30), 'Original text.')
        doc.save(path)
        doc.close()
        paper = dict(self.paper, pdf=str(path))
        first = understanding_fingerprint(self.store, paper)
        doc = pymupdf.open(path)
        doc[0].insert_text((30, 60), 'Changed text.')
        doc.saveIncr()
        doc.close()
        self.assertNotEqual(first, understanding_fingerprint(self.store, paper))

    def test_filters_keep_external_predecessors_successors_and_bridges(self):
        nodes = [self.paper, {'id': 'a', 'kind': 'external'}, {'id': 'b', 'kind': 'external'}, {'id': 'unrelated'}]
        edges = [{'src': 'p', 'dst': 'a', 'kind': 'inherits', 'status': 'confirmed'}, {'src': 'a', 'dst': 'b', 'kind': 'inherits', 'status': 'candidate'}]
        kept = scoped_nodes(nodes, edges, project=True)
        self.assertEqual({p['id'] for p in kept}, {'p', 'a', 'b'})
        self.assertIn('桥接', next((p for p in kept if p['id'] == 'b'))['selection_reason'])

    def test_retrieval_can_include_verified_context_without_inheriting_project_tags(self):
        from kbapp.research.retrieval import evidence_search
        self.store.put_papers([{'id': 'earlier', 'kind': 'external', 'title': 'Earlier method'}])
        self.store.relation({'src': 'p', 'dst': 'earlier', 'kind': 'inherits', 'status': 'confirmed', 'level': 'fulltext', 'reviewed_by': 'reviewer', 'evidence': [{'quote': 'Original support'}]})
        source = self.source

        class FakeSearch:

            def __init__(self, store):
                pass

            def search(self, query, **options):
                if options.get('paper') == 'earlier':
                    self_check = not options.get('project')
                    if not self_check:
                        raise AssertionError('verified bridge was filtered away')
                    results = [dict(source, id='bridge', paper_id='earlier')]
                else:
                    results = [source]
                return {'results': results, 'mode': 'bm25', 'warning': '', 'coverage': {}}
        with patch('kbapp.search.indexing.Search', FakeSearch):
            result = evidence_search(self.store, 'planning', {'project': True})
        bridge = next((r for r in result['results'] if r['paper_id'] == 'earlier'))
        self.assertTrue(bridge['scope_context'])
        self.assertFalse(self.store.papers()['earlier'].get('project'))

    def test_fulltext_source_alone_does_not_promote_relation_and_abstract_stays_abstract(self):
        relation = {'status': 'candidate', 'kind': 'inherits', 'level': 'fulltext', 'evidence': [{'quote': 'text'}]}
        self.assertEqual(display_relation(relation)['evidence_grade'], 'title')
        relation.update(level='abstract', assessment_verified=True)
        self.assertEqual(display_relation(relation)['evidence_grade'], 'abstract')
        self.assertEqual(relation['status'], 'candidate')

    def test_auto_confirmed_fulltext_relation_grade_follows_unified_contract(self):
        """B05/F02：自动确认的原文方法关系必须携带 reviewed_by 才算原文已核。"""
        base = {'status': 'confirmed', 'kind': 'improves', 'level': 'fulltext', 'evidence': [{'quote': 'We extend the sampling framework of A.'}], 'assessment_verified': True, 'review_verdict': {'supported': True}}
        missing = display_relation(dict(base))
        self.assertEqual(missing['evidence_grade'], 'title')
        self.assertEqual(missing['evidence_label'], '题目线索，待核')
        complete = display_relation(dict(base, reviewed_by='DeepSeek-V4.1-Flash', local_reviewed_by='gemma4:e4b'))
        self.assertEqual(complete['evidence_grade'], 'fulltext')
        self.assertEqual(complete['evidence_label'], '原文已核 · 模型复核')

    def test_relation_claims_are_split_and_numbers_pending_keeps_method_relation(self):
        """B08/B09：主张拆分；数字待核不抹去已成立的方法关系。"""
        base = {'status': 'confirmed', 'kind': 'improves', 'level': 'fulltext', 'reviewed_by': 'm', 'evidence': [{'quote': 'x'}],
                'explanation': 'B 改进 A 的采样', 'change': '自适应步长', 'numbers': [{'metric': '能耗', 'value': '30%'}], 'conditions': '静态地图'}
        claims = {c['type']: c for c in display_relation(dict(base))['claims']}
        self.assertEqual(claims['method_relation']['status'], 'verified')
        self.assertEqual(claims['change']['status'], 'verified')
        self.assertEqual(claims['numbers']['status'], 'verified')
        pending = {c['type']: c for c in display_relation(dict(base, numbers_pending=True))['claims']}
        self.assertEqual(pending['method_relation']['status'], 'verified')
        self.assertEqual(pending['numbers']['status'], 'pending')
        self.assertEqual(pending['conditions']['status'], 'pending')

    def test_manual_model_and_stale_verification_are_expressed_separately(self):
        """B07：人工确认、模型复核与证据过期分别表达，过期不伪装成原文已核。"""
        human = {'status': 'confirmed', 'kind': 'improves', 'level': 'fulltext', 'manual': True, 'evidence': [{'quote': 'x'}]}
        self.assertEqual(display_relation(human)['evidence_label'], '原文已核 · 人工确认')
        self.assertEqual(display_relation(human)['verification_origin'], 'human')
        stale = display_relation(dict(human, evidence_stale=True))
        self.assertEqual(stale['evidence_grade'], 'title')
        self.assertEqual(stale['evidence_label'], '人工确认 · 证据已过期，待复核')
        model = {'status': 'confirmed', 'kind': 'improves', 'level': 'fulltext', 'reviewed_by': 'm', 'evidence': [{'quote': 'x'}]}
        self.assertEqual(display_relation(model)['evidence_label'], '原文已核 · 模型复核')
        self.assertEqual(display_relation(model)['verification_origin'], 'model')
        self.assertEqual(display_relation(dict(model, evidence_stale=True))['evidence_label'], '模型复核 · 证据已过期，待复核')

    def test_manual_judgment_history_survives_automatic_candidate(self):
        self.store.put_papers([{'id': 'earlier', 'title': 'Earlier', 'kind': 'external'}])
        rid = self.store.relation({'src': self.paper['id'], 'dst': 'earlier', 'kind': 'improves', 'status': 'confirmed', 'level': 'fulltext', 'evidence': [{'quote': 'x'}]}, manual=True)
        self.store.relation({'src': self.paper['id'], 'dst': 'earlier', 'kind': 'improves', 'status': 'candidate', 'level': 'inference'})
        saved = next(r for r in self.store.relations() if r['id'] == rid)
        self.assertEqual(saved['status'], 'confirmed')
        with self.store.db() as db:
            self.assertGreaterEqual(db.execute('SELECT COUNT(*) FROM relation_history WHERE relation_id=?', (rid,)).fetchone()[0], 1)

    def test_graph_summary_cache_follows_revision(self):
        """H07：图谱摘要按修订号缓存，关系或文献变化后自动失效。"""
        from kbapp.research.graph import chains, overview
        first = chains(self.store)
        self.assertIs(chains(self.store), first)
        self.store.relation({'src': self.paper['id'], 'dst': 'earlier', 'kind': 'improves', 'status': 'candidate', 'level': 'inference'})
        self.assertIsNot(chains(self.store), first)
        before = overview(self.store)
        self.assertIs(overview(self.store), before)
        self.store.put_papers([{'id': 'another', 'title': 'Another', 'kind': 'library'}])
        self.assertIsNot(overview(self.store), before)

    def test_internal_links_follow_configured_port(self):
        """L08：内部文档链接使用配置端口，不再写死 8765。"""
        from kbapp.library.sources import paper_url
        from kbapp.core.config import server_base
        self.assertEqual(server_base(), 'http://127.0.0.1:8765')
        self.assertEqual(server_base(9001), 'http://127.0.0.1:9001')
        self.assertEqual(paper_url({'id': 'p'}, 2, port=9001), 'http://127.0.0.1:9001/api/file?paper=p#page=2')

    def test_manual_external_classification_has_priority(self):
        self.store.put_papers([{'id': 'x', 'kind': 'external', 'title': 'X', 'directions': ['04']}])
        with self.store.db() as db:
            db.execute('INSERT INTO paper_classifications VALUES(?,?,1,?)', ('x', json.dumps({'directions': ['02'], 'project': ''}), 'today'))
        self.assertEqual(enriched_papers(self.store)['x']['directions'], ['02'])

    def test_method_variants_are_not_collapsed_by_a_shared_alias(self):
        from kbapp.research.lineage import classify_and_assess
        self.paper['abstract'] = 'RRT* is an optimal tree method.'
        self.store.put_papers([self.paper])
        with self.store.db() as db:
            for mid, name in [('base', 'RRT'), ('star', 'RRT*')]:
                db.execute('INSERT INTO method_concepts VALUES(?,?,?,?,0,?)', (mid, name, json.dumps([name]), '{}', 'today'))
        result = {'directions': ['02'], 'project_relevance': True, 'quote': 'RRT*', 'reason': 'method named in abstract', 'methods': [{'name': 'RRT*', 'aliases': ['RRT'], 'family': 'sampling', 'quote': 'RRT*'}]}
        with patch('kbapp.integrations.cloud.complete', return_value=result), patch('kbapp.research.lineage.candidate_neighbors', return_value=[]):
            classify_and_assess(self.store, self.paper)
        with self.store.db() as db:
            mapped = db.execute('SELECT method_id FROM paper_methods WHERE paper_id=?', ('p',)).fetchone()[0]
            aliases = json.loads(db.execute('SELECT aliases FROM method_concepts WHERE id=?', ('star',)).fetchone()[0])
        self.assertEqual(mapped, 'star')
        self.assertNotIn('RRT', aliases)

    def test_query_vector_cache_is_persistent_and_works_while_model_busy(self):
        from kbapp.search.query import query_vector, INSTRUCTION_VERSION
        import kbapp.core.config as C
        signature = json.dumps([__import__('kbapp.integrations.model_identity', fromlist=['embedding_signature']).embedding_signature(), INSTRUCTION_VERSION], sort_keys=True, separators=(',', ':'))
        vector = [1.0] + [0.0] * (C.EMBED_DIMS - 1)
        with self.store.db() as db:
            db.execute('INSERT INTO query_vectors VALUES(?,?,?,?,?,?)', (digest(signature + '\nquestion'), signature, json.dumps(vector), 'ready', '', 'today'))
        with patch('kbapp.integrations.embedding.embed_texts', side_effect=AssertionError('cache should avoid model')):
            value, status = query_vector(Store(self.base), 'question', wait=True)
            self.assertEqual(status, 'cached')
            self.assertEqual(value.shape, (1024,))
        self.store.set_state('model_cancelled', {'embedding': True})
        self.assertEqual(query_vector(self.store, 'question')[1], 'cached')
        self.assertEqual(query_vector(self.store, 'uncached question')[1], 'stopped')
        from kbapp.integrations.models import prepare_local
        with patch('kbapp.integrations.models.request') as request:
            with self.assertRaisesRegex(ValueError, '手动停止'):
                prepare_local(self.store, 'embedding')
            request.assert_not_called()

    def test_evidence_search_reports_actual_modes_instead_of_always_claiming_hybrid(self):
        from kbapp.research.retrieval import evidence_search
        for mode, expected in [('bm25', 'two-stage-bm25'), ('bm25+vector(rrf)', 'two-stage-hybrid')]:
            with patch('kbapp.search.indexing.Search.search', return_value={'mode': mode, 'warning': '', 'results': [], 'coverage': {}}):
                found = evidence_search(self.store, 'question')
            self.assertEqual(found['mode'], expected)
        calls = [{'mode': mode, 'warning': '', 'results': [], 'coverage': {}} for mode in ('bm25', 'bm25+vector(rrf)')]
        with patch('kbapp.search.indexing.Search.search', side_effect=calls):
            found = evidence_search(self.store, 'question', complete=lambda *a, **kw: {'queries': ['second query']})
        self.assertEqual(found['mode'], 'two-stage-mixed+rerank')

    def test_new_question_auto_starts_models_and_inflight_request_is_deduplicated(self):
        from kbapp.search.query import query_vector
        from kbapp.integrations.models import cancelled, set_cancelled
        import kbapp.core.config as C
        set_cancelled(self.store, ['embedding', 'text'], True)
        with patch('kbapp.workflows.agents.start_dispatcher'):
            create_run(self.store, {'kind': 'answer', 'query': 'new question'})
        self.assertFalse(cancelled(self.store, 'embedding'))
        self.assertFalse(cancelled(self.store, 'text'))
        entered = threading.Event()
        finish = threading.Event()
        values = []

        def embed(*a, **kw):
            entered.set()
            finish.wait(3)
            return [[1.0] + [0.0] * (C.EMBED_DIMS - 1)]
        with patch('kbapp.integrations.embedding.embed_texts', side_effect=embed) as inference:
            self.assertEqual(query_vector(self.store, 'new question', wait=False)[1], 'queued')
            self.assertTrue(entered.wait(1))
            thread = threading.Thread(target=lambda: values.append(query_vector(self.store, 'new question', wait=True)))
            thread.start()
            finish.set()
            thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(inference.call_count, 1)
        self.assertIn(values[0][1], {'ready', 'cached'})
        self.assertEqual(query_vector(self.store, 'new question', wait=True)[1], 'cached')

    def test_agent_tasks_save_completed_results_and_resume_only_unfinished(self):
        run = create_run(self.store, {'kind': 'maintenance', 'paper_ids': ['p']}, start=False)
        with patch('kbapp.research.reading.read_paper', return_value={'verification_status': 'verified', 'id': 'saved'}), patch('kbapp.research.lineage.classify_paper', side_effect=ValueError('model unavailable')):
            step(self.store, run['id'])
            step(self.store, run['id'])
        saved = get_run(self.store, run['id'])
        self.assertEqual(saved['status'], 'failed')
        self.assertEqual(saved['completed'], 1)
        with patch('kbapp.workflows.agents.start_dispatcher'):
            control_run(self.store, run['id'], 'resume')
        with patch('kbapp.research.reading.read_paper', side_effect=AssertionError('completed task must not repeat')), patch('kbapp.research.lineage.classify_paper', return_value={'paper':self.paper,'source':self.source,'fingerprint':'fp','classification':{}}), patch('kbapp.research.lineage.candidate_neighbors', return_value=[]):
            step(self.store, run['id'])
            step(self.store, run['id'])
        self.assertEqual(get_run(self.store, run['id'])['status'], 'complete')

    def test_stop_before_task_does_not_call_model(self):
        run = create_run(self.store, {'kind': 'maintenance'}, start=False)
        control_run(self.store, run['id'], 'stop')
        with patch('kbapp.research.reading.read_paper', side_effect=AssertionError('stopped')):
            step(self.store, run['id'])
        self.assertEqual(get_run(self.store, run['id'])['status'], 'interrupted')

    def test_local_capacity_one_and_interactive_request_precedes_background(self):
        entered = []
        started = threading.Event()

        def worker(name, rank):
            token = priority.set(rank)
            try:
                started.set()
                with model_slot(self.store.root, timeout=4):
                    entered.append(name)
                    time.sleep(0.03)
            finally:
                priority.reset(token)
        with model_slot(self.store.root):
            background = threading.Thread(target=worker, args=('background', 100))
            background.start()
            started.wait()
            time.sleep(0.08)
            interactive = threading.Thread(target=worker, args=('interactive', 0))
            interactive.start()
            time.sleep(0.12)
            self.assertEqual(entered, [])
        background.join(4)
        interactive.join(4)
        self.assertEqual(entered, ['interactive', 'background'])

    def test_cloud_capacity_never_exceeds_two(self):
        active = 0
        peak = 0
        guard = threading.Lock()
        two_entered = threading.Event()
        release = threading.Event()

        def worker():
            nonlocal active, peak
            with model_slot(self.store.root, 'cloud', timeout=5):
                with guard:
                    active += 1
                    peak = max(peak, active)
                    if active == 2:
                        two_entered.set()
                release.wait(4)
                with guard:
                    active -= 1
        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        try:
            self.assertTrue(two_entered.wait(3), 'Two cloud slots did not overlap')
        finally:
            release.set()
        for t in threads:
            t.join(6)
        self.assertFalse(any(t.is_alive() for t in threads))
        self.assertEqual(peak, 2)

    def test_refresh_uses_current_pdf_and_marks_abstract_scope(self):
        from kbapp.library.evidence_sources import refresh_sources
        path = self.base / 'paper.pdf'
        doc = pymupdf.open()
        doc.new_page().insert_text((20, 40), 'Current original evidence is static only.')
        doc.save(path)
        doc.close()
        self.store.put_papers([dict(self.paper, pdf=str(path), abstract='Abstract evidence only.')])
        source = dict(self.source, page_start=1, text='Old evidence from a changed PDF.')
        results, gaps = refresh_sources(self.store, [source, {'id': 'abstract', 'paper_id': 'p', 'kind': 'metadata'}])
        self.assertIn('Current original', results[0]['text'])
        self.assertNotIn('Old evidence', results[0]['text'])
        self.assertEqual(results[1]['evidence_level'], 'abstract')
if __name__ == '__main__':
    unittest.main()
