"""kbapp.research.lineage：由职责边界组织的知识库能力。"""
import json
from kbapp.core.io import digest, now
from kbapp.core.text import locate_quote
from kbapp.research.evidence import artifact, latest, VERSION

def seed_methods(store):
    """用已有明确题名初始化方法入口；名称映射不产生任何继承结论。"""
    definitions = [('LaValle1998Rapidly', 'RRT', ['Rapidly-Exploring Random Trees'], '采样与最优规划'), ('Karaman2011Sampling', 'RRT*', ['RRT∗'], '采样与最优规划'), ('Gammell2014Informed', 'Informed RRT*', [], '采样与最优规划'), ('Gammell2015Batch', 'BIT*', ['Batch Informed Trees'], '采样与最优规划'), ('Amer2026REACT', 'REACT', [], '缆绳约束覆盖')]
    papers = store.papers()
    by_key = {p.get('citekey'): p for p in papers.values()}
    for key, name, aliases, family in definitions:
        p = by_key.get(key)
        if not p:
            continue
        payload = {'name': name, 'aliases': aliases, 'family': family, 'paper_id': p['id'], 'quote': p['title'], 'type': 'MethodConcept', 'input_fingerprint': digest(p['title'] + name + 'user-plan-v1'), 'generation_version': VERSION, 'verification_status': 'title', 'evidence_level': 'title', 'source_id': 'metadata:' + p['id'], 'origin': 'user_plan_and_existing_bibliography', 'role': '方法入口；贡献与继承需另核'}
        mid = 'method:' + digest(name.casefold())[:20]
        with store.db() as db:
            db.execute('INSERT OR IGNORE INTO method_concepts VALUES(?,?,?,?,0,?)', (mid, name, json.dumps([name] + aliases, ensure_ascii=False), json.dumps(payload, ensure_ascii=False), now()))
            existing = db.execute('SELECT payload,manual FROM method_concepts WHERE id=?', (mid,)).fetchone()
            if existing and (not existing['manual']):
                merged = dict(payload, **json.loads(existing['payload']))
                if merged != json.loads(existing['payload']):
                    db.execute('UPDATE method_concepts SET payload=? WHERE id=?', (json.dumps(merged, ensure_ascii=False), mid))
            db.execute('INSERT OR IGNORE INTO paper_methods VALUES(?,?,?)', (p['id'], mid, json.dumps(payload, ensure_ascii=False)))

def reassess_relation(store, rid, should_stop=lambda: False, progress=lambda *a: None, prepared=None):
    from kbapp.research.lineage_material import gather, assess
    with store.db() as db:
        row = db.execute('SELECT payload,manual,status FROM relations WHERE id=?', (rid,)).fetchone()
    if not row:
        raise ValueError('关系不存在')
    if row['manual'] or row['status']=='rejected':
        return {'preserved_manual':True,'relation_id':rid}
    material = prepared if prepared is not None else gather(store, json.loads(row['payload']), should_stop, progress)
    return assess(store, material, 'existing:'+rid, should_stop)

def candidate_neighbors(store, paper, limit=24, should_stop=lambda: False):
    """F01–F05：引用、相似、方法与共引通道分别召回，按配额合并，不做全库两两比较。"""
    from kbapp.research.lineage_material import snapshot
    from kbapp.research.candidates import collect
    frozen = snapshot(store)
    papers = frozen['papers']
    candidates, stop_reason = collect(store, paper, frozen, limit=limit, should_stop=should_stop)
    return [dict(papers[c['paper_id']], candidate_reasons=c['reasons'], candidate_channels=c['channels'],
                 candidate_score=c['score'], candidate_stop_reason=stop_reason)
            for c in candidates if c['paper_id'] in papers]

def text_complete(store, prompt, purpose, max_tokens):
    """G01：方向分类、方法识别与候选提议可配置为本地优先；本地失败不回退云端（避免静默改变成本与语义）。"""
    cfg = store.settings()
    if cfg.get('relation_extractor') == 'local':
        from kbapp.integrations.local_model import complete_local
        return complete_local(store, prompt, purpose, max_tokens=min(max_tokens, 1400))
    from kbapp.integrations.cloud import complete
    return complete(store, prompt, purpose, max_tokens=max_tokens)

