"""子单元业务调用；持久化与调度交给 agent_units。"""
import json
from kbapp.core.io import digest
from kbapp.workflows import agent_units


def child(stage, role, subject, payload, object_key=None):
    return dict(stage=stage, role=role, subject=subject, payload=payload, object_key=object_key)


def perform(store, unit, stopped, progress, execute_parent):
    from kbapp.research import lineage
    from kbapp.research.review import run_phase
    stage = agent_units.phase(unit['stage'])
    data = unit['payload']
    if stage == 'execute':
        return execute_parent(), [], False
    if stage == 'identify':
        with store.db() as db:
            row = db.execute('SELECT payload FROM papers WHERE id=?', (unit['subject'],)).fetchone()
        if not row:
            raise ValueError('文献记录不存在')
        context = lineage.classify_paper(store, json.loads(row[0]), stopped, progress)
        return context, [child('recall', 'retriever', unit['subject'], {'context_id': unit['id']})], False
    if stage == 'recall':
        context = agent_units.result(store, data['context_id'])
        progress('证据检索者', '召回引用邻居与方法近邻')
        candidates = lineage.candidate_neighbors(store, context['paper'], should_stop=stopped)
        children = []
        for n, target in enumerate(candidates, 1):
            target = {k: target.get(k) for k in ('id', 'title', 'year', 'abstract', 'citekey', 'candidate_reasons')}
            options = dict(context_id=data['context_id'], target=target, position=n, total=len(candidates))
            children.append(child('propose:'+target['id'], 'lineage_researcher', unit['subject']+'|'+target['id'], options))
        return {'candidate_count': len(candidates)}, children, False
    if stage == 'propose':
        context = agent_units.result(store, data['context_id'])
        target = data['target']
        progress('谱系研究者', f"候选 {data['position']}/{data['total']} · {target.get('citekey') or target['title']}")
        proposal = lineage.propose_pair(store, context, target, stopped, progress)
        if 'assessment' in proposal:
            return proposal, [], False
        from kbapp.research.lineage_material import fixed_relation
        relation = fixed_relation(context, target, proposal['proposal'].get('relation'))
        if not isinstance(relation, dict):
            result = lineage.assess_pair(store, context, target, proposal, stopped, progress)
            return result, [], False
        key = relation.get('id') or digest(f"{relation.get('src')}|{relation.get('dst')}|{relation.get('kind')}")[:24]
        options = dict(data, mode='proposal', proposal_id=unit['id'], cycle=0)
        return proposal, [child('gather-evidence:'+unit['id'], 'retriever', unit['subject'], options, 'relation:'+key)], False
    if stage == 'gather-evidence':
        from kbapp.research.lineage_material import fixed_relation, gather, assess
        if data.get('mode') == 'existing':
            with store.db() as db:
                row = db.execute('SELECT payload,manual,status FROM relations WHERE id=?', (unit['subject'].removeprefix('relation:'),)).fetchone()
            if not row:
                raise ValueError('关系不存在')
            if row['manual'] or row['status']=='rejected':
                return {'preserved_manual':True}, [], False
            relation = json.loads(row['payload'])
            subject = 'existing:'+relation['id']
        else:
            context = agent_units.result(store, data['context_id'])
            proposal = agent_units.result(store, data['proposal_id'])
            relation = fixed_relation(context, data['target'], proposal['proposal'].get('relation'))
            subject = context['paper']['id']+'|'+data['target']['id']
        material = gather(store, relation, stopped, progress)
        if material['verification_status'] != 'ready':
            return assess(store, material, subject, stopped), [], False
        options = dict(data, material_id=unit['id'])
        return material, [child('review-local:material:'+unit['id'], 'verifier', unit['subject'], options, unit['object_key'])], False
    if stage in {'review-local', 'review-cloud'}:
        phase = 'local' if stage == 'review-local' else 'cloud'
        if data.get('mode') == 'existing':
            def action():
                return lineage.reassess_relation(store, unit['subject'].removeprefix('relation:'), stopped, progress, prepared=agent_units.result(store,data['material_id']) if data.get('material_id') else None)
        else:
            context = agent_units.result(store, data['context_id'])
            proposal = agent_units.result(store, data['proposal_id'])
            def action():
                return lineage.assess_pair(store, context, data['target'], proposal, stopped, progress, prepared=agent_units.result(store,data['material_id']) if data.get('material_id') else None)
        result, pending = run_phase(phase, action)
        if pending:
            cycle = data.get('cycle', 0) + 1
            if cycle > 10:
                raise ValueError('核验材料持续变化，已保存阶段结果，请明确续跑')
            next_stage = 'gather-evidence' if pending=='material' else 'review-'+pending
            next_role = 'retriever' if pending=='material' else 'verifier' if pending == 'local' else 'cloud_reviewer'
            options = dict(data, cycle=cycle)
            next_unit = child(next_stage+':'+str(cycle)+':'+unit['parent_id']+':'+unit['subject'], next_role, unit['subject'], options, unit['object_key'])
            return {'next_phase': pending}, [next_unit], False
        partial = data.get('mode') == 'existing' and result.get('verification_status') in {'partial','unsupported','insufficient','conflict','deferred'}
        return result, [], partial
    raise ValueError('未知子单元阶段：'+stage)
