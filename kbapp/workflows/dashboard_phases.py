"""调度台的固定流程视图；只汇总持久数据，不参与执行或修改历史状态。"""
import json
from collections import Counter

ACTIVE = {'queued', 'running', 'stopping'}
PHASES = {
    'maintenance': [('planning', '范围准备'), ('reading', '全文理解与笔记维护'), ('identify', '方法识别'), ('recall', '候选召回'), ('propose', '关系提议'), ('gather-evidence', '原文补证'), ('review-local', '本地核验'), ('review-cloud', '云端复核')],
    'lineage': [('planning', '范围准备'), ('identify', '方法识别'), ('recall', '候选召回'), ('propose', '关系提议'), ('gather-evidence', '原文补证'), ('review-local', '本地核验'), ('review-cloud', '云端复核')],
    'verification': [('planning', '范围准备'), ('gather-evidence', '原文识别与补证'), ('review-local', '本地核验'), ('review-cloud', '云端复核'), ('publish', '结果发布')],
    'update': [('sync', '文献同步'), ('extract', '原文识别与切块'), ('index', '来源与向量索引'), ('analysis', '引用及关系分析'), ('publish', '笔记与导出同步')],
    'vectors': [('prepare', '来源检查'), ('index', '向量计算'), ('publish', '索引发布')],
}
ROLES = {'planning': ['coordinator'], 'reading': ['reader', 'writer'], 'identify': ['lineage_researcher'], 'recall': ['retriever'], 'propose': ['lineage_researcher'], 'gather-evidence': ['retriever', 'ocr'], 'review-local': ['verifier'], 'review-cloud': ['cloud_reviewer'], 'sync': ['indexer'], 'extract': ['ocr', 'indexer'], 'index': ['indexer'], 'analysis': ['lineage_researcher', 'verifier', 'cloud_reviewer'], 'publish': ['writer', 'indexer', 'coordinator'], 'prepare': ['coordinator', 'indexer']}


def normalize_kind(kind):
    return 'verification' if kind in {'verify', 'relations'} else kind


