"""执行进度与证据结论分别统计；只在任务终结时保存汇总。"""
import json
from collections import Counter
from kbapp.core.io import digest, now


def summary(store, rid):
    with store.db() as db:
        execution = dict(db.execute('SELECT status,COUNT(*) FROM agent_tasks WHERE run_id=? GROUP BY status',(rid,)))
        outcomes = {}
        for r in db.execute("SELECT parent_id,subject,result FROM agent_units WHERE run_id=? AND (stage LIKE 'review-%' OR stage LIKE 'gather-evidence%') ORDER BY rowid",(rid,)):
            data = json.loads(r['result'])
            status = data.get('verification_status')
            if status in {'verified','abstract','unsupported','insufficient','conflict','deferred','structure_error'}:
                relation = data.get('relation') or {}
                if status=='unsupported':
                    verdict = relation.get('verification',{}).get('status')
                    if verdict:
                        status = verdict
                    elif str(relation.get('src','')).startswith('abstract:') or any('年份冲突' in str(g) for g in data.get('gaps',[])):
                        status = 'structure_error'
                    else:
                        status = 'insufficient'
                key = (r['parent_id'], r['subject'])
                outcomes[key] = status
        counts = Counter(outcomes.values())
        unit_execution = dict(db.execute('SELECT status,COUNT(*) FROM agent_units WHERE run_id=? GROUP BY status',(rid,)))
        usage = dict(db.execute('SELECT COUNT(*) calls,SUM(t.input_tokens) input_tokens,SUM(t.output_tokens) output_tokens,SUM(u.actual) cost,CASE WHEN COUNT(t.usage_id)=COUNT(*) THEN MIN(t.cost_known) ELSE 0 END priced,SUM(CASE WHEN u.status IN (\'uncertain_or_failed\',\'reserved\') THEN 1 ELSE 0 END) uncertain_calls FROM task_usage a JOIN usage u ON u.id=a.usage_id LEFT JOIN token_usage t ON t.usage_id=u.id WHERE a.task_id=?',(rid,)).fetchone())
        cache = getattr(store,'_run_metrics_cache',{})
        cursor,local_calls,cache_hits = cache.get(rid,(0,0,0))
        for r in db.execute('SELECT seq,status,payload FROM task_events WHERE task_id=? AND seq>? ORDER BY seq',(rid,cursor)):
            local_calls += bool(json.loads(r['payload']).get('local_call'))
            cache_hits += r['status']=='cached'
            cursor = r['seq']
        cache[rid] = (cursor,local_calls,cache_hits)
        store._run_metrics_cache = cache
        row = db.execute('SELECT kind,input FROM agent_runs WHERE id=?',(rid,)).fetchone()
    if not usage['priced']:
        usage['cost'] = None
    stages=json.loads(row['input']).get('stages',['understanding','lineage','relations'])
    return dict(execution_counts=execution, unit_execution_counts=unit_execution, verification_counts=dict(counts), usage=usage, local_calls=local_calls if cursor else None, cache_hits=cache_hits if cursor else None, stage_applicability={'fulltext_reading':row['kind']=='maintenance' and 'understanding' in stages,'relation_discovery':row['kind'] in {'lineage','maintenance'},'bilateral_evidence_required':row['kind'] in {'lineage','maintenance'}}, pending_reasons={'insufficient':'原文证据不足或全文待审批','unsupported':'证据反驳','conflict':'模型结论冲突','deferred':'核验请求待续跑'})


def save_summary(store, rid):
    value = summary(store, rid)
    fingerprint = digest(json.dumps(value, sort_keys=True, ensure_ascii=False))
    with store.db() as db:
        row = db.execute('SELECT result,status FROM agent_runs WHERE id=?',(rid,)).fetchone()
        result = json.loads(row['result'])
        if result.get('summary_fingerprint') == fingerprint:
            return
        result.update(summary=value, summary_fingerprint=fingerprint, finished=now())
        db.execute('UPDATE agent_runs SET result=? WHERE id=?',(json.dumps(result,ensure_ascii=False),rid))
        db.execute("INSERT INTO task_events(task_id,step_id,role,stage,status,message,payload,created) VALUES(?,'','coordinator','run_summary',?,?,?,?)",(rid,row['status'],'本轮执行已结束；处理进度与证据结果已分别汇总',json.dumps(value,ensure_ascii=False),now()))
