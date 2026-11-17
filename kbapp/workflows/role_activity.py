"""面向界面的有限工作摘要，只使用程序事件与处理对象。"""
import json
from collections import defaultdict
from kbapp.storage.activity import ROLE_NAMES

ACTIONS = {'relation_material':'提取双方可定位的原文证据','lineage_classification':'识别方向与方法实体','abstract_lineage':'核对候选方法关系','understanding_extract':'提取原文证据','understanding_verify':'核验原文对结论的支持','independent_claim_review':'独立复核证据与结论','review_cache':'复用已保存的核验结果','review_decision':'已保存本条核验结果'}


def text(value, limit=90):
    return ' '.join(str(value or '').split())[:limit]


def views(db, key, selected, flow, observed, latest):
    units = [dict(r) for r in db.execute('SELECT id,parent_id,role,stage,subject,status,payload FROM agent_units WHERE run_id=?', (key,))] if selected['source']=='agent' else []
    by_id = {u['id']:u for u in units}
    current = [dict(r) for r in db.execute("SELECT e.* FROM task_events e JOIN (SELECT step_id,MAX(seq) n FROM task_events WHERE task_id=? AND step_id!='' GROUP BY step_id) x ON e.seq=x.n", (key,))]
    active_by_role = defaultdict(list)
    active_run = selected['status'] in {'queued','running','stopping'}
    for event in current:
        unit = by_id.get(event['step_id'])
        if active_run and unit and unit['status']=='running' and event['status'] in {'running','waiting_resource'}:
            active_by_role[event['role']].append(event)
    if active_run and not units and latest and latest.get('status') in {'running','waiting_resource'}:
        role = latest.get('role','coordinator')
        event = observed.get(role)
        if event:
            active_by_role[role].append(dict(event))
    roles = []
    for role in dict.fromkeys(flow+list(observed)+[u['role'] for u in units]):
        observation = observed.get(role)
        active = sorted(active_by_role[role], key=lambda r:r['seq'], reverse=True)
        queued = [u for u in units if u['role']==role and u['status']=='queued']
        finished = sum(u['role']==role and u['status']=='complete' for u in units)
        items = []
        for event in active[:2]:
            payload = json.loads(event['payload'])
            work = payload.get('work_item') or {}
            unit_data = json.loads(by_id[event['step_id']]['payload']) if event['step_id'] in by_id else {}
            detail = '候选 {}/{}'.format(unit_data['position'], unit_data['total']) if unit_data.get('position') else ''
            items.append({'action':text(ACTIONS.get(event['stage']) or event['message']),'subject':text(work.get('label') or work.get('subject') or '当前任务',75),'detail':detail,'updated':event['created'],'status':event['status'],'step_id':event['step_id']})
        status = observation['status'] if observation else 'not_recorded'
        if active:
            status = 'waiting_resource' if all(a['status']=='waiting_resource' for a in active) else 'running'
            message = items[0]['action']
        elif queued and active_run:
            status = 'queued'
            blocked = db.execute("SELECT 1 FROM agent_unit_dependencies d JOIN agent_units p ON p.id=d.dependency_id WHERE d.unit_id=? AND p.status!='complete' LIMIT 1", (queued[0]['id'],)).fetchone()
            message = '等待对应的前置证据' if blocked else '已就绪，等待调度槽位'
        elif role=='coordinator' and units and active_run:
            status = 'running'
            message = '按依赖安排就绪单元'
        else:
            stale = status in {'running','waiting_resource'}
            if status in {'running','waiting_resource'}:
                status = 'interrupted' if selected['status'] in {'failed','interrupted','stopping'} else 'ended' if not active_run else 'idle'
            message = ('当前无在途单元，等待可处理的证据' if active_run else '当前无在途单元，已保存结果保留') if stale else text(observation['message']) if observation else '等待可处理的证据' if active_run else '尚无执行记录'
        model = json.loads(observation['payload']).get('model') if observation else None
        if observation and not model:
            prior = db.execute("SELECT json_extract(payload,'$.model') FROM task_events WHERE task_id=? AND role=? AND json_extract(payload,'$.model') IS NOT NULL ORDER BY seq DESC LIMIT 1", (key,role)).fetchone()
            model = prior[0] if prior else None
        roles.append({'id':role,'name':ROLE_NAMES.get(role,role),'status':status,'message':message,'model':model,'activities':items,'active_count':len(active),'pending_count':len(queued),'completed_units':finished,'updated':active[0]['created'] if active else observation['created'] if observation else None})
    counts = defaultdict(int)
    for unit in units:
        counts[unit['status']] += 1
    parallel = {'running_units':counts['running'],'pending_units':counts['queued'],'completed_units':counts['complete'],'processed_units':sum(counts[k] for k in ('complete','partial','failed','blocked','interrupted')),'total_units':len(units),'background_limit':3,'cloud_limit':2,'local_limit':1}
    return roles, parallel


def handoffs(db, key):
    # A unit's events can interleave with other units; sequence alone is not a dependency.
    previous, seen, links = {},set(),[]
    for row in db.execute("SELECT role,step_id,payload FROM task_events WHERE task_id=? AND stage NOT IN ('review_cache','model_queue') ORDER BY seq", (key,)):
        unit = row['step_id'] or 'legacy-root'
        explicit = json.loads(row['payload']).get('handoff_from')
        origin = explicit or previous.get(unit)
        pair = (origin,row['role'])
        if origin and pair[0]!=pair[1] and pair not in seen:
            links.append({'source':pair[0],'target':pair[1]})
            seen.add(pair)
        previous[unit] = row['role']
    return links
