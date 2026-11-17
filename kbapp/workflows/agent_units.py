"""持久化子单元、依赖与原子认领；不执行模型调用。"""
import json
from kbapp.core.io import digest, now

TERMINAL = {'complete', 'partial', 'failed', 'interrupted', 'blocked'}


def phase(stage):
    return stage.split(':', 1)[0]


def resource(stage):
    return 'local' if phase(stage) in {'review-local', 'recall', 'gather-evidence'} else 'cloud' if phase(stage) in {'identify', 'propose', 'review-cloud'} else 'mixed'


def add(db, parent, stage, role, subject, payload=None, dependencies=(), object_key=None):
    uid = 'unit:' + digest(parent['id'] + '|' + stage + '|' + subject)[:28]
    db.execute('INSERT OR IGNORE INTO agent_units VALUES(?,?,?,?,?,?,?,?,?,?,0,?,?)', (uid, parent['id'], parent['run_id'], stage, role, subject, object_key or subject, 'queued', json.dumps(payload or {}, ensure_ascii=False), '{}', '', now()))
    for dependency in dependencies:
        db.execute('INSERT OR IGNORE INTO agent_unit_dependencies VALUES(?,?)', (uid, dependency))
    return uid


def ensure(store, rid):
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        parents = [dict(r) for r in db.execute("SELECT id,run_id,role,subject FROM agent_tasks t WHERE run_id=? AND status!='complete' AND NOT EXISTS(SELECT 1 FROM agent_units u WHERE u.parent_id=t.id)", (rid,))]
        for parent in parents:
            role = parent['role']
            stage = 'identify' if role == 'lineage_researcher' else 'gather-evidence' if role == 'verifier' else 'execute'
            add(db, parent, stage, 'retriever' if role=='verifier' else role, parent['subject'], {'mode': 'existing'} if role == 'verifier' else {})


def result(store, uid):
    with store.db() as db:
        row = db.execute('SELECT result FROM agent_units WHERE id=?', (uid,)).fetchone()
    if not row:
        raise ValueError('子单元前置记录不存在')
    return json.loads(row[0])


