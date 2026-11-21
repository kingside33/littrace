"""上游追溯引擎的离线验收：临时数据目录 + 合成论文 + 假 OpenAlex，不依赖网络与模型。

对齐验收清单：深度按引用边条数计（1/2/3/5/不限）、深度 N 节点不触发第 N+1 层
请求、不限层数跨轮与重启续跑、任务快照层数、菱形/环/自引、批量部分返回游标
不跳过、缺字段/空列表/429 的停止原因、旧两层 frontier 隔离、普通 update 兼容、
不写 relations/reference_jobs/approvals、共享元数据缓存复用。
"""
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['KB_NO_STARTUP_JOBS'] = '1'
os.environ['KB_NO_REFERENCE_WORKER'] = '1'

from kbapp.core.io import atomic  # noqa: E402
from kbapp.integrations.openalex import OpenAlex  # noqa: E402
from kbapp.storage.store import Store  # noqa: E402
from kbapp.workflows import trace  # noqa: E402
from kbapp.workflows.trace import classify, parse_depth, source_paths  # noqa: E402
from kbapp.workflows.trace import pause_task, resume_task, run_task, start_task, task_snapshot  # noqa: E402

KEY = 'synthetic-openalex-key'


def identity_protect(value, decrypt=False):
    """DPAPI 替身（同 test_settings_depth.py），含密钥的用例在非 Windows 也能跑。"""
    return value


def work(wid, title='A paper', doi=None, refs=(), count=12, omit_refs=False):
    raw = {'id': 'https://openalex.org/' + wid, 'title': title,
           'doi': 'https://doi.org/' + doi if doi else None, 'publication_year': 2020, 'cited_by_count': count}
    if not omit_refs:
        raw['referenced_works'] = ['https://openalex.org/' + x for x in refs]
    return raw


class Fake:
    """假 OpenAlex：按 path 分发；记录 (path, params) 序列供「实际请求了哪些 ID」断言。

    batch_drop(call_index, requested) 返回本批要吞掉的 wid 集合，模拟批量部分返回；
    hooks 在响应前触发（例如注入暂停），响应仍正常返回，模拟「在途请求自然完成」。
    """

    def __init__(self, works, batch_drop=None):
        self.works = works
        self.requests = []
        self.filter_calls = 0
        self.batch_drop = batch_drop or (lambda index, requested: set())
        self.hooks = []

    def fetch(self, path, params=None):
        self.requests.append((path, params))
        for predicate, action in self.hooks:
            if predicate(path, params):
                action()
        if path == 'works':
            requested = params['filter'].removeprefix('openalex_id:').split('|')
            self.filter_calls += 1
            drop = self.batch_drop(self.filter_calls, requested)
            return {'results': [dict(self.works[w]) for w in requested if w in self.works and w not in drop]}
        if path.startswith('works/'):
            tail = path[len('works/'):]
            if tail.startswith('https://doi.org/'):
                doi = tail.removeprefix('https://doi.org/').rstrip('/')
                for raw in self.works.values():
                    if (raw.get('doi') or '').removeprefix('https://doi.org/') == doi:
                        return dict(raw)
            elif tail in self.works:
                return dict(self.works[tail])
        raise ValueError('unexpected request: ' + path + ' ' + json.dumps(params or {}, sort_keys=True))


def paths_of(fake):
    return [path for path, _ in fake.requests]


def filters_of(fake):
    return [params['filter'] for _, params in fake.requests if params]


class UrlFake:
    """urlopen 层假件：真实 OpenAlex.get 的预算闸、当日额度闸与 429 折叠全部生效。

    patch.object(OpenAlex, 'get') 会连预算闸一起替换掉，凡是要验证「每轮预算」
    的用例必须从 urlopen 注入。fail(index) 返回 HTTP 状态码时抛对应 HTTPError。
    """

    def __init__(self, works, batch_drop=None, fail=None):
        self.works = works
        self.requests = []
        self.filter_calls = 0
        self.batch_drop = batch_drop or (lambda index, requested: set())
        self.fail = fail or (lambda index, path: None)

    def __call__(self, request, timeout):
        from urllib.error import HTTPError
        from urllib.parse import parse_qs, urlparse
        url = request.full_url
        parsed = urlparse(url)
        path = parsed.path.removeprefix('/')
        params = {k: (int(v[0]) if v[0].isdigit() else v[0]) for k, v in parse_qs(parsed.query).items()} \
            if parsed.query else None
        self.requests.append((path, params))
        code = self.fail(len(self.requests), path)
        if code:
            raise HTTPError(url, code, 'err', None, None)
        if path == 'works':
            requested = params['filter'].removeprefix('openalex_id:').split('|')
            self.filter_calls += 1
            drop = self.batch_drop(self.filter_calls, requested)
            payload = {'results': [dict(self.works[w]) for w in requested if w in self.works and w not in drop]}
        else:
            tail = path.removeprefix('works/')
            if tail.startswith('https://doi.org/'):
                doi = tail.removeprefix('https://doi.org/').rstrip('/')
                payload = next((dict(r) for r in self.works.values()
                                if (r.get('doi') or '').removeprefix('https://doi.org/') == doi), None)
            elif tail in self.works:
                payload = dict(self.works[tail])
            else:
                payload = None
        response = io.BytesIO(json.dumps(payload).encode('utf-8'))
        response.headers = {}
        return response


