"""HTTP 路由：graph_api；通过 AppContext 使用业务能力。"""
from __future__ import annotations
import json
import time
from fastapi import HTTPException
from kbapp.core.io import digest, now
from kbapp.research.graph import graph_page, chains, overview, timeline
from kbapp.research.relations import opportunities
from kbapp.workflows.export import export_static

def install(app, context, token):
    store = context.store

    @app.get('/api/graph')
    def graph(direction: str='', project: bool=False, q: str='', paper: str='', offset: int=0, limit: int=180, candidates: bool=False, method: str=''):
        return graph_page(store, direction, project, q, paper, max(offset, 0), min(max(limit, 1), 500), candidates, method)

    @app.get('/api/chains')
    def chain_list(compact: bool=False):
        from kbapp.research.graph import compact_chains
        items = chains(store)
        return {'items': compact_chains(items) if compact else items}

    @app.get('/api/chains/summary')
    def chain_summary(offset: int=0, limit: int=20, sort: str='value', direction: str='', project: bool=False,
                      q: str='', method: str='', candidates: bool=True, revision: str=''):
        from kbapp.research.graph import chains_page, ChainRevisionChanged
        try:
            return chains_page(store, offset, limit, sort, direction, project, q, method, candidates, revision)
        except ChainRevisionChanged as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get('/api/chains/{branch_id}')
    def chain_branch(branch_id: str, offset: int=0, limit: int=40, direction: str='', project: bool=False,
                     q: str='', method: str='', candidates: bool=True, revision: str='', paper_offset: int=0, paper_limit: int=40):
        from kbapp.research.graph import chain_detail, ChainRevisionChanged
        try:
            result = chain_detail(store, branch_id, offset, limit, direction, project, q, method, candidates, revision, paper_offset, paper_limit)
        except ChainRevisionChanged as exc:
            raise HTTPException(409, str(exc)) from exc
        if result is None:
            raise HTTPException(404, '研究分支不存在')
        return result

    @app.get('/api/overview')
    def direction_overview():
        return overview(store)

    @app.get('/api/timeline')
    def timeline_list(direction: str='', project: bool=False, q: str='', compact: bool=False):
        result = timeline(store, direction, project, q)
        if compact:
            from kbapp.research.graph import compact_chains, relation_card
            groups = [dict(g, paths=[dict(p, edges=[relation_card(e) for e in p['edges']]) for p in g['paths']]) for g in result['groups']]
            result = dict(result, groups=groups, method_families=compact_chains(result['method_families']))
        return result

    @app.get('/api/relation')
    def relation_detail(rid: str):
        with store.db() as db:
            row = db.execute('SELECT payload FROM relations WHERE id=?', (rid,)).fetchone()
        if not row:
            raise HTTPException(404, '关系不存在')
        from kbapp.research.viewmodels import display_relation
        return display_relation(json.loads(row[0]))

    @app.post('/api/graph/edges')
    def selected_edges(data: dict):
        ids = list(dict.fromkeys(data.get('ids', [])))
        if not ids:
            return {'edges': []}
        if len(ids) > 2000 or any((not isinstance(x, str) for x in ids)):
            raise ValueError('请缩小到2000个节点以内后继续展开')
        marks = ','.join(('?' for _ in ids))
        statuses = "status!='rejected'" if data.get('candidates') else "status='confirmed'"
        with store.db() as db:
            rows = db.execute(f'SELECT payload FROM relations WHERE {statuses} AND src IN ({marks}) AND dst IN ({marks})', ids + ids).fetchall()
        from kbapp.research.viewmodels import display_relation
        edges = [display_relation(json.loads(r[0])) for r in rows]
        if not data.get('candidates'):
            edges = [e for e in edges if e['evidence_grade'] in {'citation', 'fulltext'}]
        return {'edges': edges}

    @app.get('/api/methods')
    def method_list(direction: str='', project: bool=False, q: str=''):
        from kbapp.research.graph import graph_methods
        return graph_methods(store, direction, project, q)

    @app.post('/api/methods/{mid}')
    def correct_method(mid: str, payload: dict):
        from kbapp.research.evidence import artifact
        with store.db() as db:
            row = db.execute('SELECT * FROM method_concepts WHERE id=?', (mid,)).fetchone()
        if not row:
            raise ValueError('方法实体不存在')
        name = payload.get('name', row['name'])
        aliases = payload.get('aliases', json.loads(row['aliases']))
        if not isinstance(name, str) or not name.strip() or len(name) > 200 or (not isinstance(aliases, list)) or any((not isinstance(a, str) or len(a) > 200 for a in aliases)):
            raise ValueError('方法名和别名格式无效')
        old = json.loads(row['payload'])
        artifact(store, 'MethodHistory', mid, digest(str(time.time_ns())), dict(row), 'manual_history')
        updated = dict(old, name=name.strip(), aliases=list(dict.fromkeys([name.strip()] + aliases)), family=str(payload.get('family', old.get('family', '')))[:200], manual=True)
        with store.db() as db:
            db.execute('UPDATE method_concepts SET name=?,aliases=?,payload=?,manual=1,updated=? WHERE id=?', (updated['name'], json.dumps(updated['aliases'], ensure_ascii=False), json.dumps(updated, ensure_ascii=False), now(), mid))
        export_static(store)
        return updated

    @app.post('/api/classifications/{pid}')
    def correct_classification(pid: str, payload: dict):
        from kbapp.research.evidence import artifact
        pid = store.resolve(pid)
        if pid not in store.papers():
            raise ValueError('文献不存在')
        directions = payload.get('directions', [])
        if not isinstance(directions, list) or any((d not in {'00', '01', '02', '03', '04', '05', '06', '07', '08', '09', '90'} for d in directions)):
            raise ValueError('方向编号无效')
        from kbapp.core.markers import PROJECT, STAGES
        data = {'directions': directions, 'project': PROJECT if payload.get('project') is True else '', 'project_stages': [STAGES[d] for d in directions if d in STAGES], 'automatic': False, 'reason': str(payload.get('reason', '人工修正'))[:1000]}
        with store.db() as db:
            row = db.execute('SELECT payload FROM paper_classifications WHERE paper_id=?', (pid,)).fetchone()
            if row:
                artifact(store, 'ClassificationHistory', pid, digest(str(time.time_ns())), json.loads(row[0]), 'manual_history')
            db.execute('INSERT OR REPLACE INTO paper_classifications VALUES(?,?,1,?)', (pid, json.dumps(data, ensure_ascii=False), now()))
        export_static(store)
        return data

    @app.get('/api/relations')
    def relations(status: str='candidate', offset: int=0, limit: int=30):
        if status not in {'candidate', 'confirmed', 'rejected'}:
            raise ValueError('关系状态无效')
        items = store.relations(status)
        papers = store.papers()
        # H04：稳定排序——课题优先、有证据优先、最近更新优先，最后按 id 固定次序
        items.sort(key=lambda r: (not bool(papers.get(r['src'], {}).get('project')), not bool(r.get('evidence')), str(r.get('updated') or ''), r['id']), reverse=True)
        start = max(0, offset)
        size = min(max(limit, 1), 100)
        page = items[start:start + size]
        return {'items': page, 'total': len(items), 'offset': start, 'limit': size, 'has_more': start + size < len(items), 'papers': {x: papers[x] for r in page for x in (r['src'], r['dst']) if x in papers}}

    @app.post('/api/relations/{rid}')
    def correct_relation(rid: str, values: dict):
        with store.db() as db:
            row = db.execute('SELECT payload FROM relations WHERE id=?', (rid,)).fetchone()
        if not row:
            raise HTTPException(404, '关系不存在')
        data = json.loads(row[0])
        data.update({k: v for k, v in values.items() if k in {'kind', 'status', 'explanation', 'previous_problem', 'change', 'conditions'}})
        if values.get('reverse'):
            data['src'], data['dst'] = (data['dst'], data['src'])
        store.relation(data, manual=True)
        from kbapp.workflows.reports import publish_lineage
        publish_lineage(store)
        export_static(store)
        return {'id': rid, 'state': 'saved'}

    @app.get('/api/opportunities')
    def get_opportunities():
        return store.state('opportunities', {'items': [], 'message': '研究机会将在有足够证据并启用云端分析后生成。'})

    @app.post('/api/opportunities')
    def create_opportunities():
        return opportunities(store)