def classify_paper(store, paper, should_stop=lambda: False, progress=lambda *a: None):
    from kbapp.core.markers import STAGES, PROJECT
    from kbapp.library.network import ensure_research_metadata
    paper = ensure_research_metadata(store, paper)
    if should_stop():
        raise ValueError('任务已停止')
    text = '\n'.join((x for x in [paper.get('title'), paper.get('abstract')] if x))
    source = {'id': 'abstract:' + paper['id'] + ':' + digest(text)[:12], 'paper_id': paper['id'], 'kind': 'external_abstract' if paper.get('kind') != 'library' else 'metadata', 'text': text, 'url': paper.get('doi_url') or ('https://doi.org/' + paper['doi'] if paper.get('doi') else '')}
    from kbapp.research.prompts import build as build_prompt
    from kbapp.research.model_dependencies import extraction_fingerprint
    prompt = build_prompt('classification', source=source)
    fingerprint = extraction_fingerprint(store, prompt, 'lineage_classification', 'lineage-v4')
    prior = latest(store, 'Classification', paper['id'])
    if not prior or prior['input_fingerprint'] != fingerprint:
        progress('谱系研究者', '识别文献方向与方法实体')
        raw = latest(store, 'ClassificationProposal', paper['id'])
        if not raw or raw['input_fingerprint'] != fingerprint:
            result = text_complete(store, prompt, 'lineage_classification', 1800)
            raw = artifact(store, 'ClassificationProposal', paper['id'], fingerprint, {'proposal':result}, 'candidate')
        result = raw['proposal']
        quote = locate_quote(text, result.get('quote', ''), join_pdf_words=True)
        if not quote:
            raise ValueError('分类依据不能定位到题摘')
        directions = [d for d in result.get('directions', []) if d in {'00', '01', '02', '03', '04', '05', '06', '07', '08', '09', '90'}]
        data = {'directions': directions, 'project': PROJECT if result.get('project_relevance') is True else '', 'project_stages': [STAGES[d] for d in directions if d in STAGES], 'reason': result.get('reason', ''), 'quote': quote, 'source_id': source['id'], 'evidence_level': 'abstract' if paper.get('abstract') else 'title', 'abstract_scope': paper.get('abstract_scope'), 'input_fingerprint': fingerprint, 'automatic': True}
        with store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT manual FROM paper_classifications WHERE paper_id=?', (paper['id'],)).fetchone()
            if not row or not row[0]:
                db.execute('INSERT OR REPLACE INTO paper_classifications VALUES(?,?,0,?)', (paper['id'], json.dumps(data, ensure_ascii=False), now()))
            for method in result.get('methods', []):
                if not method.get('name') or not method.get('quote') or not locate_quote(text, method['quote'], join_pdf_words=True):
                    continue
                method = dict(method, quote=locate_quote(text, method['quote'], join_pdf_words=True))
                name = method['name'].strip()
                aliases = list(dict.fromkeys([name] + [a for a in method.get('aliases', []) if isinstance(a, str)]))
                concepts = db.execute('SELECT * FROM method_concepts').fetchall()
                existing = next((r for r in concepts if r['name'].casefold() == name.casefold()), None)
                if not existing:
                    matches = [r for r in concepts if set((a.casefold() for a in json.loads(r['aliases']))) & set((a.casefold() for a in aliases))]
                    existing = matches[0] if len(matches) == 1 else None
                mid = existing['id'] if existing else 'method:' + digest(name.casefold())[:20]
                foreign_names = {r['name'].casefold() for r in concepts if r['id'] != mid}
                aliases = [a for a in aliases if a.casefold() not in foreign_names]
                payload = dict(method, aliases=aliases, type='MethodConcept', source_id=source['id'], paper_id=paper['id'], evidence_level=data['evidence_level'], input_fingerprint=fingerprint, generation_version=VERSION, verification_status=data['evidence_level'])
                if not existing:
                    db.execute('INSERT INTO method_concepts VALUES(?,?,?,?,0,?)', (mid, name, json.dumps(aliases, ensure_ascii=False), json.dumps(payload, ensure_ascii=False), now()))
                elif not existing['manual']:
                    aliases = [a for a in dict.fromkeys(json.loads(existing['aliases']) + aliases) if a.casefold() not in foreign_names]
                    db.execute('UPDATE method_concepts SET aliases=?,updated=? WHERE id=?', (json.dumps(aliases, ensure_ascii=False), now(), mid))
                prior_mapping = db.execute('SELECT payload FROM paper_methods WHERE paper_id=? AND method_id=?', (paper['id'], mid)).fetchone()
                if not prior_mapping or not json.loads(prior_mapping[0]).get('manual'):
                    db.execute('INSERT OR REPLACE INTO paper_methods VALUES(?,?,?)', (paper['id'], mid, json.dumps(payload, ensure_ascii=False)))
        artifact(store, 'Classification', paper['id'], fingerprint, data, 'abstract' if paper.get('abstract') else 'title')
    if should_stop():
        raise ValueError('任务已停止')
    return {'paper': paper, 'source': source, 'fingerprint': fingerprint, 'classification': latest(store, 'Classification', paper['id'])}


