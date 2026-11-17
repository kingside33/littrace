"""调度可观测性、复核分流与缓存失效的行为回归。"""
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
from kbapp.storage.store import Store
from kbapp.core.io import now
from kbapp.storage.activity import scope, emit
from kbapp.workflows.dashboard import dashboard
from kbapp.research.review import review_claims
from kbapp.research.review import review_case, retry_deferred, verdict_state
from kbapp.workflows.agents import create_run
from kbapp.workflows.status import status as workbench_status
os.environ['KB_NO_STARTUP_JOBS'] = '1'
os.environ['KB_NO_REFERENCE_WORKER'] = '1'

class DashboardReview(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.paper = {'id': 'p', 'title': 'Test paper', 'kind': 'library', 'year': 2020}
        self.store.put_papers([self.paper])
        self.source = {'id': 's', 'paper_id': 'p', 'kind': 'paper_text', 'text': 'The method improves sampling under static conditions.', 'page_start': 1}
        self.claim = {'id': 'c', 'text': '该方法在静态条件下改进采样', 'evidence': [{'source_id': 's', 'quote': self.source['text']}]}
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def reviewer(self, decision='supported', reason=None):

        def call(store, prompt, purpose, **kwargs):
            self.calls.append(purpose)
            data = json.loads(prompt.split('\n', 1)[1])
            return {'reviews': [{'id': data['claims'][0]['id'], 'supported': decision == 'supported', 'decision': decision, 'method_relation': True, 'unsupported_details': [] if decision == 'supported' else ['需要核对'], 'reason': reason or {'supported': '原文完整支持', 'contradicted': '原文明示相反事实', 'insufficient': '上下文不足'}[decision]}]}
        return call

    def review(self, local='supported', cloud='supported'):
        return review_case(self.store, self.claim, {'s': self.source}, self.reviewer(cloud), self.reviewer(local))

    def test_non_relation_support_does_not_require_method_relation(self):
        verdict = {'supported': True, 'method_relation': False, 'unsupported_details': [], 'reason': '原文支持这项事实'}
        self.assertEqual(verdict_state(verdict), 'supported')
        self.assertEqual(verdict_state(verdict, relation=True), 'insufficient')

    def test_negative_reason_without_contradiction_decision_is_not_refutation(self):
        """B03/F01：'无原文相反事实'属于证据不足，不得凭理由关键词判为反驳。"""
        samples = [
            '各环节均有对应摘录，但未提供结果页，无法核验数值；同时未明示B对A的方法继承关系。无原文相反事实，判为insufficient。',
            '一侧仅有参考文献条目，缺少正文，无法核验研究内容，也不构成原文相反事实，判为insufficient。',
            '原文没有相反事实，只是缺少对比条件。',
            'the paper does not contradict the claim, but evidence is incomplete (not contradicted).',
            '不能认为原文反驳了该结论，只是没有给出实验条件。',
        ]
        for reason in samples:
            verdict = {'supported': False, 'decision': 'insufficient', 'unsupported_details': ['需要核对'], 'reason': reason}
            self.assertEqual(verdict_state(verdict), 'insufficient', reason)
            legacy = {'supported': False, 'unsupported_details': ['需要核对'], 'reason': reason}
            self.assertEqual(verdict_state(legacy), 'insufficient', reason)

    def test_decision_is_authoritative_for_contradiction(self):
        """B01：contradicted 只来自明确 decision，不再要求理由含反驳词。"""
        verdict = {'supported': False, 'decision': 'contradicted', 'unsupported_details': [], 'reason': '第3节给出的数值与结论中的数字不同。'}
        self.assertEqual(verdict_state(verdict), 'contradicted')

    def test_unknown_or_contradictory_decision_is_execution_error(self):
        """B02：未知判定值、decision 与结构化标志互相矛盾时按结构错误处理。"""
        self.assertEqual(verdict_state({'supported': False, 'decision': 'rejected', 'unsupported_details': [], 'reason': '理由'}), 'execution_error')
        verdict = {'supported': False, 'decision': 'supported', 'unsupported_details': [], 'reason': '理由'}
        self.assertEqual(verdict_state(verdict), 'execution_error')
        with_details = {'supported': True, 'decision': 'supported', 'unsupported_details': ['数字未核'], 'reason': '基本支持'}
        self.assertEqual(verdict_state(with_details), 'execution_error')

    def test_supported_decision_with_unresolved_reason_stays_insufficient(self):
        """decision=supported 但理由含未决表述时不发布，也不中断为执行错误。"""
        verdict = {'supported': True, 'decision': 'supported', 'unsupported_details': [], 'reason': '主体支持，但缺少实验条件，不足以确认泛化。'}
        self.assertEqual(verdict_state(verdict), 'insufficient')

    def test_wrong_paper_claim_is_rejected_before_calls(self):
        accepted, rejected = review_claims(self.store, [dict(self.claim, paper_id='other')], {'s': self.source}, self.reviewer())
        self.assertFalse(accepted)
        self.assertTrue(rejected)
        self.assertFalse(self.calls)

    def test_dashboard_api_selection_and_event_cursor(self):
        from fastapi.testclient import TestClient
        from support import service_for
        service = service_for(self.store)
        run = create_run(self.store, {'kind': 'answer', 'query': 'test'}, start=False)
        with scope(self.store, run['id']):
            emit(self.store, 'read', message='读取原文')
        with patch.object(service, 'store', self.store):
            client = TestClient(service.app)
            response = client.get('/api/task-dashboard', params={'task_id': run['id'], 'limit': 1})
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(data['selected']['id'], run['id'])
            self.assertEqual(len(data['events']), 1)
            self.assertEqual(client.get('/api/task-dashboard', params={'task_id': run['id'], 'after_event_id': data['cursor']}).json()['events'], [])

    def test_legacy_active_job_shows_real_progress_without_fake_elapsed(self):
        with self.store.db() as db:
            db.execute('INSERT INTO jobs(id,kind,status,started,finished,progress,error,payload) VALUES(?,?,?,?,?,?,?,?)', ('j', 'update', 'running', now(), None, '正在识别第3页', '', '{}'))
        data = dashboard(self.store)['selected']
        self.assertEqual(data['id'], 'job:j')
        self.assertEqual(data['current_message'], '正在识别第3页')
        self.assertIsNone(data['elapsed_seconds'])

    def test_parse_issues_are_bucketed_and_linked_to_repairs(self):
        """D10：解析问题分为待处理、失败、已修复、历史保留，并关联修复记录。"""
        from kbapp.workflows.status import parse_issues
        with self.store.db() as db:
            db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?)', ('doc-x', 'p', '', 'hash', 'unused', 2, '{}', now()))
            db.execute('INSERT INTO document_pages VALUES(?,?,?,?,?,?,?,?)', ('doc-x', 1, 'NATIVE_TEXT', 'complete', 'text', '', '{}', now()))
            db.execute('INSERT INTO document_pages VALUES(?,?,?,?,?,?,?,?)', ('doc-x', 2, 'SCANNED', 'pending', '', '', '{}', now()))
        with self.store.db() as db:
            db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?)', ('doc-y', 'p', '', 'hash2', 'unused', 1, '{}', now()))
            db.execute('INSERT INTO document_pages VALUES(?,?,?,?,?,?,?,?)', ('doc-y', 1, 'LOW_QUALITY', 'failed', '', '', '{}', now()))
        self.store.set_state('data_quality_repairs', {'updated': '2026-11-02', 'resolved_warning_count': 14, 'reindexed_documents': ['doc:acc']})
        self.store.set_state('coverage', {'warnings': [], 'warnings_archive': [{'warning': '旧警告', 'archived_at': '2026-11-03', 'reason': '历史运行残留'}]})
        issues = parse_issues(self.store)
        self.assertEqual((issues['pending'], issues['failed']), (1, 1))
        self.assertEqual(issues['failed_details'], [{'document_id': 'doc-y', 'page': 1}])
        self.assertEqual(issues['repaired'], 14)
        self.assertEqual(issues['repaired_documents'], ['doc:acc'])
        self.assertEqual(issues['archived'], 1)
        self.assertEqual(workbench_status(self.store)['parse_issues']['failed'], 1)

    def test_numeric_gap_keeps_method_relation_partial(self):
        """B09：方法关系成立、缺口仅为数字/条件时判为 partial，不当作证据不足或反驳。"""
        from kbapp.research.review import numeric_gap_only

        def numeric_reviewer(store, prompt, purpose, **kwargs):
            self.calls.append(purpose)
            data = json.loads(prompt.split('\n', 1)[1])
            return {'reviews': [{'id': data['claims'][0]['id'], 'supported': False, 'decision': 'insufficient', 'method_relation': True,
                                 'unsupported_details': ['未核验 49.4% 与 34.2% 的数字', '缺少实验基线条件'], 'reason': '方法关系成立，但数字与条件待核'}]}
        self.assertTrue(numeric_gap_only({'decision': 'insufficient', 'supported': False, 'method_relation': True, 'unsupported_details': ['未核验 49.4% 数字']}))
        self.assertFalse(numeric_gap_only({'decision': 'insufficient', 'supported': False, 'method_relation': True, 'unsupported_details': ['未明示方法继承关系']}))
        self.assertFalse(numeric_gap_only({'decision': 'contradicted', 'supported': False, 'method_relation': True, 'unsupported_details': ['数字']}))
        result = review_case(self.store, self.claim, {'s': self.source}, numeric_reviewer, self.reviewer('insufficient'))
        self.assertEqual(result['status'], 'partial')
        self.assertFalse(result['accepted'])
        self.assertTrue(result['method_relation_established'])

    def test_completed_job_reports_result_summary_and_gaps_instead_of_placeholder(self):
        """M09：完成的任务显示结果摘要、跳过原因与剩余缺口，而不是进行中占位文案。"""
        payload = {'coverage': {'sources': 102199, 'vector_count': 102199, 'vector_pending': 12, 'warnings': ['某来源：PDF切块失败']},
                   'analysis': {'state': 'deferred', 'reason': '云端未启用'}}
        with self.store.db() as db:
            db.execute('INSERT INTO jobs(id,kind,status,started,finished,progress,error,payload) VALUES(?,?,?,?,?,?,?,?)', ('done', 'update', 'complete', now(), now(), '完成', '', json.dumps(payload, ensure_ascii=False)))
        data = dashboard(self.store, 'job:done')['selected']
        self.assertEqual(data['status'], 'complete')
        self.assertIsNone(data['current_message'])
        self.assertIn('102,199', data['summary'])
        self.assertIn('12', ' '.join(data['gaps']))
        self.assertTrue(any('关系分析未执行' in g for g in data['gaps']))
        self.assertIn('某来源：PDF切块失败', data['skipped'])

    def test_completed_agent_run_reports_verification_outcome(self):
        run = create_run(self.store, {'kind': 'answer', 'query': 'test'}, start=False)
        summary = {'verification_counts': {'verified': 3, 'insufficient': 2, 'unsupported': 0}, 'pending_reasons': {'insufficient': '原文证据不足'}, 'unit_execution_counts': {'failed': 1}}
        with self.store.db() as db:
            db.execute("UPDATE agent_runs SET status='complete',result=? WHERE id=?", (json.dumps({'summary': summary}), run['id']))
        data = dashboard(self.store, run['id'])['selected']
        self.assertIn('已通过 3 条', data['summary'])
        self.assertTrue(any('原文证据不足' in g for g in data['gaps']))
        self.assertTrue(any('执行失败' in s for s in data['skipped']))

    def test_cross_entry_claim_ids_reuse_review_cache(self):
        self.review()
        review_case(self.store, dict(self.claim, id='another-entry', category='method'), {'s': self.source}, self.reviewer(), self.reviewer())
        self.assertEqual(len(self.calls), 2)

    def test_supplement_budget_persists_when_entry_or_evidence_changes(self):
        from kbapp.research.review import reserve_supplement
        self.assertEqual(reserve_supplement(self.store, 'same-claim', 'fp1', lambda: False), 1)
        reopened = Store(self.temp.name)
        self.assertEqual(reserve_supplement(reopened, 'same-claim', 'fp2', lambda: False), 2)
        self.assertEqual(reserve_supplement(reopened, 'same-claim', 'fp1', lambda: False), 1)
        self.assertIsNone(reserve_supplement(reopened, 'same-claim', 'fp3', lambda: False))

    def test_inflight_cloud_result_is_saved_after_stop(self):
        stopped = [False]
        call = self.reviewer()

        def finish(*args, **kwargs):
            result = call(*args, **kwargs)
            stopped[0] = True
            return result
        result = review_case(self.store, self.claim, {'s': self.source}, finish, should_stop=lambda: stopped[0])
        self.assertTrue(result['accepted'])
        stopped[0] = False
        review_case(self.store, self.claim, {'s': self.source}, finish)
        self.assertEqual(len(self.calls), 1)

    def test_comparison_marks_different_experiment_conditions(self):
        from kbapp.research.qa import comparison_warnings

        def claim(pid, conditions):
            return {'evidence': [{'source_id': pid, 'paper_id': pid}], 'numbers': [{'source_id': pid, 'metric': 'runtime', 'unit': 'ms', 'baseline': 'RRT*', 'conditions': conditions}]}
        warnings = comparison_warnings([claim('a', '2D obstacles'), claim('b', '3D obstacles')], ['a', 'b'])
        self.assertTrue(any(('不可直接比较' in w for w in warnings)))
        self.assertEqual(comparison_warnings([claim('a', 'same'), claim('b', 'same')], ['a', 'b']), [])

    def test_legacy_run_requires_explicit_resume_to_adopt_new_rules(self):
        from kbapp.workflows.agents import control_run, AGENT_VERSION
        run = create_run(self.store, {'kind': 'answer', 'query': 'legacy'}, start=False)
        old = dict(run['input'], generation_version='research-agents-v1')
        with self.store.db() as db:
            db.execute("UPDATE agent_runs SET input=?,status='interrupted' WHERE id=?", (json.dumps(old), run['id']))
        with patch('kbapp.workflows.agents.start_dispatcher'):
            result = control_run(self.store, run['id'], 'resume')
        self.assertEqual(result['input']['generation_version'], AGENT_VERSION)
        self.assertEqual(dashboard(self.store, run['id'])['selected']['activity']['message'], '已续跑，等待调度')

    def test_uncertain_local_can_be_verified_by_cloud(self):
        r = self.review('insufficient')
        self.assertTrue(r['accepted'])
        self.assertEqual(r['label'], '云端复核通过')

    def test_local_contradiction_cloud_support_remains_conflict(self):
        r = self.review('contradicted')
        self.assertFalse(r['accepted'])
        self.assertEqual(r['status'], 'conflict')

    def test_cloud_contradiction_is_not_published(self):
        r = self.review(cloud='contradicted')
        self.assertFalse(r['accepted'])
        self.assertEqual(r['status'], 'unsupported')

    def test_actual_local_failure_is_not_low_confidence_success(self):

        def fail(*a, **kw):
            raise ValueError('model unavailable')
        r = review_case(self.store, self.claim, {'s': self.source}, self.reviewer(), fail)
        self.assertEqual(r['status'], 'execution_error')
        self.assertFalse(r['accepted'])

    def test_no_new_evidence_means_no_repeat_calls(self):
        self.review('insufficient', 'insufficient')
        first = len(self.calls)
        self.review('insufficient', 'insufficient')
        self.assertEqual(len(self.calls), first)
        self.assertEqual(first, 2)

    def test_changed_original_context_invalidates_review_cache(self):
        self.review()
        self.review()
        self.assertEqual(len(self.calls), 2)
        self.source['text'] += ' Additional limitations.'
        self.review()
        self.assertEqual(len(self.calls), 4)

    def test_supplement_budget_maximum_two_rounds(self):

        def expand(store, contexts, distance):
            return [dict(contexts[0], supplementary_pages=[{'page': distance + 1, 'text': 'Additional original evidence.'}])]
        with patch('kbapp.research.review.expand_contexts', side_effect=expand):
            r = self.review('contradicted', 'supported')
        self.assertEqual(r['supplement_rounds'], 2)
        self.assertLessEqual(len(self.calls), 6)

    def test_malformed_cloud_response_is_deferred_and_not_repeated(self):
        calls = []

        def broken(*a, **kw):
            calls.append(1)
            return {'reviews': []}
        for _ in range(2):
            with self.assertRaises(ValueError):
                review_case(self.store, self.claim, {'s': self.source}, broken)
        self.assertEqual(len(calls), 1)

    def test_cloud_timeout_requires_explicit_resume(self):
        calls = []

        def fail(*a, **kw):
            calls.append(1)
            raise TimeoutError()
        with scope(self.store, 'run:test'):
            for _ in range(2):
                with self.assertRaises(ValueError):
                    review_case(self.store, self.claim, {'s': self.source}, fail)
            self.assertEqual(len(calls), 1)
            retry_deferred(self.store, 'run:other')
            with self.assertRaises(ValueError):
                review_case(self.store, self.claim, {'s': self.source}, fail)
            self.assertEqual(len(calls), 1)
            retry_deferred(self.store, 'run:test')
            with self.assertRaises(ValueError):
                review_case(self.store, self.claim, {'s': self.source}, fail)
            self.assertEqual(len(calls), 2)

    def test_fabricated_excerpt_never_calls_models(self):
        bad = dict(self.claim, evidence=[{'source_id': 's', 'quote': 'Invented text.'}])
        accepted, rejected = review_claims(self.store, [bad], {'s': self.source}, self.reviewer(), self.reviewer())
        self.assertFalse(accepted)
        self.assertTrue(rejected)
        self.assertFalse(self.calls)

    def test_abstract_evidence_stays_abstract_after_cloud_support(self):
        source = dict(self.source, kind='metadata')
        accepted, _ = review_claims(self.store, [self.claim], {'s': source}, self.reviewer(), self.reviewer('insufficient'))
        self.assertEqual(accepted[0]['evidence'][0]['evidence_level'], 'abstract')

    def test_stop_before_review_does_not_call_models(self):
        with self.assertRaisesRegex(ValueError, '停止'):
            review_case(self.store, self.claim, {'s': self.source}, self.reviewer(), should_stop=lambda: True)
        self.assertFalse(self.calls)

    def test_identical_concurrent_reviews_share_cloud_request(self):
        results = []
        errors = []
        call = self.reviewer()

        def slow(*a, **kw):
            time.sleep(0.1)
            return call(*a, **kw)

        def work():
            try:
                results.append(review_case(self.store, self.claim, {'s': self.source}, slow))
            except Exception as e:
                errors.append(e)
        threads = [threading.Thread(target=work) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertFalse(errors)
        self.assertEqual(len(results), 2)
        self.assertEqual(len(self.calls), 1)

    def test_history_has_no_invented_elapsed_or_activity(self):
        run = create_run(self.store, {'kind': 'answer', 'query': 'test'}, start=False)
        d = dashboard(self.store)
        self.assertEqual(d['selected']['id'], run['id'])
        self.assertIsNone(d['selected']['elapsed_seconds'])
        self.assertIsNone(d['selected']['usage'])
        self.assertEqual(d['selected']['dependencies'], [])
        self.assertTrue(all((r['status'] == 'not_recorded' for r in d['selected']['roles'])))

    def test_event_pagination_survives_reopen_and_is_read_only(self):
        run = create_run(self.store, {'kind': 'answer', 'query': 'test'}, start=False)
        with scope(self.store, run['id'], 'step', 'reader'):
            emit(self.store, 'read', message='阅读')
            emit(self.store, 'verify', message='核验', role='verifier')
            emit(self.store, 'cloud', 'waiting_resource', '等待云端槽位', role='cloud_reviewer')
        d = dashboard(self.store, run['id'], limit=2)
        self.assertEqual(len(d['events']), 2)
        self.assertTrue(d['has_more'])
        reopened = Store(self.temp.name)
        tail = dashboard(reopened, run['id'], d['cursor'])
        self.assertEqual(len(tail['events']), 1)
        self.assertEqual(tail['selected']['waiting_reason'], '等待云端槽位')
        before = reopened.path.stat().st_mtime_ns
        dashboard(reopened, run['id'], tail['cursor'])
        self.assertEqual(reopened.path.stat().st_mtime_ns, before)

    def test_final_result_does_not_erase_activity(self):
        run = create_run(self.store, {'kind': 'answer', 'query': 'test'}, start=False)
        with scope(self.store, run['id']):
            emit(self.store, 'verify', 'partial', '尚有证据缺口', role='verifier')
        with self.store.db() as db:
            db.execute("UPDATE agent_runs SET result='{}',status='partial' WHERE id=?", (run['id'],))
        d = dashboard(self.store, run['id'])
        self.assertEqual(d['selected']['activity']['role'], 'verifier')
        self.assertIn('resume', d['selected']['actions'])

    def test_selected_task_is_independent_from_other_active_task(self):
        a = create_run(self.store, {'kind': 'answer', 'query': 'a'}, start=False)
        b = create_run(self.store, {'kind': 'answer', 'query': 'b'}, start=False)
        with scope(self.store, b['id']):
            emit(self.store, 'write', message='B')
        d = dashboard(self.store, a['id'])
        self.assertEqual(d['selected']['id'], a['id'])
        self.assertEqual(d['events'], [])

    def test_partial_processed_is_not_complete(self):
        r = create_run(self.store, {'kind': 'answer', 'query': 'test'}, start=False)
        with self.store.db() as db:
            db.execute('INSERT INTO agent_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?)', ('t', r['id'], 'writer', 'p', 'fp', 'partial', '{}', '{}', 1, '', now()))
            db.execute("UPDATE agent_runs SET status='partial' WHERE id=?", (r['id'],))
        d = dashboard(self.store)['selected']
        self.assertEqual(d['processed'], 1)
        self.assertEqual(d['total'], 1)
        self.assertEqual(d['completed'], 0)
        self.assertEqual(d['status'], 'partial')

    def test_long_evidence_is_sharded_for_local_review_and_merged(self):
        """G05：超预算证据按上下文预算分片核验并汇总，不直接转云端。"""
        from kbapp.research.review import review_case
        long_text = 'adaptive sampling under static conditions ' * 40
        source_a = {'id': 'sa', 'paper_id': 'p', 'kind': 'paper_text', 'text': long_text, 'page_start': 1}
        source_b = {'id': 'sb', 'paper_id': 'p', 'kind': 'paper_text', 'text': 'second evidence ' + long_text, 'page_start': 2}
        claim = {'id': 'c', 'text': '该方法在静态条件下改进采样', 'evidence': [{'source_id': 'sa', 'quote': long_text[:60]}, {'source_id': 'sb', 'quote': long_text[:40]}]}
        self.store.save_settings({'local_text_context': 2048})
        local_calls = []

        def local(store, prompt, purpose, **kwargs):
            local_calls.append(purpose)
            data = json.loads(prompt.split('\n', 1)[1])
            return {'reviews': [{'id': data['claims'][0]['id'], 'supported': True, 'decision': 'supported', 'method_relation': True, 'unsupported_details': [], 'reason': '分片原文支持'}]}
        result = review_case(self.store, claim, {'sa': source_a, 'sb': source_b}, self.reviewer(), local)
        self.assertEqual(len(local_calls), 2, '两段长证据应分片核验')
        self.assertTrue(result['accepted'])

    def test_single_oversized_context_falls_back_to_insufficient(self):
        from kbapp.research.review import review_case
        huge = 'adaptive sampling ' * 4000
        source = {'id': 's', 'paper_id': 'p', 'kind': 'paper_text', 'text': huge, 'page_start': 1}
        claim = {'id': 'c', 'text': '结论', 'evidence': [{'source_id': 's', 'quote': huge[:80]}]}
        self.store.save_settings({'local_text_context': 2048})
        result = review_case(self.store, claim, {'s': source}, self.reviewer(), self.reviewer('insufficient'))
        self.assertIn('本地上下文预算不足', (result['local'] or {}).get('unsupported_details', []))
        self.assertEqual(result['local_state'], 'insufficient')
        self.assertTrue(result['accepted'], '单片超限时本地记为预算不足，由云端独立复核决定是否通过')


if __name__ == '__main__':
    unittest.main()