def claim(store, rid):
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        run = db.execute('SELECT stop,status,kind FROM agent_runs WHERE id=?', (rid,)).fetchone()
        if not run or run['stop'] or run['status'] not in {'queued', 'running'}:
            return None
        active = [dict(r) for r in db.execute("SELECT u.role,u.object_key,u.stage,r.kind FROM agent_units u JOIN agent_runs r ON r.id=u.run_id WHERE u.status='running'")]
        if len(active) >= 4 or (run['kind'] in {'maintenance','lineage'} and sum(a['kind'] in {'maintenance','lineage'} for a in active) >= 3):
            return None
        last = db.execute("SELECT value FROM state WHERE key='agent_scheduler_local_phase'").fetchone()
        # Recall and verification share the local lane; neither batch may monopolize it.
        prefer_recall = not last or json.loads(last[0]) != 'recall'
        candidates = [dict(r) for r in db.execute("""SELECT u.* FROM agent_units u WHERE u.run_id=? AND u.status='queued'
          AND NOT EXISTS(SELECT 1 FROM agent_unit_dependencies d JOIN agent_units p ON p.id=d.dependency_id WHERE d.unit_id=u.id AND p.status!='complete')
          ORDER BY CASE WHEN u.stage LIKE 'review-cloud%' THEN 0 WHEN u.stage='recall' OR u.stage LIKE 'gather-evidence%' THEN CASE WHEN ? THEN 1 ELSE 2 END WHEN u.stage LIKE 'review-local%' THEN CASE WHEN ? THEN 2 ELSE 1 END WHEN u.stage='execute' AND u.role='reader' THEN 2 WHEN u.stage LIKE 'propose:%' THEN 3 WHEN u.stage='identify' THEN 4 ELSE 5 END,u.rowid""", (rid, prefer_recall, prefer_recall))]
        for unit in candidates:
            if any(a['object_key'] == unit['object_key'] for a in active):
                continue
            group = resource(unit['stage'])
            if group != 'mixed' and sum(resource(a['stage']) == group for a in active) >= (1 if group == 'local' else 2):
                continue
            if sum(a['role'] == unit['role'] for a in active) >= (1 if unit['role']=='reader' else 2):
                continue
            if not db.execute("UPDATE agent_units SET status='running',attempt=attempt+1,error='',updated=? WHERE id=? AND status='queued'", (now(), unit['id'])).rowcount:
                continue
            from kbapp.workflows.lease import acquire as acquire_lease
            acquire_lease(store, unit['id'], db=db)
            db.execute("UPDATE agent_tasks SET status='running',attempt=CASE WHEN attempt=0 THEN 1 ELSE attempt END,updated=? WHERE id=?", (now(), unit['parent_id']))
            db.execute("UPDATE agent_runs SET status='running',updated=? WHERE id=?", (now(), rid))
            if group == 'local':
                db.execute("INSERT INTO state VALUES('agent_scheduler_local_phase',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps('recall' if phase(unit['stage']) in {'recall','gather-evidence'} else phase(unit['stage'])),))
            unit['payload'] = json.loads(unit['payload'])
            return unit
    return None


def finish(store, unit, value, children=(), status='complete'):
    from kbapp.workflows.lease import release as release_lease
    release_lease(store, unit['id'])
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        parent = dict(db.execute('SELECT id,run_id,role,subject FROM agent_tasks WHERE id=?', (unit['parent_id'],)).fetchone())
        # Children and the result commit together, so an interruption cannot lose a handoff.
        for child in children:
            add(db, parent, **dict(child, dependencies=tuple(child.get('dependencies', ())) + (unit['id'],)))
        db.execute('UPDATE agent_units SET status=?,result=?,updated=? WHERE id=?', (status, json.dumps(value, ensure_ascii=False), now(), unit['id']))
        if unit['stage'] == 'execute' and parent['role'] in {'writer', 'retriever'}:
            db.execute('UPDATE agent_runs SET result=?,updated=? WHERE id=?', (json.dumps(value, ensure_ascii=False), now(), unit['run_id']))


def fail(store, unit, error, interrupted=False, fatal=False):
    from kbapp.workflows.lease import release as release_lease
    release_lease(store, unit['id'])
    from kbapp.core.errors import classify as classify_error, retry_policy
    kind = classify_error(error)
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('UPDATE agent_units SET status=?,error=?,updated=? WHERE id=?', ('interrupted' if interrupted else 'failed', str(error)[:1500], now(), unit['id']))
        payload = {'error_kind': kind, 'retry': retry_policy(kind), 'reason': str(error)[:500]}
        if kind == 'output_format' or '结构错误' in str(error):
            payload['verification_status'] = 'structure_error'
        db.execute('UPDATE agent_units SET result=? WHERE id=?',(json.dumps(payload,ensure_ascii=False),unit['id']))
        if fatal:
            value = json.loads(db.execute('SELECT result FROM agent_runs WHERE id=?', (unit['run_id'],)).fetchone()[0])
            value['halt_error'] = True
            db.execute("UPDATE agent_runs SET stop=1,status='stopping',result=?,error=?,updated=? WHERE id=?", (json.dumps(value, ensure_ascii=False), str(error)[:1500], now(), unit['run_id']))


def settle(store, rid, parent_id=''):
    from kbapp.workflows.states import stage_status, run_status
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        # Failed prerequisites retain an explicit blocked state for a later user resume.
        while db.execute("""UPDATE agent_units SET status='blocked',updated=? WHERE run_id=? AND status='queued'
          AND EXISTS(SELECT 1 FROM agent_unit_dependencies d JOIN agent_units p ON p.id=d.dependency_id WHERE d.unit_id=agent_units.id AND p.status IN ('failed','blocked','partial'))""", (now(), rid)).rowcount:
            pass
        parents = db.execute('SELECT id,role,result FROM agent_tasks WHERE run_id=? AND id=?', (rid, parent_id)).fetchall() if parent_id else db.execute('SELECT id,role,result FROM agent_tasks t WHERE run_id=? AND EXISTS(SELECT 1 FROM agent_units u WHERE u.parent_id=t.id)', (rid,)).fetchall() if parent_id is None else []
        for parent in parents:
            counts = dict(db.execute('SELECT status,COUNT(*) FROM agent_units WHERE parent_id=? GROUP BY status', (parent['id'],)))
            if not counts:
                continue
            status = stage_status(counts)
            value = json.loads(parent['result'])
            value['subunits'] = counts
            if status == 'complete':
                last = db.execute('SELECT result FROM agent_units WHERE parent_id=? ORDER BY rowid DESC LIMIT 1', (parent['id'],)).fetchone()
                value.update(json.loads(last[0]) if last else {})
            db.execute('UPDATE agent_tasks SET status=?,result=?,updated=? WHERE id=?', (status, json.dumps(value, ensure_ascii=False), now(), parent['id']))
        run = db.execute('SELECT stop,result FROM agent_runs WHERE id=?', (rid,)).fetchone()
        counts = dict(db.execute('SELECT status,COUNT(*) FROM agent_tasks WHERE run_id=? GROUP BY status', (rid,)))
        running = db.execute("SELECT COUNT(*) FROM agent_units WHERE run_id=? AND status='running'", (rid,)).fetchone()[0]
        state = run_status(counts, running, bool(run['stop']), bool(json.loads(run['result']).get('halt_error')))
        db.execute('UPDATE agent_runs SET status=?,updated=? WHERE id=?', (state, now(), rid))
    if state not in {'running','queued','stopping'}:
        from kbapp.workflows.run_summary import save_summary
        save_summary(store, rid)
    return state


def resume(db, rid):
    db.execute("UPDATE agent_units SET status='queued',error='' WHERE run_id=? AND status IN ('failed','interrupted','blocked','partial')", (rid,))
