"""trace 子命令的 CLI 层离线验收：经 kbapp.workflows.cli.main 全链路驱动（临时数据目录 + 假 OpenAlex）。

覆盖：启动→查看路径→暂停→继续→完成的生命周期；「3 层启动 A→B→C→D→E：
结果含 D，E 不被请求」的端到端用例；paths 的 --paper 过滤与 --limit 截断；
status 省略 --id 的最近任务列表；错误收敛为一句中文 JSON + 退出码 1。
层数只来自设置页（network_depth / network_depth_unlimited），CLI 不传层数。
"""
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['KB_NO_STARTUP_JOBS'] = '1'
os.environ['KB_NO_REFERENCE_WORKER'] = '1'

from kbapp.core.io import atomic  # noqa: E402
from kbapp.integrations.openalex import OpenAlex  # noqa: E402
from kbapp.storage.store import Store  # noqa: E402
from kbapp.workflows.cli import main  # noqa: E402
from kbapp.workflows.trace import pause_task  # noqa: E402

KEY = 'synthetic-openalex-key'


def identity_protect(value, decrypt=False):
    """DPAPI 替身（同 test_trace.py），含密钥的用例在非 Windows 也能跑。"""
    return value


def work(wid, title='A paper', doi=None, refs=(), omit_refs=False):
    raw = {'id': 'https://openalex.org/' + wid, 'title': title,
           'doi': 'https://doi.org/' + doi if doi else None, 'publication_year': 2020, 'cited_by_count': 12}
    if not omit_refs:
        raw['referenced_works'] = ['https://openalex.org/' + x for x in refs]
    return raw


class Fake:
    """假 OpenAlex（同 test_trace.py 的 Fake）：按 path 分发并记录请求序列；hooks 在响应前触发。"""

    def __init__(self, works):
        self.works = works
        self.requests = []
        self.hooks = []

    def get(self, path, params=None):
        self.requests.append((path, params))
        for predicate, action in self.hooks:
            if predicate(path, params):
                action()
        if path == 'works':
            requested = params['filter'].removeprefix('openalex_id:').split('|')
            return {'results': [dict(self.works[w]) for w in requested if w in self.works]}
        tail = path.removeprefix('works/')
        if tail.startswith('https://doi.org/'):
            doi = tail.removeprefix('https://doi.org/').rstrip('/')
            for raw in self.works.values():
                if (raw.get('doi') or '').removeprefix('https://doi.org/') == doi:
                    return dict(raw)
        if tail in self.works:
            return dict(self.works[tail])
        raise ValueError('unexpected request: ' + path + ' ' + json.dumps(params or {}, sort_keys=True))


def filters_of(fake):
    return [params['filter'] for _, params in fake.requests if params]


class TraceCliBase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.store = Store(self.base)
        patcher = patch('kbapp.storage.store.protect', identity_protect)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.store.save_settings({'openalex_key': KEY})

    def add_library(self, **paper):
        record = {'id': paper.pop('pid'), 'kind': 'library', 'title': paper.pop('title', 'A paper'),
                  'year': 2020, 'citekey': paper.get('openalex_id', paper.get('doi', 'citekey'))}
        record.update(paper)
        self.store.put_papers([record])
        return record

    def cli(self, *argv):
        """经 cli.main 全链路执行一条命令，返回 (退出码, 解析后的 JSON 输出, 原始文本)。"""
        saved = {key: os.environ.get(key) for key in ('KB_BASE', 'KB_DATA_DIR')}
        buffer = io.StringIO()
        with redirect_stdout(buffer), patch('kbapp.storage.store.protect', identity_protect):
            try:
                code = main(['--base', str(self.base), *argv])
            finally:
                for key, value in saved.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
        text = buffer.getvalue()
        return code, (json.loads(text) if text.strip() else None), text


