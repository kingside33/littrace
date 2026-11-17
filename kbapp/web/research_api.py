"""HTTP 路由：research_api；通过 AppContext 使用业务能力。"""
from __future__ import annotations
import json
from kbapp.core.markers import PROJECT

def install(app, context, token):
    store = context.store

    @app.post('/api/verification')
    def verify_relations(payload: dict):
        from kbapp.workflows.verification import control_verification
        return control_verification(store, payload)

    @app.get('/api/agent-runs')
    def agent_runs():
        from kbapp.workflows.agents import list_runs, ROLES
        return {'items': list_runs(store), 'roles': ROLES}

    @app.get('/api/task-dashboard')
    def task_dashboard(task_id: str='', after_event_id: int=0, limit: int=100, kind: str=''):
        from kbapp.workflows.dashboard import dashboard
        return dashboard(store, task_id, after_event_id, limit, kind)

    @app.post('/api/agent-runs')
    def new_agent_run(payload: dict):
        from kbapp.workflows.agents import create_run
        return create_run(store, payload)

    @app.get('/api/agent-runs/{rid}')
    def agent_run(rid: str):
        from kbapp.workflows.agents import get_run
        return get_run(store, rid)

    @app.post('/api/agent-runs/{rid}/control')
    def control_agent_run(rid: str, payload: dict):
        from kbapp.workflows.agents import control_run
        return control_run(store, rid, payload.get('action'))

    @app.get('/api/research-overview')
    def research_overview():
        from kbapp.workflows.agents import list_runs
        from kbapp.research.evidence import latest_many
        from kbapp.library.catalog import enriched_papers
        from kbapp.research.viewmodels import relation_grade, METHOD_KINDS
        papers = enriched_papers(store)
        library_ids = [pid for pid in papers if papers[pid].get('kind') == 'library']
        batch = latest_many(store, 'PaperUnderstanding', library_ids)
        understandings = [batch.get(pid) for pid in library_ids]
        with store.db() as db:
            methods = [dict(r, id=r['id'], aliases=json.loads(r['aliases']), payload=json.loads(r['payload'])) for r in db.execute('SELECT * FROM method_concepts ORDER BY name')]
            conflicts = db.execute("SELECT COUNT(DISTINCT replace(paper_id,'::metadata','')) FROM note_sync WHERE status='conflict'").fetchone()[0]
            classifications = db.execute('SELECT COUNT(*) FROM paper_classifications').fetchone()[0]
        rels = [r for r in store.relations() if r['status'] != 'rejected' and r['kind'] in METHOD_KINDS]
        return {'project': PROJECT, 'library': sum((p.get('kind') == 'library' for p in papers.values())), 'read_complete': sum((bool(u and u.get('coverage', {}).get('complete')) for u in understandings)), 'read_partial': sum((bool(u and (not u.get('coverage', {}).get('complete'))) for u in understandings)), 'methods': methods, 'lineage_coverage': {grade: sum((relation_grade(r) == grade for r in rels)) for grade in ('fulltext', 'abstract', 'title')}, 'classified': classifications, 'note_conflicts': conflicts, 'tasks': list_runs(store), 'offline_sync': store.state('agent_graph_sync', {}), 'gaps': ['未读全文不计为精读完成', '题目与摘要关系保留证据等级', '评测集需逐项核对来源并完成人工验收']}
