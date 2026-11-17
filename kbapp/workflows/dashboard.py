"""kbapp.workflows.dashboard：由职责边界组织的知识库能力。"""
import json
from datetime import datetime
from kbapp.core.io import now
from kbapp.workflows.role_activity import views as role_views, handoffs
from kbapp.workflows.dashboard_phases import normalize_kind, summaries as phase_summaries, PHASES
ACTIVE = {'queued', 'running', 'stopping'}
TITLES = {'maintenance': '全文理解与笔记维护', 'lineage': '方法谱系补全', 'search': '证据检索', 'answer': '逐项核验问答', 'compare': '论文比较', 'verification': '研究关系核验', 'verify': '研究关系核验', 'relations': '研究关系核验', 'update': '完整更新知识库', 'vectors': '向量索引'}
FLOW = {'maintenance': ['coordinator', 'reader', 'writer', 'verifier', 'cloud_reviewer', 'lineage_researcher'], 'lineage': ['coordinator', 'retriever', 'lineage_researcher', 'verifier', 'cloud_reviewer'], 'answer': ['coordinator', 'retriever', 'writer', 'verifier', 'cloud_reviewer'], 'compare': ['coordinator', 'retriever', 'writer', 'verifier', 'cloud_reviewer'], 'search': ['coordinator', 'retriever'], 'verification': ['coordinator', 'ocr', 'verifier', 'cloud_reviewer'], 'verify': ['coordinator', 'ocr', 'verifier', 'cloud_reviewer'], 'update': ['coordinator', 'ocr', 'indexer', 'lineage_researcher', 'verifier', 'cloud_reviewer'], 'vectors': ['coordinator', 'indexer']}
FLOW['relations'] = FLOW['verification']

def decode(value):
    try:
        return json.loads(value or '{}')
    except (ValueError, TypeError):
        return {}

def job_outcome(kind, status, payload):
    """M09：完成/中断的任务给出结果摘要、跳过原因与剩余缺口，不用进度占位文案代替结果。"""
    summary, skipped, gaps = None, [], []
    coverage = payload.get('coverage') or {}
    if status in {'complete', 'partial', 'interrupted'}:
        if kind in {'update', 'vectors'} and coverage:
            sources, vectors = coverage.get('sources'), coverage.get('vector_count')
            if sources is not None:
                summary = f"已发布 {sources:,} 条来源" + (f" · 向量 {vectors:,} 条" if vectors is not None else '')
            pending = coverage.get('vector_pending') or 0
            if pending:
                gaps.append(f'还有 {pending:,} 条来源待向量化，可继续索引')
            skipped.extend(str(w) for w in (coverage.get('warnings') or [])[:5])
            analysis = payload.get('analysis') or {}
            if analysis.get('state') == 'deferred':
                gaps.append('关系分析未执行：' + (analysis.get('reason') or '等待后续运行'))
            elif analysis.get('state') == 'not_requested':
                skipped.append('本轮未请求关系分析')
        elif kind in {'verification', 'verify', 'relations'}:
            confirmed = payload.get('confirmed_added')
            if confirmed is not None:
                summary = f'新增已确认关系 {confirmed} 条'
            if payload.get('skipped'):
                skipped.append(str(payload['skipped']))
    return summary, skipped, gaps

def run_outcome(result):
    summary, skipped, gaps = None, [], []
    data = result.get('summary') or {}
    counts = data.get('verification_counts') or {}
    if counts:
        summary = f"已通过 {counts.get('verified', 0)} 条 · 证据不足 {counts.get('insufficient', 0)} 条 · 反驳 {counts.get('unsupported', 0)} 条"
        pending = data.get('pending_reasons') or {}
        for key in ('insufficient', 'unsupported', 'conflict', 'deferred'):
            if counts.get(key) and pending.get(key):
                gaps.append(f'{counts[key]} 条：{pending[key]}')
    failures = (data.get('unit_execution_counts') or {}).get('failed')
    if failures:
        skipped.append(f'{failures} 个单元执行失败，结果保留可续跑')
    return summary, skipped, gaps