def summaries(db, selected, inputs=None):
    kind = normalize_kind(selected['kind'])
    template = PHASES.get(kind, [])
    if not template:
        return []
    inputs = inputs or {}
    key = selected['id']
    active = selected['status'] in ACTIVE
    phases = {pid: dict(id=pid, title=title, roles=ROLES[pid], status='not_recorded', completed=0, total=0, counts={}, active_items=[], active_count=0, details=[], details_truncated=False) for pid, title in template}
    papers, relations = {}, {}

    def label(subject):
        if subject.startswith('relation:'):
            if subject not in relations:
                row = db.execute('SELECT payload FROM relations WHERE id=?', (subject[9:],)).fetchone()
                relation = json.loads(row[0]) if row else {}
                relations[subject] = ' → '.join(label(relation[p]) for p in ('src', 'dst') if relation.get(p)) or '关系记录'
            return relations[subject]
        if subject not in papers:
            row = db.execute('SELECT payload FROM papers WHERE id=?', (subject,)).fetchone()
            paper = json.loads(row[0]) if row else {}
            papers[subject] = paper.get('citekey') or paper.get('title') or subject
        return papers[subject]

    if selected['source'] == 'agent':
        parents = [dict(r) for r in db.execute('SELECT id,role,subject,status FROM agent_tasks WHERE run_id=?', (key,))]
        units = [dict(r) for r in db.execute("SELECT u.id,u.stage,u.status,u.role,u.payload,u.error,u.attempt,u.updated,t.subject parent_subject FROM agent_units u JOIN agent_tasks t ON t.id=u.parent_id WHERE u.run_id=? ORDER BY CASE u.status WHEN 'running' THEN 0 WHEN 'failed' THEN 1 WHEN 'blocked' THEN 2 WHEN 'interrupted' THEN 3 WHEN 'queued' THEN 4 ELSE 5 END,u.updated DESC,u.id", (key,))]
        planned = bool(parents) or bool(db.execute("SELECT 1 FROM task_events WHERE task_id=? AND stage='planning' AND status='complete'", (key,)).fetchone())
        phases['planning'].update(status='complete' if planned else 'running' if active else 'not_recorded', total=1 if planned else 0, completed=int(planned))
        stages = inputs.get('stages', ['understanding', 'lineage', 'relations'])
        if kind == 'maintenance':
            for pid in phases:
                applicable = pid == 'planning' or (pid == 'reading' and 'understanding' in stages) or (pid in {'identify', 'recall', 'propose'} and 'lineage' in stages) or (pid in {'gather-evidence', 'review-local', 'review-cloud'} and bool({'lineage', 'relations'} & set(stages)))
                if not applicable:
                    phases[pid]['status'] = 'not_applicable'
        buckets = {pid: [] for pid in phases}
        for unit in units:
            pid = unit['stage'].split(':', 1)[0]
            if pid == 'execute' and kind == 'maintenance':
                pid = 'reading'
            if pid in buckets:
                buckets[pid].append(unit)
        closed = {'planning': planned}
        relation_parents = [p for p in parents if p['role'] in {'lineage_researcher', 'verifier'}]
        relations_closed = planned and all(p['status'] == 'complete' for p in relation_parents)
        for pid, phase in phases.items():
            if pid == 'planning' or phase['status'] == 'not_applicable':
                continue
            rows = buckets[pid]
            counts = Counter(r['status'] for r in rows)
            if pid == 'reading':
                can_close = planned and all(p['status'] == 'complete' for p in parents if p['role'] == 'reader')
            elif pid in {'identify', 'recall', 'propose'}:
                predecessor = {'identify': 'planning', 'recall': 'identify', 'propose': 'recall'}[pid]
                can_close = closed.get(predecessor, False)
            else:
                # 补证/核验可循环生成子单元，关系父单元全部结束前不提前打勾。
                can_close = relations_closed
            closed[pid] = can_close and not any(r['status'] != 'complete' for r in rows)
            phase.update(counts=dict(counts), total=len(rows), completed=counts['complete'])
            if counts['running'] and active:
                state = 'running'
            elif counts['failed'] or counts['blocked']:
                state = 'failed' if counts['failed'] else 'blocked'
            elif counts['partial']:
                state = 'partial'
            elif rows and closed[pid]:
                state = 'complete'
            elif rows:
                state = 'queued' if active else 'interrupted'
            elif units and planned:
                state = 'not_applicable' if can_close else 'queued' if active else 'not_recorded'
            else:
                state = 'not_recorded'
            phase['status'] = state
            for unit in rows[:20]:
                data = json.loads(unit['payload'])
                subject = unit['parent_subject']
                title = label(subject)
                target = data.get('target') or {}
                if target:
                    title += ' → ' + (target.get('citekey') or target.get('title') or label(target['id']))
                phase['details'].append(dict(id=unit['id'], title=title, subject=subject, role=unit['role'], status=unit['status'] if active or unit['status'] != 'running' else 'interrupted', error=unit['error'], attempt=unit['attempt']))
            phase['details_truncated'] = len(rows) > 20
            running = [r for r in rows if r['status'] == 'running'] if active else []
            phase['active_count'] = len(running)
            phase['active_items'] = [dict(title=d['title'], subject=d['subject']) for d in phase['details'] if d['status'] == 'running'][:2]
    else:
        # 新作业提供明确阶段事件。旧作业仅映射有证据的活动，不反推其余阶段已完成。
        latest = {}
        current_phase = None
        for event in db.execute('SELECT stage,role,status,message,payload FROM task_events WHERE task_id=? ORDER BY seq', (key,)):
            data = json.loads(event['payload'])
            pid = data.get('dashboard_phase')
            if not pid and kind == 'verification':
                pid = {'prepare': 'planning', 'ocr': 'gather-evidence', 'relation_material': 'gather-evidence', 'supplement': 'gather-evidence', 'local_verification': 'review-local', 'cloud_review': 'review-cloud'}.get(event['stage'])
                if not pid and event['stage'] not in {'model_queue', 'finished', 'review_decision', 'review_cache'}:
                    pid = {'verifier': 'review-local', 'cloud_reviewer': 'review-cloud', 'retriever': 'gather-evidence'}.get(event['role'])
            if pid in phases:
                latest[pid] = dict(event, payload=data)
                current_phase = pid
        for pid, event in latest.items():
            phase = phases[pid]
            state = event['status']
            # 一次模型调用完成不等于整个批次核验完成，只有明确的阶段边界可完成。
            boundary = event['stage'] == 'dashboard_phase'
            phase['status'] = state if boundary else 'running' if active else 'not_recorded'
            if not active and phase['status'] in {'running', 'queued', 'waiting_resource'}:
                phase['status'] = 'interrupted'
            phase['total'] = event['payload'].get('total', 1 if boundary and state == 'complete' else 0)
            phase['completed'] = event['payload'].get('completed', int(boundary and state == 'complete'))
            phase['message'] = event['message']
            phase['details'] = [dict(id=pid, title=event['message'], subject='', role='', status=phase['status'], error='', attempt=None)]
            work = event['payload'].get('work_item') or {}
            if active and pid == current_phase and phase['status'] in {'running', 'waiting_resource'}:
                phase['active_count'] = 1
                phase['active_items'] = [dict(title=work.get('label') or (label(work['subject']) if work.get('subject') else event['message']), subject=work.get('subject', ''))]
    return list(phases.values())