class TraceCliLifecycle(TraceCliBase):
    """A→B→C→D→E 五篇链；CLI 启动、状态、路径、暂停、续跑、完成全链路。"""

    def setUp(self):
        super().setUp()
        self.works = {
            'WA': work('WA', 'Seed A', doi='10.1234/a', refs=['WB']),
            'WB': work('WB', 'Paper B', refs=['WC']),
            'WC': work('WC', 'Paper C', refs=['WD']),
            'WD': work('WD', 'Paper D', refs=['WE']),
            'WE': work('WE', 'Paper E', refs=[]),
        }
        self.add_library(pid='p1', title='Seed A', doi='10.1234/a', openalex_id='WA')

    def test_three_layers_via_cli_contains_d_and_never_requests_e(self):
        """3 层启动：结果含 D（深度 3、depth_limit），E 不被发现也不被请求。"""
        self.store.save_settings({'openalex_key': KEY, 'network_depth': 3})
        fake = Fake(self.works)
        with patch.object(OpenAlex, 'get', side_effect=fake.get):
            code, snapshot, _ = self.cli('trace', 'run', '--paper', 'WA')
        self.assertEqual(code, 0)
        self.assertEqual(snapshot['status'], 'complete')
        # 状态输出展示本任务采用的层数。
        self.assertEqual(snapshot['depth_display'], '3 层')
        self.assertEqual(snapshot['depth'], 3)
        depths = {n['wid']: n['min_depth'] for n in snapshot['nodes']}
        self.assertEqual(depths, {'WA': 0, 'WB': 1, 'WC': 2, 'WD': 3})
        wd = next(n for n in snapshot['nodes'] if n['wid'] == 'WD')
        self.assertEqual(wd['stop_reason'], 'depth_limit')
        self.assertEqual(wd['paths'], [['WA', 'WB', 'WC', 'WD']])
        self.assertNotIn('WE', depths)
        # E 从未被请求：请求序列恰为种子单篇 + 每层一次批量 filter。
        self.assertEqual([path for path, _ in fake.requests], ['works/WA', 'works', 'works', 'works'])
        self.assertEqual(filters_of(fake), ['openalex_id:WB', 'openalex_id:WC', 'openalex_id:WD'])
        self.assertNotIn('WE', json.dumps(fake.requests))

    def test_run_status_paths_pause_resume_complete(self):
        self.store.save_settings({'openalex_key': KEY, 'network_depth': 3})
        fake = Fake(self.works)

        def pause_latest():
            with self.store.db() as db:
                row = db.execute('SELECT id FROM trace_tasks ORDER BY created DESC LIMIT 1').fetchone()
            pause_task(self.store, row[0])

        # WC 批量响应的瞬间注入暂停：run 以 paused 收轮，WC 分支留作断点。
        fake.hooks.append((lambda path, params: path == 'works' and 'WC' in params['filter'], pause_latest))
        with patch.object(OpenAlex, 'get', side_effect=fake.get):
            code, snapshot, _ = self.cli('trace', 'run', '--paper', 'WA')
        self.assertEqual((code, snapshot['status']), (0, 'paused'))
        self.assertIn('暂停', snapshot['hint'])
        self.assertEqual([q['wid'] for q in snapshot['queue']], ['WC'])
        tid = snapshot['id']
        # 查看状态：含本任务采用的层数与进度。
        code, status, _ = self.cli('trace', 'status', '--id', tid)
        self.assertEqual((code, status['status'], status['depth_display']), (0, 'paused', '3 层'))
        # 查看路径：已发现的 B、C 各自连回种子的路径可见。
        code, paths, _ = self.cli('trace', 'paths', '--id', tid)
        self.assertEqual(code, 0)
        by_wid = {p['wid']: p for p in paths['papers']}
        self.assertEqual(by_wid['WB']['paths'], [['WA', 'WB']])
        self.assertEqual(by_wid['WC']['paths'], [['WA', 'WB', 'WC']])
        # 暂停（幂等）→ 继续 → 完成：断点续推，D 到第 3 层定格，E 零请求。
        code, paused_again, _ = self.cli('trace', 'pause', '--id', tid)
        self.assertEqual((code, paused_again['status']), (0, 'paused'))
        with patch.object(OpenAlex, 'get', side_effect=fake.get):
            code, resumed, _ = self.cli('trace', 'resume', '--id', tid)
        self.assertEqual((code, resumed['status']), (0, 'complete'))
        self.assertEqual(resumed['depth_display'], '3 层')
        self.assertEqual({n['wid']: n['min_depth'] for n in resumed['nodes']},
                         {'WA': 0, 'WB': 1, 'WC': 2, 'WD': 3})
        self.assertNotIn('WE', json.dumps(fake.requests))
        code, last, _ = self.cli('trace', 'paths', '--id', tid, '--paper', 'WD')
        self.assertEqual(last['papers'][0]['paths'], [['WA', 'WB', 'WC', 'WD']])
        self.assertEqual(last['papers'][0]['stop_reason'], 'depth_limit')


