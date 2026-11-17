"""kbapp.research.candidates：分通道候选发现（F01–F05）。

通道：引用邻居、题摘相似、方法别名、共引/文献耦合。
各通道保留来源与得分，按配额合并，按得分下限与预算分层停止，
避免高被引或同主题论文垄断候选，也避免全库两两比较。
"""
from __future__ import annotations
from collections import defaultdict
import json
from inspect import Parameter, signature
from kbapp.search.ranking import rrf_multi

QUOTAS = {'citation': 12, 'similar': 8, 'method': 8, 'co_citation': 6, 'context': 8}
CHANNEL_LABELS = {'citation': '引用邻居', 'similar': '题摘向量近邻', 'method': '方法别名检索', 'co_citation': '共引或文献耦合', 'context': '正文引用语境'}


def _method_queries(store, paper_id):
    with store.db() as db:
        rows = db.execute('SELECT m.aliases FROM method_concepts m JOIN paper_methods p ON m.id=p.method_id WHERE p.paper_id=?', (paper_id,)).fetchall()
    return [' '.join(json.loads(row[0])) for row in rows[:3] if row[0]]


def citation_channel(store, paper, frozen):
    results = []
    for other, count in frozen['neighbors'].get(paper['id'], {}).items():
        results.append({'paper_id': other, 'channel': 'citation', 'score': 3.0 * count, 'reason': '引用邻居'})
    return results


def similar_channel(store, paper, frozen, should_stop):
    engine = frozen['search']
    results = []
    query = paper.get('title', '')
    if not query:
        return results
    if should_stop():
        raise ValueError('任务已停止')
    return _search_candidates(engine, query, paper['id'], 'similar', 40, 1.0)


def _search_candidates(engine, query, own_id, channel, k, weight):
    # Request paper-level results so repeated passages cannot exhaust the search budget.
    parameters = signature(engine.search).parameters
    paper_level = 'level' in parameters or any(p.kind == Parameter.VAR_KEYWORD for p in parameters.values())
    data = engine.search(query, k=k, wait_vectors=True, **({'level': 'paper'} if paper_level else {}))
    mode = data.get('mode', '')
    retrieval = 'BM25关键词' if mode == 'bm25' else '向量与关键词融合' if 'vector' in mode else '检索模式未报告'
    label = ('题摘近邻' if channel == 'similar' else '方法别名检索') + '（' + retrieval + '）'
    seen = {own_id}
    results = []
    for result in data['results']:
        pid = result.get('paper_id')
        if not pid or pid in seen:
            continue
        seen.add(pid)
        results.append({'paper_id': pid, 'channel': channel, 'score': weight / (1 + len(results)), 'reason': label})
    return results


def method_channel(store, paper, frozen, should_stop):
    engine = frozen['search']
    results = []
    for query in _method_queries(store, paper['id']):
        if should_stop():
            raise ValueError('任务已停止')
        results.extend(_search_candidates(engine, query, paper['id'], 'method', 30, 1.5))
    return results


def co_citation_channel(store, paper):
    """共引（同被一篇引用）与文献耦合（共同引用同一文献）。"""
    with store.db() as db:
        co_cited = db.execute("""SELECT b.dst, COUNT(DISTINCT a.src) FROM relations a
            JOIN relations b ON b.src=a.src AND b.kind='cites' AND b.status='confirmed'
            WHERE a.kind='cites' AND a.status='confirmed' AND a.dst=? AND b.dst!=?
            GROUP BY b.dst ORDER BY b.dst""", (paper['id'], paper['id'])).fetchall()
        coupling = db.execute("""SELECT b.src, COUNT(DISTINCT a.dst) FROM relations a
            JOIN relations b ON b.dst=a.dst AND b.kind='cites' AND b.status='confirmed'
            WHERE a.kind='cites' AND a.status='confirmed' AND a.src=? AND b.src!=?
            GROUP BY b.src ORDER BY b.src""", (paper['id'], paper['id'])).fetchall()
    results = []
    for other, count in co_cited:
        results.append({'paper_id': other, 'channel': 'co_citation', 'score': 0.8 * count, 'reason': '共引'})
    for other, count in coupling:
        results.append({'paper_id': other, 'channel': 'co_citation', 'score': 0.5 * count, 'reason': '文献耦合'})
    return results


