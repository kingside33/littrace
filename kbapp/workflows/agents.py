"""kbapp.workflows.agents：由职责边界组织的知识库能力。"""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from kbapp.workflows import agent_units
from kbapp.core.io import digest, now
from kbapp.research.evidence import artifact
from kbapp.core.execution import priority
from kbapp.storage.activity import emit, current_role, ALIASES
KINDS = {'maintenance', 'lineage', 'search', 'answer', 'compare'}
AGENT_VERSION = 'research-agents-v2'
ROLES = {'coordinator': {'tools': ['任务分解', '缺口识别'], 'completion': '已保存范围及依赖指纹'}, 'reader': {'tools': ['PDF只读', 'GLM-OCR', 'Gemma'], 'completion': '每节已读/失败记录与逐字证据已保存'}, 'lineage_researcher': {'tools': ['题摘', '引用邻居', '方法别名', '混合检索'], 'completion': '分类与关系分级、有依据且未覆盖人工判断'}, 'retriever': {'tools': ['BM25', '向量', '图谱'], 'completion': '最多4个子问题及实际检索模式已保存'}, 'writer': {'tools': ['已核材料'], 'completion': '结论与来源映射已保存'}, 'verifier': {'tools': ['原始证据', 'Gemma', 'DeepSeek'], 'completion': '语义支持与数字条件逐项核验，失败最多补读两轮'}}
_guard = threading.Lock()
_threads = {}
_stops = {}
MAX_WORKERS = 4
BACKGROUND_WORKERS = 3

def sync_graph(store):
    if not store.state('agent_graph_dirty', False):
        return
    from kbapp.workflows.export import export_static
    try:
        export_static(store)
        store.set_state('agent_graph_dirty', False)
        store.set_state('agent_graph_sync', {'status': 'synced', 'updated': now()})
    except Exception as exc:
        store.set_state('agent_graph_sync', {'status': 'failed', 'error': str(exc)[:1000], 'updated': now()})

def get_run(store, rid, include_tasks=True):
    with store.db() as db:
        row = db.execute('SELECT * FROM agent_runs WHERE id=?', (rid,)).fetchone()
        tasks = db.execute('SELECT id,role,subject,status,attempt,error,updated FROM agent_tasks WHERE run_id=? ORDER BY rowid', (rid,)).fetchall() if include_tasks else []
        counts = dict(db.execute('SELECT status,COUNT(*) FROM agent_tasks WHERE run_id=? GROUP BY status', (rid,)).fetchall())
    if not row:
        raise ValueError('研究任务不存在')
    result = dict(row)
    for key in ('input', 'result'):
        result[key] = json.loads(result[key])
    result.update(type='AgentRun', input_fingerprint=result['fingerprint'], generation_version=result['input'].get('generation_version', 'legacy-agent-run'), verification_status=result['status'])
    result['tasks'] = [dict(t) for t in tasks]
    result['completed'] = counts.get('complete', 0)
    result['total'] = sum(counts.values())
    result['task_counts'] = counts
    result['processed'] = sum((counts.get(k, 0) for k in ('complete', 'partial', 'failed')))
    from kbapp.workflows.run_summary import summary
    metrics = result['result'].get('summary') if result['status'] not in {'running','queued','stopping'} else None
    if include_tasks or metrics:
        result.update(metrics or summary(store, rid))
    return result

def list_runs(store):
    with store.db() as db:
        ids = [r[0] for r in db.execute('SELECT id FROM agent_runs ORDER BY created DESC,rowid DESC LIMIT 30')]
    return [get_run(store, rid, False) for rid in ids]

