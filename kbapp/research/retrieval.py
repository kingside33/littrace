"""kbapp.research.retrieval：由职责边界组织的知识库能力。"""
import json
import threading
import time
from kbapp.core.io import digest
from kbapp.search.query import source_quality
_engine_lock = threading.Lock()

def evidence_search(store, query, filters=None, complete=None, should_stop=lambda: False):
    """最多4路问题、40个重排片段、16条证据；原始出处始终保留。"""
    from kbapp.search.indexing import Search
    started = time.perf_counter()
    with _engine_lock:
        if not hasattr(store, '_research_search'):
            store._research_search = threading.local()
    if not hasattr(store._research_search, 'engine'):
        store._research_search.engine = Search(store)
    engine = store._research_search.engine
    questions = [query]
    gaps = []
    timings = {}
    stage = time.perf_counter()
    if complete:
        plan = complete(store, '将研究问题拆为最多4个互补检索表达，保留英文方法名、别名和中英文表达；不增加问题未要求的事实。返回 {queries:[字符串]}。\n' + query, 'interactive_plan', max_tokens=800)
        questions = list(dict.fromkeys([query] + [q for q in plan.get('queries', []) if isinstance(q, str) and q.strip()]))[:4]
    timings['query_expansion'] = round((time.perf_counter() - stage) * 1000, 3)
    stage = time.perf_counter()
    merged = {}
    traces = []
    paper_scores = {}
    for question in questions:
        if should_stop():
            raise ValueError('任务已停止')
        found = engine.search(question, k=40, wait_vectors=True, **filters or {})
        traces.append({'query': question, 'mode': found['mode'], 'warning': found['warning'], 'vector_status': found.get('vector_status'), 'timings_ms': found.get('timings_ms', {})})
        for rank, src in enumerate(found['results']):
            score = source_quality(src) / (30 + rank)
            sid = src['id']
            pid = src.get('paper_id', '')
            paper_scores[pid] = max(paper_scores.get(pid, 0), score)
            if sid not in merged:
                merged[sid] = dict(src, retrieval_score=0)
            merged[sid]['retrieval_score'] += score
    timings['retrieval'] = round((time.perf_counter() - stage) * 1000, 3)
    stage = time.perf_counter()
    paper_ids = sorted(paper_scores, key=paper_scores.get, reverse=True)[:20]
    papers = store.papers()
    from kbapp.research.viewmodels import relation_grade
    near = set()
    verified_near = set()
    for edge in store.relations():
        if edge['status'] == 'rejected' or edge['kind'] not in {'inherits', 'improves', 'combines', 'transfers'}:
            continue
        neighbors = []
        if edge['src'] in paper_ids:
            neighbors.append(edge['dst'])
        if edge['dst'] in paper_ids:
            neighbors.append(edge['src'])
        near.update(neighbors)
        if relation_grade(edge) in {'fulltext', 'abstract'}:
            verified_near.update(neighbors)
    scoped = bool((filters or {}).get('project') or (filters or {}).get('direction'))
    for pid in sorted(near - set(paper_ids), key=lambda pid: (pid not in verified_near if scoped else False, pid))[:8]:
        if filters and filters.get('paper') and (store.resolve(filters['paper']) != pid):
            continue
        candidate_filters = dict(filters or {}, paper=pid)
        context = pid in verified_near and bool(candidate_filters.get('project') or candidate_filters.get('direction'))
        if context:
            candidate_filters.pop('project', None)
            candidate_filters.pop('direction', None)
        for src in engine.search(query, k=2, vectors=False, **candidate_filters)['results']:
            merged.setdefault(src['id'], dict(src, retrieval_score=source_quality(src) / 80, graph_neighbor=True, scope_context=context, selection_reason='按已分级方法关系保留的前驱、后继或桥接证据' if context else '相关论文的图谱邻居'))
    timings['graph_neighbors'] = round((time.perf_counter() - stage) * 1000, 3)
    stage = time.perf_counter()
    seen = set()
    candidates = []
    for src in sorted(merged.values(), key=lambda r: -r['retrieval_score']):
        identity = (src.get('paper_id'), digest(src['text']))
        if identity in seen or source_quality(src) == 0:
            continue
        seen.add(identity)
        candidates.append(src)
        if len(candidates) == 40:
            break
    ranked = candidates
    if candidates and complete:
        prompt = '按研究问题相关性与证据质量独立重排。论文事实优先原文，摘要只能支持摘要层结论；排除无关片段、参考文献表和派生笔记。返回 {ranking:[{source_id:字符串,relevant:布尔值,reason:字符串}]}，最多16条。\n' + json.dumps({'question': query, 'sources': [{k: r.get(k) for k in ('id', 'paper_id', 'kind', 'origin', 'section', 'text')} for r in candidates]}, ensure_ascii=False)
        order = complete(store, prompt, 'interactive_rerank', max_tokens=2000)
        by_id = {s['id']: s for s in candidates}
        ranked = []
        chosen = set()
        for item in order.get('ranking', []):
            sid = item.get('source_id') if isinstance(item, dict) else None
            if sid in by_id and sid not in chosen and (item.get('relevant') is True):
                ranked.append(dict(by_id[sid], rerank_reason=item.get('reason', '')))
                chosen.add(sid)
        if not ranked:
            gaps.append('重排未找到与问题直接相关的证据')
    timings['merge_and_rerank'] = round((time.perf_counter() - stage) * 1000, 3)
    timings['total'] = round((time.perf_counter() - started) * 1000, 3)
    evidence = ranked[:16]
    vector_modes = ['vector' in trace['mode'] for trace in traces]
    mode = 'two-stage-hybrid' if all(vector_modes) else 'two-stage-mixed' if any(vector_modes) else 'two-stage-bm25'
    return {'query': query, 'mode': mode + ('+rerank' if complete else ''), 'results': evidence, 'papers': [{'id': pid, 'title': papers.get(pid, {}).get('title', ''), 'score': paper_scores[pid]} for pid in paper_ids], 'process': traces, 'subquestions': questions, 'reranked': len(candidates), 'warning': '；'.join(dict.fromkeys((t['warning'] for t in traces if t['warning']))), 'gaps': gaps, 'coverage': found.get('coverage', {}), 'timings_ms': timings}