class TraceBase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name))
        patcher = patch('kbapp.storage.store.protect', identity_protect)
        patcher.start()
        self.addCleanup(patcher.stop)
        # 引擎先查 openalex_key（与 expand 的早退先例一致）；假客户端测试也走真实设置。
        self.store.save_settings({'openalex_key': KEY})

    def add_library(self, **paper):
        record = {'id': paper.pop('pid'), 'kind': 'library', 'title': paper.pop('title', 'A paper'),
                  'year': 2020, 'citekey': paper.get('openalex_id', paper.get('doi', 'citekey'))}
        record.update(paper)
        self.store.put_papers([record])
        return record

    def rows(self, table, task_id=None):
        with self.store.db() as db:
            sql = f'SELECT * FROM {table}'
            params = ()
            if task_id:
                sql += ' WHERE task_id=?'
                params = (task_id,)
            return [dict(r) for r in db.execute(sql, params)]

    def node(self, snapshot, wid):
        return next(n for n in snapshot['nodes'] if n['wid'] == wid)


class DepthChain(TraceBase):
    """A→B→C→D→E 五篇链；深度边界与请求分层。"""

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

    def run_depth(self, depth=None, unlimited=None):
        fake = Fake(self.works)
        with patch.object(OpenAlex, 'get', side_effect=fake.fetch):
            created = start_task(self.store, 'p1', depth=depth, unlimited=unlimited)
            snapshot = run_task(self.store, created['id'])
        return fake, snapshot

    def test_depth_three_includes_d_and_never_requests_e(self):
        fake, snapshot = self.run_depth(depth=3)
        self.assertEqual(snapshot['status'], 'complete')
        self.assertEqual(snapshot['depth_display'], '3 层')
        self.assertEqual({n['wid']: n['min_depth'] for n in snapshot['nodes']},
                         {'WA': 0, 'WB': 1, 'WC': 2, 'WD': 3})
        wd = self.node(snapshot, 'WD')
        self.assertEqual(wd['stop_reason'], 'depth_limit')
        self.assertEqual(wd['paths'], [['WA', 'WB', 'WC', 'WD']])
        # 请求分层：种子单篇 + 每层一次批量 filter；第 N+1 层（E）零请求。
        self.assertEqual(paths_of(fake), ['works/WA', 'works', 'works', 'works'])
        self.assertEqual(filters_of(fake), ['openalex_id:WB', 'openalex_id:WC', 'openalex_id:WD'])
        self.assertNotIn('WE', ' '.join(filters_of(fake)))
        self.assertNotIn('WE', {n['wid'] for n in snapshot['nodes']})

    def test_depth_one_only_requests_seed_and_first_layer(self):
        fake, snapshot = self.run_depth(depth=1)
        self.assertEqual(snapshot['status'], 'complete')
        self.assertEqual({n['wid']: n['min_depth'] for n in snapshot['nodes']}, {'WA': 0, 'WB': 1})
        self.assertEqual(self.node(snapshot, 'WB')['stop_reason'], 'depth_limit')
        self.assertEqual(paths_of(fake), ['works/WA', 'works'])
        self.assertEqual(filters_of(fake), ['openalex_id:WB'])

    def test_depth_two_stops_after_second_layer(self):
        fake, snapshot = self.run_depth(depth=2)
        self.assertEqual(snapshot['status'], 'complete')
        self.assertEqual({n['wid']: n['min_depth'] for n in snapshot['nodes']}, {'WA': 0, 'WB': 1, 'WC': 2})
        self.assertEqual(self.node(snapshot, 'WC')['stop_reason'], 'depth_limit')
        self.assertNotIn('WD', ' '.join(filters_of(fake)))

    def test_custom_five_layers_walks_whole_chain(self):
        fake, snapshot = self.run_depth(depth=5)
        self.assertEqual(snapshot['status'], 'complete')
        self.assertEqual({n['wid']: n['min_depth'] for n in snapshot['nodes']},
                         {'WA': 0, 'WB': 1, 'WC': 2, 'WD': 3, 'WE': 4})
        # E 在第 4 层 < 5，可展开；它的引用为空列表 → 零上游引用。
        self.assertEqual(self.node(snapshot, 'WE')['stop_reason'], 'no_upstream')
        self.assertEqual(filters_of(fake),
                         ['openalex_id:WB', 'openalex_id:WC', 'openalex_id:WD', 'openalex_id:WE'])

    def test_unlimited_setting_walks_same_chain_without_depth_gate(self):
        self.store.save_settings({'openalex_key': KEY, 'network_depth_unlimited': True})
        fake, snapshot = self.run_depth()
        self.assertEqual(snapshot['status'], 'complete')
        self.assertTrue(snapshot['unlimited'])
        self.assertIsNone(snapshot['depth'])
        self.assertEqual(snapshot['depth_display'], '不限层数')
        self.assertEqual(self.node(snapshot, 'WE')['stop_reason'], 'no_upstream')
        self.assertEqual(len(filters_of(fake)), 4)


