"""固定类型选择与完整数据阶段汇总；所有任务均为本地模拟，不启动执行器。"""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['KB_NO_STARTUP_JOBS'] = '1'
os.environ['KB_NO_REFERENCE_WORKER'] = '1'
from kbapp.storage.store import Store
from kbapp.storage.activity import scope, emit, dashboard_phase
from kbapp.workflows.agents import create_run
from kbapp.workflows.agent_units import add
from kbapp.workflows.dashboard import dashboard
from kbapp.core.io import now


class DashboardPhases(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.store.put_papers([{'id':'p', 'title':'Paper P', 'citekey':'PaperP', 'kind':'library'}, {'id':'q', 'title':'Paper Q', 'citekey':'PaperQ', 'kind':'library'}])

    def tearDown(self):
        self.temp.cleanup()

    def run_record(self, kind='lineage', **values):
        return create_run(self.store, dict(kind=kind, **values), start=False)['id']

    def parent(self, rid, pid='parent', role='lineage_researcher', status='running'):
        with self.store.db() as db:
            db.execute('INSERT INTO agent_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?)', (pid, rid, role, 'p', pid, status, '{}', '{}', 0, '', now()))
        return dict(id=pid, run_id=rid, role=role, subject='p')

    def unit(self, parent, stage, status='complete', subject='p', payload=None):
        with self.store.db() as db:
            uid = add(db, parent, stage, 'verifier' if stage.startswith('review-local') else 'lineage_researcher', subject, payload)
            db.execute('UPDATE agent_units SET status=? WHERE id=?', (status, uid))
        return uid

    def phases(self, rid):
        return {p['id']:p for p in dashboard(self.store, rid)['selected']['phase_summaries']}

    def test_kind_selection_prefers_active_then_resumable_over_recent_complete(self):
        old = self.run_record(query='old')
        new = self.run_record(query='new')
        with self.store.db() as db:
            db.execute("UPDATE agent_runs SET status='interrupted',created='2020-01-01T00:00:00+08:00' WHERE id=?", (old,))
            db.execute("UPDATE agent_runs SET status='complete' WHERE id=?", (new,))
        self.assertEqual(dashboard(self.store, kind='lineage')['selected']['id'], old)
        with self.store.db() as db:
            db.execute("UPDATE agent_runs SET status='running' WHERE id=?", (new,))
        self.assertEqual(dashboard(self.store, kind='lineage')['selected']['id'], new)
        self.assertIsNone(dashboard(self.store, kind='vectors')['selected'])

    def test_type_and_exact_history_selection_escape_recent_window(self):
        old = self.run_record('maintenance')
        with self.store.db() as db:
            db.execute("UPDATE agent_runs SET status='complete',created='2020-01-01T00:00:00+08:00' WHERE id=?", (old,))
            for n in range(65):
                db.execute('INSERT INTO agent_runs(id,kind,status,input,created,updated) VALUES(?,?,?,?,?,?)', (f'history{n}', 'lineage', 'complete', '{}', now(), now()))
        self.assertEqual(dashboard(self.store, kind='maintenance')['selected']['id'], old)
        self.assertEqual(dashboard(self.store, old)['selected']['id'], old)

    def test_verification_aliases_and_original_scope(self):
        with self.store.db() as db:
            db.execute('INSERT INTO jobs(id,kind,status,payload,started,finished) VALUES(?,?,?,?,?,?)', ('v', 'verify', 'interrupted', json.dumps({'filters':{'project':False,'q':'RRT'}}), now(), now()))
        self.store.set_state('analysis_state', {'job':'v'})
        task = dashboard(self.store, kind='verification')['selected']
        self.assertEqual(task['kind'], 'verification')
        self.assertEqual(task['control']['filters']['q'], 'RRT')
        self.assertEqual(len(task['phase_summaries']), 5)

    def test_full_aggregation_parallel_objects_and_stopped_state(self):
        rid = self.run_record()
        parent = self.parent(rid)
        for n in range(125):
            self.unit(parent, f'propose:{n}', 'running' if n<3 else 'complete', payload={'target':{'id':'q'}})
        phase = self.phases(rid)['propose']
        self.assertEqual((phase['total'], phase['completed'], phase['active_count']), (125,122,3))
        self.assertEqual(len(phase['active_items']), 2)
        self.assertEqual(phase['active_items'][0]['title'], 'PaperP → PaperQ')
        self.assertTrue(phase['details_truncated'])
        with self.store.db() as db:
            db.execute("UPDATE agent_runs SET status='interrupted' WHERE id=?", (rid,))
        phase = self.phases(rid)['propose']
        self.assertEqual(phase['active_items'], [])
        self.assertNotEqual(phase['status'], 'running')

    def test_upstream_generation_and_supplement_cycle_do_not_complete_early(self):
        rid = self.run_record()
        parent = self.parent(rid)
        identify = self.unit(parent, 'identify', 'running')
        self.unit(parent, 'recall')
        self.unit(parent, 'review-local:1')
        self.assertNotEqual(self.phases(rid)['recall']['status'], 'complete')
        self.assertNotEqual(self.phases(rid)['review-local']['status'], 'complete')
        with self.store.db() as db:
            db.execute("UPDATE agent_units SET status='complete' WHERE id=?", (identify,))
        self.assertEqual(self.phases(rid)['recall']['status'], 'complete')
        self.unit(parent, 'review-local:2', 'running')
        self.assertEqual(self.phases(rid)['review-local']['status'], 'running')

    def test_single_paper_reading_marks_unrequested_stages_not_applicable(self):
        rid = self.run_record('maintenance', paper_ids=['p'], stages=['understanding'])
        parent = self.parent(rid, role='reader', status='complete')
        self.unit(parent, 'execute')
        phases = self.phases(rid)
        self.assertEqual(phases['reading']['status'], 'complete')
        self.assertEqual(phases['identify']['status'], 'not_applicable')
        self.assertEqual(phases['review-cloud']['status'], 'not_applicable')

    def test_missing_history_is_unknown_and_failed_unit_remains_failed(self):
        rid = self.run_record()
        self.assertEqual(self.phases(rid)['review-local']['status'], 'not_recorded')
        parent = self.parent(rid)
        self.unit(parent, 'review-local:1', 'failed')
        self.assertEqual(self.phases(rid)['review-local']['status'], 'failed')

    def test_explicit_job_boundaries_and_model_events_share_phase(self):
        with self.store.db() as db:
            db.execute('INSERT INTO jobs(id,kind,status,started) VALUES(?,?,?,?)', ('j','vectors','running',now()))
        with scope(self.store, 'job:j'):
            with dashboard_phase(self.store, 'prepare', '来源检查'):
                emit(self.store, 'index', message='已读取来源')
            with dashboard_phase(self.store, 'index', '向量计算'):
                emit(self.store, 'model', message='当前文献 PaperP')
                phase = self.phases('job:j')['index']
                self.assertEqual(phase['active_items'][0]['title'], '当前文献 PaperP')
        self.assertEqual(self.phases('job:j')['prepare']['status'], 'complete')
        self.assertEqual(self.phases('job:j')['publish']['status'], 'not_recorded')

    def test_api_accepts_kind_and_retains_event_cursor(self):
        from fastapi.testclient import TestClient
        from support import service_for
        rid = self.run_record()
        service = service_for(self.store)
        with TestClient(service.app) as client:
            data = client.get('/api/task-dashboard?kind=lineage').json()
            self.assertEqual(data['selected']['id'], rid)
            self.assertEqual(client.get('/api/task-dashboard?kind=vectors').json()['selected'], None)


if __name__ == '__main__':
    unittest.main()