def pair_identity(context, target, store=None):
    paper = context['paper']
    public = {k: target.get(k) for k in ('id', 'title', 'year', 'abstract')}
    fingerprint = digest(context['fingerprint'] + json.dumps(public, sort_keys=True, ensure_ascii=False))
    if store is not None:
        from kbapp.research.prompts import build
        from kbapp.research.model_dependencies import extraction_fingerprint
        fingerprint = extraction_fingerprint(store, build('abstract_lineage', source=context['source'], target=public),
                                             'abstract_lineage', fingerprint)
    return paper['id'] + '|' + target['id'], fingerprint


def propose_pair(store, context, target, should_stop=lambda: False, progress=lambda *a: None):
    paper, source = context['paper'], context['source']
    subject, pair_fp = pair_identity(context, target, store)
    prior = latest(store, 'RelationAssessment', subject)
    if prior and prior['input_fingerprint'] == pair_fp and prior.get('relation') is None:
        return {'assessment': prior}
    prior = latest(store, 'RelationProposal', subject)
    if prior and prior['input_fingerprint'] == pair_fp:
        return prior
    if should_stop():
        raise ValueError('任务已停止')
    if not paper.get('abstract'):
        return {'assessment': artifact(store, 'RelationAssessment', subject, pair_fp, {'relation': None, 'candidate_reasons': target.get('candidate_reasons', [])}, 'unsupported')}
    progress('谱系研究者', '提出候选方法关系 · ' + (target.get('citekey') or target['title']))
    public = {k: target.get(k) for k in ('id', 'title', 'year', 'abstract')}
    from kbapp.research.prompts import build as build_prompt
    proposal = text_complete(store, build_prompt('abstract_lineage', source=source, target=public), 'abstract_lineage', 1800)
    # Save an already returned model result before honoring a stop at the next boundary.
    from kbapp.research.lineage_material import fixed_relation
    proposal = dict(proposal, relation=fixed_relation(context, target, proposal.get('relation'), legacy=False))
    return artifact(store, 'RelationProposal', subject, pair_fp, {'proposal': proposal}, 'candidate')


def assess_pair(store, context, target, proposal_record, should_stop=lambda: False, progress=lambda *a: None, prepared=None):
    from kbapp.research.lineage_material import fixed_relation, gather, assess
    if 'assessment' in proposal_record:
        return proposal_record['assessment']
    subject, fp = pair_identity(context, target, store)
    relation = fixed_relation(context, target, proposal_record['proposal'].get('relation'))
    if relation is None:
        return artifact(store, 'RelationAssessment', subject, fp, {'relation':None}, 'not_applicable')
    material = prepared if prepared is not None else gather(store, relation, should_stop, progress)
    return assess(store, material, subject, should_stop)


def classify_and_assess(store, paper, should_stop=lambda: False, progress=lambda *a: None):
    """同步调用沿用相同的分类、候选与逐对核验能力。"""
    context = classify_paper(store, paper, should_stop, progress)
    paper = context['paper']
    progress('证据检索者', '检索引用邻居与方法近邻')
    candidates = candidate_neighbors(store, paper, should_stop=should_stop)
    for target in candidates:
        proposal = propose_pair(store, context, target, should_stop, progress)
        assess_pair(store, context, target, proposal, should_stop, progress)
    return {'paper_id': paper['id'], 'candidate_count': len(candidates), 'classification': context['classification']}