class UnlimitedRounds(TraceBase):
    """不限层数 + 每轮预算切轮 + 重启续跑，走完 ≥6 跳引用链。"""

    def test_unlimited_walks_six_hop_chain_across_rounds_and_restart(self):
        works = {f'W{i}': work(f'W{i}', f'Paper {i}', refs=[f'W{i + 1}'] if i < 7 else []) for i in range(1, 8)}
        self.add_library(pid='p1', title='Paper 1', openalex_id='W1')
        self.store.save_settings({'openalex_key': KEY, 'external_requests_per_run': 2})
        fake = UrlFake(works)
        with patch('kbapp.integrations.openalex.urlopen', side_effect=fake), \
                patch('kbapp.integrations.openalex.time.sleep', lambda s: None):
            created = start_task(self.store, 'p1', unlimited=True)
            snapshot = run_task(self.store, created['id'])
            self.assertEqual(snapshot['status'], 'awaiting_budget')
            self.assertEqual(snapshot['requests'], 2)
            row = next(r for r in self.rows('trace_queue', created['id']) if r['wid'] == 'W2')
            self.assertEqual(row['status'], 'pending')
            # 跨进程重启：同一数据目录重建 Store，从断点继续。
            store = Store(Path(self.tmp.name))
            snapshot = resume_task(store, created['id'])
            self.assertEqual(snapshot['status'], 'awaiting_budget')
            snapshot = resume_task(store, created['id'])
            self.assertEqual(snapshot['status'], 'awaiting_budget')
            snapshot = resume_task(store, created['id'])
        self.assertEqual(snapshot['status'], 'complete')
        depths = {n['wid']: n['min_depth'] for n in snapshot['nodes']}
        self.assertEqual(depths, {f'W{i}': i - 1 for i in range(1, 8)})
        self.assertEqual(self.node(snapshot, 'W7')['paths'], [['W1', 'W2', 'W3', 'W4', 'W5', 'W6', 'W7']])
        # 实际向假客户端请求的 ID 序列：种子单篇 + 每跳一次批量 filter，共 7 个请求。
        self.assertEqual(fake.requests, [('works/W1', None)] + [
            ('works', {'filter': f'openalex_id:W{i}', 'per_page': 100}) for i in range(2, 8)])


class DepthSnapshot(TraceBase):
    """任务快照层数；期间改设置不影响正在运行或等待续跑的旧任务。"""

    def test_task_depth_snapshotted_and_immune_to_setting_changes(self):
        works = {
            'WS1': work('WS1', 'Seed one', refs=['WA1']),
            'WA1': work('WA1', 'Ancestor one', refs=['WA2']),
            'WA2': work('WA2', 'Root one', refs=['WA3']),
            'WA3': work('WA3', 'Deep one', refs=[]),
            'WS2': work('WS2', 'Seed two', refs=['WB1']),
            'WB1': work('WB1', 'Ancestor two', refs=['WB2']),
            'WB2': work('WB2', 'Root two', refs=[]),
        }
        self.add_library(pid='p1', title='Seed one', openalex_id='WS1')
        self.add_library(pid='p2', title='Seed two', openalex_id='WS2')
        fake = Fake(works)
        with patch.object(OpenAlex, 'get', side_effect=fake.fetch):
            created = start_task(self.store, 'p1', depth=5)
            task1 = created['id']
            # 在返回 WA2 批量响应的瞬间把任务一暂停（在途请求自然完成）。
            fake.hooks.append((lambda path, params: path == 'works' and 'WA2' in params['filter'],
                               lambda: pause_task(self.store, task1)))
            snapshot = run_task(self.store, task1)
            self.assertEqual(snapshot['status'], 'paused')
            self.assertEqual([q['wid'] for q in snapshot['queue']], ['WA2'])  # 未完成分支可见
            # 改设置为 1 层后启动任务二：任务二按 1 层执行，任务一仍暂停。
            self.store.save_settings({'network_depth': 1})
            created2 = start_task(self.store, 'p2')
            self.assertEqual(created2['depth'], 1)
            snapshot2 = run_task(self.store, created2['id'])
            self.assertEqual(snapshot2['status'], 'complete')
            self.assertEqual({n['wid'] for n in snapshot2['nodes']}, {'WS2', 'WB1'})
            self.assertEqual(self.node(snapshot2, 'WB1')['stop_reason'], 'depth_limit')
            self.assertNotIn('WB2', ' '.join(filters_of(fake)))
            still = task_snapshot(self.store, task1)
            self.assertEqual(still['status'], 'paused')  # 任务二不解除任务一的暂停
            # 任务一续跑：仍按快照的 5 层执行（继续请求第 3 层的 WA3）。
            resumed = resume_task(self.store, task1)
        self.assertEqual(resumed['status'], 'complete')
        self.assertEqual(resumed['depth'], 5)
        self.assertEqual({n['wid'] for n in resumed['nodes']}, {'WS1', 'WA1', 'WA2', 'WA3'})
        self.assertIn('WA3', ' '.join(filters_of(fake)))


