"""HTTP 路由：library_api；通过 AppContext 使用业务能力。"""
from __future__ import annotations
import json
from pathlib import Path
from fastapi import HTTPException
from fastapi.responses import FileResponse
from kbapp.workflows.notes import publish_notes

def paper_view(store, paper):
    """H01/H02：文献详情载荷，v1 与 v2 共用同一实现。"""
    pid = store.resolve(paper)
    p = store.papers().get(pid)
    if not p:
        raise ValueError('文献不存在')
    with store.db() as db:
        rel = [json.loads(r[0]) for r in db.execute('SELECT payload FROM relations WHERE src=? OR dst=?', (pid, pid))]
    from kbapp.research.evidence import latest
    from kbapp.research.reading import note_status, understanding_fingerprint
    understanding = latest(store, 'PaperUnderstanding', pid)
    if understanding and understanding['input_fingerprint'] != understanding_fingerprint(store, p):
        understanding = dict(understanding, verification_status='stale', coverage=dict(understanding['coverage'], complete=False), gaps=understanding.get('gaps', []) + ['PDF、模型或提示版本已变化，需重新理解'])
    from kbapp.workflows.references import view, citation_info
    return {'paper': dict(p, **citation_info(p)), 'relations': rel, 'approvals': [a for a in store.approvals() if a['paper_id'] == pid], 'understanding': understanding, 'note_sync': note_status(store, p), 'references': view(store, pid)}

def install(app, context, token):
    store = context.store
    search_engine = context.search

    @app.get('/api/paper')
    def paper_info(paper: str):
        try:
            return paper_view(store, paper)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from None

    @app.get('/api/references')
    def paper_references(paper: str):
        from kbapp.workflows.references import view
        return view(store, paper)

    @app.post('/api/references/refresh')
    def refresh_references(payload: dict):
        from kbapp.workflows.references import enqueue, view
        pid = payload.get('paper_id', '')
        task = enqueue(store, pid, force=True)
        result = view(store, pid, auto=False)
        if task.get('status') not in {'queued', 'running'}:
            result['notice'] = '刚刚请求过更新，请在30秒后再次刷新；已完成结果保留。'
        return result

    @app.get('/api/papers')
    def paper_choices(q: str='', limit: int=30, semantic: bool=False):
        from kbapp.library.catalog import enriched_papers
        if len(q) > (2000 if semantic else 200):
            raise ValueError('查找关键词过长')
        if semantic:
            if not q.strip():
                raise ValueError('请填写论文检索问题')
            found = search_engine.search(q, k=40)
            from kbapp.workflows.references import attach_citations
            attach_citations(store, found)
            return {'items': found['papers'][:min(max(limit, 1), 20)], 'mode': found['mode'], 'warning': found['warning'], 'vector_status': found['vector_status']}
        papers = enriched_papers(store)
        found = [p for p in papers.values() if q.lower() in (p.get('title', '') + ' ' + p.get('citekey', '')).lower()]
        found.sort(key=lambda p: (p.get('kind') != 'library', not bool(p.get('project')), p.get('title', '')))
        from kbapp.workflows.references import citation_info
        return {'items': [dict({k: p.get(k) for k in ('id', 'title', 'citekey', 'year', 'kind', 'methods')}, **citation_info(p)) for p in found[:min(max(limit, 1), 100)]]}

    @app.get('/api/conflicts')
    def metadata_conflicts():
        from kbapp.library.conflicts import list_conflicts, duplicates
        return {'conflicts': list_conflicts(store), 'duplicates': duplicates(store)}

    @app.post('/api/conflicts/resolve')
    def resolve_conflict(data: dict):
        from kbapp.library.conflicts import resolve
        return resolve(store, data.get('id', ''), data.get('choice', ''), data.get('note', ''))

    @app.post('/api/conflicts/undo')
    def undo_conflict(data: dict):
        from kbapp.library.conflicts import undo
        return undo(store, data.get('id', ''))

    @app.get('/api/approvals')
    def approvals(offset: int=0, limit: int=50, status: str=''):
        papers = store.papers()
        rows = store.approvals()
        if status:
            rows = [a for a in rows if a['status'] == status]
        # H04：稳定排序（最近创建优先，同时间按 paper_id）
        rows.sort(key=lambda a: (str(a.get('created') or ''), a['paper_id']), reverse=True)
        start = max(0, offset)
        size = min(max(limit, 1), 200)
        page = rows[start:start + size]
        from kbapp.web.document_api import list_item
        items = [dict(a, paper=list_item(papers.get(a['paper_id'], {}))) for a in page]
        return {'items': items, 'total': len(rows), 'pending': sum(1 for a in rows if a['status'] == 'pending'), 'offset': start, 'limit': size, 'has_more': start + size < len(rows)}

    @app.post('/api/approvals/request')
    def request_fulltext(data: dict):
        store.request_fulltext(data.get('paper_id', ''), str(data.get('reason') or '需要核对关键研究关系'), str(data.get('question') or '核实问题、方法与适用条件'))
        return {'state': 'pending'}

    @app.post('/api/approvals/decide')
    def decide_fulltext(data: dict):
        ids = data.get('paper_ids', [])
        if not isinstance(ids, list) or len(ids) > 200:
            raise ValueError('每批最多审批200篇')
        store.decide(ids, data.get('decision', ''))
        publish_notes(store, [p for p in store.papers().values() if p.get('kind') == 'library'])
        return {'state': 'saved', 'count': len(ids)}

    @app.get('/api/file')
    def pdf_file(paper: str):
        pid = store.resolve(paper)
        p = store.papers().get(pid)
        if not p:
            raise HTTPException(404, '文献不存在')
        if p.get('kind') == 'library' and p.get('pdf'):
            path = store.base / p['pdf']
        else:
            a = next((a for a in store.approvals() if a['paper_id'] == pid and a['status'] == 'downloaded'), None)
            if not a:
                raise HTTPException(404, '该全文尚未经过审批并获取')
            path = Path(a['filepath'])
        if not path.is_file():
            raise HTTPException(404, '全文文件不存在')
        return FileResponse(path, media_type='application/pdf', content_disposition_type='inline')

    @app.get('/api/asset')
    def asset(source_id: str):
        search_engine.load()
        r = search_engine.by_id.get(source_id)
        if r and r.get('document_id') and r.get('block_id'):
            from kbapp.library.documents import get_document
            from kbapp.core.files import file_hash
            from fastapi.responses import Response
            import pymupdf
            document = get_document(store, r['document_id'])
            if file_hash(document['path']) != document['sha256']:
                raise HTTPException(409, '附件版本已变化')
            with store.db() as db:
                row = db.execute('SELECT page,payload FROM document_blocks WHERE id=? AND document_id=?', (r['block_id'], r['document_id'])).fetchone()
            if not row:
                raise HTTPException(404, '原图区域不存在')
            with pymupdf.open(document['path']) as pdf:
                png = pdf[row['page'] - 1].get_pixmap(matrix=pymupdf.Matrix(2, 2), clip=json.loads(row['payload']).get('bbox')).tobytes('png')
            return Response(png, media_type='image/png')
        if not r or not r.get('file'):
            raise HTTPException(404, '原图不存在')
        path = (store.base / r['file']).resolve()
        allowed = (store.base / '_知识库/_attachments').resolve()
        if not path.is_relative_to(allowed) or not path.is_file():
            raise HTTPException(404, '原图不可用')
        return FileResponse(path)
