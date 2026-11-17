"""F01–F05：分通道候选发现、通道配额与分层停止。"""
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
from kbapp.research.candidates import citation_channel, co_citation_channel, collect, QUOTAS


class FakeEngine:
    def __init__(self, hits):
        self.hits = hits

    def search(self, query, k=40, wait_vectors=False):
        return {'results': [{'paper_id': pid} for pid in self.hits[:k]]}


class Candidates(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.store.put_papers([{'id': pid, 'title': pid.upper(), 'kind': 'library'} for pid in ('a', 'b', 'c', 'd', 'e', 'r1')])
        for src, dst in [('b', 'a'), ('d', 'a'), ('b', 'e'), ('d', 'e'), ('a', 'r1'), ('c', 'r1')]:
            self.store.relation({'src': src, 'dst': dst, 'kind': 'cites', 'status': 'confirmed', 'level': 'citation', 'evidence': [{'quote': 'x'}]})

    def test_citation_channel_keeps_channel_and_score(self):
        frozen = {'neighbors': {'a': {'b': 2, 'd': 1}}}
        results = citation_channel(self.store, {'id': 'a'}, frozen)
        self.assertEqual({r['paper_id'] for r in results}, {'b', 'd'})
        self.assertTrue(all(r['channel'] == 'citation' for r in results))
        self.assertEqual(max(r['score'] for r in results), 6.0)

    def test_co_citation_and_coupling_are_found(self):
        results = co_citation_channel(self.store, {'id': 'a'})
        by_paper = {}
        for r in results:
            by_paper.setdefault(r['paper_id'], set()).add(r['reason'])
        self.assertIn('共引', by_paper['e'], 'b、d 同时引用 a 与 e，e 与 a 共引')
        self.assertIn('文献耦合', by_paper['c'], 'a 与 c 共同引用 r1')

    def test_channel_quotas_prevent_dominance(self):
        frozen = {'neighbors': {}, 'search': FakeEngine([f's{n}' for n in range(10)])}
        self.store.put_papers([{'id': f's{n}', 'title': f'S{n}', 'kind': 'library'} for n in range(10)])
        candidates, reason = collect(self.store, {'id': 'a', 'title': 'A'}, frozen, limit=24, quotas={'similar': 3})
        similar = [c for c in candidates if c['channels'] == ['similar']]
        self.assertLessEqual(len(similar), 3)
        self.assertEqual(reason, 'quota')

    def test_stop_reason_is_exhausted_when_no_more_candidates(self):
        frozen = {'neighbors': {'a': {'b': 1}}, 'search': FakeEngine([])}
        candidates, reason = collect(self.store, {'id': 'a', 'title': 'A'}, frozen, limit=24)
        self.assertIn('b', [c['paper_id'] for c in candidates])
        self.assertEqual(reason, 'exhausted')
        self.assertGreater(QUOTAS['citation'], 0)



    def test_context_channel_uses_body_citations(self):
        from kbapp.research.candidates import context_channel
        with self.store.db() as db:
            db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?)', ('doc', 'a', '', 'hash', 'unused', 1, '{}', 'now'))
            db.execute('INSERT INTO document_pages VALUES(?,?,?,?,?,?,?,?)', ('doc', 1, 'NATIVE_TEXT', 'complete', 'We extend [1].', '', '{}', 'now'))
            db.execute('INSERT INTO document_references VALUES(?,?,?,?,?,?,?)', ('ref', 'doc', 1, '1', 'raw', 'e', '{}'))
            db.execute('INSERT INTO citation_contexts VALUES(?,?,?,?,?,?,?)', ('ctx', 'ref', 'doc', 1, 'Method', 'We extend [1].', 'unknown'))
        results = context_channel(self.store, {'id': 'a'})
        self.assertEqual([r['paper_id'] for r in results], ['e'])
        self.assertEqual(results[0]['channel'], 'context')

    def test_candidate_explanation_states_why_checked_and_next(self):
        from kbapp.research.candidates import explain
        candidate = {'candidate_reasons': ['引用邻居', '共引']}
        text = explain(candidate, {'verification_status': 'insufficient', 'gaps': ['缺少双方可定位的 PDF 原文证据']})
        self.assertIn('引用邻居', text['why'])
        self.assertIn('证据不足', text['checked'])
        self.assertIn('PDF 原文证据', text['next'])
        empty = explain(candidate)
        self.assertIn('尚未开始核验', empty['checked'])



    def test_method_entities_are_classified_and_alias_conflicts_reported(self):
        from kbapp.research.methods import classify, alias_conflicts
        self.store.put_papers([{'id': 'm1', 'title': 'RRT', 'kind': 'library'}, {'id': 'm2', 'title': 'RRT star', 'kind': 'library'},
                               {'id': 'm3', 'title': 'Informed RRT star', 'kind': 'library'}])
        with self.store.db() as db:
            for mid, name, aliases, family in [
                ('method:rrt', 'RRT', ['RRT'], '采样与最优规划'),
                ('method:rrtstar', 'RRT*', ['RRT star', 'RRT*'], '采样与最优规划'),
                ('method:informed', 'Informed RRT*', ['RRT star'], '采样与最优规划'),
            ]:
                db.execute('INSERT INTO method_concepts VALUES(?,?,?,?,0,?)', (mid, name, json.dumps(aliases), json.dumps({'family': family}), 'now'))
        kinds = classify(self.store)
        self.assertEqual(kinds['method:rrt']['kind'], 'algorithm')
        self.assertEqual(kinds['method:informed']['kind'], 'variant')
        conflicts = alias_conflicts(self.store)
        self.assertTrue(any(c['alias'] == 'rrt star' and len(c['methods']) == 2 for c in conflicts))


if __name__ == '__main__':
    unittest.main()
