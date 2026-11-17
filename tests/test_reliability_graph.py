"""Isolated graph reliability regression: real Store, retrieval and HTTP routes."""
import json
import tempfile
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from kbapp.storage.store import Store
from kbapp.research import candidates, graph
from kbapp.web.graph_api import install


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)

    def papers(self, *ids):
        self.store.put_papers([{'id': pid, 'title': pid, 'kind': 'library'} for pid in ids])

    def relation(self, src, dst, **values):
        return self.store.relation(dict(src=src, dst=dst, kind='cites', status='confirmed', **values))


class CandidateReliability(StoreCase):
    def test_duplicate_passages_and_self_do_not_spend_channel_quota_or_rank(self):
        self.papers('seed', 'a', 'b', 'c')
        engine = SimpleNamespace(search=lambda *a, **k: {'mode': 'bm25', 'results': [
            {'paper_id': pid} for pid in ['seed'] * 10 + ['a'] * 20 + ['b', 'c']]})
        selected, stop = candidates.collect(self.store, {'id': 'seed', 'title': 'query'},
            {'neighbors': {'seed': {'seed': 100}}, 'search': engine},
            quotas={'citation': 1, 'similar': 3, 'method': 0, 'co_citation': 0, 'context': 0})
        self.assertEqual({r['paper_id'] for r in selected}, {'a', 'b', 'c'})
        self.assertEqual(stop, 'exhausted')
        self.assertTrue(all('BM25' in ' '.join(r['reasons']) for r in selected))

    def test_budget_reserves_every_channel_and_fuses_ranks_not_raw_scales(self):
        self.papers('seed')
        rows = {name: [{'paper_id': f'{name}-{i:02}', 'channel': name, 'score': scale / (i + 1), 'reason': name}
                       for i in range(quota)]
                for (name, quota), scale in zip(candidates.QUOTAS.items(), [100000, 1, 1.5, 10000, 1000])}
        patches = [patch.object(candidates, name + '_channel', return_value=data) for name, data in rows.items()]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        first, _ = candidates.collect(self.store, {'id': 'seed'}, {}, limit=24)
        counts = {name: sum(name in r['channels'] for r in first) for name in rows}
        self.assertTrue(all(n >= 2 for n in counts.values()), counts)
        self.assertTrue(all(0 < r['score'] < 1 for r in first), 'scores must be RRF, not raw channel scores')
        for data in rows.values():
            data.reverse()
        second, _ = candidates.collect(self.store, {'id': 'seed'}, {}, limit=24)
        self.assertEqual(first, second)

    def test_tied_scores_and_duplicate_reasons_are_deterministic(self):
        rows = [{'paper_id': pid, 'channel': 'citation', 'score': 4, 'reason': reason}
                for pid, reason in [('z', 'one'), ('a', 'one'), ('a', 'two')]]
        engine = SimpleNamespace(search=lambda *a, **k: {'results': []})
        with patch.object(candidates, 'citation_channel', return_value=rows):
            result, _ = candidates.collect(self.store, {'id': 'seed'}, {'search': engine}, limit=1)
        self.assertEqual(result[0]['paper_id'], 'a')
        self.assertEqual(result[0]['reasons'], ['one', 'two'])

    def test_actual_bm25_call_chain_does_not_claim_vector_neighbours(self):
        from kbapp.search.indexing import Search, build_keyword_index
        self.papers('seed', *[f'p{i}' for i in range(10)])
        index = self.store.paths.indexes / 'versions' / 'graph-test'
        index.mkdir(parents=True)
        rows = [{'id': f'metadata:p{i}', 'paper_id': f'p{i}', 'kind': 'metadata',
                 'title': 'adaptive sampling', 'text': 'adaptive sampling method'} for i in range(10)]
        (index / 'corpus.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows), encoding='utf-8')
        (index / 'corpus_meta.json').write_text(json.dumps({'embedding': {}, 'sources': 10, 'vector_ids': []}), encoding='utf-8')
        build_keyword_index(index, rows)
        self.store.set_state('active_version', 'graph-test')
        with patch('kbapp.integrations.model_identity.embedding_signature', return_value={'revision': 'isolated'}):
            result, _ = candidates.collect(self.store, {'id': 'seed', 'title': 'adaptive sampling'},
                {'neighbors': {}, 'search': Search(self.store)})
        self.assertEqual(len(result), 8)
        self.assertTrue(all('BM25' in ' '.join(r['reasons']) for r in result))
        self.assertFalse(any('向量' in ' '.join(r['reasons']) for r in result))

    def test_legacy_search_adapter_remains_compatible_and_method_aliases_deduplicate(self):
        from test_candidates import FakeEngine
        self.papers('seed', 'a', 'b', 'c')
        with self.store.db() as db:
            for mid in ('m1', 'm2'):
                db.execute('INSERT INTO method_concepts VALUES(?,?,?,?,0,?)', (mid, mid, '["alias"]', '{}', 'now'))
                db.execute('INSERT INTO paper_methods VALUES(?,?,?)', ('seed', mid, '{}'))
        try:
            result, _ = candidates.collect(self.store, {'id': 'seed', 'title': 'query'},
                {'neighbors': {}, 'search': FakeEngine(['seed'] + ['a'] * 20 + ['b', 'c'])},
                quotas={'similar': 0, 'method': 3})
        except TypeError as exc:
            self.fail('legacy search protocol must still work: ' + str(exc))
        self.assertEqual({r['paper_id'] for r in result}, {'a', 'b', 'c'})
        self.assertTrue(all(r['channels'] == ['method'] for r in result))

    def test_co_citation_uses_bounded_sql_and_preserves_counts(self):
        self.papers('seed', 'co', 'coupled', *[f'c{i}' for i in range(8)], *[f'r{i}' for i in range(8)])
        for i in range(8):
            for src, dst in [(f'c{i}', 'seed'), (f'c{i}', 'co'), ('seed', f'r{i}'), ('coupled', f'r{i}')]:
                self.relation(src, dst)
        statements = []
        original = self.store.db
        @contextmanager
        def traced():
            with original() as db:
                db.set_trace_callback(statements.append)
                yield db
        with patch.object(self.store, 'db', traced):
            rows = candidates.co_citation_channel(self.store, {'id': 'seed'})
        by_id = {r['paper_id']: r for r in rows}
        self.assertAlmostEqual(by_id['co']['score'], 6.4)
        self.assertAlmostEqual(by_id['coupled']['score'], 4)
        self.assertLessEqual(sum(s.lstrip().upper().startswith(('SELECT', 'WITH')) for s in statements), 2)


class ChainReliability(StoreCase):
    def setUp(self):
        super().setUp()
        self.store.put_papers([
            {'id': 'a', 'title': 'Ancestor', 'kind': 'external', 'year': 2000},
            {'id': 'b', 'title': 'Anchor robot', 'kind': 'library', 'year': 2010, 'directions': ['02'], 'project': 'yes'},
            {'id': 'c', 'title': 'Continuation', 'kind': 'library', 'year': 2020},
            {'id': 'd', 'title': 'Disconnected', 'kind': 'external'},
            {'id': 'e', 'title': 'Separate component', 'kind': 'external'},
            {'id': 'f', 'title': 'Coverage', 'kind': 'library', 'directions': ['03']},
        ])
        with self.store.db() as db:
            for mid, name in [('m', 'Sampling'), ('n', 'Coverage')]:
                db.execute('INSERT INTO method_concepts VALUES(?,?,?,?,0,?)', (mid, name, json.dumps([name]), json.dumps({'family': name}), 'now'))
            for pid, mid in [('b', 'm'), ('c', 'm'), ('e', 'm'), ('f', 'n')]:
                db.execute('INSERT INTO paper_methods VALUES(?,?,?)', (pid, mid, '{}'))
        self.verified = self.store.relation({'src': 'b', 'dst': 'a', 'kind': 'inherits', 'status': 'confirmed',
            'level': 'fulltext', 'reviewed_by': 'fixture', 'evidence': [{'quote': 'supported'}]})
        self.pending = self.store.relation({'src': 'c', 'dst': 'b', 'kind': 'improves', 'status': 'confirmed',
            'level': 'fulltext', 'pipeline_legacy': True})
        self.separate = self.store.relation({'src': 'e', 'dst': 'd', 'kind': 'inherits', 'status': 'candidate'})
        self.store.relation({'src': 'f', 'dst': 'a', 'kind': 'transfers', 'status': 'candidate'})
        app = FastAPI()
        install(app, SimpleNamespace(store=self.store, search=None, paths=self.store.paths), '')
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def summary(self, **params):
        response = self.client.get('/api/chains/summary', params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_cold_summary_never_builds_detailed_chains_or_displays_edges(self):
        with patch.object(graph, 'chains', side_effect=AssertionError('cold summary constructed detailed chains')), \
             patch.object(graph, 'display_relation', side_effect=AssertionError('summary decoded detailed evidence')):
            result = self.summary()
        self.assertEqual(result['total'], 2)
        self.assertTrue(result.get('revision'))
        self.assertIsInstance(result['items'][0]['nodes'], int)
        self.assertTrue(result['items'][0].get('id'))

    def test_summary_filters_and_candidate_grade_are_consistent_with_detail(self):
        for filters in [{'direction': '02'}, {'project': 'true'}, {'q': 'Anchor'}, {'method': 'm', 'q': 'Anchor'}]:
            with self.subTest(filters=filters):
                result = self.summary(candidates='false', **filters)
                self.assertEqual(result['total'], 1)
                item = result['items'][0]
                self.assertEqual((item['nodes'], item['edges'], item['confirmed'], item['pending']), (2, 1, 1, 0))
                detail = self.client.get('/api/chains/' + item['id'], params=dict(filters, candidates='false')).json()
                self.assertEqual({r['id'] for r in detail['edges']}, {self.verified})
                self.assertEqual({r['id'] for r in detail['nodes']}, {'a', 'b'})
        result = self.summary(q='Anchor', candidates='true')['items'][0]
        self.assertEqual((result['nodes'], result['edges'], result['confirmed'], result['pending']), (3, 2, 1, 1))
        self.assertEqual(self.summary(q='no matching paper')['items'], [])
        self.assertEqual(self.summary(method='missing')['items'], [])

    def test_branch_ids_stable_and_edge_pagination_has_no_loss(self):
        result = self.summary(sort='name', limit=1)
        self.assertTrue(result['has_more'])
        all_items = self.summary(sort='name')['items']
        item = next(i for i in all_items if i['name'] == '基于采样')
        self.assertTrue(item.get('id'), 'summary must expose a stable branch ID')
        pages = [self.client.get('/api/chains/' + item['id'], params={'offset': i, 'limit': 1}).json() for i in range(3)]
        self.assertEqual({e['id'] for page in pages for e in page['edges']}, {self.verified, self.pending, self.separate})
        self.assertEqual(sum(len(page['edges']) for page in pages), 3)
        for page in pages:
            self.assertEqual(page['total'], 3)
            ids = {n['id'] for n in page['nodes']}
            self.assertTrue(all(e['src'] in ids and e['dst'] in ids for e in page['edges']))
        self.assertFalse(pages[-1]['has_more'])
        self.assertEqual(self.client.get('/api/chains/not-a-branch').status_code, 404)
        self.assertEqual(self.client.get('/api/chains/summary', params={'sort': 'invalid'}).status_code, 400)
        old = self.client.get('/api/chains').json()['items']
        self.assertEqual(next(b['id'] for b in old if b['name'] == '基于采样'), item['id'])
        self.assertIsInstance(old[0]['nodes'], list)

    def test_network_visibility_uses_same_evidence_grade_as_chain_summary(self):
        response = self.client.get('/api/graph', params={'candidates': 'false'})
        self.assertEqual({e['id'] for e in response.json()['edges']}, {self.verified})
        response = self.client.post('/api/graph/edges', json={'ids': ['a', 'b', 'c'], 'candidates': False})
        self.assertEqual({e['id'] for e in response.json()['edges']}, {self.verified})
        response = self.client.get('/api/graph', params={'candidates': 'true'})
        self.assertIn(self.pending, {e['id'] for e in response.json()['edges']})

    def test_summary_cache_revision_reuse_and_stale_detail_conflict(self):
        first = self.summary()
        self.assertTrue(first.get('revision'), 'pagination must identify its revision')
        with patch.object(graph, 'enriched_papers', side_effect=AssertionError('warm summary rescanned papers')):
            self.assertEqual(self.summary(), first)
            self.summary(q='Anchor')
        item = next(i for i in first['items'] if i['name'] == '基于采样')
        self.store.relation({'src': 'c', 'dst': 'a', 'kind': 'combines', 'status': 'candidate'})
        updated = self.summary()
        self.assertNotEqual(first['revision'], updated['revision'])
        self.assertIn(item['id'], [i['id'] for i in updated['items']])
        response = self.client.get('/api/chains/' + item['id'], params={'revision': first['revision']})
        self.assertEqual(response.status_code, 409)
        with self.store.db() as db:
            db.execute("UPDATE paper_methods SET method_id='n' WHERE paper_id='c'")
        self.assertNotEqual(updated['revision'], self.summary()['revision'])


if __name__ == '__main__':
    unittest.main()
