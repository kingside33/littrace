"""kbapp.workflows.status：由职责边界组织的知识库能力。"""
from __future__ import annotations
from datetime import datetime
from kbapp.core.io import TZ, now

def recent_invalidations(store, limit=10):
    from kbapp.research.dependencies import recent
    return recent(store, limit)

def parse_issues(store, limit=10):
    """D10：解析问题分为待处理、失败、已修复、历史保留，并关联修复记录。"""
    with store.db() as db:
        pending = db.execute("SELECT COUNT(*) FROM document_pages WHERE status='pending'").fetchone()[0]
        failed = db.execute("SELECT COUNT(*) FROM document_pages WHERE status='failed'").fetchone()[0]
        failed_details = [dict(r) for r in db.execute("SELECT document_id,page FROM document_pages WHERE status='failed' ORDER BY document_id,page LIMIT ?", (limit,))]
    repairs = store.state('data_quality_repairs', {}) or {}
    coverage = store.state('coverage', {}) or {}
    archive = coverage.get('warnings_archive') or []
    return {'pending': pending, 'failed': failed, 'failed_details': failed_details,
            'repaired': repairs.get('resolved_warning_count', 0), 'repaired_documents': repairs.get('reindexed_documents', [])[:limit], 'repaired_at': repairs.get('updated'),
            'current_warnings': len(coverage.get('warnings') or []), 'archived': len(archive), 'archived_recent': archive[-limit:]}

def status(store, detail=True):
    cfg = store.settings(public=True)
    with store.db() as db:
        jobs = [dict(r) for r in db.execute('SELECT * FROM jobs ORDER BY started DESC LIMIT 12')]
        frontier = {r[0]: r[1] for r in db.execute('SELECT status,COUNT(*) FROM frontier GROUP BY status')}
        frontier_detail = [dict(r) for r in db.execute('SELECT direction,depth,status,COUNT(*) AS count FROM frontier GROUP BY direction,depth,status')]
        counts = dict(db.execute('SELECT kind,COUNT(*) FROM relations GROUP BY kind').fetchall())
        method_counts = dict(db.execute("SELECT status,COUNT(*) FROM relations WHERE kind IN ('inherits','improves','combines','transfers') GROUP BY status").fetchall())
        updating = db.execute("SELECT id,kind,status,progress,started FROM jobs WHERE kind IN ('update','vectors') AND status IN ('queued','running') ORDER BY started DESC LIMIT 1").fetchone()
        last_analysis = db.execute("SELECT status,progress,finished FROM jobs WHERE kind='relations' ORDER BY started DESC LIMIT 1").fetchone()
        usage = db.execute('SELECT COALESCE(SUM(COALESCE(actual,reserved)),0) FROM usage WHERE day=?', (datetime.now(TZ).date().isoformat(),)).fetchone()[0]
        tokens = db.execute('SELECT COALESCE(SUM(t.input_tokens),0),COALESCE(SUM(t.output_tokens),0),COALESCE(MIN(t.cost_known),1) FROM token_usage t JOIN usage u ON u.id=t.usage_id WHERE u.day=?', (datetime.now(TZ).date().isoformat(),)).fetchone()
        paper_counts = dict(db.execute('SELECT library,COUNT(*) FROM papers GROUP BY library'))
        query_vectors = dict(db.execute('SELECT status,COUNT(*) FROM query_vectors GROUP BY status').fetchall())
    papers = store.papers() if detail else None
    coverage = store.state('coverage', {})
    from kbapp.search.index_status import index_health
    health = index_health(store, coverage, papers) if detail else {}
    if not detail:
        coverage = {k: coverage[k] for k in ('sources', 'vector_count', 'vector_pending', 'by_kind', 'fulltext_papers', 'warnings') if k in coverage}
    indexing = {'state': 'idle'}
    if updating:
        import re
        indexing = dict(updating, state='running')
        build = store.state('vector_build', {})
        if build.get('version') == updating['id']:
            indexing.update({k: v for k, v in build.items() if k not in {'state', 'version'}})
        match = re.search('Qwen 向量化 (\\d+)/(\\d+)；复用 (\\d+)', updating['progress'] or '')
        if match and build.get('version') != updating['id']:
            completed, pending, reused = map(int, match.groups())
            indexing.update(phase='vectors', completed=reused + completed, total=reused + pending, new_completed=completed, reused=reused, pending=max(0, pending - completed))
    analysis = store.state('analysis_state', {})
    if analysis.get('state') == 'not_requested' and last_analysis and (last_analysis['status'] == 'interrupted'):
        analysis = {'state': 'paused', 'reason': last_analysis['progress'], 'updated': last_analysis['finished']}
    result = {'index_published_at':store.state('last_success'),'update_finished_at':store.state('last_update_finished'),'export_sync':store.state('export_sync',{}),'active_version': store.state('active_version'), 'updated': store.state('last_success'), 'papers': paper_counts.get(1, 0), 'external': paper_counts.get(0, 0), 'coverage': coverage, 'parse_issues': parse_issues(store), 'invalidations': recent_invalidations(store), 'index_health': health, 'observed_at': health.get('observed_at') or now(), 'query_vectors': query_vectors, 'index_actions': {'vectors': True, 'update': True}, 'indexing': indexing, 'relations': counts, 'method_relations': method_counts, 'analysis_state': analysis, 'local_setup': store.state('local_setup', {}), 'local_model_state': store.state('local_model_state', {}), 'model_control': store.state('model_control', {}), 'last_local_model_error': store.state('last_local_model_error', {}), 'recognition_state': store.state('recognition_state', {}), 'relation_pipeline': {'recognition': 'GLM-OCR', 'local_verifier': cfg['local_text_model'], 'cloud_reviewer': cfg['model'], 'local_enabled': cfg['relation_extractor'] == 'local', 'version': 'glm-ocr-local-verify-cloud-v1'}, 'frontier': frontier, 'frontier_detail': frontier_detail, 'pending_approvals': sum((a['status'] == 'pending' for a in store.approvals())), 'jobs': jobs, 'today_cost': round(usage, 4), 'today_input_tokens': tokens[0], 'today_output_tokens': tokens[1], 'today_cost_known': bool(tokens[2]) and (not (cfg['cloud_enabled'] and (not (cfg['input_price'] and cfg['output_price'])))), 'cloud_enabled': cfg['cloud_enabled'], 'budget_mode': cfg['budget_mode'], 'schedule': store.state('schedule', {'enabled': False}), 'timezone': 'Asia/Shanghai'}

    result['status_detail'] = bool(detail)
    result['data_revision'] = [store.state('paper_revision', 0), store.state('relation_revision', 0), store.state('active_version')]
    if not detail:
        result.pop('index_health', None)
    return result