class TraceCliPaths(TraceCliBase):
    """paths 的 --paper 过滤（WID 与库内 ID 两种形态）与 --limit 截断；status 列表。"""

    def setUp(self):
        super().setUp()
        self.works = {
            'WA': work('WA', 'Seed', doi='10.1234/a', refs=['WB', 'WC']),
            'WB': work('WB', 'Via B', refs=['WD', 'WB']),
            'WC': work('WC', 'Via C', refs=['WD', 'WA']),
            'WD': work('WD', 'Diamond', refs=[]),
            'WQ': work('WQ', 'Seed two', refs=['WM']),
            'WM': work('WM', 'Ancestor two', refs=[]),
        }
        self.add_library(pid='p1', title='Seed', doi='10.1234/a', openalex_id='WA')
        self.add_library(pid='p2', title='Seed two', openalex_id='WQ')
        self.store.save_settings({'openalex_key': KEY, 'network_depth': 2})
        fake = Fake(self.works)
        with patch.object(OpenAlex, 'get', side_effect=fake.get):
            code, one, _ = self.cli('trace', 'run', '--paper', 'p1')
            code, two, _ = self.cli('trace', 'run', '--paper', 'p2')
        self.assertEqual((one['status'], two['status']), ('complete', 'complete'))
        self.task_id = one['id']

    def test_paths_filter_by_wid_and_by_library_id(self):
        code, view, _ = self.cli('trace', 'paths', '--id', self.task_id)
        self.assertEqual(code, 0)
        diamond = {p['wid']: p for p in view['papers']}['WD']
        self.assertEqual(diamond['paths'], [['WA', 'WB', 'WD'], ['WA', 'WC', 'WD']])
        # OpenAlex WID 形态过滤。
        code, single, _ = self.cli('trace', 'paths', '--id', self.task_id, '--paper', 'WD')
        self.assertEqual((code, len(single['papers'])), (0, 1))
        self.assertEqual(single['papers'][0]['paths'], [['WA', 'WB', 'WD'], ['WA', 'WC', 'WD']])
        # 库内文献 ID 形态过滤（种子本身）。
        code, seed_view, _ = self.cli('trace', 'paths', '--id', self.task_id, '--paper', 'p1')
        self.assertEqual((code, seed_view['papers'][0]['wid']), (0, 'WA'))
        self.assertEqual(seed_view['papers'][0]['paths'], [['WA']])

    def test_paths_limit_truncates_with_total(self):
        code, limited, _ = self.cli('trace', 'paths', '--id', self.task_id, '--paper', 'WD', '--limit', '1')
        self.assertEqual(limited['papers'][0]['paths'], [['WA', 'WB', 'WD']])
        self.assertEqual(limited['papers'][0]['paths_total'], 2)

    def test_status_without_id_lists_recent_tasks(self):
        code, listing, _ = self.cli('trace', 'status')
        self.assertEqual(code, 0)
        self.assertEqual(len(listing['tasks']), 2)
        for task in listing['tasks']:
            self.assertIn(task['depth_display'], ('2 层',))
            self.assertIn(task['status'], ('complete',))
            self.assertIn('seed', task)
        code, single, _ = self.cli('trace', 'status', '--id', self.task_id)
        self.assertEqual((code, single['status'], single['counts']['nodes']), (0, 'complete', 4))


