"""上游追溯引擎：从指定种子沿 OpenAlex 引用逐层向上游追溯（A 引用 B，B 引用 C）。

与全库两层展开（kbapp/library/network.py 的 expand/frontier）完全分离：
本引擎按「指定种子 + 任务作用域队列」推进，层数在任务行快照，设置变化只影响
之后新建的任务；frontier 表不读不写，旧两层队列的 complete 与本任务无关。
文献记录（papers）与 OpenAlex 原始缓存（<cache>/references/openalex/）跨任务
共享（经 references.cache_raw / references.ingest 复用），种子、层数、状态、
可达路径按任务各记各的（kbapp/storage/trace.py 四张表）。

深度按「引用边的条数」计：种子=深度 0，深度 d 的文献经 d 条引用边可达。
选 N 层时深度 ≤N 的文献与通向它们的边可见；深度 N 的节点由其父节点的批量
元数据请求带入展示字段，不入队展开、不取其引用（stop_reason='depth_limit'），
绝不为该任务发第 N+1 层请求。「不限层数」（network_depth_unlimited）不设
深度闸，靠每轮预算分轮推进、持久化剩余队列续跑。

请求形态只有两种：种子的单篇 works/<wid>（其余节点通常已随父节点批量响应
入场，仅在缺引用状态时补一发），以及每窗 ≤50 个 wid 的批量 filter 请求。
不写 relations、不写 reference_jobs、不读 PDF、不写 approvals；不动
#48/#49/#50 的额度字段；PDF 全文仍走既有审批；Zotero 全程只读。

暂停/续跑由构造保证不被调度器重启：本模块不注册线程、不提供 start_worker
（service.py 的后台清单与调度线程不含追溯），只有 trace resume 显式续跑。
"""
from __future__ import annotations
import json
import math
import os
import time
from urllib.parse import quote
from kbapp.core.io import atomic, digest, now
from kbapp.core.locks import update_lock
from kbapp.integrations.openalex import OpenAlex
from kbapp.storage.trace import ensure_schema
from kbapp.workflows.references import TTL, age, cache_raw, ingest

WINDOW = 50
MAX_ATTEMPTS = 2
PATH_LIMIT = 50
MIN_DEPTH = 1
MAX_DEPTH = 100
TASK_STATES = ('running', 'paused', 'awaiting_budget', 'deferred', 'awaiting_key', 'complete')
STOP_REASONS = ('depth_limit', 'no_upstream', 'missing_source', 'budget_wait', 'network_wait', 'awaiting_key', 'paused')
STOP_NOTES = {
    'depth_limit': '已达任务设定的层数，不再向上展开',
    'no_upstream': 'OpenAlex 当前记录没有上游引用',
    'missing_source': '数据源缺引用字段或未返回该文献',
    'budget_wait': '本轮外部请求预算或限流已到，待续跑',
    'network_wait': '临时网络故障，待续跑',
    'awaiting_key': '缺少 OpenAlex key，请到工作台设置页配置',
    'paused': '用户暂停，等待继续',
}
# OpenAlex.get 把一切失败折叠成 ValueError 且不保留类型（openalex.py:57-60），
# 只能按消息前缀分类；前缀与 openalex.py 的文案一一对应，这里是唯一出处。
ERROR_CLASSES = (
    ('本轮外部请求上限已到', 'budget_wait'),
    ('OpenAlex 限流或当日额度不足', 'budget_wait'),
    ('OpenAlex 返回的当日额度已用尽', 'budget_wait'),
    ('OpenAlex HTTP 404', 'missing_source'),
    ('OpenAlex HTTP ', 'network_wait'),
    ('请在网页配置 OpenAlex key', 'awaiting_key'),
)
ROUND_STATES = {'complete': 'complete', 'paused': 'paused', 'budget_wait': 'awaiting_budget',
                'network_wait': 'deferred', 'awaiting_key': 'awaiting_key'}
HINTS = {
    'awaiting_key': '请在工作台设置页配置 OpenAlex key 后继续追溯',
    'awaiting_budget': '本轮外部请求预算或限流已到，进度已保留，可稍后继续',
    'deferred': '临时网络故障，进度已保留，可稍后继续',
    'paused': '任务已暂停，继续后从断点推进',
}