def attention(run):
    error = run['error'] or ''
    if run['status']=='failed' and decode(run['result']).get('halt_error') and '云端调用' in error:
        if 'HTTP 429' in error:
            return {'kind':'cloud_rate_limit', 'label':'服务限流 · 待续跑', 'message':'云端服务返回 HTTP 429，任务已暂停', 'hint':'完成结果与缓存已保留。服务解除限流后点击“续跑”，继续原任务；未自动重复请求。'}
        if any(word in error for word in ('结构不完整','长度上限')):
            return {'kind':'cloud_output', 'label':'输出异常 · 待续跑', 'message':'云端返回未通过完整性检查，任务已暂停', 'hint':'已返回内容和完成结果已保留。查看执行记录后可续跑当前任务；不将异常输出当作有效结论。'}
    if run['status']=='failed' and decode(run['result']).get('halt_error') and '云端调用' in error and any(word in error for word in ('URLError','TimeoutError','ConnectionError','连接','域名','TLS')):
        return {'kind':'cloud_connection', 'label':'连接中断 · 待续跑', 'message':'云端连接中断，任务已暂停', 'hint':'完成结果与缓存已保留。连接恢复后点击“续跑”，继续原任务；结果不确定的单元可能重新请求，未自动重试。'}
    return None

def dashboard(store, task_id='', after_event_id=0, limit=100, kind=''):
    requested_kind = normalize_kind(kind)
    if requested_kind and requested_kind not in PHASES:
        raise ValueError('请选择有效的调度任务类型')
    limit = max(1, min(200, limit))
    after_event_id = max(0, after_event_id)
    with store.db() as db:
        runs = db.execute("SELECT * FROM agent_runs ORDER BY CASE WHEN status IN ('running','stopping','queued') THEN 0 ELSE 1 END,created DESC LIMIT 60").fetchall()
        jobs = db.execute("SELECT * FROM jobs WHERE kind IN ('update','vectors','verification','verify','relations') ORDER BY CASE WHEN status IN ('running','queued') THEN 0 ELSE 1 END,started DESC LIMIT 40").fetchall()
        # 类型选择与历史深链接不受最近记录窗口限制。
        extra_runs = db.execute("SELECT * FROM agent_runs WHERE id=? OR kind=? ORDER BY CASE WHEN status IN ('running','queued','stopping') THEN 0 WHEN status!='complete' THEN 1 ELSE 2 END,created DESC LIMIT 60", (task_id, requested_kind)).fetchall()
        extra_jobs = db.execute("SELECT * FROM jobs WHERE id=? OR kind IN (?,?,?) ORDER BY CASE WHEN status IN ('running','queued','stopping') THEN 0 WHEN status!='complete' THEN 1 ELSE 2 END,started DESC LIMIT 40", (task_id.removeprefix('job:'), requested_kind, 'verify' if requested_kind=='verification' else requested_kind, 'relations' if requested_kind=='verification' else requested_kind)).fetchall()
        exact_run = db.execute('SELECT * FROM agent_runs WHERE id=?', (task_id,)).fetchall()
        exact_job = db.execute("SELECT * FROM jobs WHERE id=? AND kind IN ('update','vectors','verification','verify','relations')", (task_id.removeprefix('job:'),)).fetchall()
        runs = list({r['id']:r for r in [*runs, *extra_runs, *exact_run]}.values())
        jobs = list({r['id']:r for r in [*jobs, *extra_jobs, *exact_job]}.values())
        counts = {}
        for r in db.execute('SELECT run_id,status,COUNT(*) AS n FROM agent_tasks GROUP BY run_id,status'):
            counts.setdefault(r['run_id'], {})[r['status']] = r['n']
        activities = {r['task_id']: dict(decode(r['payload']), updated=r['updated']) for r in db.execute('SELECT * FROM task_activity')}
        items = []
        for r in runs:
            inp = decode(r['input'])
            result = decode(r['result'])
            c = counts.get(r['id'], {})
            summary, skipped, gaps = run_outcome(result)
            items.append(dict(id=r['id'], source='agent', kind=r['kind'], title=inp.get('query', '')[:90] or TITLES.get(r['kind'], r['kind']), status=r['status'], created=r['created'], updated=r['updated'], scope=inp.get('query') or ('仅当前课题' if inp.get('filters', {}).get('project') else '当前课题优先 · 全库增量'), processed=sum((c.get(k, 0) for k in ('complete', 'partial', 'failed'))), total=sum(c.values()) or None, completed=c.get('complete', 0), verified=len(result['claims']) if 'claims' in result else None, error=r['error'], result_available=bool(result.get('answer')), summary=summary, skipped=skipped, gaps=gaps, actions=['stop'] if r['status'] in ACTIVE else ['resume'] if r['status'] != 'complete' else [], control={'url': '/api/agent-runs/' + r['id'] + '/control'}, filters=inp.get('filters', {})))
        analysis = store.state('analysis_state', {})
        for r in jobs:
            p = decode(r['payload'])
            p = dict(p, **analysis) if analysis.get('job') == r['id'] else p
            key = 'job:' + r['id']
            kind = r['kind']
            actions = []
            control = {}
            if kind in {'verification', 'verify', 'relations'}:
                if analysis.get('job') == r['id']:
                    actions = ['stop'] if r['status'] in ACTIVE else ['resume'] if r['status'] != 'complete' else []
                    control = {'url': '/api/verification', 'resume_action': 'start', 'filters': p.get('filters', {})}
            elif kind == 'vectors' and r['status'] not in ACTIVE and (r['status'] != 'complete'):
                actions = ['resume']
                control = {'url': '/api/jobs', 'resume_action': 'vectors'}
            summary, skipped, gaps = job_outcome(kind, r['status'], p)
            items.append(dict(id=key, source='job', kind=kind, title=p.get('title') or TITLES.get(kind, kind), status=r['status'], created=r['started'], updated=r['finished'] or p.get('updated') or r['started'], finished=r['finished'], scope=r['progress'] or '后台研究作业', current_message=r['progress'] if r['status'] in ACTIVE else None, processed=p.get('completed'), total=p.get('total'), completed=None, verified=p.get('confirmed_added'), error=r['error'], result_available=False, summary=summary, skipped=skipped, gaps=gaps, actions=actions, control=control, filters=p.get('filters', {k:p[k] for k in ('project','direction','q') if k in p})))
        items.sort(key=lambda r: (r['status'] in ACTIVE, r['created'] or ''), reverse=True)
        for item in items:
            source = next((r for r in runs if r['id']==item['id']), None)
            item['attention'] = attention(source) if source else None
            a = activities.get(item['id'])
            item['activity'] = a
            item['waiting_reason'] = a.get('message') if a and a['status'].startswith('waiting') and (item['status'] in ACTIVE) else None
            item['elapsed_seconds'] = None
            if a and item['created']:
                try:
                    item['elapsed_seconds'] = max(0, int((datetime.fromisoformat(now() if item['status'] in ACTIVE else a['updated']) - datetime.fromisoformat(item['created'])).total_seconds()))
                except ValueError:
                    pass
        candidates = [r for r in items if not requested_kind or normalize_kind(r['kind']) == requested_kind]
        candidates.sort(key=lambda r: (0 if r['status'] in ACTIVE else 1 if 'resume' in r['actions'] else 2, -(datetime.fromisoformat(r['created']).timestamp()) if r['created'] else 0))
        selected = next((r for r in items if r['id'] == task_id), None) or (candidates[0] if candidates else None)
        if not selected:
            return {'items': items, 'selected': None, 'events': [], 'cursor': 0, 'has_more': False, 'updated': now(), 'active': any(i['status'] in ACTIVE for i in items)}
        key = selected['id']
        a = activities.get(key)
        steps = []
        if selected['source'] == 'agent':
            rows = db.execute("SELECT id,role,subject,status,attempt,error,updated FROM agent_tasks WHERE run_id=? ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'failed' THEN 1 WHEN 'partial' THEN 2 WHEN 'queued' THEN 3 ELSE 4 END,rowid LIMIT 100", (key,)).fetchall()
            for row in rows:
                s = dict(row)
                p = db.execute('SELECT payload FROM papers WHERE id=?', (s['subject'],)).fetchone()
                s['title'] = decode(p[0]).get('title', s['subject']) if p else selected['title'] if s['subject'] == key else s['subject']
                if s['subject'].startswith('relation:'):
                    relation = db.execute('SELECT payload FROM relations WHERE id=?', (s['subject'].removeprefix('relation:'),)).fetchone()
                    s['title'] = '关系核验：' + (decode(relation[0]).get('explanation') or s['subject'])[:160] if relation else s['subject']
                steps.append(s)
        if selected['source'] == 'job':
            for row in db.execute("SELECT e.* FROM task_events e JOIN (SELECT stage,MAX(seq) n FROM task_events WHERE task_id=? AND stage NOT IN ('model_queue','review_cache','review_decision','finished') GROUP BY stage) x ON e.seq=x.n ORDER BY e.seq", (key,)):
                payload = decode(row['payload'])
                steps.append({'id': str(row['seq']), 'title': row['message'] or row['stage'], 'role': row['role'], 'subject': payload.get('paper_id', ''), 'status': 'ended' if selected['status'] not in ACTIVE and row['status'] in {'running', 'waiting_resource'} else row['status'], 'attempt': None, 'error': ''})
        unit_rows = db.execute("SELECT u.*,t.subject parent_subject FROM agent_units u JOIN agent_tasks t ON t.id=u.parent_id WHERE u.run_id=? ORDER BY CASE u.status WHEN 'running' THEN 0 WHEN 'failed' THEN 1 WHEN 'queued' THEN 2 ELSE 3 END,u.rowid LIMIT 100", (key,)).fetchall() if selected['source']=='agent' else []
        if unit_rows:
            stage_names = {'identify':'识别文献方法','recall':'召回候选文献','propose':'提出候选关系','gather-evidence':'双边原文补证','review-local':'本地核验','review-cloud':'云端复核','execute':'处理文献'}
            steps = []
            for row in unit_rows:
                unit = dict(row)
                payload = decode(unit['payload'])
                paper = db.execute('SELECT payload FROM papers WHERE id=?',(unit['parent_subject'],)).fetchone()
                label = decode(paper[0]).get('citekey') or decode(paper[0]).get('title') if paper else unit['parent_subject']
                target = payload.get('target') or {}
                if target:
                    label += ' → '+(target.get('citekey') or target.get('title') or target['id'])
                unit['title'] = stage_names.get(unit['stage'].split(':',1)[0],unit['stage'])+' · '+label
                unit['subject'] = unit['parent_subject']
                steps.append(unit)
        observed = {r['role']: dict(r) for r in db.execute('SELECT e.* FROM task_events e JOIN (SELECT role,MAX(seq) n FROM task_events WHERE task_id=? GROUP BY role) x ON e.seq=x.n', (key,))}
        roles, parallel = role_views(db, key, selected, FLOW.get(selected['kind'], ['coordinator']), observed, a)
        links = handoffs(db, key)
        events = [dict(r, payload=decode(r['payload'])) for r in db.execute('SELECT * FROM task_events WHERE task_id=? AND seq>? ORDER BY seq LIMIT ?', (key, after_event_id, limit))]
        cursor = events[-1]['seq'] if events else after_event_id
        more = bool(db.execute('SELECT 1 FROM task_events WHERE task_id=? AND seq>? LIMIT 1', (key, cursor)).fetchone())
        usage = db.execute('SELECT COUNT(*) calls,SUM(t.input_tokens) input_tokens,SUM(t.output_tokens) output_tokens,SUM(u.actual) cost,CASE WHEN COUNT(t.usage_id)=COUNT(*) THEN MIN(t.cost_known) ELSE 0 END priced FROM task_usage a JOIN usage u ON u.id=a.usage_id LEFT JOIN token_usage t ON t.usage_id=u.id WHERE a.task_id=?', (key,)).fetchone()
        decisions = {}
        local_calls = 0
        cache_hits = 0
        for event in db.execute('SELECT stage,status,payload FROM task_events WHERE task_id=?', (key,)):
            payload = decode(event['payload'])
            if event['stage'] == 'review_decision':
                decisions[payload.get('claim_id')] = payload.get('accepted', False)
            if payload.get('local_call'):
                local_calls += 1
            if event['status'] == 'cached':
                cache_hits += 1
        if decisions:
            selected['verified'] = sum(decisions.values())
        selected['local_calls'] = local_calls if observed else None
        selected['cache_hits'] = cache_hits if observed else None
        if selected['source']=='agent':
            from kbapp.workflows.run_summary import summary
            saved = decode(next(r['result'] for r in runs if r['id']==key)).get('summary') if selected['status'] not in ACTIVE else None
            metrics = saved or summary(store,key)
            metrics.setdefault('usage', {})
            metrics.setdefault('verification_counts', {})
            if not metrics['usage'].get('calls'):
                metrics = dict(metrics,usage=None)
            selected.update(metrics)
            selected['verified'] = metrics['verification_counts'].get('verified',0)
        inputs = decode(next(r['input'] for r in runs if r['id']==key)) if selected['source']=='agent' else {}
        selected = dict(selected, kind=normalize_kind(selected['kind']), steps=steps, roles=roles, parallel=parallel, dependencies=links, usage=selected.get('usage') or (dict(usage) if usage['calls'] else None), events_recorded=bool(observed), stages=inputs.get('stages'), paper_ids=inputs.get('paper_ids', []), phase_summaries=phase_summaries(db, selected, inputs))
    return {'items': items, 'selected': selected, 'events': events, 'cursor': cursor, 'has_more': more, 'updated': now(), 'active': any((i['status'] in ACTIVE for i in items))}