class TraceCliListing(TraceCliBase):
    """status 无 --id 的轻量列表：输出与全量快照投影一致，且不触发全量 papers/DFS。"""

    def setUp(self):
        super().setUp()
        self.works = {
            'WA': work('WA', 'Seed', doi='10.1234/a', refs=['WB', 'WC']),
            'WB': work('WB', 'Via B', refs=['WD']),
            'WC': work('WC', 'Via C', refs=['WD']),
            'WD': work('WD', 'Diamond', refs=[]),
        }
        self.add_library(pid='p1', title='Seed', doi='10.1234/a', openalex_id='WA')
        self.store.save_settings({'openalex_key': KEY, 'network_depth': 2})
        fake = Fake(self.works)
        with patch.object(OpenAlex, 'get', side_effect=fake.get):
            code, snapshot, _ = self.cli('trace', 'run', '--paper', 'p1')
        self.assertEqual(snapshot['status'], 'complete')
        self.task_id = snapshot['id']

    def test_list_projection_matches_full_snapshot(self):
        keys = ('id', 'status', 'depth_display', 'seed', 'requests', 'error', 'hint', 'created', 'updated', 'counts')
        code, listing, _ = self.cli('trace', 'status')
        self.assertEqual(code, 0)
        self.assertEqual(len(listing['tasks']), 1)
        code, snapshot, _ = self.cli('trace', 'status', '--id', self.task_id)
        self.assertEqual(listing['tasks'][0], {key: snapshot[key] for key in keys})

    def test_list_path_skips_full_papers_and_dfs(self):
        # 大库 × 多任务下列表必须保持轻查询：不全量加载 papers()、不建邻接表、
        # 不逐节点 DFS 找路径（这些只属于 --id 单任务全量快照与 paths）。
        from kbapp.workflows import trace as trace_module

        def forbidden(name):
            raise AssertionError(f'列表路径不应触发 {name}')

        with patch.object(Store, 'papers', side_effect=lambda: forbidden('papers()')), \
                patch.object(trace_module, '_adjacency', side_effect=lambda *a: forbidden('_adjacency')), \
                patch.object(trace_module, '_paths', side_effect=lambda *a: forbidden('_paths')):
            code, listing, _ = self.cli('trace', 'status')
        self.assertEqual(code, 0)
        entry = listing['tasks'][0]
        self.assertEqual((entry['id'], entry['status'], entry['depth_display']),
                         (self.task_id, 'complete', '2 层'))
        self.assertEqual(entry['counts'],
                         {'nodes': 4, 'stopped': 1, 'edges': 4, 'pending_branches': 0})


class TraceCliErrors(TraceCliBase):
    """错误收敛为一句中文 JSON + 退出码 1，不产生半成品任务。"""

    def setUp(self):
        super().setUp()
        self.works = {'WA': work('WA', 'Seed', doi='10.1234/a', refs=[])}
        self.add_library(pid='p1', title='Seed', doi='10.1234/a', openalex_id='WA')

    def rows(self, table):
        with self.store.db() as db:
            try:
                return [dict(r) for r in db.execute(f'SELECT * FROM {table}')]
            except sqlite3.OperationalError:  # 表尚未建立 = 没有任何任务落盘
                return []

    def assert_single_error(self, text):
        payload = json.loads(text)
        self.assertIn('error', payload)
        self.assertNotIn('Traceback', text)
        return payload

    def test_run_without_paper(self):
        code, _, text = self.cli('trace', 'run')
        self.assertEqual(code, 1)
        payload = self.assert_single_error(text)
        self.assertIn('--paper', payload['error'])
        self.assertEqual(self.rows('trace_tasks'), [])

    def test_run_with_unknown_paper(self):
        code, _, text = self.cli('trace', 'run', '--paper', 'nope')
        self.assertEqual(code, 1)
        self.assertEqual(self.assert_single_error(text)['error'], '文献不存在')
        self.assertEqual(self.rows('trace_tasks'), [])

    def test_run_with_invalid_settings_depth(self):
        atomic(self.store.paths.state / 'settings.json', {'network_depth': 2.8})
        code, _, text = self.cli('trace', 'run', '--paper', 'p1')
        self.assertEqual(code, 1)
        self.assertIn('层数应为整数', self.assert_single_error(text)['error'])
        self.assertEqual(self.rows('trace_tasks'), [])

    def test_status_pause_resume_need_id(self):
        code, _, text = self.cli('trace', 'status', '--id', 'trace:missing')
        self.assertEqual(code, 1)
        self.assertEqual(self.assert_single_error(text)['error'], '追溯任务不存在')
        for action in ('pause', 'resume'):
            code, _, text = self.cli('trace', action)
            self.assertEqual(code, 1)
            self.assertIn('--id', self.assert_single_error(text)['error'])

    def test_paths_with_paper_outside_task(self):
        with patch.object(OpenAlex, 'get', side_effect=Fake(self.works).get):
            code, snapshot, _ = self.cli('trace', 'run', '--paper', 'p1')
        self.assertEqual(code, 0)
        code, _, text = self.cli('trace', 'paths', '--id', snapshot['id'], '--paper', 'WZ')
        self.assertEqual(code, 1)
        self.assertIn('不在本追溯任务', self.assert_single_error(text)['error'])


if __name__ == '__main__':
    unittest.main()
