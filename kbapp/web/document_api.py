"""kbapp.web.document_api：由职责边界组织的知识库能力。"""
import json
import os
import threading
import time
from pathlib import Path
from fastapi import HTTPException
from fastapi.responses import FileResponse, Response
from kbapp.core.io import digest, now
from kbapp.library import documents as documents

def recover_documents(store):
    """不自动重跑模型；退出进程的任务可由页面明确续跑。"""
    with store.db() as db:
        rows = db.execute("SELECT id,pid FROM jobs WHERE kind='documents' AND status IN ('queued','running','stopping')").fetchall()
    for row in rows:
        alive = False
        if row['pid']:
            if os.name == 'nt':
                from kbapp.core.win_runtime import process_info
                alive = process_info(row['pid']) is not None
            else:
                try:
                    os.kill(row['pid'], 0)
                    alive = True
                except ProcessLookupError:
                    pass
                except PermissionError:
                    alive = True
        if not alive:
            with store.db() as db:
                db.execute("UPDATE jobs SET status='interrupted',finished=?,progress='解析进程已退出；已完成页面和人工复核保留，可继续解析' WHERE id=? AND status IN ('queued','running','stopping')", (now(), row['id']))

LIST_FIELDS = ('id', 'kind', 'title', 'year', 'citekey', 'authors', 'doi', 'abstract', 'collections', 'tags', 'directions', 'project', 'project_stages', 'methods', 'attachment_key', 'zotero_key', 'pdf', 'citation_count', 'citation_updated', 'classification', 'card')

def list_item(p):
    """H03：列表只返回展示字段；raw_zotero、refs 等大字段留给详情接口。"""
    return {k: p.get(k) for k in LIST_FIELDS if k in p}