def create_run(store, values, start=True):
    if not isinstance(values, dict) or values.get('kind', 'maintenance') not in KINDS:
        raise ValueError('任务类型无效')
    kind = values.get('kind', 'maintenance')
    query = values.get('query', '')
    if not isinstance(query, str) or len(query) > 2000 or (kind in {'answer', 'search'} and (not query.strip())):
        raise ValueError('问题长度应为1至2000字符')
    filters = values.get('filters', {})
    if not isinstance(filters, dict):
        raise ValueError('任务筛选无效')
    allowed = {'kind', 'direction', 'year', 'project', 'paper', 'section', 'q'}
    if any((k not in allowed for k in filters)):
        raise ValueError('任务含未知筛选字段')
    if 'project' in filters and (not isinstance(filters['project'], bool)):
        raise ValueError('课题筛选应为布尔值')
    if kind in {'maintenance', 'lineage'} and any((k in filters for k in ('kind', 'section'))):
        raise ValueError('维护范围支持论文、课题、方向、年份和关键词')
    ids = values.get('paper_ids', [])
    if not isinstance(ids, list) or any((not isinstance(i, str) for i in ids)) or len(ids) > 13000:
        raise ValueError('论文范围无效')
    if kind == 'compare' and (not 2 <= len(set(ids)) <= 4):
        raise ValueError('比较需要2至4篇论文')
    stages = values.get('stages', ['understanding', 'lineage', 'relations'])
    if not isinstance(stages, list) or not stages or any((s not in {'understanding', 'lineage', 'relations'} for s in stages)):
        raise ValueError('维护阶段无效')
    inputs = {'kind': kind, 'query': query.strip(), 'filters': filters, 'paper_ids': list(dict.fromkeys((store.resolve(p) for p in ids))), 'stages': stages, 'generation_version': AGENT_VERSION}
    known = set(store.papers())
    if any((pid not in known for pid in inputs['paper_ids'])):
        raise ValueError('论文范围包含不存在的节点')
    fingerprint = digest(json.dumps(inputs, sort_keys=True, ensure_ascii=False))
    rank = 0 if kind in {'answer', 'search', 'compare'} else 100
    with store.db() as db:
        duplicate = db.execute("SELECT id FROM agent_runs WHERE fingerprint=? AND status IN ('queued','running','stopping')", (fingerprint,)).fetchone()
        if duplicate:
            if start:
                start_dispatcher(store)
            return get_run(store, duplicate[0])
        rid = 'run:' + digest(str(time.time_ns()) + fingerprint)[:24]
        db.execute('INSERT INTO agent_runs VALUES(?,?,?,?,?,?,0,?,?,?,?)', (rid, kind, 'queued', json.dumps(inputs, ensure_ascii=False), fingerprint, rank, '{}', '', now(), now()))
    if start:
        from kbapp.integrations.models import set_cancelled
        set_cancelled(store, ['embedding', 'ocr', 'text'], False)
        start_dispatcher(store)
    return get_run(store, rid)

def control_run(store, rid, action):
    run = get_run(store, rid, False)
    if action not in {'stop', 'resume'}:
        raise ValueError('任务控制仅支持stop或resume')
    with store.db() as db:
        if action == 'stop':
            if run['status'] not in {'complete', 'partial', 'failed', 'interrupted'}:
                db.execute('UPDATE agent_runs SET stop=1,status=?,updated=? WHERE id=?', ('stopping' if run['status'] == 'running' else 'interrupted', now(), rid))
        else:
            if run['status'] in {'queued', 'running', 'stopping'}:
                return run
            if db.execute("SELECT 1 FROM agent_units WHERE run_id=? AND status='running' LIMIT 1", (rid,)).fetchone():
                raise ValueError('在途单元尚未结束，请稍后续跑')
            result = dict(run['result'])
            result.pop('halt_error', None)
            db.execute('UPDATE agent_runs SET result=? WHERE id=?', (json.dumps(result, ensure_ascii=False), rid))
            agent_units.resume(db, rid)
            inputs = dict(run['input'], generation_version=AGENT_VERSION)
            db.execute("UPDATE agent_runs SET stop=0,status='queued',error='',input=?,updated=? WHERE id=?", (json.dumps(inputs, ensure_ascii=False), now(), rid))
            db.execute("UPDATE agent_tasks SET status='queued',error='' WHERE run_id=? AND status!='complete'", (rid,))
    from kbapp.storage.activity import scope
    with scope(store, rid):
        current = get_run(store, rid, False)['status']
        emit(store, 'control', current, '已续跑，等待调度' if action == 'resume' else '停止请求已保存，等待在途请求完成' if current == 'stopping' else '任务已停止，完成结果保留')
    if action == 'resume':
        from kbapp.research.review import retry_deferred
        retry_deferred(store, rid)
        from kbapp.integrations.models import set_cancelled
        set_cancelled(store, ['embedding', 'ocr', 'text'], False)
        start_dispatcher(store)
    return get_run(store, rid)

