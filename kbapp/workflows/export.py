"""kbapp.workflows.export：由职责边界组织的知识库能力。"""
from __future__ import annotations
from kbapp.workflows.status import status as workbench_status
import json
from kbapp.core.io import atomic
from kbapp.research.graph import chains, overview, timeline

def export_static(store):
    from kbapp.integrations.scheduling import file_slot
    with file_slot(store.root, 'workbench-static-export.lock', timeout=60):
        _export_static(store)

def _export_static(store):
    web = store.paths.assets
    if not (web / 'index.html').exists():
        return
    from kbapp.research.graph import graph_papers
    from kbapp.research.method_taxonomy import method_options, FAMILIES, DIRECTION_FAMILIES, allowed_families
    from kbapp.research.viewmodels import display_relation
    papers = graph_papers(store)
    fields = ('id', 'kind', 'title', 'year', 'citekey', 'directions', 'project_stages', 'project', 'card', 'doi', 'abstract', 'attachment_key', 'zotero_key', 'openalex_id', 'authors', 'methods', 'method_families', 'method_family_scores', 'primary_method_family', 'direction_basis', 'classification', 'tagfields', 'cited_by', 'citation_count', 'citation_source', 'citation_updated')
    payload = {'nodes': [{k: p.get(k) for k in fields} for p in papers.values()], 'edges': [{k: graded.get(k) for k in ('id', 'src', 'dst', 'kind', 'status', 'level', 'explanation', 'evidence', 'change', 'previous_problem', 'conditions', 'manual', 'reviewed_by', 'extracted_by', 'independent_review', 'review_verdict', 'local_reviewed_by', 'local_review_verdict', 'pipeline_version', 'pipeline_legacy', 'evidence_grade', 'evidence_label', 'assessment_verified', 'evidence_stale')} for r in store.relations() if r['status'] != 'rejected' for graded in [display_relation(r)]], 'status': workbench_status(store), 'chains': chains(store), 'overview': overview(store), 'timeline': timeline(store), 'opportunities': store.state('opportunities', {'items': []})}
    payload['methods'] = method_options(papers)
    from kbapp.research.graph import _branch_id
    payload['taxonomy'] = {'families': FAMILIES, 'branches': {mid: _branch_id(name) for mid, name in FAMILIES.items()},
                           'directions': {d: sorted(allowed_families(d)) for d in DIRECTION_FAMILIES}}
    with store.db() as db:
        payload['references'] = {r['paper_id']: dict(json.loads(r['payload']), updated=r['updated']) for r in db.execute('SELECT * FROM reference_lists')}
    with store.db() as db:
        rejected = {(r[0], r[1]) for r in db.execute("SELECT src,dst FROM relations WHERE kind='cites' AND manual=1 AND status='rejected'")}
    for pid, saved in payload['references'].items():
        for row in saved['items']:
            if (pid, row.get('paper_id')) in rejected:
                row['association_status'] = 'manual_rejected'
    from kbapp.core.web_assets import render_html
    atomic(store.paths.exports / 'evolution.html', render_html(payload))