def context_channel(store, paper):
    """F02：正文中指向目标论文的引用语境（含方法段与贡献段）作为候选通道。"""
    with store.db() as db:
        rows = db.execute("SELECT r.target_paper_id, COUNT(*) FROM citation_contexts c JOIN document_references r ON r.id=c.reference_id JOIN documents d ON d.id=c.document_id WHERE d.paper_id=? AND r.target_paper_id IS NOT NULL GROUP BY r.target_paper_id", (paper['id'],)).fetchall()
    return [{'paper_id': row[0], 'channel': 'context', 'score': 1.2 + 0.2 * row[1], 'reason': '正文引用语境'} for row in rows if row[0] != paper['id']]


def explain(candidate, assessment=None):
    """F10：候选可读说明：为何进入、检查到哪里、下一步缺什么。"""
    reasons = candidate.get('candidate_reasons') or []
    why = '进入原因：' + '、'.join(reasons) if reasons else '进入原因：未记录'
    checked = '检查到：尚未开始核验'
    nxt = '下一步：选择原文证据后核验方法关系'
    if assessment:
        status = assessment.get('verification_status') or assessment.get('status') or ''
        labels = {'verified': '已通过核验', 'insufficient': '证据不足，待补证', 'unsupported': '证据反驳', 'conflict': '模型判断冲突',
                  'partial': '方法关系成立，数字/条件待核', 'ready': '证据已就绪，等待核验', 'structure_error': '结构错误，需修复后重试'}
        checked = '检查到：' + labels.get(status, status or '未记录')
        gaps = assessment.get('gaps') or []
        if gaps:
            nxt = '下一步：' + '；'.join(str(g) for g in gaps[:3])
        elif status == 'insufficient':
            nxt = '下一步：补充双方原文证据或全文'
    return {'why': why, 'checked': checked, 'next': nxt}


def collect(store, paper, frozen, limit=24, quotas=None, min_score=0.05, should_stop=lambda: False):
    """按通道配额合并候选，返回 (候选列表, 停止原因)。"""
    quotas = dict(QUOTAS, **(quotas or {}))
    channels = {
        'citation': citation_channel(store, paper, frozen),
        'similar': similar_channel(store, paper, frozen, should_stop),
        'method': method_channel(store, paper, frozen, should_stop),
        'co_citation': co_citation_channel(store, paper),
        'context': context_channel(store, paper),
    }
    reasons = defaultdict(set)
    picked_channels = defaultdict(set)
    ranks = {}
    truncated = below_floor = False
    for name, results in channels.items():
        unique = {}
        channel_reasons = defaultdict(set)
        for result in results:
            pid = result.get('paper_id')
            if not pid or pid == paper['id']:
                continue
            unique[pid] = max(unique.get(pid, float('-inf')), result['score'])
            channel_reasons[pid].add(result['reason'])
        eligible = sorted((pid for pid in unique if unique[pid] >= min_score), key=lambda pid: (-unique[pid], pid))
        quota = max(0, int(quotas.get(name, 0)))
        below_floor |= bool(quota and len(eligible) < len(unique))
        truncated |= len(eligible) > quota
        ranks[name] = [(pid, unique[pid]) for pid in eligible[:quota]]
        for pid, _ in ranks[name]:
            reasons[pid].update(channel_reasons[pid])
            picked_channels[pid].add(name)
    # Use one identity namespace across lists: rrf_multi otherwise keeps channels separate.
    fused = {pid: score for (_, pid), score in rrf_multi([('paper', rows) for rows in ranks.values()])}
    ordered = sorted(fused, key=lambda pid: (-fused[pid], pid))
    budget = max(0, int(limit))
    active = [rows for rows in ranks.values() if rows]
    # Reserve up to half the budget evenly across nonempty channels, then fill by RRF.
    # Round-robin also handles budgets smaller than the number of channels deterministically.
    reserve = max(1, budget // max(1, 2 * len(active)))
    selected = set()
    for rank in range(reserve):
        for rows in active:
            if len(selected) < budget and rank < len(rows):
                selected.add(rows[rank][0])
    for pid in ordered:
        if len(selected) >= budget:
            break
        selected.add(pid)
    stop_reason = 'budget' if len(ordered) > budget else 'quota' if truncated else 'score_floor' if below_floor else 'exhausted'
    return [{'paper_id': pid, 'score': round(fused[pid], 6), 'reasons': sorted(reasons[pid]),
             'channels': sorted(picked_channels[pid])} for pid in ordered if pid in selected], stop_reason
