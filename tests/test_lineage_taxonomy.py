"""谱系大类归并、全库覆盖与方向联动的真实存储/HTTP回归。"""
import tempfile
import unittest
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kbapp.storage.store import Store
from kbapp.research import graph, method_taxonomy as taxonomy
from kbapp.web.graph_api import install


class TaxonomyRegression(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        app = FastAPI()
        install(app, SimpleNamespace(store=self.store, search=None, paths=self.store.paths), '')
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def papers(self, rows):
        self.store.put_papers([dict(kind='library', **p) for p in rows])

    def test_chinese_english_variants_and_components_share_coarse_family(self):
        self.papers([{'id': str(i), 'title': 'path planning', 'directions': ['02']} for i in range(3)])
        with self.store.db() as db:
            for i, name in enumerate(['Rapidly-Exploring Random Trees', '快速扩展随机树', '改进RRT*FN']):
                db.execute('INSERT INTO method_concepts VALUES(?,?,?,?,0,?)', (str(i), name, '[]', '{}', 'now'))
                db.execute('INSERT INTO paper_methods VALUES(?,?,?)', (str(i), str(i), '{}'))
        for src in ('1', '2'):
            self.store.relation(dict(src=src, dst='0', kind='improves', status='candidate'))
        result = graph.chains_page(self.store)
        self.assertEqual([b['name'] for b in result['items']], ['基于采样'])
        self.assertEqual(result['coverage']['edges'], 2)
        methods = self.client.get('/api/methods', params={'direction': '02'}).json()['items']
        self.assertEqual([m['id'] for m in methods], ['family:sampling'])
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM method_concepts').fetchone()[0], 3)

    def test_options_follow_direction_project_and_query_without_foreign_algorithms(self):
        self.papers([
            {'id': 'rrt', 'title': 'RRT path planning', 'directions': ['02'], 'project': 'p'},
            {'id': 'astar', 'title': '改进A*算法', 'directions': ['02']},
            {'id': 'sonar', 'title': 'Sonar perception and SLAM', 'directions': ['08']},
            {'id': 'cnn', 'title': 'Deep learning image recognition', 'directions': ['08']},
        ])
        def options(**params):
            return {m['id'] for m in self.client.get('/api/methods', params=params).json()['items']}
        self.assertEqual(options(direction='02'), {'family:sampling', 'family:graph-search'})
        self.assertEqual(options(direction='02', project='true'), {'family:sampling'})
        self.assertEqual(options(direction='02', q='改进'), {'family:graph-search'})
        self.assertEqual(options(direction='08'), {'family:perception', 'family:learning'})
        branches = graph.chains_page(self.store, direction='02')['items']
        self.assertEqual({b['family_id'] for b in branches}, {'family:sampling', 'family:graph-search'})

    def test_all_relation_types_and_disconnected_papers_are_counted_once(self):
        self.papers([{'id': str(i), 'title': 'RRT path planning', 'directions': ['02']} for i in range(244)])
        expected = set()
        for i in range(1, 204):
            expected.add(self.store.relation(dict(src=str(i), dst='0', kind='cites', status='confirmed', level='citation')))
        for i, kind in enumerate(['improves', 'compares', 'similar'], 1):
            expected.add(self.store.relation(dict(src=str(i), dst='0', kind=kind, status='candidate')))
        result = graph.chains_page(self.store)
        coverage = result['coverage']
        self.assertEqual((coverage['papers'], coverage['connected'], coverage['unlinked']), (244, 204, 40))
        self.assertEqual((coverage['edges'], coverage['citations'], coverage['method_edges'], coverage['other_edges']), (206, 203, 1, 2))
        branch = result['items'][0]
        pages = [graph.chain_detail(self.store, branch['id'], offset=o, limit=100, paper_offset=p, paper_limit=17)
                 for o, p in [(0, 0), (100, 17), (200, 34)]]
        ids = [e['id'] for page in pages for e in page['edges']]
        self.assertEqual(set(ids), expected)
        self.assertEqual(len(ids), len(expected))
        self.assertEqual(len({p['id'] for page in pages for p in page['unlinked_nodes']}), 40)
        self.assertFalse(pages[-1]['paper_has_more'])
        for page in pages:
            nodes = {n['id'] for n in page['nodes']}
            self.assertTrue(all(e['src'] in nodes and e['dst'] in nodes for e in page['edges']))
        verified = graph.chains_page(self.store, candidates=False)['coverage']
        self.assertEqual(verified['citations'], 203)
        self.assertEqual(verified['method_edges'], 0)
        self.assertEqual(verified['confirmed'], 0, '引用事实不能计为已核方法关系')

    def test_family_filter_keeps_one_branch_and_external_reference_endpoints(self):
        self.papers([{'id': 'a', 'title': 'RRT path planning', 'directions': ['02']},
                     {'id': 'b', 'title': 'A* path planning', 'directions': ['02']}])
        self.store.put_papers([{'id': 'external', 'kind': 'external', 'title': 'Mathematical foundations'}])
        rid = self.store.relation(dict(src='a', dst='external', kind='cites', status='confirmed', level='citation'))
        result = graph.chains_page(self.store, direction='02', method='family:sampling')
        self.assertEqual(len(result['items']), 1)
        detail = graph.chain_detail(self.store, result['items'][0]['id'], direction='02', method='family:sampling')
        self.assertEqual({p['id'] for p in detail['nodes']}, {'a', 'external'})
        self.assertEqual([r['id'] for r in detail['edges']], [rid])

    def test_title_direction_fallback_preserves_manual_classification(self):
        papers = {'auto': {'title': 'RRT path planning'}, 'manual': {'title': 'RRT path planning',
                  'directions': [], 'classification': {'automatic': False}}}
        taxonomy.annotate(papers, [])
        self.assertIn('02', papers['auto']['directions'])
        self.assertEqual(papers['auto']['direction_basis'], 'title_keywords')
        self.assertEqual(papers['manual']['directions'], [])


if __name__ == '__main__':
    unittest.main()