class _PauseRequested(Exception):
    """运行循环在每个请求间隙重读任务行发现 paused 时抛出。"""


class _StopRound(Exception):
    """预算/限流/网络故障/缺 key：本行保留断点，整轮停止。"""

    def __init__(self, category, message):
        super().__init__(message)
        self.category = category
        self.message = message


def classify(exc):
    """把客户端异常折叠成四类；未知 ValueError 与非 ValueError 都按网络故障待续。"""
    if isinstance(exc, ValueError):
        message = str(exc)
        for prefix, category in ERROR_CLASSES:
            if message.startswith(prefix):
                return category
    return 'network_wait'


def parse_depth(value):
    """校验层数值（显式参数与设置值共用）；与 save_settings 的键级校验同口径。

    整数值字符串接受（手写 settings.json 的 "2" 与 API 保存的 2 行为一致），
    布尔拒绝，小数拒绝而不是静默截断。"""
    if isinstance(value, bool):
        raise ValueError('层数应为数字')
    if isinstance(value, int):
        number = value
    else:
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError('层数应为数字') from None
        if not math.isfinite(number):
            raise ValueError('层数应为数字')
        if not number.is_integer():
            raise ValueError('层数应为整数')
        number = int(number)
    if not MIN_DEPTH <= number <= MAX_DEPTH:
        raise ValueError('层数应在 1 至 100 之间；不限层数请选择「不限层数」')
    return number


def _resolve_depth(store, depth, unlimited):
    """任务快照层数：显式参数优先；都未给时读设置（network_depth_unlimited 是
    「不限层数」的唯一持久化表示，布尔键；缺键按 False，旧配置即有限层数）。"""
    if depth is None and unlimited is None:
        settings = store.settings()
        if settings.get('network_depth_unlimited'):
            return None, True
        return parse_depth(settings.get('network_depth')), False
    if unlimited:
        return None, True
    if depth is None:
        depth = store.settings().get('network_depth')
    return parse_depth(depth), False