def install(app, store):

    @app.get('/api/library')
    def library(q: str='', collection: str='', tag: str='', offset: int=0, limit: int=50):
        papers = [p for p in store.papers().values() if p.get('kind') == 'library']
        items = [p for p in papers if (not q or q.casefold() in ' '.join((str(p.get(k) or '') for k in ('title', 'doi', 'authors'))).casefold()) and (not collection or collection in p.get('collections', [])) and (not tag or tag in p.get('tags', []))]
        items.sort(key=lambda p: (-(p.get('year') or 0), p.get('title', '')))
        return {'items': [list_item(p) for p in items[max(0, offset):max(0, offset) + min(100, max(1, limit))]], 'total': len(items), 'collections': sorted({c for p in papers for c in p.get('collections', [])}), 'tags': sorted({t for p in papers for t in p.get('tags', [])})}

    @app.get('/api/documents')
    def list_documents(paper: str):
        from kbapp.library.attachments import attachments_of
        listing = attachments_of(store, paper)
        with store.db() as db:
            items = []
            rows = db.execute('SELECT * FROM documents WHERE paper_id=? ORDER BY updated DESC', (store.resolve(paper),)).fetchall()
            pages_by_doc = {}
            if rows:
                ids = [row['id'] for row in rows]
                for start in range(0, len(ids), 400):
                    chunk = ids[start:start + 400]
                    placeholders = ','.join('?' * len(chunk))
                    for page_row in db.execute('SELECT document_id,page,classification,status FROM document_pages WHERE document_id IN (' + placeholders + ') ORDER BY document_id,page', chunk):
                        pages_by_doc.setdefault(page_row['document_id'], []).append({'page': page_row['page'], 'classification': page_row['classification'], 'status': page_row['status']})
            for row in rows:
                r = dict(row)
                r['pages'] = pages_by_doc.get(r['id'], [])
                r.pop('path', None)
                match = next((i for i in listing['items'] if i.get('document_id') == r['id']), {})
                r['role'] = match.get('role')
                r['status'] = match.get('status')
                r['active'] = match.get('active', False)
                items.append(r)
            conflicts = [dict(r) for r in db.execute('SELECT * FROM metadata_conflicts WHERE paper_id=?', (store.resolve(paper),))]
        return {'items': items, 'conflicts': conflicts, 'attachments': listing['items'], 'primary_document': listing['primary_document']}

    @app.post('/api/documents/primary')
    def set_primary_document(data: dict):
        from kbapp.library.attachments import set_primary
        return set_primary(store, data.get('paper', ''), data.get('document', ''), data.get('attachment_key', ''))

    def valid_document(identity):
        try:
            doc = documents.get_document(store, identity)
        except ValueError:
            raise HTTPException(404, '文档不存在') from None
        if not Path(doc['path']).is_file():
            raise HTTPException(404, '附件文件不存在')
        if documents.file_hash(doc['path']) != doc['sha256']:
            raise HTTPException(409, '附件已替换，请同步新版本；不能将旧证据页码用于新文件')
        return doc

    @app.get('/api/documents/{identity}/file')
    def file(identity: str):
        return FileResponse(valid_document(identity)['path'], media_type='application/pdf', content_disposition_type='inline')

    @app.get('/api/documents/{identity}/pages/{number}')
    def page(identity: str, number: int):
        valid_document(identity)
        result = documents.page_record(store, identity, number)
        if not result:
            raise HTTPException(404, '该页尚未解析')
        with store.db() as db:
            result['blocks'] = [dict(r, payload=json.loads(r['payload'])) for r in db.execute('SELECT * FROM document_blocks WHERE document_id=? AND page=?', (identity, number))]
        with store.db() as db:
            result['references'] = [dict(r) for r in db.execute('SELECT * FROM document_references WHERE document_id=? AND page=?', (identity, number))]
            result['citation_contexts'] = [dict(r, context=json.loads(payload[0]) if payload else {}) for r in db.execute('SELECT * FROM citation_contexts WHERE document_id=? AND page=?', (identity, number)) for payload in [db.execute('SELECT payload FROM citation_context_payload WHERE context_id=?', (r['id'],)).fetchone()]]
            result['review_history'] = [dict(r) for r in db.execute('SELECT * FROM document_reviews WHERE document_id=? AND page=?', (identity, number))]
        return result

    @app.get('/api/documents/{identity}/pages/{number}/image')
    def image(identity: str, number: int, block: str=''):
        import pymupdf
        doc = valid_document(identity)
        clip = None
        if block:
            with store.db() as db:
                row = db.execute('SELECT payload FROM document_blocks WHERE id=? AND document_id=? AND page=?', (block, identity, number)).fetchone()
            if not row:
                raise HTTPException(404, '原文区域不存在')
            clip = json.loads(row[0]).get('bbox')
        with pymupdf.open(doc['path']) as pdf:
            if number < 1 or number > len(pdf):
                raise HTTPException(404, '页码超出范围')
            png = pdf[number - 1].get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5), clip=clip).tobytes('png')
        return Response(png, media_type='image/png')

    @app.post('/api/documents/process')
    def process(payload: dict):
        from kbapp.core.locks import update_lock
        paper = store.papers().get(store.resolve(payload.get('paper_id', '')))
        identity = payload.get('document_id')
        selected = payload.get('pages')
        if selected is not None and (not isinstance(selected, list) or not selected or len(selected) > 100 or any((type(n) is not int or n < 1 for n in selected))):
            raise ValueError('pages应为1至100个正整数页码')
        if not paper or paper.get('kind') != 'library':
            raise ValueError('请选择库内文献；库外全文沿用审批入口')
        doc = valid_document(identity) if identity else None
        if doc and doc['paper_id'] != paper['id']:
            raise ValueError('文档不属于指定论文')
        if doc and selected and (max(selected) > doc['page_count']):
            raise ValueError('页码超出范围')
        with update_lock(store.root):
            pass
        jid = digest('documents:' + str(time.time_ns()))[:20]
        with store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running','stopping') AND kind IN ('update','vectors','documents','relations')").fetchone():
                raise ValueError('已有更新或解析任务运行中，完成后可重试')
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)', (jid, 'documents', 'queued', os.getpid(), json.dumps(payload), '等待解析', now(), None, None))

        def work():
            from kbapp.integrations.ocr import GLMOCR
            try:
                with update_lock(store.root):
                    with store.db() as db:
                        db.execute("UPDATE jobs SET status='running' WHERE id=? AND status='queued'", (jid,))

                    def stopped():
                        with store.db() as db:
                            return db.execute('SELECT status FROM jobs WHERE id=?', (jid,)).fetchone()[0] == 'stopping'

                    def progress(message):
                        with store.db() as db:
                            db.execute('UPDATE jobs SET progress=? WHERE id=?', (message, jid))
                    client = GLMOCR()
                    budget = store.settings()['ocr_pages_per_run']
                    if doc:
                        result = documents.process(store, doc, selected, client, budget, force=bool(selected), progress=progress, should_stop=stopped)
                    else:
                        result = documents.sync_documents(store, [paper], client, budget, progress, stopped)
                    status = 'interrupted' if stopped() else 'partial' if any((r.get('failed') or r.get('pending') or r.get('error') for r in (result if isinstance(result, list) else [result]))) else 'complete'
                    with store.db() as db:
                        db.execute('UPDATE jobs SET status=?,progress=?,finished=? WHERE id=?', (status, json.dumps(result, ensure_ascii=False), now(), jid))
            except Exception as exc:
                with store.db() as db:
                    db.execute("UPDATE jobs SET status='failed',error=?,finished=? WHERE id=?", (str(exc), now(), jid))
        threading.Thread(target=work, daemon=True, name='kb-documents').start()
        return {'id': jid, 'state': 'started'}

    @app.get('/api/documents/jobs/{jid}')
    def job(jid: str):
        with store.db() as db:
            r = db.execute("SELECT * FROM jobs WHERE id=? AND kind='documents'", (jid,)).fetchone()
        if not r:
            raise HTTPException(404, '任务不存在')
        return dict(r)

    @app.post('/api/documents/jobs/{jid}/stop')
    def stop(jid: str):
        with store.db() as db:
            db.execute("UPDATE jobs SET status='stopping' WHERE id=? AND kind='documents' AND status IN ('running','queued')", (jid,))
        return {'state': 'stopping'}

    @app.post('/api/documents/{identity}/blocks/{bid}/review')
    def review(identity: str, bid: str, payload: dict):
        valid_document(identity)
        status = payload.get('status')
        if status not in {'confirmed', 'rejected', 'pending'}:
            raise ValueError('无效审核状态')
        with store.db() as db:
            row = db.execute('SELECT payload FROM document_blocks WHERE id=? AND document_id=?', (bid, identity)).fetchone()
            if not row:
                raise HTTPException(404, '正文块不存在')
            data = json.loads(row[0])
            data.setdefault('review_history', []).append({'status': status, 'note': str(payload.get('note', ''))[:2000], 'at': now()})
            data['review_status'] = status
            db.execute('UPDATE document_blocks SET payload=? WHERE id=?', (json.dumps(data, ensure_ascii=False), bid))
        return data