class GraphShapes(TraceBase):
    """菱形多路径、引用环、自引：边正确、任务能结束。"""

    def setUp(self):
        super().setUp()
        self.works = {
            'WA': work('WA', 'Seed', doi='10.1234/a', refs=['WB', 'WC']),
            'WB': work('WB', 'Via B', refs=['WD', 'WB']),   # 自引
            'WC': work('WC', 'Via C', refs=['WD', 'WA']),   # 环：C 引用种子
            'WD': work('WD', 'Diamond D', refs=[]),
        }
        self.add_library(pid='p1', title='Seed', doi='10.1234/a', openalex_id='WA')

    def test_diamond_cycle_and_self_citation_terminate(self):
        fake = Fake(self.works)
        with patch.object(OpenAlex, 'get', side_effect=fake.fetch):
            created = start_task(self.store, 'p1', depth=2)
            snapshot = run_task(self.store, created['id'])
        self.assertEqual(snapshot['status'], 'complete')
        self.assertEqual(self.node(snapshot, 'WD')['min_depth'], 2)
        self.assertEqual(self.node(snapshot, 'WD')['paths'], [['WA', 'WB', 'WD'], ['WA', 'WC', 'WD']])
        edges = {(e['src_wid'], e['dst_wid']) for e in self.rows('trace_edges', created['id'])}
        self.assertIn(('WB', 'WD'), edges)
        self.assertIn(('WC', 'WD'), edges)
        self.assertIn(('WC', 'WA'), edges)   # 环边作为真实引用路径保留
        self.assertNotIn(('WB', 'WB'), edges)  # 自引不记边
        self.assertEqual(self.node(snapshot, 'WA')['min_depth'], 0)  # 环不把种子拉深
        self.assertEqual(self.node(snapshot, 'WA')['status'], 'done')
        self.assertEqual(len(fake.requests), 3)  # 有穷：单篇 + filter[WB,WC] + filter[WD,WA]

    def test_min_depth_rejudgement_on_later_shorter_path(self):
        # 更短路径后到时按距种子的最短深度判定能否继续展开。深度序的广度优先
        # 使「后到的更短路径」在正常流里结构性不可达（更浅父行必然先处理），
        # 因此这里直接锚定判定机制本身：min_depth 只减不增、旧停止原因被清除、
        # 深度闸按新深度重新判定。
        tid = start_task(self.store, 'p1', depth=3)['id']
        self.assertTrue(trace._touch_node(self.store, tid, 'WX', 3, {}, {'WX': 'oa:WX'}, False, 3))
        node = trace._node(self.store, tid, 'WX')
        self.assertEqual((node['min_depth'], node['stop_reason'], node['status']), (3, 'depth_limit', 'done'))
        self.assertTrue(trace._touch_node(self.store, tid, 'WX', 2, {}, {'WX': 'oa:WX'}, False, 3))
        node = trace._node(self.store, tid, 'WX')
        self.assertEqual((node['min_depth'], node['stop_reason'], node['status']), (2, '', 'pending'))
        self.assertFalse(trace._touch_node(self.store, tid, 'WX', 4, {}, {'WX': 'oa:WX'}, False, 3))
        self.assertEqual(trace._node(self.store, tid, 'WX')['min_depth'], 2)

    def test_two_seeds_share_ancestor_metadata(self):
        works = {
            'WP': work('WP', 'Seed P', refs=['WM']),
            'WQ': work('WQ', 'Seed Q', refs=['WM']),
            'WM': work('WM', 'Shared ancestor', refs=[]),
        }
        self.add_library(pid='p1', title='Seed P', openalex_id='WP')
        self.add_library(pid='p2', title='Seed Q', openalex_id='WQ')
        fake = Fake(works)
        with patch.object(OpenAlex, 'get', side_effect=fake.fetch):
            one = run_task(self.store, start_task(self.store, 'p1', depth=2)['id'])
            two = run_task(self.store, start_task(self.store, 'p2', depth=2)['id'])
        self.assertEqual(one['status'], 'complete')
        self.assertEqual(two['status'], 'complete')
        self.assertEqual({n['wid'] for n in one['nodes']}, {'WP', 'WM'})
        self.assertEqual({n['wid'] for n in two['nodes']}, {'WQ', 'WM'})
        # 元数据共享：两任务只产生一份祖先文献记录。
        shared = [pid for pid, p in self.store.papers().items() if p.get('openalex_id') == 'WM']
        self.assertEqual(shared, ['oa:WM'])
        self.assertEqual({e['src_wid'] for e in self.rows('trace_edges', one['id'])}, {'WP'})
        self.assertEqual({e['src_wid'] for e in self.rows('trace_edges', two['id'])}, {'WQ'})


class WideSeed(TraceBase):
    """#50 限制的是进入关系判定的候选数，不限制 OpenAlex 原始引用条数。"""

    def test_more_than_ten_direct_refs_at_one_layer(self):
        wids = [f'W{i:02d}' for i in range(1, 13)]
        works = {'WA': work('WA', 'Seed', refs=wids)}
        works.update({w: work(w, 'Ref ' + w, refs=[]) for w in wids})
        self.add_library(pid='p1', title='Seed', openalex_id='WA')
        fake = Fake(works)
        with patch.object(OpenAlex, 'get', side_effect=fake.fetch):
            snapshot = run_task(self.store, start_task(self.store, 'p1', depth=1)['id'])
        self.assertEqual(snapshot['status'], 'complete')
        self.assertEqual(len(snapshot['nodes']), 13)
        self.assertEqual(filters_of(fake), ['openalex_id:' + '|'.join(wids)])
        # 不把新引用送入付费模型：不写 relations，也不写引用采集队列。
        self.assertEqual(self.rows('relations'), [])
        self.assertEqual(self.rows('reference_jobs'), [])


class PartialBatch(TraceBase):
    """批量部分返回：游标不跳过未处理项，attempts 耗尽才定格。"""

    def setUp(self):
        super().setUp()
        self.wids = [f'W{i:02d}' for i in range(1, 31)]
        self.works = {'WA': work('WA', 'Seed', refs=self.wids)}
        self.works.update({w: work(w, 'Ref ' + w, refs=[]) for w in self.wids})
        self.add_library(pid='p1', title='Seed', openalex_id='WA')
        # 第 1 批吞掉最后 10 个；第 2 批（只含重发的 10 个）再吞掉最后 2 个。
        self.drop_log = []

        def drop(index, requested):
            self.drop_log.append(list(requested))
            if index == 1:
                return set(requested[-10:])
            return set(requested[-2:])

        self.drop = drop

    def test_partial_batch_retries_pending_without_skipping(self):
        self.store.save_settings({'openalex_key': KEY, 'external_requests_per_run': 2})
        tid = start_task(self.store, 'p1', depth=1)['id']
        fake = UrlFake(self.works, batch_drop=self.drop)
        with patch('kbapp.integrations.openalex.urlopen', side_effect=fake), \
                patch('kbapp.integrations.openalex.time.sleep', lambda s: None):
            snapshot = run_task(self.store, tid)
            self.assertEqual(snapshot['status'], 'awaiting_budget')
            row = next(r for r in self.rows('trace_queue', tid) if r['wid'] == 'WA')
            # 游标停在窗口起点，缺的 10 个带 attempts 留在 pending，一个都不跳过。
            self.assertEqual((row['cursor'], json.loads(row['pending'])),
                             ('0', [{'wid': w, 'attempts': 1} for w in self.wids[20:]]))
            snapshot = resume_task(self.store, tid)
        self.assertEqual(snapshot['status'], 'complete')
        # 第二次 filter 只重发缺的 10 个，20 个已解析项不重发。
        self.assertEqual(self.drop_log[1], self.wids[20:])
        edges = {e['dst_wid'] for e in self.rows('trace_edges', tid)}
        self.assertEqual(edges, set(self.wids))  # 30 条引用边一条不少
        settled = [n for n in snapshot['nodes'] if n['stop_reason'] == 'missing_source']
        self.assertEqual({n['wid'] for n in settled}, set(self.wids[28:]))
        remaining = {n['wid'] for n in snapshot['nodes'] if n['status'] == 'pending'}
        self.assertEqual(remaining, set())