def plan(store, run):
    from kbapp.library.catalog import enriched_papers
    from kbapp.research.viewmodels import scoped_nodes, relation_grade, METHOD_KINDS
    from kbapp.research.reading import understanding_fingerprint
    inputs = run['input']
    papers = enriched_papers(store)
    filters = inputs['filters']
    ids = inputs['paper_ids']
    verified_edges = [r for r in store.relations() if r['status'] != 'rejected' and r['kind'] in METHOD_KINDS and (relation_grade(r) in {'fulltext', 'abstract'})]
    priority_ids = {p['id'] for p in scoped_nodes(list(papers.values()), verified_edges, project=True)}
    project_scope = {p['id'] for p in scoped_nodes(list(papers.values()), verified_edges, filters.get('direction', ''), True, filters.get('q', ''))} if filters.get('project') else None

    def year_matches(p):
        if not filters.get('year'):
            return True
        text = str(filters['year'])
        parts = text.split('-')
        try:
            return int(parts[0]) <= int(p.get('year') or 0) <= int(parts[-1])
        except ValueError:
            raise ValueError('维护年份格式应为2020或2020-2026')
    selected = [p for p in papers.values() if (not ids or p['id'] in ids) and (not filters.get('paper') or store.resolve(filters['paper']) == p['id']) and (project_scope is None or p['id'] in project_scope) and (project_scope is not None or not filters.get('direction') or filters['direction'] in p.get('directions', [])) and year_matches(p) and (project_scope is not None or not filters.get('q') or filters['q'].lower() in ((p.get('title') or '') + ' ' + (p.get('abstract') or '')).lower())]
    selected.sort(key=lambda p: (p['id'] not in priority_ids, p.get('kind') != 'library', -(p.get('year') or 0), p['id']))
    tasks = []
    for p in selected:
        if run['kind'] == 'maintenance' and 'understanding' in inputs.get('stages', ['understanding']):
            tasks.append(('reader', p['id'], understanding_fingerprint(store, p)))
        if run['kind'] == 'lineage' or (run['kind'] == 'maintenance' and 'lineage' in inputs.get('stages', ['lineage'])):
            tasks.append(('lineage_researcher', p['id'], digest(str(p.get('title')) + str(p.get('abstract')) + str(store.settings()['model']))))
    if run['kind'] == 'lineage' or (run['kind'] == 'maintenance' and 'relations' in inputs.get('stages', ['relations'])):
        scope = {p['id'] for p in selected}
        relations = store.relations()
        for relation in relations:
            if relation['status'] == 'rejected' or relation.get('manual') or relation['kind'] not in {'inherits', 'improves', 'combines', 'transfers'}:
                continue
            if relation['src'] in scope or relation['dst'] in scope:
                tasks.append(('verifier', 'relation:' + relation['id'], digest(json.dumps(relation, sort_keys=True))))
        subjects = {r['id']: r for r in relations}

        def in_project(task):
            subject = task[1]
            if subject.startswith('relation:'):
                r = subjects[subject.removeprefix('relation:')]
                return r['src'] in priority_ids or r['dst'] in priority_ids
            return subject in priority_ids
        tasks.sort(key=lambda t: not in_project(t))
    if run['kind'] in {'search', 'answer', 'compare'}:
        tasks.append(('retriever' if run['kind'] == 'search' else 'writer', run['id'], run['fingerprint']))
    manifest = artifact(store, 'ResearchPlan', run['id'], run['fingerprint'], {'paper_ids': [p['id'] for p in selected], 'task_count': len(tasks), 'roles': ROLES, 'priority': '当前课题优先，然后全库'})
    with store.db() as db:
        for role, subject, fp in tasks:
            tid = 'task:' + digest(run['id'] + role + subject + fp)[:24]
            db.execute('INSERT OR IGNORE INTO agent_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?)', (tid, run['id'], role, subject, fp, 'queued', '{}', '{}', 0, '', now()))
        db.execute('UPDATE agent_runs SET result=?,updated=? WHERE id=?', (json.dumps({'plan_id': manifest['id']}, ensure_ascii=False), now(), run['id']))

def prepare_run(store, rid):
    run = get_run(store, rid, False)
    if not run['total'] and not run['stop']:
        from kbapp.storage.activity import scope
        with scope(store, rid, role='coordinator'):
            emit(store, 'planning', message='确定研究范围与处理单元')
            plan(store, run)
            run = get_run(store, rid, False)
            emit(store, 'planning', 'complete', f"已安排 {run['total']} 个父单元，按证据依赖推进")
    if not run['stop']:
        agent_units.ensure(store, rid)
    return run


def _paper(store, pid):
    with store.db() as db:
        row = db.execute('SELECT payload FROM papers WHERE id=?', (pid,)).fetchone()
    return json.loads(row[0]) if row else None


