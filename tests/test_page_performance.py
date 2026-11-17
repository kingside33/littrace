"""性能契约：复用分类、缓存失效、轻量状态及卡片与完整证据分离。"""
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kbapp.storage.store import Store
from kbapp.research import graph, method_taxonomy
from kbapp.workflows.status import status
from kbapp.web.graph_api import install


class PagePerformance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.store.put_papers([
            {'id': 'a', 'title': 'Original RRT sampling paper', 'directions': ['02'], 'kind': 'library', 'abstract': 'large abstract ' * 1000},
            {'id': 'b', 'title': 'Later sampling paper', 'directions': ['02'], 'kind': 'external', 'abstract': 'large abstract ' * 1000},
            {'id': 'c', 'title': 'Sonar image paper', 'directions': ['08'], 'kind': 'library'},
        ])
        self.rid = self.store.relation({'src': 'b', 'dst': 'a', 'kind': 'improves', 'status': 'confirmed',
            'level': 'fulltext', 'reviewed_by': 'fixture', 'evidence': [{'quote': 'original evidence', 'page': 3}]})

    def test_switching_direction_reuses_base_classification_and_topology(self):
        with patch.object(method_taxonomy, 'matched', wraps=method_taxonomy.matched) as identify:
            first = graph.chains_page(self.store, direction='02')
            count = identify.call_count
            graph.chains_page(self.store, direction='08')
            again = graph.chains_page(self.store, direction='02')
            self.assertEqual(identify.call_count, count)
            self.assertEqual(first, again)
            self.assertIs(graph._chain_index(self.store, '02'), graph._chain_index(self.store, '02'))

    def test_restart_reuses_persisted_classification_and_only_changed_title_recomputes(self):
        graph.graph_papers(self.store)
        restarted = Store(self.temp.name)
        with patch.object(method_taxonomy, 'matched', wraps=method_taxonomy.matched) as identify:
            graph.graph_papers(restarted)
            self.assertEqual(identify.call_count, 0)
            self.store.patch_paper('a', {'title': 'Changed RRT title'})
            graph.graph_papers(restarted)
            self.assertEqual([c.args[0] for c in identify.call_args_list], ['Changed RRT title'])

    def test_manual_direction_and_method_alias_changes_invalidate_catalog(self):
        graph.graph_papers(self.store)
        with self.store.db() as db:
            db.execute('INSERT INTO paper_classifications VALUES(?,?,1,?)', ('b', json.dumps({'directions': ['08'], 'automatic': False}), 'now'))
            db.execute('INSERT INTO method_concepts VALUES(?,?,?,?,0,?)', ('m', 'RRT', '[]', '{}', 'now'))
            db.execute('INSERT INTO paper_methods VALUES(?,?,?)', ('c', 'm', '{}'))
        changed = graph.graph_papers(self.store)
        self.assertEqual(changed['b']['directions'], ['08'])
        self.assertIn('family:sampling', changed['c']['method_families'])
        with self.store.db() as db:
            db.execute("UPDATE method_concepts SET name='Particle swarm',aliases='[]' WHERE id='m'")
        changed = graph.graph_papers(self.store)
        self.assertNotIn('family:sampling', changed['c']['method_families'])
        self.assertIn('family:metaheuristic', changed['c']['method_families'])

    def test_relation_edit_invalidates_all_direction_views_without_reclassifying(self):
        before = graph.chains_page(self.store, direction='02')
        graph.chains_page(self.store, direction='08')
        with patch.object(method_taxonomy, 'matched', side_effect=AssertionError('relation changes must not reclassify papers')):
            self.store.relation({'src': 'c', 'dst': 'a', 'kind': 'cites', 'status': 'confirmed', 'level': 'citation'})
            after = graph.chains_page(self.store, direction='02')
        self.assertNotEqual(before['revision'], after['revision'])
        self.assertEqual(graph.chains_page(self.store)['coverage']['edges'], 2)

    def test_citation_count_metadata_refresh_keeps_topology_but_updates_cards(self):
        graph.chains_page(self.store)
        topology = self.store._graph_topology[1]
        self.store.patch_paper('a', {'citation_count': 99})
        result = graph.chains_page(self.store)
        self.assertIs(self.store._graph_topology[1], topology)
        detail = graph.chain_detail(self.store, result['items'][0]['id'])
        self.assertEqual(next(p['citation_count'] for p in detail['nodes'] if p['id'] == 'a'), 99)

    def test_light_status_never_scans_full_papers_or_source_corpus(self):
        with patch.object(self.store, 'papers', side_effect=AssertionError('full paper scan')), \
             patch('kbapp.search.index_status.index_health', side_effect=AssertionError('full source scan')):
            result = status(self.store, detail=False)
        self.assertEqual((result['papers'], result['external']), (2, 1))
        self.assertFalse(result['status_detail'])
        self.assertNotIn('index_health', result, 'unverified health must not be invented')
        self.assertTrue(result['data_revision'])

    def test_compact_cards_preserve_ids_and_grades_and_detail_returns_original_evidence(self):
        app = FastAPI()
        install(app, SimpleNamespace(store=self.store, search=None, paths=self.store.paths), '')
        with TestClient(app) as client:
            full = client.get('/api/chains').json()
            compact = client.get('/api/chains', params={'compact': 'true'}).json()
            self.assertEqual([e['id'] for b in full['items'] for e in b['edges']], [e['id'] for b in compact['items'] for e in b['edges']])
            card = compact['items'][0]
            self.assertNotIn('abstract', card['nodes'][0])
            self.assertNotIn('evidence', card['edges'][0])
            self.assertEqual(card['edges'][0]['evidence_grade'], 'fulltext')
            self.assertEqual(card['edges'][0]['evidence_pages'], [3])
            detail = client.get('/api/relation', params={'rid': self.rid}).json()
            self.assertEqual(detail['evidence'][0]['quote'], 'original evidence')
            timeline = client.get('/api/timeline', params={'compact': 'true'}).json()
            self.assertEqual(timeline['method_families'][0]['edges'][0]['id'], self.rid)
            self.assertNotIn('abstract', timeline['method_families'][0]['nodes'][0])


if __name__ == '__main__':
    unittest.main()