class StopReasons(TraceBase):
    """引用字段缺失、明确空列表、429：逐项断言停止原因与续跑行为。"""

    def test_missing_field_and_empty_list_stop_reasons(self):
        works = {
            'WA': work('WA', 'Seed', refs=['WM', 'WE']),
            'WM': work('WM', 'No refs field', omit_refs=True),
            'WE': work('WE', 'Empty refs', refs=[]),
        }
        self.add_library(pid='p1', title='Seed', openalex_id='WA')
        fake = Fake(works)
        with patch.object(OpenAlex, 'get', side_effect=fake.fetch):
            snapshot = run_task(self.store, start_task(self.store, 'p1', depth=2)['id'])
        self.assertEqual(snapshot['status'], 'complete')
        missing = self.node(snapshot, 'WM')
        self.assertEqual((missing['status'], missing['stop_reason'], missing['refs_state']),
                         ('done', 'missing_source', 'missing'))
        self.assertIn('缺引用字段', missing['note'])
        empty = self.node(snapshot, 'WE')
        self.assertEqual((empty['status'], empty['stop_reason'], empty['refs_state']),
                         ('done', 'no_upstream', 'empty'))
        self.assertEqual(len(snapshot['queue']), 0)

    def test_http_429_preserves_progress_and_resumes(self):
        works = {'WA': work('WA', 'Seed', refs=['WT']), 'WT': work('WT', 'Target', refs=[])}
        self.add_library(pid='p1', title='Seed', openalex_id='WA')
        calls = []

        def urlopen(request, timeout):
            calls.append(request.full_url)
            if len(calls) == 1:
                body = json.dumps(work('WA', 'Seed', doi='10.1234/a', refs=['WT'])).encode('utf-8')
                response = io.BytesIO(body)
                response.headers = {}
                return response
            from urllib.error import HTTPError
            raise HTTPError('https://api.openalex.org/works', 429, 'Too Many Requests', None, None)

        tid = start_task(self.store, 'p1', depth=2)['id']
        with patch('kbapp.integrations.openalex.urlopen', side_effect=urlopen), \
                patch('kbapp.integrations.openalex.time.sleep', lambda s: None):
            snapshot = run_task(self.store, tid)
        self.assertEqual(snapshot['status'], 'awaiting_budget')
        self.assertEqual(self.node(snapshot, 'WA')['stop_reason'], 'budget_wait')
        self.assertEqual(snapshot['queue'][0]['error'], 'OpenAlex 限流或当日额度不足，已保留采集位置')
        self.assertEqual(len(calls), 2)  # 单篇成功 + 429，进度未丢
        fake = Fake(works)
        with patch.object(OpenAlex, 'get', side_effect=fake.fetch):
            snapshot = resume_task(self.store, tid)
        self.assertEqual(snapshot['status'], 'complete')
        self.assertEqual({n['wid']: n['min_depth'] for n in snapshot['nodes']}, {'WA': 0, 'WT': 1})
        self.assertEqual(self.node(snapshot, 'WT')['stop_reason'], 'no_upstream')
        self.assertEqual(filters_of(fake), ['openalex_id:WT'])  # 种子单篇没有重发

    def test_budget_zero_and_missing_key_guidance(self):
        works = {'WA': work('WA', 'Seed', refs=['WT']), 'WT': work('WT', 'Target', refs=[])}
        self.add_library(pid='p1', title='Seed', openalex_id='WA')
        self.store.save_settings({'openalex_key': KEY, 'external_requests_per_run': 0})
        tid = start_task(self.store, 'p1', depth=2)['id']
        fake = UrlFake(works)
        with patch('kbapp.integrations.openalex.urlopen', side_effect=fake), \
                patch('kbapp.integrations.openalex.time.sleep', lambda s: None):
            snapshot = run_task(self.store, tid)
            self.assertEqual(snapshot['status'], 'awaiting_budget')
            self.assertEqual(snapshot['requests'], 0)  # 预算闸在发请求前挡下
            self.assertEqual(fake.requests, [])
            row = next(r for r in self.rows('trace_queue', tid) if r['wid'] == 'WA')
            self.assertEqual((row['wid'], row['status'], row['cursor']), ('WA', 'pending', '*'))
            # 缺 key：任务态 awaiting_key，零请求，状态输出给引导文案。
            self.store.save_settings({'openalex_key': ''})
            snapshot = resume_task(self.store, tid)
            self.assertEqual(snapshot['status'], 'awaiting_key')
            self.assertIn('设置页', snapshot['hint'])
            self.store.save_settings({'openalex_key': KEY, 'external_requests_per_run': 10})
            snapshot = resume_task(self.store, tid)
        self.assertEqual(snapshot['status'], 'complete')

    def test_classify_prefix_table_covers_client_messages(self):
        self.assertEqual(classify(ValueError('本轮外部请求上限已到，保留分页位置，下次续跑')), 'budget_wait')
        self.assertEqual(classify(ValueError('OpenAlex 限流或当日额度不足，已保留采集位置')), 'budget_wait')
        self.assertEqual(classify(ValueError('OpenAlex 返回的当日额度已用尽')), 'budget_wait')
        self.assertEqual(classify(ValueError('OpenAlex HTTP 404')), 'missing_source')
        self.assertEqual(classify(ValueError('OpenAlex HTTP 500')), 'network_wait')
        self.assertEqual(classify(ValueError('请在网页配置 OpenAlex key 后继续分页补全')), 'awaiting_key')
        self.assertEqual(classify(ValueError('无法解析的响应结构')), 'network_wait')
        self.assertEqual(classify(TimeoutError('timed out')), 'network_wait')

    def test_http_404_settles_branch_without_retry(self):
        self.add_library(pid='p1', title='Seed', openalex_id='WA')
        calls = []

        def urlopen(request, timeout):
            calls.append(request.full_url)
            if len(calls) == 1:
                body = json.dumps(work('WA', 'Seed', doi='10.1234/a', refs=['WD'])).encode('utf-8')
                response = io.BytesIO(body)
                response.headers = {}
                return response
            from urllib.error import HTTPError
            raise HTTPError('https://api.openalex.org/works', 404, 'Not Found', None, None)

        tid = start_task(self.store, 'p1', depth=2)['id']
        with patch('kbapp.integrations.openalex.urlopen', side_effect=urlopen), \
                patch('kbapp.integrations.openalex.time.sleep', lambda s: None):
            snapshot = run_task(self.store, tid)
        self.assertEqual(snapshot['status'], 'complete')  # 死分支定格，不拖垮任务
        dead = self.node(snapshot, 'WD')
        self.assertEqual((dead['status'], dead['stop_reason']), ('done', 'missing_source'))
        self.assertEqual(len(calls), 2)  # 定格后不浪费重试

    def test_seed_own_404_completes_with_missing_source_and_one_request(self):
        # 种子自身 404：任务以 complete 收尾（而非失败态）、种子节点定格
        # missing_source、hint 为空、请求恰 1 次不重试不拖垮——这是用户可见
        # 的纯逻辑（trace run 一个失效种子），用 UrlFake 让真实 404 折叠生效。
        self.add_library(pid='p1', title='Seed', doi='10.1234/a', openalex_id='WA')
        fake = UrlFake({}, fail=lambda index, path: 404)
        with patch('kbapp.integrations.openalex.urlopen', side_effect=fake), \
                patch('kbapp.integrations.openalex.time.sleep', lambda s: None):
            snapshot = run_task(self.store, start_task(self.store, 'p1', depth=2)['id'])
        self.assertEqual(snapshot['status'], 'complete')
        self.assertEqual(snapshot['error'], '')
        self.assertEqual(snapshot['hint'], '')
        self.assertEqual(snapshot['requests'], 1)
        self.assertEqual(fake.requests, [('works/WA', None)])
        seed = self.node(snapshot, 'WA')
        self.assertEqual((seed['status'], seed['stop_reason'], seed['refs_state']),
                         ('done', 'missing_source', 'missing'))
        self.assertEqual(seed['note'], '数据源缺引用字段或未返回该文献')
        self.assertEqual(snapshot['counts'],
                         {'nodes': 1, 'stopped': 1, 'edges': 0, 'pending_branches': 0})
        self.assertEqual(snapshot['queue'], [])