def execute_unit(store, unit, shutdown=lambda: False):
    from kbapp.storage.activity import scope, stop_check
    from kbapp.workflows.unit_work import perform
    rid = unit['run_id']
    run = get_run(store, rid, False)
    with store.db() as db:
        parent = dict(db.execute('SELECT role,subject FROM agent_tasks WHERE id=?', (unit['parent_id'],)).fetchone())
        predecessor = db.execute('SELECT p.role FROM agent_unit_dependencies d JOIN agent_units p ON p.id=d.dependency_id WHERE d.unit_id=? ORDER BY p.updated DESC LIMIT 1',(unit['id'],)).fetchone()
        handoff_from = predecessor[0] if predecessor else 'coordinator'
    paper = _paper(store, parent['subject'])
    label = (paper.get('citekey') or paper.get('title')) if paper else ('既有关系 '+parent['subject'].removeprefix('relation:')) if parent['subject'].startswith('relation:') else run['input'].get('query', '')
    if unit['payload'].get('target'):
        target = unit['payload']['target']
        label = str(label)+' → '+str(target.get('citekey') or target.get('title') or target['id'])

    def stopped():
        if shutdown():
            return True
        with store.db() as db:
            return bool(db.execute('SELECT stop FROM agent_runs WHERE id=?', (rid,)).fetchone()[0])

    def progress(role, message):
        current_role.set(ALIASES.get(role, role))
        emit(store, 'progress', 'running', message, role=role)
        with store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            result = json.loads(db.execute('SELECT result FROM agent_runs WHERE id=?', (rid,)).fetchone()[0])
            result.update(role=role, message=message)
            db.execute('UPDATE agent_runs SET result=?,updated=? WHERE id=?', (json.dumps(result, ensure_ascii=False), now(), rid))

    def execute_parent():
        from kbapp.research.reading import read_paper
        from kbapp.research.qa import answer
        from kbapp.research.retrieval import evidence_search
        from kbapp.integrations.cloud import complete
        inputs = run['input']
        filters = {k:v for k,v in inputs['filters'].items() if k!='q'}
        if parent['role'] == 'reader':
            return read_paper(store, paper, stopped, progress)
        if run['kind'] == 'search':
            return evidence_search(store, inputs['query'], filters, complete, stopped)
        query = inputs['query']
        if run['kind'] == 'compare':
            titles = [_paper(store, pid)['title'] for pid in inputs['paper_ids']]
            query = '比较这些论文的方法、假设、指标和局限，逐篇核实并指出不可比条件：'+'；'.join(titles)+('。'+query if query else '')
            filters['paper_ids'] = inputs['paper_ids']
        return answer(store, query, filters, stopped, progress)

    token = priority.set(run['priority'])
    stop_token = stop_check.set(stopped)
    from kbapp.workflows.lease import Lease
    try:
        with Lease(store, unit['id']), scope(store, rid, unit['id'], unit['role'], subject=unit['subject'], label=label):
            emit(store, 'step', 'running', '开始处理当前单元', parent_id=unit['parent_id'], handoff_from=handoff_from)
            try:
                if stopped():
                    raise ValueError('任务已停止')
                result, children, partial = perform(store, unit, stopped, progress, execute_parent)
                if unit['stage']=='execute':
                    partial = result.get('verification_status') in {'partial','unsupported','insufficient','conflict','deferred'}
                agent_units.finish(store, unit, result, children, 'partial' if partial else 'complete')
                emit(store, 'step', 'partial' if partial else 'complete', '当前单元已保存', parent_id=unit['parent_id'])
            except Exception as exc:
                import traceback
                from kbapp.core.io import atomic
                atomic(store.paths.runtime/'logs/agents'/(unit['id'].replace(':','-')+'.log'), traceback.format_exc())
                interrupted = stopped()
                infrastructure = any(v in str(exc).lower() for v in ('云端调用','本地关系核验未完成','模型队列','请先','请在','费用','额度','api','模型未','等待下载','模型已停止','model unavailable','connection','timeout'))
                fatal = not interrupted and (infrastructure or run['kind'] not in {'maintenance','lineage'})
                agent_units.fail(store, unit, exc, interrupted, fatal)
                emit(store, 'step', 'interrupted' if interrupted else 'failed', '当前单元未完成，已保存结果保留', error_type=type(exc).__name__, parent_id=unit['parent_id'])
    finally:
        if parent['role'] in {'lineage_researcher','verifier'}:
            store.set_state('agent_graph_dirty', True)
        stop_check.reset(stop_token)
        priority.reset(token)
        agent_units.settle(store, rid, unit['parent_id'])


def step(store, rid):
    """同步入口每次执行一个就绪子单元，与后台共用认领及依赖规则。"""
    run = prepare_run(store, rid)
    if not run['stop']:
        unit = agent_units.claim(store, rid)
        if unit:
            execute_unit(store, unit)
    state = agent_units.settle(store, rid, None)
    if state not in {'queued','running','stopping'}:
        sync_graph(store)


