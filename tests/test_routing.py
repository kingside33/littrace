"""G07/G08：云端复核路由条件与本地拒绝结果抽样审计。"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['KB_NO_STARTUP_JOBS'] = '1'
os.environ['KB_NO_REFERENCE_WORKER'] = '1'
from kbapp.storage.store import Store
from kbapp.research.routing import route_reasons, sample_for_audit


class Routing(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)

    def test_route_reasons_cover_core_relation_divergence_numbers_and_audit(self):
        reasons = route_reasons({'kind': 'improves', 'numbers': [{'metric': 'energy'}]}, 'contradicted', relation=True)
        self.assertIn('核心方法关系', reasons)
        self.assertIn('本地判断分歧或执行失败', reasons)
        self.assertIn('复杂数字与条件', reasons)
        self.assertEqual(route_reasons({'kind': 'improves'}, 'supported', relation=True), ['核心方法关系'])
        self.assertEqual(route_reasons({'kind': 'paper_fact'}, 'supported'), ['抽样审计'])

    def test_audit_sampling_excludes_supported_and_is_deterministic(self):
        def put(key, subject, verdict):
            with self.store.db() as db:
                db.execute('INSERT INTO verification_attempts VALUES(?,?,?,?,?,?)', (key, subject, 'fp', 'complete', json.dumps({'verdict': verdict}), '2026-11-03'))
        put('local:a', 'subj-a', {'supported': False, 'decision': 'insufficient', 'reason': '证据不足'})
        put('local:b', 'subj-b', {'supported': False, 'decision': 'contradicted', 'reason': '原文相反'})
        put('local:c', 'subj-c', {'supported': True, 'decision': 'supported', 'reason': '支持'})
        first = sample_for_audit(self.store, rate=1.0)
        second = sample_for_audit(self.store, rate=1.0)
        self.assertEqual(first, second)
        self.assertEqual({row['subject'] for row in first}, {'subj-a', 'subj-b'})
        with self.assertRaises(ValueError):
            sample_for_audit(self.store, rate=0)


if __name__ == '__main__':
    unittest.main()