class DepthSettings(TraceBase):
    """层数读取与校验：接口契约由 #81 的键级校验承载，这里锚引擎侧。"""

    def test_invalid_depth_values_rejected_keeping_previous_settings(self):
        self.add_library(pid='p1', title='Seed', doi='10.1234/a', openalex_id='WA')
        self.store.save_settings({'network_depth': 3})
        for value, message in ((2.8, '层数应为整数'), (-1, '层数应在 1 至 100 之间'), (True, '层数应为数字'),
                               (0, '层数应在 1 至 100 之间'), (101, '层数应在 1 至 100 之间'), ('x', '层数应为数字')):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, message):
                    start_task(self.store, 'p1', depth=value)
            self.assertEqual(self.rows('trace_tasks'), [])
        # 设置文件里的非法值同样报可理解错误，且引擎不改写任何设置。
        atomic(self.store.paths.state / 'settings.json', {'network_depth': 2.8})
        junk = (self.store.paths.state / 'settings.json').read_text(encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '层数应为整数'):
            start_task(self.store, 'p1')
        self.assertEqual((self.store.paths.state / 'settings.json').read_text(encoding='utf-8'), junk)
        self.assertEqual(self.rows('trace_tasks'), [])

    def test_unlimited_persisted_as_boolean_flag(self):
        self.add_library(pid='p1', title='Seed', doi='10.1234/a', openalex_id='WA')
        # 「不限层数」的唯一持久化表示是布尔键 network_depth_unlimited（0/-1/'unlimited'
        # 都不是哨兵：0 与「0 层」混淆、-1 明确非法、整型键存不进字符串）。
        with self.assertRaisesRegex(ValueError, '层数应在 1 至 100 之间'):
            parse_depth(0)
        self.store.save_settings({'network_depth_unlimited': True})
        snapshot = run_task(self.store, start_task(self.store, 'p1')['id'])
        self.assertTrue(snapshot['unlimited'])
        self.assertIsNone(snapshot['depth'])
        self.assertEqual(snapshot['depth_display'], '不限层数')
        row = self.rows('trace_tasks')[0]
        self.assertEqual((row['unlimited'], row['depth']), (1, None))

    def test_depth_read_from_settings_at_start(self):
        self.add_library(pid='p1', title='Seed', doi='10.1234/a', openalex_id='WA')
        self.store.save_settings({'network_depth': 4})
        tid = start_task(self.store, 'p1')['id']
        self.assertEqual(task_snapshot(self.store, tid)['depth'], 4)
        self.store.save_settings({'network_depth': 1})
        self.assertEqual(task_snapshot(self.store, tid)['depth'], 4)  # 已建任务不随设置漂移

    def test_legacy_written_settings_depth_reaches_engine_snapshot(self):
        # 直锚「引擎不把存量值重置为 2」：手写旧配置文件（无 network_depth_unlimited
        # 键、层数为存量 3）→ start_task（无显式层数）→ 快照与任务行都取 3。
        self.add_library(pid='p1', title='Seed', doi='10.1234/a', openalex_id='WA')
        atomic(self.store.paths.state / 'settings.json', {'network_depth': 3})
        snapshot = start_task(self.store, 'p1')
        self.assertEqual(snapshot['depth'], 3)
        self.assertFalse(snapshot['unlimited'])
        row = self.rows('trace_tasks')[0]
        self.assertEqual((row['depth'], row['unlimited']), (3, 0))

    def test_parse_depth_contract_matches_save_settings(self):
        # parse_depth 与 save_settings 的键级校验同口径：整数值字符串接受——
        # 手写 settings.json 的 "2" 与经 API 保存的 2 行为一致；布尔、小数、
        # 非数值、越界仍拒绝。
        for value, expected in (('2', 2), ('100', 100), (2.0, 2), (3, 3)):
            with self.subTest(value=value):
                self.assertEqual(parse_depth(value), expected)
        for value, message in (('2.8', '层数应为整数'), ('x', '层数应为数字'), (None, '层数应为数字'),
                               (True, '层数应为数字'), (-1, '层数应在 1 至 100 之间'),
                               (0, '层数应在 1 至 100 之间'), (101, '层数应在 1 至 100 之间')):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, message):
                    parse_depth(value)
        # 手写 "network_depth": "2" 的设置文件 → start_task 与 API 保存同一口径。
        self.add_library(pid='p2', title='Seed two', doi='10.1234/b', openalex_id='WB')
        atomic(self.store.paths.state / 'settings.json', {'network_depth': '2'})
        self.assertEqual(start_task(self.store, 'p2')['depth'], 2)


