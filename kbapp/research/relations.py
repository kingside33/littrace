"""kbapp.research.relations：由职责边界组织的知识库能力。"""
from __future__ import annotations
import json
import re
from kbapp.research.rules import validate_ai_relation
from kbapp.core.io import digest, now, jsonl
from kbapp.core.markers import PROJECT
from kbapp.integrations.cloud import complete
from kbapp.core.evidence_rules import fully_supported

def review_relations(store, paper, papers, pair_refs, strong, progress, sources=None, should_stop=lambda: False):
    from kbapp.research.review import review_relation
    from kbapp.integrations.local_model import complete_local
    verdicts = {}
    sources = sources or {}
    for item in strong:
        progress('云端独立复核：' + paper['title'][:60])
        review = review_relation(store, item, sources, papers.get(item['dst'], {}), pair_refs.get(item['dst'], []), complete, complete_local, should_stop)
        verdict = dict(review.get('cloud') or {}, supported=review['accepted'], verification=review)
        verdicts[item['review_index']] = verdict
    return verdicts

def ask(store, query, filters=None):
    from kbapp.research.qa import answer
    return answer(store, query, filters)

def analyze_relations(store, progress=lambda x: None, paper=None, *, filters=None, on_status=None, should_stop=lambda: False, locked=False, completed_ids=None):
    if not store.settings()['cloud_enabled']:
        return {'state': 'awaiting_configuration', 'processed': 0}
    options = dict(filters=filters, on_status=on_status, should_stop=should_stop, completed_ids=completed_ids)
    if store.settings().get('relation_extractor') != 'local':
        return _analyze_relations(store, progress, paper, **options)
    import contextlib
    import os
    from kbapp.core.locks import update_lock
    from kbapp.integrations.models import release_local
    with store.db() as db:
        own_update = db.execute("SELECT 1 FROM jobs WHERE kind='update' AND status='running' AND pid=?", (os.getpid(),)).fetchone()
    guard = contextlib.nullcontext() if own_update or locked else update_lock(store.root)
    with guard:
        try:
            return _analyze_relations(store, progress, paper, **options)
        finally:
            release_local(store)

def relation_scope_matches(paper, filters=None):
    filters = filters or {}
    if filters.get('project') and (not paper.get('project')):
        return False
    if filters.get('direction') and filters['direction'] not in paper.get('directions', []):
        return False
    query = (filters.get('q') or '').strip().casefold()
    text = ' '.join((str(paper.get(k) or '') for k in ('title', 'citekey', 'authors')))
    return not query or query in text.casefold()

def relation_sources(docs):
    sources = []
    for section in ('Intro', 'Introduction', 'Related', 'Method', 'Front'):
        sources.extend([r for r in docs if r['kind'] == 'paper_text' and r.get('section') == section][:2])
    extra = sorted([r for r in docs if r['kind'] == 'paper_text'], key=lambda r: (not bool(re.search('we (?:extend|build|adopt|combine|improve)|based on|本文.{0,10}(?:改进|扩展|融合)', r['text'], re.I)), -len(re.findall('\\[\\d', r['text']))))
    return list({r['id']: r for r in sources + extra[:14]}.values())[:14] or docs[:4]