def start_task(store, paper, depth=None, unlimited=None):
    """创建追溯任务（不发请求，状态 paused）；层数在此刻快照进任务行。"""
    ensure_schema(store)
    paper_id = store.resolve(paper)
    record = store.papers().get(paper_id)
    if not record:
        raise ValueError('文献不存在')
    finite, unlimited = _resolve_depth(store, depth, unlimited)
    wid = record.get('openalex_id') or ''
    if not wid and not record.get('doi'):
        raise ValueError('种子文献缺少 OpenAlex ID 与 DOI，无法沿 OpenAlex 向上游追溯')
    task_id = 'trace:' + digest(f'{time.time_ns()}|{paper_id}|{finite}|{unlimited}')[:20]
    stamp = now()
    with store.db() as db:
        db.execute('INSERT INTO trace_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                   (task_id, paper_id, wid, record.get('title') or paper_id, finite, int(unlimited),
                    'paused', 0, None, '', stamp, stamp))
        db.execute('INSERT OR REPLACE INTO trace_nodes VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (task_id, wid, paper_id, 0, '', 'pending', '', '', 0, stamp))
        db.execute('INSERT OR REPLACE INTO trace_queue VALUES(?,?,?,?,?,?,?,?)',
                   (task_id, wid, 0, '*', '[]', 'pending', '', stamp))
    return task_snapshot(store, task_id)


def pause_task(store, task_id):
    """条件暂停；运行循环在下一个请求间隙看到 paused 即停，在途请求自然完成。"""
    ensure_schema(store)
    stamp = now()
    with store.db() as db:
        row = db.execute('SELECT status FROM trace_tasks WHERE id=?', (task_id,)).fetchone()
        if not row:
            raise ValueError('追溯任务不存在')
        if row[0] == 'complete':
            raise ValueError('追溯任务已完成，不能暂停')
        db.execute("UPDATE trace_tasks SET status='paused',pid=NULL,updated=? WHERE id=?"
                   " AND status IN ('running','awaiting_budget','deferred','awaiting_key')", (stamp, task_id))
        db.execute("UPDATE trace_nodes SET stop_reason='paused',updated=? WHERE task_id=? AND status='pending'"
                   " AND wid IN (SELECT wid FROM trace_queue WHERE task_id=? AND status='pending')",
                   (stamp, task_id, task_id))
    return task_snapshot(store, task_id)


def run_task(store, task_id):
    """首次驱动：仅刚创建（paused）的任务可跑。"""
    return _drive(store, task_id, {'paused'})


def resume_task(store, task_id):
    """显式续跑：暂停/预算待续/网络故障/缺 key/异常中断的任务从断点继续。

    'running' 也接受：进程死亡后文件锁自动释放，能取到锁即证明没有活的运行方，
    遗留的 running 是崩溃现场，恢复之。
    """
    return _drive(store, task_id, {'paused', 'awaiting_budget', 'deferred', 'awaiting_key', 'running'})


def _drive(store, task_id, allowed):
    ensure_schema(store)
    with store.db() as db:
        row = db.execute('SELECT status FROM trace_tasks WHERE id=?', (task_id,)).fetchone()
    if not row:
        raise ValueError('追溯任务不存在')
    if row[0] not in allowed:
        if row[0] == 'complete':
            raise ValueError('追溯任务已完成，如需重新追溯请新建任务')
        raise ValueError(f'追溯任务当前为 {row[0]}，不能在此状态下驱动')
    try:
        with update_lock(store.root, f"workbench-trace-{task_id.replace(':', '-')}.lock"):
            return _run_rounds(store, task_id)
    except ValueError as exc:
        if str(exc) == '已有更新任务运行中':
            raise ValueError('同一追溯任务已在运行，不可双开') from None
        raise


def _run_rounds(store, task_id):
    """一轮推进：按深度序处理队列，直到队列空/预算耗尽/网络故障/暂停。

    OpenAlex 实例自带每轮预算闸与当日额度闸（openalex.py:39-42），本引擎不
    新增、不放宽任何预算键。
    """
    client = OpenAlex(store)
    if not client.cfg['openalex_key']:
        _finish_run(store, task_id, 'awaiting_key', '', 0)
        return task_snapshot(store, task_id)
    with store.db() as db:
        db.execute("UPDATE trace_tasks SET status='running',pid=?,error='',updated=? WHERE id=?",
                   (os.getpid(), now(), task_id))
    papers = store.papers()
    widmap = {p['openalex_id']: p['id'] for p in papers.values() if p.get('openalex_id')}
    try:
        category, message = _loop(store, task_id, client, papers, widmap)
    except _PauseRequested:
        category, message = 'paused', ''
    except _StopRound as stop:
        category, message = stop.category, stop.message
    except Exception as exc:
        category = 'network_wait'
        message = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
    state = ROUND_STATES.get(category, 'deferred')
    error = message if category in ('budget_wait', 'network_wait', 'awaiting_key') else ''
    _finish_run(store, task_id, state, error, client.calls)
    return task_snapshot(store, task_id)


def _finish_run(store, task_id, state, error, requests):
    with store.db() as db:
        db.execute('UPDATE trace_tasks SET status=?,pid=NULL,requests=requests+?,error=?,updated=? WHERE id=?',
                   (state, requests, error, now(), task_id))


def _task(store, task_id):
    with store.db() as db:
        row = db.execute('SELECT * FROM trace_tasks WHERE id=?', (task_id,)).fetchone()
    if not row:
        raise ValueError('追溯任务不存在')
    return dict(row)


def _task_status(store, task_id):
    with store.db() as db:
        row = db.execute('SELECT status FROM trace_tasks WHERE id=?', (task_id,)).fetchone()
    return row[0] if row else None


def _node(store, task_id, wid):
    with store.db() as db:
        row = db.execute('SELECT * FROM trace_nodes WHERE task_id=? AND wid=?', (task_id, wid)).fetchone()
    return dict(row) if row else None


def _loop(store, task_id, client, papers, widmap):
    while True:
        if _task_status(store, task_id) == 'paused':
            return 'paused', ''
        with store.db() as db:
            row = db.execute("SELECT * FROM trace_queue WHERE task_id=? AND status='pending'"
                             ' ORDER BY depth,updated,wid LIMIT 1', (task_id,)).fetchone()
        if not row:
            return 'complete', ''
        _process_row(store, task_id, dict(row), client, papers, widmap)


def _request(store, task_id, call):
    """发请求前的暂停闸 + 异常分类。

    任务级停轮（预算/网络/缺 key）抛 _StopRound；404 属分支定格，由调用方
    携带失败信息就地处理。"""
    if _task_status(store, task_id) == 'paused':
        raise _PauseRequested()
    try:
        return call(), None
    except Exception as exc:
        category = classify(exc)
        message = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        if category in ('budget_wait', 'network_wait', 'awaiting_key'):
            raise _StopRound(category, message) from None
        return None, (category, message)


def _refs_state_of(raw):
    """从原始响应区分三种引用状态：缺字段 / 明确空列表 / 有引用。"""
    if 'referenced_works' not in raw:
        return 'missing'
    return 'empty' if not raw.get('referenced_works') else 'present'


def _cache_raw(store, wid, raw, stamp):
    atomic(store.paths.cache / 'references/openalex' / f'{wid}.json', {'raw': raw, 'updated': stamp})


def _absorb(store, raw, stamp, papers, widmap):
    """经 references.ingest 并入共享 papers；同步内存映射，返回映射后的记录。"""
    data = ingest(store, raw, stamp, papers)
    papers[data['id']] = data
    widmap[data['openalex_id']] = data['id']
    return data


def _fetch_own_work(store, task_id, client, row, task, papers, widmap):
    """确保本节点的 referenced_works 已知（写入 trace_nodes.refs_state）。

    种子优先共享缓存（TTL 内不发请求）；其余节点通常已随父节点批量响应入场，
    只有缺引用状态时才补一发单篇请求。DOI 种子在此解析出 wid 后改写任务行。"""
    wid = row['wid']
    cached = cache_raw(store, wid) if wid else {}
    if cached.get('raw') and age(cached['updated']) < TTL:
        _absorb_own_raw(store, task_id, wid, cached['raw'], cached['updated'], papers, widmap)
        return wid
    data, failure = _request(store, task_id, lambda: _seed_query(store, client, task, wid))
    if failure:
        _settle_missing(store, task_id, wid, row['depth'], failure[1], widmap)
        if wid == task['seed_wid']:
            _finish_row(store, task_id, wid)
            _finish_node(store, task_id, wid, 'missing_source')
        return wid
    raw = data
    resolved = str(raw.get('id', '')).rstrip('/').rsplit('/', 1)[-1]
    if wid and resolved != wid:
        raise ValueError('返回的 OpenAlex ID 不一致')
    if not wid:
        wid = _rewrite_seed(store, task_id, resolved)
    _cache_raw(store, wid, raw, now())
    _absorb_own_raw(store, task_id, wid, raw, now(), papers, widmap)
    return wid


def _seed_query(store, client, task, wid):
    """种子单篇请求：wid 直取；仅 DOI 的走 DOI 精确。

    既无 wid 也无 DOI 的种子已被 start_task 拒绝（守卫文案同下），这里不再有
    第三条解析路径；守卫分支只为不变量被破坏时给出可理解错误。"""
    if wid:
        return client.get('works/' + wid)
    record = store.papers().get(task['seed_paper_id'], {})
    doi = (record.get('doi') or '').removeprefix('https://doi.org/')
    if not doi:
        raise ValueError('种子文献缺少 OpenAlex ID 与 DOI，无法沿 OpenAlex 向上游追溯')
    return client.get('works/https://doi.org/' + quote(doi, safe='/'))


def _rewrite_seed(store, task_id, wid):
    """DOI/题名解析出的种子 wid 回填任务行，替换占位的空 wid 行。"""
    stamp = now()
    with store.db() as db:
        db.execute('UPDATE trace_tasks SET seed_wid=?,updated=? WHERE id=?', (wid, stamp, task_id))
        db.execute("DELETE FROM trace_nodes WHERE task_id=? AND wid=''", (task_id,))
        db.execute("DELETE FROM trace_queue WHERE task_id=? AND wid=''", (task_id,))
        db.execute('INSERT OR REPLACE INTO trace_nodes VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (task_id, wid, None, 0, '', 'pending', '', '', 0, stamp))
        db.execute('INSERT OR REPLACE INTO trace_queue VALUES(?,?,?,?,?,?,?,?)',
                   (task_id, wid, 0, '*', '[]', 'pending', '', stamp))
    return wid


def _absorb_own_raw(store, task_id, wid, raw, stamp, papers, widmap):
    data = _absorb(store, raw, stamp, papers, widmap)
    with store.db() as db:
        db.execute('UPDATE trace_nodes SET refs_state=?,paper_id=?,attempts=attempts+1,updated=? WHERE task_id=? AND wid=?',
                   (_refs_state_of(raw), data['id'], stamp, task_id, wid))


def _settle_missing(store, task_id, wid, depth, message, widmap):
    """该分支定格 missing_source：不再重试，游标可越过。

    refs_state 一并标 missing（缺引用字段或未返回该文献同属一类），行处理器
    看到它即终结本行，不会对着死 wid 反复补发请求。"""
    stamp = now()
    with store.db() as db:
        row = db.execute('SELECT 1 FROM trace_nodes WHERE task_id=? AND wid=?', (task_id, wid)).fetchone()
        if row:
            db.execute("UPDATE trace_nodes SET status='done',stop_reason='missing_source',refs_state='missing',"
                       'error=?,paper_id=COALESCE(?,paper_id),updated=? WHERE task_id=? AND wid=?',
                       (message, widmap.get(wid), stamp, task_id, wid))
        else:
            db.execute('INSERT INTO trace_nodes VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (task_id, wid, widmap.get(wid), depth, 'missing', 'done', 'missing_source', message,
                        MAX_ATTEMPTS, stamp))


def _finish_row(store, task_id, wid):
    with store.db() as db:
        db.execute("UPDATE trace_queue SET status='done',pending='[]',error='',updated=? WHERE task_id=? AND wid=?",
                   (now(), task_id, wid))


def _finish_node(store, task_id, wid, stop_reason, error=''):
    with store.db() as db:
        db.execute("UPDATE trace_nodes SET status='done',stop_reason=?,error=?,updated=? WHERE task_id=? AND wid=?",
                   (stop_reason, error, now(), task_id, wid))


def _branch_stop(store, task_id, wid, category, message):
    """整轮停止时给当前分支留下「为什么停」：节点挂停止原因，行保留断点。"""
    stamp = now()
    with store.db() as db:
        db.execute('UPDATE trace_nodes SET stop_reason=?,updated=? WHERE task_id=? AND wid=? AND status!=?',
                   (category, stamp, task_id, wid, 'done'))
        db.execute("UPDATE trace_queue SET error=?,updated=? WHERE task_id=? AND wid=? AND status='pending'",
                   (message, stamp, task_id, wid))


def _edge(store, task_id, src, dst, depth):
    with store.db() as db:
        db.execute('INSERT INTO trace_edges VALUES(?,?,?,?,?) ON CONFLICT(task_id,src_wid,dst_wid) DO UPDATE'
                   ' SET depth=MIN(trace_edges.depth,excluded.depth),updated=excluded.updated',
                   (task_id, src, dst, depth, now()))


def _touch_node(store, task_id, target, depth_child, states, widmap, unlimited, limit):
    """创建/改善节点；min_depth 只减不增（更短路径后到时按最短深度重新判定）。

    改善时清除旧停止原因并置回 pending；深度 N 的子节点标 depth_limit。
    返回是否改善（改善的已入队节点会被调用方重新入队）。"""
    stamp = now()
    refs_state = states.get(target, '')
    with store.db() as db:
        row = db.execute('SELECT min_depth FROM trace_nodes WHERE task_id=? AND wid=?', (task_id, target)).fetchone()
        if row is None:
            db.execute('INSERT INTO trace_nodes VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (task_id, target, widmap.get(target), depth_child, refs_state, 'pending', '', '', 0, stamp))
            improved = True
        elif depth_child < row['min_depth']:
            db.execute('UPDATE trace_nodes SET min_depth=?,status=?,stop_reason=?,refs_state=CASE WHEN refs_state=?'
                       ' THEN ? ELSE refs_state END,paper_id=COALESCE(?,paper_id),updated=? WHERE task_id=? AND wid=?',
                       (depth_child, 'pending', '', '', refs_state, widmap.get(target), stamp, task_id, target))
            improved = True
        else:
            improved = False
    if improved and not (unlimited or depth_child < limit):
        _finish_node(store, task_id, target, 'depth_limit')
    return improved


def _enqueue_row(store, task_id, wid, depth):
    """入队展开单元；改善重入队时 REPLACE 重置游标（边幂等，可安全重走）。"""
    with store.db() as db:
        db.execute('INSERT OR REPLACE INTO trace_queue VALUES(?,?,?,?,?,?,?,?)',
                   (task_id, wid, depth, '*', '[]', 'pending', '', now()))


def _existing_rows(store, task_id, wids):
    if not wids:
        return set()
    with store.db() as db:
        rows = db.execute('SELECT wid FROM trace_queue WHERE task_id=? AND wid IN (%s)' % ','.join('?' * len(wids)),
                          (task_id, *wids)).fetchall()
    return {r[0] for r in rows}


def _settled(store, task_id, wid):
    """只有 missing_source 是「数据源已放弃」的定格；depth_limit 的节点仍在等
    批量响应补展示元数据，游标不得越过它。"""
    with store.db() as db:
        row = db.execute('SELECT status,stop_reason FROM trace_nodes WHERE task_id=? AND wid=?', (task_id, wid)).fetchone()
    return bool(row) and row[0] == 'done' and row[1] == 'missing_source'


def _save_row(store, task_id, wid, cursor, pending, error=''):
    with store.db() as db:
        db.execute('UPDATE trace_queue SET cursor=?,pending=?,error=?,updated=? WHERE task_id=? AND wid=?',
                   (cursor, json.dumps(pending, ensure_ascii=False), error, now(), task_id, wid))


def _ingest_results(store, task_id, data, papers, widmap):
    """批量响应入场：原始缓存 + 共享元数据 + 各命中节点的引用状态。"""
    returned = set()
    states = {}
    stamp = now()
    for hit in data.get('results', []):
        target = str(hit.get('id', '')).rstrip('/').rsplit('/', 1)[-1]
        if not target:
            continue
        returned.add(target)
        _cache_raw(store, target, hit, stamp)
        record = _absorb(store, hit, stamp, papers, widmap)
        states[target] = _refs_state_of(hit)
        with store.db() as db:
            db.execute('UPDATE trace_nodes SET refs_state=CASE WHEN refs_state=? THEN ? ELSE refs_state END,'
                       'paper_id=?,attempts=attempts+1,updated=? WHERE task_id=? AND wid=?',
                       ('', states[target], record['id'], stamp, task_id, target))
    return returned, states


def _process_row(store, task_id, row, client, papers, widmap):
    """处理一个展开单元：确保自身引用状态，再按窗口游标把引用变成边与子节点。"""
    wid = row['wid']
    depth = row['depth']
    try:
        task = _task(store, task_id)
        if not wid:
            wid = _fetch_own_work(store, task_id, client, row, task, papers, widmap)
            task = _task(store, task_id)
        node = _node(store, task_id, wid)
        if node and node['refs_state'] not in ('present', 'empty', 'missing'):
            _fetch_own_work(store, task_id, client, dict(row, wid=wid), task, papers, widmap)
            node = _node(store, task_id, wid)
        if node and node['refs_state'] == 'missing':
            _finish_node(store, task_id, wid, 'missing_source', node['error'] or '数据源缺引用字段')
            _finish_row(store, task_id, wid)
            return
        record = papers.get(widmap.get(wid), {})
        refs = record.get('refs') or [] if node and node['refs_state'] == 'present' else []
        unlimited = bool(task['unlimited'])
        limit = task['depth']
        cursor = 0 if row['cursor'] in ('', '*') else int(row['cursor'])
        pending = json.loads(row['pending'] or '[]')
        while cursor < len(refs):
            window = refs[cursor:cursor + WINDOW]
            need = [w for w in window if w not in widmap]
            states = {}
            if need:
                data, failure = _request(store, task_id, lambda: client.get(
                    'works', {'filter': 'openalex_id:' + '|'.join(need), 'per_page': 100}))
                if failure:
                    for target in need:
                        _settle_missing(store, task_id, target, depth + 1, failure[1], widmap)
                    pending = [p for p in pending if p['wid'] not in need]
                else:
                    returned, states = _ingest_results(store, task_id, data, papers, widmap)
                    for target in need:
                        if target in returned:
                            pending = [p for p in pending if p['wid'] != target]
                            continue
                        attempts = next((p['attempts'] for p in pending if p['wid'] == target), 0) + 1
                        pending = [p for p in pending if p['wid'] != target]
                        if attempts >= MAX_ATTEMPTS:
                            _settle_missing(store, task_id, target, depth + 1, '批量响应两次未返回该文献', widmap)
                        else:
                            pending.append({'wid': target, 'attempts': attempts})
            existing = _existing_rows(store, task_id, window)
            for target in window:
                if target == wid:
                    continue
                child = depth + 1
                _edge(store, task_id, wid, target, child)
                improved = _touch_node(store, task_id, target, child, states, widmap, unlimited, limit)
                expandable = unlimited or child < limit
                if states.get(target) == 'missing' and expandable:
                    _settle_missing(store, task_id, target, child, '数据源缺引用字段', widmap)
                    continue
                if target in widmap and expandable and (improved or target not in existing):
                    _enqueue_row(store, task_id, target, child)
            unresolved = [w for w in window if w != wid and w not in widmap and not _settled(store, task_id, w)]
            if unresolved:
                _save_row(store, task_id, wid, cursor, pending)
                return
            cursor += len(window)
            _save_row(store, task_id, wid, cursor, pending)
        _finish_row(store, task_id, wid)
        _finish_node(store, task_id, wid, 'no_upstream' if node and node['refs_state'] == 'empty' else '')
    except _StopRound as stop:
        _branch_stop(store, task_id, wid, stop.category, stop.message)
        raise
    except _PauseRequested:
        raise


def task_snapshot(store, task_id):
    """状态输出：本任务采用的层数、每篇沿哪条路径找到、未完成分支与停止原因。"""
    ensure_schema(store)
    with store.db() as db:
        row = db.execute('SELECT * FROM trace_tasks WHERE id=?', (task_id,)).fetchone()
        if not row:
            raise ValueError('追溯任务不存在')
        nodes = [dict(r) for r in db.execute('SELECT * FROM trace_nodes WHERE task_id=? ORDER BY min_depth,wid', (task_id,))]
        queue = [dict(r) for r in db.execute("SELECT wid,depth,cursor,pending,status,error,updated FROM trace_queue"
                                             " WHERE task_id=? AND status='pending' ORDER BY depth,wid", (task_id,))]
        edge_count = db.execute('SELECT COUNT(*) FROM trace_edges WHERE task_id=?', (task_id,)).fetchone()[0]
    task = dict(row)
    papers = store.papers()
    widmap = {p['openalex_id']: p['id'] for p in papers.values() if p.get('openalex_id')}
    adjacency = _adjacency(store, task_id)
    seed_wid = task['seed_wid']
    views = []
    for node in nodes:
        paper_id = node['paper_id'] or widmap.get(node['wid'])
        paper = papers.get(paper_id, {})
        title = paper.get('title') or ('待补全元数据 · ' + node['wid'] if node['wid'] else task['seed_label'])
        views.append({
            'wid': node['wid'], 'paper_id': paper_id, 'title': title, 'min_depth': node['min_depth'],
            'status': node['status'], 'stop_reason': node['stop_reason'],
            'note': STOP_NOTES.get(node['stop_reason'], ''), 'refs_state': node['refs_state'],
            'attempts': node['attempts'], 'error': node['error'],
            'paths': _paths(adjacency, seed_wid, node['wid']),
        })
    stopped = sum(1 for n in views if n['stop_reason'])
    return {
        'id': task_id, 'status': task['status'], 'depth': task['depth'],
        'unlimited': bool(task['unlimited']),
        'depth_display': '不限层数' if task['unlimited'] else f"{task['depth']} 层",
        'seed': {'paper_id': task['seed_paper_id'], 'wid': seed_wid, 'label': task['seed_label']},
        'requests': task['requests'], 'error': task['error'], 'pid': task['pid'],
        'created': task['created'], 'updated': task['updated'],
        'hint': HINTS.get(task['status'], ''),
        'counts': {'nodes': len(views), 'stopped': stopped, 'edges': edge_count, 'pending_branches': len(queue)},
        'queue': [{'wid': q['wid'], 'depth': q['depth'], 'error': q['error']} for q in queue],
        'nodes': views,
    }


def _adjacency(store, task_id):
    with store.db() as db:
        rows = db.execute('SELECT src_wid,dst_wid FROM trace_edges WHERE task_id=?', (task_id,)).fetchall()
    adjacency = {}
    for src, dst in rows:
        adjacency.setdefault(src, set()).add(dst)
    return adjacency


def _paths(adjacency, start, target):
    """种子到目标节点的全部真实引用路径（沿边反向即来源链）；环由在途集合挡住。"""
    if start == target:
        return [[start]]
    found = []

    def walk(node, trail, on_trail):
        if len(found) >= PATH_LIMIT:
            return
        for nxt in sorted(adjacency.get(node, ())):
            if nxt == target:
                found.append(trail + [nxt])
            elif nxt not in on_trail:
                walk(nxt, trail + [nxt], on_trail | {nxt})

    walk(start, [start], {start})
    return found


def source_paths(store, task_id, wid):
    """查询某篇文献沿哪些引用路径连回种子。"""
    snapshot = task_snapshot(store, task_id)
    node = next((n for n in snapshot['nodes'] if n['wid'] == wid), None)
    if node is None:
        raise ValueError('该文献不在本追溯任务的结果里')
    return {'task_id': task_id, 'wid': wid, 'seed': snapshot['seed'], 'paths': node['paths']}


def handle_start(store, paper, depth=None, unlimited=None):
    """CLI 处理函数：创建任务并立即推进一轮（接线阶段挂到 kb.py 的 trace 子命令）。"""
    created = start_task(store, paper, depth=depth, unlimited=unlimited)
    return run_task(store, created['id'])


def handle_status(store, task_id):
    return task_snapshot(store, task_id)


def handle_list(store, limit=30):
    """最近任务的轻量列表：只取任务行摘要与两条 COUNT 聚合。

    与 task_snapshot（--id 单任务全量快照）的差别：不全量加载 papers、不建
    邻接表、不逐节点 DFS 找路径——大库 × 多任务下列表保持常量级查询。
    输出键与快照的投影完全一致，一致性由 tests/test_trace_cli.py 锚定。"""
    ensure_schema(store)
    with store.db() as db:
        rows = db.execute('SELECT id,status,depth,unlimited,seed_paper_id,seed_wid,seed_label,'
                          'requests,error,created,updated FROM trace_tasks '
                          'ORDER BY created DESC,rowid DESC LIMIT ?', (limit,)).fetchall()
        nodes = dict(db.execute('SELECT task_id,COUNT(*) FROM trace_nodes GROUP BY task_id').fetchall())
        stopped = dict(db.execute("SELECT task_id,COUNT(*) FROM trace_nodes WHERE stop_reason!=''"
                                  ' GROUP BY task_id').fetchall())
        edges = dict(db.execute('SELECT task_id,COUNT(*) FROM trace_edges GROUP BY task_id').fetchall())
        pending = dict(db.execute("SELECT task_id,COUNT(*) FROM trace_queue WHERE status='pending'"
                                  ' GROUP BY task_id').fetchall())
    tasks = []
    for row in rows:
        task = dict(row)
        tasks.append({
            'id': task['id'], 'status': task['status'],
            'depth_display': '不限层数' if task['unlimited'] else f"{task['depth']} 层",
            'seed': {'paper_id': task['seed_paper_id'], 'wid': task['seed_wid'], 'label': task['seed_label']},
            'requests': task['requests'], 'error': task['error'],
            'hint': HINTS.get(task['status'], ''),
            'created': task['created'], 'updated': task['updated'],
            'counts': {'nodes': nodes.get(task['id'], 0), 'stopped': stopped.get(task['id'], 0),
                       'edges': edges.get(task['id'], 0), 'pending_branches': pending.get(task['id'], 0)},
        })
    return {'tasks': tasks}


def handle_pause(store, task_id):
    return pause_task(store, task_id)


def handle_resume(store, task_id):
    return resume_task(store, task_id)


def handle_paths(store, task_id, wid):
    return source_paths(store, task_id, wid)
