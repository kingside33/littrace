"""原任务的定向修复：预览不调用模型，应用只追加可恢复步骤。"""
import json
from collections import Counter
from kbapp.core.io import digest, now
from kbapp.research.lineage_material import RULE_VERSION


def preview(db, rid):
    run = db.execute('SELECT * FROM agent_runs WHERE id=?',(rid,)).fetchone()
    if not run or run['kind'] not in {'lineage','maintenance'}:
        raise ValueError('仅支持既有谱系或维护任务')
    parents = {r['subject']:dict(r) for r in db.execute("SELECT * FROM agent_tasks WHERE run_id=? AND role='lineage_researcher'",(rid,))}
    actions = []
    for r in db.execute("SELECT * FROM agent_units WHERE run_id=? AND stage='identify' AND status='failed'",(rid,)):
        actions.append(dict(kind='classification',parent_id=r['parent_id'],subject=r['subject'],unit_id=r['id']))
    proposals = {}
    for r in db.execute("SELECT payload FROM research_artifacts WHERE type='RelationProposal' ORDER BY updated,rowid"):
        record = json.loads(r[0])
        source = record['subject'].split('|',1)[0]
        if source in parents and isinstance((record.get('proposal') or {}).get('relation'),dict):
            proposals[record['subject']] = record
    for subject, record in proposals.items():
        source,target = subject.split('|',1)
        actions.append(dict(kind='proposal',parent_id=parents[source]['id'],subject=subject,source=source,target=target,record=record))
    for r in db.execute("SELECT t.* FROM agent_tasks t JOIN relations r ON r.id=substr(t.subject,10) WHERE t.run_id=? AND t.role='verifier' AND t.subject LIKE 'relation:%' AND r.manual=0 AND r.status!='rejected'",(rid,)):
        actions.append(dict(kind='existing',parent_id=r['id'],subject=r['subject']))
    selected = []
    stages = {r[0] for r in db.execute('SELECT stage FROM agent_units WHERE run_id=?',(rid,))}
    markers = {r[0] for r in db.execute("SELECT key FROM state WHERE key LIKE 'repair-classification:%'")}
    for action in actions:
        marker = digest(RULE_VERSION+'|'+action['kind']+'|'+action['subject'])[:24]
        action['marker'] = marker
        if 'gather-evidence:repair:'+marker not in stages:
            if action['kind']=='classification' and 'repair-classification:'+marker in markers:
                continue
            selected.append(action)
    return selected


def repair(store, rid, apply=False, limit=None, subject=None):
    from kbapp.workflows import agent_units
    with store.db() as db:
        actions = preview(db,rid)
        if subject:
            actions = [a for a in actions if a['subject']==subject]
    report = dict(run_id=rid, rule_version=RULE_VERSION, pending=dict(Counter(a['kind'] for a in actions)), total=len(actions), applied=False)
    if not apply:
        report['examples'] = [{k:v for k,v in a.items() if k!='record'} for a in actions[:5]]
        return report
    if limit is not None:
        actions = actions[:max(0,limit)]
    papers = store.papers()
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute("SELECT 1 FROM agent_units WHERE run_id=? AND status='running'",(rid,)).fetchone() or db.execute("SELECT 1 FROM agent_runs WHERE id=? AND status IN ('running','queued','stopping')",(rid,)).fetchone():
            raise ValueError('请等待原任务停止并落盘后应用修复')
        actions = preview(db,rid)
        if subject:
            actions = [a for a in actions if a['subject']==subject]
        if limit is not None:
            actions = actions[:max(0,limit)]
        for action in actions:
            parent = dict(db.execute('SELECT * FROM agent_tasks WHERE id=?',(action['parent_id'],)).fetchone())
            if action['kind']=='classification':
                db.execute("UPDATE agent_units SET status='queued',error='',updated=? WHERE id=?",(now(),action['unit_id']))
                db.execute('INSERT OR REPLACE INTO state VALUES(?,?)',('repair-classification:'+action['marker'],json.dumps({'applied':now()})))
            else:
                if action['kind']=='existing':
                    options = dict(mode='existing',cycle=0)
                    object_key = action['subject']
                else:
                    paper = papers[action['source']]
                    row = db.execute("SELECT payload FROM research_artifacts WHERE type='Classification' AND subject=? ORDER BY updated DESC,rowid DESC LIMIT 1",(paper['id'],)).fetchone()
                    if not row:
                        raise ValueError('修复提议缺少已保存的分类：'+paper['id'])
                    classification=json.loads(row[0])
                    text='\n'.join(x for x in (paper.get('title'),paper.get('abstract')) if x)
                    context=dict(paper=paper,source=dict(id=classification.get('source_id') or 'abstract:'+paper['id']+':'+digest(text)[:12],paper_id=paper['id'],kind='metadata' if paper.get('kind')=='library' else 'external_abstract',text=text),fingerprint=classification['input_fingerprint'],classification=classification)
                    original = db.execute("SELECT result FROM agent_units WHERE parent_id=? AND stage='identify' AND status='complete'",(parent['id'],)).fetchone()
                    if original and json.loads(original[0]).get('paper',{}).get('id')==paper['id']:
                        context=json.loads(original[0])
                    context_id = agent_units.add(db,parent,'identify:recovered:'+action['marker'],'lineage_researcher',paper['id'])
                    db.execute("UPDATE agent_units SET status='complete',result=? WHERE id=?",(json.dumps(context,ensure_ascii=False),context_id))
                    proposal_id = agent_units.add(db,parent,'propose:recovered:'+action['marker'],'lineage_researcher',action['subject'])
                    db.execute("UPDATE agent_units SET status='complete',result=? WHERE id=?",(json.dumps(dict(action['record'],verification_status='candidate'),ensure_ascii=False),proposal_id))
                    options=dict(mode='proposal',context_id=context_id,proposal_id=proposal_id,target=papers[action['target']],cycle=0)
                    rel=action['record']['proposal']['relation']
                    object_key='relation:'+digest(action['source']+'|'+action['target']+'|'+str(rel.get('kind')))[:24]
                    db.execute("UPDATE research_artifacts SET status='candidate',payload=json_set(payload,'$.verification_status','candidate') WHERE id=? AND type='RelationProposal'",(action['record']['id'],))
                agent_units.add(db,parent,'gather-evidence:repair:'+action['marker'],'retriever',action['subject'],options,object_key=object_key)
                # These old steps executed successfully but could not establish evidence; retain their results.
                db.execute("UPDATE agent_units SET status='complete' WHERE parent_id=? AND status='partial'",(parent['id'],))
            db.execute("UPDATE agent_tasks SET status='queued',error='',updated=? WHERE id=?",(now(),parent['id']))
        if actions:
            payload=json.dumps({'rule_version':RULE_VERSION,'actions':dict(Counter(a['kind'] for a in actions)),'markers':[a['marker'] for a in actions]},ensure_ascii=False)
            db.execute("INSERT INTO task_events(task_id,step_id,role,stage,status,message,payload,created) VALUES(?,'','coordinator','repair','interrupted',?,?,?)",(rid,'定向修复步骤已保存，等待原任务续跑',payload,now()))
            db.execute("UPDATE agent_runs SET status='interrupted',stop=1,error='',updated=? WHERE id=?",(now(),rid))
            db.execute('INSERT INTO state VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',('lineage_repair:'+rid,json.dumps({'rule_version':RULE_VERSION,'applied':now(),'actions':len(actions)},ensure_ascii=False)))
    report.update(applied=True,scheduled=len(actions),message='修复步骤已保存；按原任务ID续跑')
    return report
