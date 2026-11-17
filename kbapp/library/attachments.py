"""kbapp.library.attachments：附件角色、可用状态与主附件切换（C04）。

同一文献可能有主附件、补充材料、作者稿与失效附件。主附件可由人工指定，
检索证据时优先主附件版本，但不隐藏其他版本的证据（版本切换可回退）。
"""
from __future__ import annotations
import json
from pathlib import Path
from kbapp.core.io import now

SUPPLEMENTARY = ('supplement', 'supplementary', 'appendix', '补充', '附录')
AUTHOR_COPY = ('author', 'manuscript', 'accepted', 'preprint', 'postprint', '作者稿')


def classify_attachment(attachment):
    """返回 (role, status)：role 为主附件/补充材料/作者稿/网页快照/未知。"""
    if not attachment.get('exists'):
        return 'unknown', 'missing'
    name = str(attachment.get('abs_path') or attachment.get('raw_path') or '').lower()
    content_type = str(attachment.get('content_type') or '').lower()
    if any(word in name for word in SUPPLEMENTARY):
        return 'supplementary', 'available'
    if any(word in name for word in AUTHOR_COPY):
        return 'author_manuscript', 'available'
    if 'pdf' in content_type or name.endswith('.pdf'):
        return 'primary', 'available'
    if 'html' in content_type or name.endswith(('.html', '.htm')):
        return 'alternative', 'available'
    return 'unknown', 'available'


def attachments_of(store, paper_id):
    """列出附件与已登记文档，标注角色、状态与当前证据版本。"""
    pid = store.resolve(paper_id)
    paper = store.papers().get(pid, {})
    if not paper:
        raise ValueError('文献不存在')
    primary_key = paper.get('primary_attachment_key') or ''
    primary_path = paper.get('pdf') or ''
    with store.db() as db:
        documents = [dict(r) for r in db.execute('SELECT id,attachment_key,sha256,path,page_count,payload,updated FROM documents WHERE paper_id=? ORDER BY updated DESC', (pid,))]
    primary_document = paper.get('primary_document') or ''
    if not primary_document:
        for doc in documents:
            if Path(doc['path']).is_file():
                primary_document = doc['id']
                break
    items = []
    for att in paper.get('attachments') or []:
        role, status = classify_attachment(att)
        active = bool((primary_key and att.get('key') == primary_key) or (primary_path and att.get('abs_path') == primary_path))
        if not primary_key and not primary_path:
            active = role == 'primary' and status == 'available'
        items.append({'attachment_key': att.get('key'), 'path': att.get('abs_path'), 'content_type': att.get('content_type'),
                      'role': role, 'status': status, 'active': active})
    for doc in documents:
        payload = json.loads(doc['payload'] or '{}')
        exists = Path(doc['path']).is_file()
        role = payload.get('role') or next((item['role'] for item in items if item['attachment_key'] == doc['attachment_key']), 'unknown')
        items.append({'document_id': doc['id'], 'attachment_key': doc['attachment_key'], 'path': doc['path'],
                      'role': role, 'status': 'available' if exists else 'missing',
                      'active': doc['id'] == primary_document, 'pages': doc['page_count']})
    return {'paper_id': pid, 'primary_document': primary_document or None, 'items': items}


def set_primary(store, paper_id, document_id='', attachment_key=''):
    """人工指定主附件/证据版本；只记录人工判断，不删除其他版本。"""
    pid = store.resolve(paper_id)
    if not document_id and not attachment_key:
        raise ValueError('需要指定文档或附件')
    with store.db() as db:
        if document_id:
            row = db.execute('SELECT paper_id,path FROM documents WHERE id=?', (document_id,)).fetchone()
            if not row or row['paper_id'] != pid:
                raise ValueError('文档不属于该文献')
            if not Path(row['path']).is_file():
                raise ValueError('附件文件不可用，不能设为主附件')
            if not attachment_key:
                key = db.execute('SELECT attachment_key FROM documents WHERE id=?', (document_id,)).fetchone()[0]
                attachment_key = key or ''
    return store.patch_paper(pid, {'primary_document': document_id, 'primary_attachment_key': attachment_key,
                                   'primary_set_by': 'manual', 'primary_set_at': now()})
