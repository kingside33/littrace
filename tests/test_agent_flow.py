"""并行调度的行为回归，模型均为模拟调用。"""

import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kbapp.core.io import now
from kbapp.storage.store import Store
from kbapp.storage.activity import scope, emit
from kbapp.workflows import agents, agent_units
from kbapp.workflows.dashboard import dashboard
from kbapp.research.review import review_case
from kbapp.integrations.scheduling import model_slot


class AgentFlow(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.papers = [
            dict(
                id=p,
                title='Paper ' + p,
                citekey=p,
                kind='library',
                abstract='Original evidence.',
                year=2020,
            )
            for p in ['a', 'b', 'c']
        ]
        self.store.put_papers(self.papers)
        self.addCleanup(patch.stopall)
        patch('kbapp.workflows.agents.sync_graph').start()
        patch('kbapp.integrations.model_identity.signature', return_value='test-weights').start()
        self.calls = []
        self.source = {
            'id': 's',
            'paper_id': 'a',
            'kind': 'paper_text',
            'text': 'Original evidence.',
            'page_start': 1,
        }
        self.claim = {
            'text': 'Original evidence.',
            'evidence': [{'source_id': 's', 'quote': 'Original evidence.'}],
        }

    def run_with(self, roles):
        run = agents.create_run(
            self.store, {'kind': 'lineage', 'query': str(time.time_ns())}, start=False
        )
        with self.store.db() as db:
            for n, (role, subject) in enumerate(roles):
                db.execute(
                    'INSERT INTO agent_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (
                        run['id'] + ':' + str(n),
                        run['id'],
                        role,
                        subject,
                        'fp',
                        'queued',
                        '{}',
                        '{}',
                        0,
                        '',
                        now(),
                    ),
                )
                # Seed historical review units; material acquisition is exercised by test_lineage_repair.
                if role == 'verifier':
                    parent = dict(id=run['id'] + ':' + str(n), run_id=run['id'])
                    agent_units.add(db, parent, 'review-local', role, subject, {'mode': 'existing'})
        agent_units.ensure(self.store, run['id'])
        return run['id']

    def reviewer(self, cloud=False, on_call=lambda: None):
        def call(store, prompt, purpose, **kwargs):
            self.calls.append('cloud' if cloud else 'local')
            on_call()
            emit(
                store,
                purpose,
                'running',
                '复核当前证据' if cloud else '核验当前证据',
                role='cloud_reviewer' if cloud else 'verifier',
                model='mock',
            )
            claim = json.loads(prompt.split('\n', 1)[1])['claims'][0]
            return {
                'reviews': [
                    {
                        'id': claim['id'],
                        'supported': True,
                        'decision': 'supported',
                        'method_relation': True,
                        'unsupported_details': [],
                        'reason': '原文支持',
                    }
                ]
            }

        return call

    def review_relation(self, cloud=None):
        def execute(store, *args, **kwargs):
            r = review_case(
                store, self.claim, {'s': self.source}, cloud or self.reviewer(True), self.reviewer()
            )
            return dict(r, verification_status=r['status'])

        return execute

    def context(self, paper):
        return {
            'paper': paper,
            'source': dict(self.source, paper_id=paper['id']),
            'fingerprint': 'fp-' + paper['id'],
            'classification': {},
        }

    def test_parallel_pipeline_advances_review_before_all_lineage_finishes(self):
        rid = self.run_with(
            [('lineage_researcher', 'a'), ('lineage_researcher', 'b'), ('verifier', 'relation:r')]
        )
        gate = threading.Event()
        timeline = []
        both_started = threading.Barrier(2)

        def classify(store, paper, *args):
            timeline.append(('start', paper['id']))
            both_started.wait(3)
            if paper['id'] == 'b':
                self.assertTrue(
                    gate.wait(3), 'cloud review was held behind the whole lineage batch'
                )
            timeline.append(('finish', paper['id']))
            return self.context(paper)

        def cloud_started():
            timeline.append(('cloud', 'r'))
            gate.set()

        def recall(store, paper, **kwargs):
            return [self.papers[1]] if paper['id'] == 'a' else []

        with (
            patch('kbapp.research.lineage.classify_paper', side_effect=classify),
            patch('kbapp.research.lineage.candidate_neighbors', side_effect=recall),
            patch(
                'kbapp.research.lineage.propose_pair', return_value={'proposal': {'relation': None}}
            ),
            patch(
                'kbapp.research.lineage.reassess_relation',
                side_effect=self.review_relation(self.reviewer(True, cloud_started)),
            ),
        ):
            agents.dispatch(self.store, threading.Event())
        self.assertLess(timeline.index(('cloud', 'r')), timeline.index(('finish', 'b')))
        self.assertLess(timeline.index(('start', 'b')), timeline.index(('finish', 'a')))
        self.assertEqual(self.calls.count('local'), 1)
        self.assertEqual(self.calls.count('cloud'), 1)
        r = agents.get_run(self.store, rid)
        self.assertEqual(r['status'], 'complete')
        self.assertEqual(r['total'], 3)
        with self.store.db() as db:
            self.assertGreater(
                db.execute('SELECT COUNT(*) FROM agent_units WHERE run_id=?', (rid,)).fetchone()[0],
                3,
            )
        self.assertEqual(dashboard(self.store, rid)['selected']['total'], 3)

    def test_claim_is_atomic_and_same_object_is_not_claimed_twice(self):
        rid = self.run_with([('lineage_researcher', 'a')])
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: agent_units.claim(self.store, rid), range(6)))
        self.assertEqual(sum(r is not None for r in results), 1)
        other = self.run_with([('lineage_researcher', 'a')])
        self.assertIsNone(agent_units.claim(self.store, other))
        relation = dict(
            id='r',
            src='a',
            dst='b',
            kind='improves',
            status='candidate',
            level='abstract',
            explanation='automatic',
        )

        def write(manual):
            for _ in range(8):
                self.store.relation(
                    dict(relation, explanation='human' if manual else 'automatic'), manual=manual
                )

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(write, [False, True]))
        saved = self.store.relations()[0]
        self.assertTrue(saved['manual'])
        self.assertEqual(saved['explanation'], 'human')

    def test_review_stage_survives_reopen_and_reuses_local_result(self):
        rid = self.run_with([('verifier', 'relation:r')])
        with patch('kbapp.research.lineage.reassess_relation', side_effect=self.review_relation()):
            agents.step(self.store, rid)
            self.assertEqual(self.calls, ['local'])
            cloud = agent_units.claim(self.store, rid)
            self.assertEqual(cloud['role'], 'cloud_reviewer')
            self.store = Store(self.temp.name)
            # I02：模拟原执行进程已退出（同进程内的租约仍然有效，不应被自己抢占）
            with self.store.db() as db:
                db.execute(
                    "UPDATE agent_unit_leases SET owner='999999:dead:0' WHERE unit_id=?",
                    (cloud['id'],),
                )
            agents.recover_units(self.store)
            agents.step(self.store, rid)
        self.assertEqual(self.calls, ['local', 'cloud'])
        self.assertEqual(agents.get_run(self.store, rid)['status'], 'complete')
        with self.store.db() as db:
            self.assertEqual(
                db.execute('SELECT attempt FROM agent_units WHERE id=?', (cloud['id'],)).fetchone()[
                    0
                ],
                2,
            )

    def test_ready_recall_is_not_held_behind_unrelated_review_batch(self):
        rid = self.run_with(
            [('verifier', 'relation:r'), ('verifier', 'relation:s'), ('lineage_researcher', 'a')]
        )
        local = agent_units.claim(self.store, rid)
        identity = agent_units.claim(self.store, rid)
        self.assertEqual(identity['stage'], 'identify')
        agent_units.finish(
            self.store,
            identity,
            self.context(self.papers[0]),
            [
                dict(
                    stage='recall',
                    role='retriever',
                    subject='a',
                    payload={'context_id': identity['id']},
                )
            ],
        )
        agent_units.finish(self.store, local, {}, status='partial')
        ready = agent_units.claim(self.store, rid)
        self.assertEqual(ready['stage'], 'recall')
        agent_units.finish(
            self.store,
            ready,
            {},
            [dict(stage='recall:next', role='retriever', subject='b', payload={})],
        )
        review = agent_units.claim(self.store, rid)
        self.assertEqual(
            review['stage'], 'review-local', 'Recall batch starved a ready verification'
        )
        agent_units.finish(self.store, review, {}, status='partial')
        self.assertEqual(agent_units.claim(self.store, rid)['stage'], 'recall:next')

    def test_failed_dependencies_stay_blocked_until_explicit_resume(self):
        rid = self.run_with([('lineage_researcher', 'a')])
        u = agent_units.claim(self.store, rid)
        agent_units.finish(
            self.store,
            u,
            {'saved': True},
            [dict(stage='recall', role='retriever', subject='a', payload={})],
        )
        agent_units.settle(self.store, rid, u['parent_id'])
        v = agent_units.claim(self.store, rid)
        agent_units.finish(
            self.store,
            v,
            {},
            [dict(stage='propose:b', role='lineage_researcher', subject='a|b', payload={})],
        )
        with self.store.db() as db:
            db.execute("UPDATE agent_units SET status='failed' WHERE id=?", (v['id'],))
        agent_units.settle(self.store, rid, None)
        with self.store.db() as db:
            self.assertEqual(
                db.execute("SELECT status FROM agent_units WHERE stage='propose:b'").fetchone()[0],
                'blocked',
            )
        with patch('kbapp.workflows.agents.start_dispatcher'):
            agents.control_run(self.store, rid, 'resume')
        with self.store.db() as db:
            self.assertEqual(
                db.execute('SELECT status FROM agent_units WHERE id=?', (u['id'],)).fetchone()[0],
                'complete',
            )
            self.assertEqual(
                db.execute('SELECT status FROM agent_units WHERE id=?', (v['id'],)).fetchone()[0],
                'queued',
            )

    def test_stop_waits_for_every_inflight_result_and_resume_keeps_them(self):
        rid = self.run_with([('lineage_researcher', 'a'), ('lineage_researcher', 'b')])
        units = [agent_units.claim(self.store, rid), agent_units.claim(self.store, rid)]
        gates = {p: threading.Event() for p in ['a', 'b']}
        started = threading.Barrier(3)
        calls = []

        def classify(store, paper, *args):
            calls.append(paper['id'])
            started.wait()
            gates[paper['id']].wait(3)
            return self.context(paper)

        with patch('kbapp.research.lineage.classify_paper', side_effect=classify):
            threads = [
                threading.Thread(target=agents.execute_unit, args=(self.store, u)) for u in units
            ]
            for thread in threads:
                thread.start()
            started.wait()
            agents.control_run(self.store, rid, 'stop')
            gates['a'].set()
            threads[0].join(3)
            self.assertEqual(agents.get_run(self.store, rid)['status'], 'stopping')
            gates['b'].set()
            threads[1].join(3)
        self.assertEqual(agents.get_run(self.store, rid)['status'], 'interrupted')
        with patch('kbapp.workflows.agents.start_dispatcher'):
            agents.control_run(self.store, rid, 'resume')
        with (
            patch(
                'kbapp.research.lineage.classify_paper',
                side_effect=AssertionError('completed stage repeated'),
            ),
            patch('kbapp.research.lineage.candidate_neighbors', return_value=[]),
        ):
            for _ in range(3):
                agents.step(self.store, rid)
        self.assertEqual(agents.get_run(self.store, rid)['status'], 'complete')
        self.assertCountEqual(calls, ['a', 'b'])

    def test_interactive_unit_uses_reserved_slot_while_background_is_busy(self):
        self.run_with(
            [('lineage_researcher', 'a'), ('lineage_researcher', 'b'), ('verifier', 'relation:r')]
        )
        ready = threading.Event()
        interactive = threading.Event()
        lock = threading.Lock()
        entered = []
        failures = []

        def perform(store, unit, *args):
            with lock:
                entered.append(unit['role'])
                if len(entered) == 3:
                    ready.set()
            if unit['role'] == 'writer':
                interactive.set()
            elif not interactive.wait(3):
                failures.append('interactive slot unavailable')
            return {}, [], False

        def submit_interactive():
            if not ready.wait(3):
                failures.append('background did not fill slots')
                return
            r = agents.create_run(
                self.store, {'kind': 'answer', 'query': 'interactive'}, start=False
            )
            with self.store.db() as db:
                db.execute(
                    'INSERT INTO agent_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (
                        'interactive-parent',
                        r['id'],
                        'writer',
                        r['id'],
                        'fp',
                        'queued',
                        '{}',
                        '{}',
                        0,
                        '',
                        now(),
                    ),
                )

        producer = threading.Thread(target=submit_interactive)
        producer.start()
        with patch('kbapp.workflows.unit_work.perform', side_effect=perform):
            agents.dispatch(self.store, threading.Event())
        producer.join(3)
        self.assertFalse(failures)
        self.assertTrue(interactive.is_set())
        self.assertIn('writer', entered)

    def test_cloud_capacity_two_and_local_capacity_one(self):
        rid = self.run_with(
            [('writer', 'a'), ('retriever', 'b'), ('reader', 'c'), ('verifier', 'relation:r')]
        )
        self.assertTrue(all(agent_units.claim(self.store, rid) for _ in range(3)))
        self.assertIsNone(
            agent_units.claim(self.store, rid), 'Background units exceeded the persistent limit'
        )
        for resource, capacity in [('cloud', 2), ('local', 1)]:
            lock = threading.Lock()
            active = [0]
            maximum = [0]
            enough = threading.Event()

            def work(_):
                with model_slot(self.store.root, resource):
                    with lock:
                        active[0] += 1
                        maximum[0] = max(maximum[0], active[0])
                        if active[0] == capacity:
                            enough.set()
                    enough.wait(2)
                    time.sleep(0.025)
                    with lock:
                        active[0] -= 1

            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(work, range(4)))
            self.assertEqual(maximum[0], capacity)

    def test_parallel_role_summaries_and_handoffs_are_isolated_by_unit(self):
        rid = self.run_with([('lineage_researcher', 'a'), ('verifier', 'relation:r')])
        one = agent_units.claim(self.store, rid)
        two = agent_units.claim(self.store, rid)
        with scope(self.store, rid, one['id'], 'lineage_researcher', subject='a', label='Paper A'):
            emit(self.store, 'abstract_lineage', message='核对候选关系')
        with scope(
            self.store, rid, two['id'], 'verifier', subject='relation:r', label='Relation R'
        ):
            emit(self.store, 'understanding_verify', message='核验当前证据')
        with scope(self.store, rid, one['id'], 'cloud_reviewer', subject='a', label='Paper A'):
            emit(
                self.store,
                'independent_claim_review',
                message='独立复核',
                model='mock',
                prompt='PRIVATE_PROMPT',
                reasoning_content='PRIVATE_REASONING',
            )
        data = dashboard(self.store, rid)['selected']
        roles = {r['id']: r for r in data['roles']}
        self.assertEqual(roles['cloud_reviewer']['active_count'], 1)
        self.assertEqual(roles['verifier']['active_count'], 1)
        self.assertEqual(roles['lineage_researcher']['active_count'], 0)
        self.assertIn('当前无在途单元', roles['lineage_researcher']['message'])
        self.assertNotIn('PRIVATE_', json.dumps(roles))
        self.assertIn(
            {'source': 'lineage_researcher', 'target': 'cloud_reviewer'}, data['dependencies']
        )
        self.assertNotIn(
            {'source': 'lineage_researcher', 'target': 'verifier'}, data['dependencies']
        )
        self.assertEqual(data['parallel']['running_units'], 2)

    def test_existing_completed_parents_are_never_expanded(self):
        rid = self.run_with([('lineage_researcher', 'a')])
        with self.store.db() as db:
            db.execute('DELETE FROM agent_units WHERE run_id=?', (rid,))
            db.execute(
                "UPDATE agent_tasks SET status='complete',result=? WHERE run_id=?",
                (json.dumps({'historical': 'unchanged'}), rid),
            )
        agent_units.ensure(self.store, rid)
        with self.store.db() as db:
            self.assertEqual(
                db.execute('SELECT COUNT(*) FROM agent_units WHERE run_id=?', (rid,)).fetchone()[0],
                0,
            )
            self.assertEqual(
                json.loads(
                    db.execute('SELECT result FROM agent_tasks WHERE run_id=?', (rid,)).fetchone()[
                        0
                    ]
                ),
                {'historical': 'unchanged'},
            )

    def test_local_failure_is_not_retried_without_resume_and_cloud_result_is_retained(self):
        rid = self.run_with([('verifier', 'relation:r')])
        failed = [True]

        def local(*args, **kwargs):
            self.calls.append('local')
            if failed[0]:
                raise ValueError('model unavailable')
            claim = json.loads(args[1].split('\n', 1)[1])['claims'][0]
            return {
                'reviews': [
                    {
                        'id': claim['id'],
                        'supported': True,
                        'decision': 'supported',
                        'method_relation': True,
                        'unsupported_details': [],
                        'reason': '原文支持',
                    }
                ]
            }

        def execute(store, *args, **kwargs):
            result = review_case(store, self.claim, {'s': self.source}, self.reviewer(True), local)
            if result['status'] == 'execution_error':
                raise ValueError('重核模型未返回完整结论')
            return dict(result, verification_status=result['status'])

        with patch('kbapp.research.lineage.reassess_relation', side_effect=execute):
            for _ in range(3):
                agents.step(self.store, rid)
            self.assertEqual(agents.get_run(self.store, rid)['status'], 'failed')
            self.assertEqual(self.calls, ['local', 'cloud'])
            failed[0] = False
            with patch('kbapp.workflows.agents.start_dispatcher'):
                agents.control_run(self.store, rid, 'resume')
            for _ in range(5):
                agents.step(self.store, rid)
        self.assertEqual(agents.get_run(self.store, rid)['status'], 'complete')
        self.assertEqual(self.calls.count('local'), 2)
        self.assertEqual(self.calls.count('cloud'), 1)

    def test_local_context_overflow_handoff_does_not_loop(self):
        rid = self.run_with([('verifier', 'relation:r')])
        with (
            patch('kbapp.core.chunking.est_tokens', return_value=100000),
            patch('kbapp.research.lineage.reassess_relation', side_effect=self.review_relation()),
        ):
            for _ in range(4):
                agents.step(self.store, rid)
        self.assertEqual(self.calls, ['cloud'])
        self.assertEqual(agents.get_run(self.store, rid)['status'], 'complete')