class Compatibility(TraceBase):
    """旧两层 frontier 隔离、普通 update/expand 兼容、写入边界。"""

    def test_old_frontier_complete_not_mistaken_and_untouched(self):
        from kbapp.library.network import enqueue as frontier_enqueue
        works = {'WA': work('WA', 'Seed', refs=['WB']), 'WB': work('WB', 'Old', refs=[])}
        self.add_library(pid='p1', title='Seed', openalex_id='WA')
        frontier_enqueue(self.store, 'WA', 'upstream', 0)
        with self.store.db() as db:
            db.execute("UPDATE frontier SET status='complete'")
        before = self.rows('frontier')
        fake = Fake(works)
        with patch.object(OpenAlex, 'get', side_effect=fake.fetch):
            snapshot = run_task(self.store, start_task(self.store, 'p1', depth=1)['id'])
        self.assertEqual(snapshot['status'], 'complete')
        self.assertEqual({n['wid'] for n in snapshot['nodes']}, {'WA', 'WB'})  # 引擎自己追，不看旧队列
        self.assertEqual(self.rows('frontier'), before)  # frontier 表逐行不变

    def test_expand_compat_and_trace_untouched_by_scheduler(self):
        from kbapp.library.network import enqueue as frontier_enqueue, expand
        works = {'WA': work('WA', 'Seed', refs=['WB']), 'WB': work('WB', 'Old', refs=[])}
        self.add_library(pid='p1', title='Seed', openalex_id='WA')
        self.store.save_settings({'openalex_key': KEY})
        frontier_enqueue(self.store, 'WA', 'upstream', 0)
        start_task(self.store, 'p1', depth=2, unlimited=False)
        with self.store.db() as db:
            db.execute("UPDATE trace_tasks SET status='paused'")
        before = {t: self.rows(t) for t in ('trace_tasks', 'trace_nodes', 'trace_edges', 'trace_queue')}
        fake = Fake(works)
        with patch.object(OpenAlex, 'get', side_effect=fake.fetch):
            result = expand(self.store)
        self.assertEqual(result['state'], 'complete')
        # 普通两层展开行为不变；追溯表逐行不变（调度器不重启追溯任务）。
        self.assertEqual(len(fake.requests), 2)
        after = {t: self.rows(t) for t in ('trace_tasks', 'trace_nodes', 'trace_edges', 'trace_queue')}
        self.assertEqual(after, before)

    def test_run_update_leaves_trace_tables_untouched(self):
        from kbapp.workflows.update import run_update
        record = self.add_library(pid='p1', title='Seed', doi='10.1234/a', openalex_id='WA')
        tid = start_task(self.store, 'p1', depth=2)['id']
        before = {t: self.rows(t) for t in ('trace_tasks', 'trace_nodes', 'trace_edges', 'trace_queue')}
        snapshot = dict(record, citation_count=10, citation_updated='2020-01-01T00:00:00+08:00')

        def build(store, index, corpus, progress, limit):
            from kbapp.core.io import atomic
            atomic(index / 'corpus_meta.json', {'sources': 0, 'vector_count': 0, 'vector_pending': 0})
            return []

        with patch('kbapp.workflows.update.sync_library', return_value=([snapshot], [], [])), \
                patch('kbapp.workflows.update.build_corpus', return_value=[]), \
                patch('kbapp.workflows.update.build_vectors', side_effect=build), \
                patch('kbapp.workflows.update.publish_notes'), \
                patch('kbapp.workflows.export.export_static'), \
                patch('kbapp.workflows.reports.publish_lineage'), \
                patch('kbapp.workflows.reports.publish_audit'):
            run_update(self.store, bootstrap=True)
        after = {t: self.rows(t) for t in ('trace_tasks', 'trace_nodes', 'trace_edges', 'trace_queue')}
        self.assertEqual(after, before)
        self.assertEqual(task_snapshot(self.store, tid)['status'], 'paused')

    def test_engine_writes_no_relations_jobs_or_approvals(self):
        works = {'WA': work('WA', 'Seed', refs=['WB']), 'WB': work('WB', 'Child', refs=[])}
        self.add_library(pid='p1', title='Seed', doi='10.1234/a', openalex_id='WA')
        fake = Fake(works)
        with patch.object(OpenAlex, 'get', side_effect=fake.fetch):
            snapshot = run_task(self.store, start_task(self.store, 'p1', depth=2)['id'])
        self.assertEqual(snapshot['status'], 'complete')
        self.assertEqual(self.rows('relations'), [])
        self.assertEqual(self.rows('reference_jobs'), [])
        self.assertEqual(self.rows('approvals'), [])
        self.assertEqual(self.rows('agent_runs'), [])

    def test_shared_raw_cache_reused_across_tasks(self):
        works = {'WA': work('WA', 'Seed', refs=['WB']), 'WB': work('WB', 'Child', refs=[])}
        self.add_library(pid='p1', title='Seed', doi='10.1234/a', openalex_id='WA')
        first = Fake(works)
        with patch.object(OpenAlex, 'get', side_effect=first.fetch):
            run_task(self.store, start_task(self.store, 'p1', depth=1)['id'])
        cached = self.store.paths.cache / 'references/openalex' / 'WA.json'
        self.assertTrue(cached.exists())
        self.assertEqual(json.loads(cached.read_text(encoding='utf-8'))['raw']['id'], 'https://openalex.org/WA')
        second = Fake(works)
        with patch.object(OpenAlex, 'get', side_effect=second.fetch):
            snapshot = run_task(self.store, start_task(self.store, 'p1', depth=1)['id'])
        # 种子 raw 与子文献元数据全部来自共享缓存/papers：第二个任务零网络请求。
        self.assertEqual(snapshot['status'], 'complete')
        self.assertEqual({n['wid'] for n in snapshot['nodes']}, {'WA', 'WB'})
        self.assertEqual(second.requests, [])

    def test_doi_only_seed_resolved_on_first_run(self):
        works = {'WA': work('WA', 'Seed A', doi='10.1234/a', refs=['WB']), 'WB': work('WB', 'Child', refs=[])}
        self.add_library(pid='p1', title='Seed A', doi='10.1234/a')
        fake = Fake(works)
        with patch.object(OpenAlex, 'get', side_effect=fake.fetch):
            snapshot = run_task(self.store, start_task(self.store, 'p1', depth=2)['id'])
        self.assertEqual(snapshot['status'], 'complete')
        self.assertEqual(snapshot['seed']['wid'], 'WA')
        self.assertEqual({n['wid']: n['min_depth'] for n in snapshot['nodes']}, {'WA': 0, 'WB': 1})
        self.assertEqual(paths_of(fake)[0], 'works/https://doi.org/10.1234/a')