def _analyze_relations(store, progress=lambda x: None, paper=None, *, filters=None, on_status=None, should_stop=lambda: False, completed_ids=None):
    cfg = store.settings()
    if not cfg['cloud_enabled']:
        return {'state': 'awaiting_configuration', 'processed': 0}
    corpus = jsonl(store.active_dir() / 'corpus.jsonl')
    if not corpus:
        raise ValueError('统一来源索引尚未发布')
    papers = store.papers()
    grouped = {}
    for r in corpus:
        if r.get('paper_id') and r['kind'] in {'paper_text', 'metadata', 'external_abstract'}:
            grouped.setdefault(r['paper_id'], []).append(r)
    candidate_grouped = dict(grouped)
    rels = store.relations()
    neighbors = {}
    for r in rels:
        if r['kind'] == 'cites':
            neighbors.setdefault(r['src'], []).append(r['dst'])

    def priority(p):
        tf = p.get('tagfields') or {}
        core = any((x in (tf.get('priority') or []) for x in ('A', 'B'))) or any((x in (tf.get('reading_tier') or []) for x in ('Core-A', 'Core-B')))
        return (not core, not bool(p.get('project')), -(p.get('year') or 0), str(p.get('id') or ''))
    buckets = {}
    target = store.resolve(paper) if paper else None
    for p in sorted(papers.values(), key=priority):
        if target and p['id'] != target:
            continue
        if not relation_scope_matches(p, filters):
            continue
        docs = grouped.get(p['id'], [])
        if not docs or not neighbors.get(p['id']):
            continue
        full = any((r['kind'] == 'paper_text' for r in docs))
        if p.get('kind') != 'library' and (not full) or (not full and len(p.get('abstract') or '') < 150):
            continue
        buckets.setdefault(p.get('primary_direction') or '90', []).append(p)
    order = [d for d in ('02', '03', '04', '05', '01', '06', '07', '08', '00', '09', '90') if d in buckets]
    order.extend((d for d in buckets if d not in order))
    queue = []
    while any(buckets.values()):
        for d in order:
            if buckets[d]:
                queue.append(buckets[d].pop(0))
    total = len(queue)
    batched = completed_ids is not None
    completed_before = {p['id'] for p in queue if p['id'] in (completed_ids or set())}
    batch_size = max(1, min(12, int(cfg['cloud_requests_per_run'])))
    batch = [p for p in queue if p['id'] not in completed_before]
    if batched:
        batch = batch[:batch_size]
    processed = 0
    reused = 0
    batch_completed = []

    def status(state='running', **extra):
        done = len(completed_before) + len(batch_completed)
        result = dict(state=state, processed=processed, reused=reused, total=total, completed=done, pending=total - done, batch_completed_ids=list(batch_completed), **extra)
        if on_status:
            on_status(result)
        return result
    status()
    if cfg.get('relation_extractor') == 'local' and batch:
        from kbapp.library.recognition import prepare_relation_sources

        def ocr_status(**values):
            if 'ocr_papers_done' in values:
                values['ocr_papers_done'] += len(completed_before)
            if 'ocr_papers_total' in values:
                values['ocr_papers_total'] = total
            status(**values)
        try:
            prepare_relation_sources(store, batch, grouped, relation_sources, progress, should_stop, ocr_status)
        except ValueError as exc:
            return status('interrupted' if should_stop() else 'deferred', reason=str(exc))
    for p in batch:
        if should_stop():
            return status('interrupted', reason='核验已停止，完成的证据与缓存已保留')
        docs = grouped[p['id']]
        sources = relation_sources(docs)
        corpus_text = '\n'.join((r['text'] for r in sources))
        pair_refs = {}
        for r in rels:
            if r['src'] == p['id'] and r['kind'] == 'cites':
                pair_refs[r['dst']] = [str(e['ref_index']) for e in r.get('evidence', []) if e.get('ref_index')]
        candidate_ids = sorted(set(neighbors[p['id']]), key=lambda pid: (not any((re.search('\\[[^\\]]*\\b' + idx + '\\b[^\\]]*\\]', corpus_text) for idx in pair_refs.get(pid, []))), priority(papers.get(pid, {}))))[:24]
        sources += [r for x in candidate_ids[:6] for r in relation_sources(candidate_grouped.get(x, []))[:2]]
        sources += [r for x in candidate_ids[:6] for r in candidate_grouped.get(x, []) if r['kind'] in {'metadata', 'external_abstract'}][:6]
        sources = list({r['id']: r for r in sources}.values())
        metadata = [{k: papers.get(x, {}).get(k) for k in ('id', 'title', 'authors', 'year', 'doi')} for x in [p['id']] + candidate_ids]
        extractor = cfg['local_text_model'] + str(cfg['local_text_context']) if cfg.get('relation_extractor') == 'local' else cfg['model']
        fp = digest(cfg['model'] + extractor + 'relation-v10-bilateral' + json.dumps([(r['id'], r['text_hash']) for r in sources]) + json.dumps(candidate_ids) + json.dumps(metadata))
        with store.db() as db:
            old = db.execute('SELECT fingerprint FROM analyses WHERE id=?', ('relations:' + p['id'],)).fetchone()
        if old and old[0] == fp:
            reused += 1
            batch_completed.append(p['id'])
            status()
            continue
        progress('分析研究关系：' + p['title'][:60])
        def public(paper):
            return {k: paper.get(k) for k in ('id', 'title', 'authors', 'year', 'doi', 'abstract')}
        prompt = '分析主论文与候选前作的具体关系。每次最多提出4条有具体依据的关系，宁缺毋滥。src必须是主论文id，dst必须为候选id。kind仅可为inherits/improves/combines/transfers/compares/similar/parallel/contradicts。必须各引用一段双方原文，缺少任何一方须请求全文并保留证据不足。explanation、previous_problem、change、conditions用中文，evidence逐字引用来源中最短的完整证据句，每段引用不超过200个字符。返回 {relations:[{src,dst,kind,explanation,previous_problem,change,conditions,evidence:[{source_id,quote}]}],fulltext_requests:[{paper_id,reason,question}]}。摘要不足以证实重要关系时申请全文，最多3篇。不要虚构关系。\n' + json.dumps({'paper': public(p), 'candidates': [public(papers[x]) for x in candidate_ids if x in papers], 'citation_reference_indices': pair_refs, 'sources': [{k: r.get(k) for k in ('id', 'text', 'kind', 'paper_id', 'section')} for r in sources]}, ensure_ascii=False)
        try:
            if cfg.get('relation_extractor') == 'local':
                from kbapp.research.local_verification import verify_relations
                status(stage='local_verification')
                result = verify_relations(store, p, papers, candidate_ids, pair_refs, sources, progress)
            else:
                result = complete(store, prompt, 'relations:' + p['id'], max_tokens=6000)
        except ValueError as exc:
            return status('interrupted' if should_stop() else 'deferred', reason=str(exc))
        if should_stop():
            return status('interrupted', reason='核验已停止，完成的提取缓存已保留')
        valid = []
        for item in result.get('relations', [])[:32]:
            if item.get('src') != p['id'] or item.get('dst') not in candidate_ids:
                continue
            try:
                valid.append(validate_ai_relation(item, {r['id']: r for r in sources}, papers, [r for r in rels if r['src'] == item['src'] and r['dst'] == item['dst'] and (r['kind'] == 'cites')]))
            except (ValueError, KeyError, TypeError):
                continue
        deferred_review = None
        local_mode = cfg.get('relation_extractor') == 'local'
        for item in valid:
            verdict = item.get('local_review_verdict', {})
            local_ok = not local_mode or (fully_supported(verdict) and verdict.get('method_relation') is True and (verdict.get('unsupported_details') == []))
            item['local_review_passed'] = local_ok if local_mode else None
            if local_mode and (not local_ok):
                item['eligible_for_review'] = item['status'] == 'confirmed' or bool(item.get('eligible_for_review'))
                item['status'] = 'candidate'

        def proposal_rank(item):
            ready = item['status'] == 'confirmed' or item.get('eligible_for_review')
            return (not ready, item['kind'] not in {'inherits', 'improves', 'combines', 'transfers'}, item.get('level') != 'fulltext')
        unique = {}
        for item in sorted(valid, key=proposal_rank):
            unique.setdefault((item['src'], item['dst'], item['kind']), item)
        valid = list(unique.values())[:4]
        strong = [dict(item, review_index=i) for i, item in enumerate(valid) if item['status'] == 'confirmed' or item.get('eligible_for_review')]
        if strong:
            try:
                status(stage='cloud_review')
                progress('DeepSeek 独立复核：' + p['title'][:60])
                verdicts = review_relations(store, p, papers, pair_refs, strong, progress, {s['id']: s for s in sources}, should_stop)
            except ValueError as exc:
                verdicts = {}
                deferred_review = str(exc)
                progress('独立复核暂未完成，关系保留为候选')
            for i, item in enumerate(valid):
                if item['status'] == 'confirmed' or item.get('eligible_for_review'):
                    verdict = verdicts.get(i, {})
                    complete_review = verdict.get('method_relation') is True and verdict.get('unsupported_details') == []
                    item['status'] = 'confirmed' if item.get('bilateral_evidence') and complete_review and fully_supported(verdict) else 'candidate'
                    if item['status'] == 'confirmed' and item.get('review_context'):
                        item['evidence'] = item['review_context']
                    item['independent_review'] = verdict.get('reason') or '独立复核未通过或尚未完成'
                    item['review_verdict'] = verdict
                    item['verification'] = verdict.get('verification')
                    item['reviewed_by'] = cfg['local_text_model'] + ' 本地核验；' + cfg['model'] + ' 独立复核' if local_mode else cfg['model'] + '：分析及第二次调用复核'
        if cfg.get('relation_extractor') == 'local':
            for item in valid:
                recognized = any((e.get('ocr_provenance', {}).get('model') for e in item['evidence']))
                item.update(extracted_by='GLM-OCR' if recognized else '文献元数据/摘要', local_reviewed_by=cfg['local_text_model'], pipeline_version='evidence-review-v2', pipeline_legacy=False)
        for item in valid:
            store.relation(item)
        for req in result.get('fulltext_requests', [])[:3]:
            target = papers.get(req.get('paper_id'))
            if target and target.get('kind') != 'library' and (target['id'] in candidate_ids):
                store.request_fulltext(target['id'], req.get('reason', '需要核实关键研究关系'), req.get('question', '核实具体继承与改进'))
        if deferred_review:
            return status('interrupted' if should_stop() else 'deferred', reason=deferred_review)
        with store.db() as db:
            db.execute('INSERT OR REPLACE INTO analyses VALUES(?,?,?,?)', ('relations:' + p['id'], fp, json.dumps(result, ensure_ascii=False), now()))
        processed += 1
        batch_completed.append(p['id'])
        status()
        if not batched and processed >= cfg['cloud_requests_per_run']:
            break
    return status('interrupted' if should_stop() else 'complete')

def opportunities(store):
    rels = [r for r in store.relations('confirmed') if r['kind'] != 'cites'][:50]
    if not rels:
        return {'items': [], 'message': '尚无足够已确认研究关系；可先审核候选关系或配置模型分析。'}
    fp = digest(json.dumps(rels, ensure_ascii=False))
    old = store.state('opportunities')
    if old and old.get('fingerprint') == fp:
        return old
    result = complete(store, '围绕' + PROJECT + '，在当前证据范围内提出可验证研究机会。全部标记待验证。返回 {items:[{title,hypothesis,evidence_ids:[关系id],missing_evidence,validation}]}。仅可引用提供的关系id。\n' + json.dumps(rels, ensure_ascii=False), 'research_opportunities')
    valid = {r['id'] for r in rels}
    items = []
    for item in result.get('items', []):
        ids = item.get('evidence_ids', [])
        if ids and all((x in valid for x in ids)):
            items.append(dict(item, status='待验证', evidence=[r for r in rels if r['id'] in ids]))
    output = {'items': items, 'fingerprint': fp, 'updated': now()}
    store.set_state('opportunities', output)
    return output