def dispatch(store, shutdown):
    prepared = set()
    with ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix='research-unit') as pool:
        inflight = {}
        while True:
            for future in list(inflight):
                if future.done():
                    unit, _ = inflight.pop(future)
                    try:
                        future.result()
                    except Exception as exc:
                        agent_units.fail(store, unit, exc, fatal=True)
                        agent_units.settle(store, unit['run_id'], unit['parent_id'])
            claimed = False
            if not shutdown.is_set():
                with store.db() as db:
                    runs = [dict(r) for r in db.execute("SELECT id,kind,stop FROM agent_runs WHERE status IN ('queued','running','stopping') ORDER BY priority,created")]
                for run in runs:
                    rid = run['id']
                    if rid not in prepared:
                        prepare_run(store, rid)
                        agent_units.settle(store, rid, None)
                        prepared.add(rid)
                    if run['stop']:
                        agent_units.settle(store, rid)
                        continue
                    background = run['kind'] in {'maintenance','lineage'}
                    if len(inflight)>=MAX_WORKERS or (background and sum(bg for _,bg in inflight.values())>=BACKGROUND_WORKERS):
                        continue
                    unit = agent_units.claim(store, rid)
                    if unit:
                        inflight[pool.submit(execute_unit, store, unit, shutdown.is_set)] = (unit, background)
                        claimed = True
                    else:
                        agent_units.settle(store, rid)
            if not inflight:
                if shutdown.is_set():
                    break
                with store.db() as db:
                    pending = db.execute("SELECT 1 FROM agent_runs WHERE status IN ('queued','running','stopping') LIMIT 1").fetchone()
                if not pending:
                    break
            if not claimed and inflight:
                wait(inflight, timeout=0.1, return_when=FIRST_COMPLETED)
            elif not claimed:
                shutdown.wait(0.1)


def stop_dispatcher(store, wait=True):
    with _guard:
        event, thread = _stops.get(str(store.path)), _threads.get(str(store.path))
        if event:
            event.set()
    if wait and thread and thread is not threading.current_thread():
        thread.join()


def recover_units(store):
    """I02：只接管原进程已退出或租约过期的在途单元；仍被存活进程持有的单元保持不变。"""
    from kbapp.workflows.lease import takeover_allowed
    with store.db() as db:
        running = [r[0] for r in db.execute("SELECT id FROM agent_units WHERE status='running'")]
    taken = []
    for unit_id in running:
        if takeover_allowed(store, unit_id):
            with store.db() as db:
                db.execute("UPDATE agent_units SET status='queued',error='接管：原执行进程已退出或租约过期' WHERE id=? AND status='running'", (unit_id,))
                db.execute('DELETE FROM agent_unit_leases WHERE unit_id=?', (unit_id,))
            taken.append(unit_id)
    with store.db() as db:
        db.execute("UPDATE agent_tasks SET status='queued' WHERE status='running' AND NOT EXISTS(SELECT 1 FROM agent_units u WHERE u.parent_id=agent_tasks.id AND u.status='running')")
        db.execute("UPDATE agent_runs SET status=CASE WHEN stop=1 THEN 'interrupted' ELSE 'queued' END WHERE status IN ('running','stopping') AND NOT EXISTS(SELECT 1 FROM agent_units u WHERE u.run_id=agent_runs.id AND u.status='running')")
    return {'running': len(running), 'taken_over': len(taken)}


def start_dispatcher(store):
    with _guard:
        key = str(store.path)
        if key in _threads and _threads[key].is_alive():
            return
        shutdown = threading.Event()
        _stops[key] = shutdown

        def work():
            from kbapp.core.locks import update_lock
            owned = False
            try:
                with update_lock(store.root, 'workbench-agents.lock'):
                    owned = True
                    recover_units(store)
                    with store.db() as db:
                        for legacy in db.execute("SELECT id,input FROM agent_runs WHERE status='queued'").fetchall():
                            if json.loads(legacy['input']).get('generation_version') != AGENT_VERSION:
                                db.execute("UPDATE agent_runs SET status='interrupted',error=?,updated=? WHERE id=?", ('核验规则已更新；请明确续跑以使用新规则，历史完成结果保留',now(),legacy['id']))
                    dispatch(store, shutdown)
            except ValueError:
                pass
            finally:
                if owned:
                    sync_graph(store)
                with _guard:
                    _threads.pop(key,None)
                    _stops.pop(key,None)
                if owned and not shutdown.is_set():
                    with store.db() as db:
                        pending = db.execute("SELECT 1 FROM agent_runs WHERE status='queued' LIMIT 1").fetchone()
                    if pending:
                        start_dispatcher(store)
        _threads[key] = threading.Thread(target=work, daemon=True, name='research-coordinator')
        _threads[key].start()
