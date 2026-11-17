"""I01：父任务、阶段与子单元状态聚合语义（partial/blocked/failed）。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['KB_NO_STARTUP_JOBS'] = '1'
os.environ['KB_NO_REFERENCE_WORKER'] = '1'
from kbapp.storage.store import Store
from kbapp.workflows.states import STATES, TERMINAL, aggregate, explain
from kbapp.workflows import agent_units


class States(unittest.TestCase):

    def test_vocabulary_and_explanations_are_complete(self):
        self.assertEqual(set(STATES), {'queued', 'running', 'stopping', 'complete', 'partial', 'blocked', 'failed', 'interrupted'})
        self.assertEqual(TERMINAL, {'complete', 'partial', 'blocked', 'failed', 'interrupted'})
        for state in STATES:
            self.assertTrue(explain(state))
        self.assertIn('受阻', explain('blocked'))

    def test_aggregate_matrix(self):
        self.assertEqual(aggregate({'running': 1, 'complete': 2}), 'running')
        self.assertEqual(aggregate({'queued': 1, 'complete': 2}), 'queued')
        self.assertEqual(aggregate({'complete': 3}), 'complete')
        self.assertEqual(aggregate({'complete': 2, 'blocked': 1}), 'blocked', '剩余项全部受阻时是受阻而不是部分完成')
        self.assertEqual(aggregate({'blocked': 2}), 'blocked')
        self.assertEqual(aggregate({'failed': 2}), 'failed')
        self.assertEqual(aggregate({'complete': 1, 'failed': 1}), 'partial')
        self.assertEqual(aggregate({'complete': 1, 'partial': 1}), 'partial')
        self.assertEqual(aggregate({}), 'queued')
        self.assertEqual(aggregate({'complete': 1}, stopped=True), 'interrupted')
        self.assertEqual(aggregate({'complete': 1}, stopped=True, running=1), 'stopping')
        self.assertEqual(aggregate({'complete': 1}, stopped=True, halt_error=True), 'failed')

    def test_settle_marks_blocked_stage_and_run(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = Store(temp.name)
        with store.db() as db:
            db.execute("INSERT INTO agent_runs VALUES('r','lineage','running','{}','fp',0,0,'{}','','2026-11-03','2026-11-03')")
            db.execute("INSERT INTO agent_tasks VALUES('t','r','verifier','p','fp','running','{}','{}',0,'','2026-11-03')")
            db.execute("INSERT INTO agent_units VALUES('u1','t','r','gather-evidence','retriever','p','k1','blocked','{}','{}',1,'','2026-11-03')")
            db.execute("INSERT INTO agent_units VALUES('u2','t','r','propose','lineage_researcher','p','k2','blocked','{}','{}',0,'','2026-11-03')")
        state = agent_units.settle(store, 'r', 't')
        self.assertEqual(state, 'blocked')
        with store.db() as db:
            self.assertEqual(db.execute("SELECT status FROM agent_tasks WHERE id='t'").fetchone()[0], 'blocked')
            self.assertEqual(db.execute("SELECT status FROM agent_runs WHERE id='r'").fetchone()[0], 'blocked')

    def test_mixed_stage_is_partial(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = Store(temp.name)
        with store.db() as db:
            db.execute("INSERT INTO agent_runs VALUES('r','lineage','running','{}','fp',0,0,'{}','','2026-11-03','2026-11-03')")
            db.execute("INSERT INTO agent_tasks VALUES('t','r','verifier','p','fp','running','{}','{}',0,'','2026-11-03')")
            db.execute("INSERT INTO agent_units VALUES('u1','t','r','gather-evidence','retriever','p','k1','complete','{}','{}',1,'','2026-11-03')")
            db.execute("INSERT INTO agent_units VALUES('u2','t','r','propose','lineage_researcher','p','k2','failed','{}','{}',0,'boom','2026-11-03')")
        state = agent_units.settle(store, 'r', 't')
        self.assertEqual(state, 'partial')


if __name__ == '__main__':
    unittest.main()