class Control(TraceBase):
    """暂停/继续状态机与锁。"""

    def test_pause_resume_and_lock_conflict(self):
        works = {'WA': work('WA', 'Seed', refs=['WB']), 'WB': work('WB', 'Child', refs=[])}
        self.add_library(pid='p1', title='Seed', doi='10.1234/a', openalex_id='WA')
        tid = start_task(self.store, 'p1', depth=2)['id']
        paused = pause_task(self.store, tid)
        self.assertEqual(paused['status'], 'paused')
        fake = Fake(works)
        with patch.object(OpenAlex, 'get', side_effect=fake.fetch):
            with self.assertRaisesRegex(ValueError, '追溯任务不存在'):
                run_task(self.store, 'trace:missing')
            # 双开：同任务锁被占用时给出可理解的文案，而非「已有更新任务运行中」。
            from kbapp.core.locks import update_lock
            with update_lock(self.store.root, f"workbench-trace-{tid.replace(':', '-')}.lock"):
                with self.assertRaisesRegex(ValueError, '同一追溯任务已在运行，不可双开'):
                    run_task(self.store, tid)
            snapshot = run_task(self.store, tid)
            self.assertEqual(snapshot['status'], 'complete')
            with self.assertRaisesRegex(ValueError, '已完成'):
                resume_task(self.store, tid)
            with self.assertRaisesRegex(ValueError, '不能暂停'):
                pause_task(self.store, tid)
        self.assertEqual(self.node(snapshot, 'WB')['stop_reason'], 'no_upstream')

    def test_source_paths_command(self):
        works = {
            'WA': work('WA', 'Seed', refs=['WB', 'WC']),
            'WB': work('WB', 'Via B', refs=['WD']),
            'WC': work('WC', 'Via C', refs=['WD']),
            'WD': work('WD', 'Diamond', refs=[]),
        }
        self.add_library(pid='p1', title='Seed', openalex_id='WA')
        fake = Fake(works)
        with patch.object(OpenAlex, 'get', side_effect=fake.fetch):
            tid = run_task(self.store, start_task(self.store, 'p1', depth=2)['id'])['id']
        result = source_paths(self.store, tid, 'WD')
        self.assertEqual(result['paths'], [['WA', 'WB', 'WD'], ['WA', 'WC', 'WD']])
        self.assertEqual(source_paths(self.store, tid, 'WA')['paths'], [['WA']])
        with self.assertRaisesRegex(ValueError, '不在本追溯任务'):
            source_paths(self.store, tid, 'WZ')


if __name__ == '__main__':
    unittest.main()
