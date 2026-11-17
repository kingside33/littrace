"""kbapp.library.conflicts：元数据冲突与重复条目工作列表（C10）。

默认不自动合并：只列出冲突与同作品多条目，人工选择后才写入；
每次选择保存前值与选择记录，可撤销。
"""
from __future__ import annotations
import json
from kbapp.core.io import digest, now


def list_conflicts(store, status='pending', limit=100):
    papers = store.papers()
    with store.db() as db:
        rows = [dict(r) for r in db.execute('SELECT * FROM metadata_conflicts WHERE status=? ORDER BY created DESC LIMIT ?', (status, max(1, min(500, limit))))]
    for row in rows:
        paper = papers.get(row['paper_id'], {})
        row['paper_title'] = paper.get('title')
        row['authoritative_value'] = json.loads(row['authoritative']) if row.get('authoritative') else None
        row['proposed_value'] = json.loads(row['proposed']) if row.get('proposed') else None
    return rows


def duplicates(store):
    """同一作品关联多个条目的工作列表；只报告，不自动合并。"""
    papers = store.papers()
    with store.db() as db:
        rows = db.execute('SELECT work_id,COUNT(*) n FROM paper_works GROUP BY work_id HAVING COUNT(*)>1 ORDER BY n DESC').fetchall()
        result = []
        for work_id, _count in rows:
            members = [r[0] for r in db.execute('SELECT paper_id FROM paper_works WHERE work_id=? ORDER BY paper_id', (work_id,))]
            versions = {r[0]: (r[1], r[2]) for r in db.execute('SELECT paper_id,version,venue FROM work_versions WHERE work_id=?', (work_id,))}
            result.append({'work_id': work_id, 'papers': [{'id': pid, 'title': papers.get(pid, {}).get('title'), 'year': papers.get(pid, {}).get('year'),
                                                           'version': versions.get(pid, ('unknown', ''))[0], 'venue': versions.get(pid, ('unknown', ''))[1]} for pid in members]})
    return result


def resolve(store, conflict_id, choice, note=''):
    """choice: authoritative|proposed。写入所选值并记录可撤销的前值。"""
    if choice not in {'authoritative', 'proposed'}:
        raise ValueError('无效的选择')
    with store.db() as db:
        row = db.execute('SELECT * FROM metadata_conflicts WHERE id=?', (conflict_id,)).fetchone()
    if not row:
        raise ValueError('冲突不存在')
    if row['status'] == 'resolved':
        raise ValueError('该冲突已处理')
    value = json.loads(row[choice]) if row[choice] else None
    field = row['field']
    paper = store.papers().get(row['paper_id'])
    if not paper:
        raise ValueError('文献不存在')
    previous = paper.get(field)
    updated = store.patch_paper(row['paper_id'], {field: value}, force=True)
    stamp = now()
    with store.db() as db:
        db.execute('UPDATE metadata_conflicts SET status=? WHERE id=?', ('resolved', conflict_id))
        db.execute('INSERT INTO conflict_resolutions VALUES(?,?,?,?,?,?,NULL)',
                   (digest(conflict_id + stamp)[:32], conflict_id, choice, json.dumps(previous, ensure_ascii=False),
                    json.dumps({'field': field, 'value': value, 'note': note}, ensure_ascii=False), stamp))
    return {'conflict_id': conflict_id, 'field': field, 'value': value, 'paper': updated}


def undo(store, conflict_id):
    with store.db() as db:
        row = db.execute("SELECT * FROM conflict_resolutions WHERE conflict_id=? AND undone_at IS NULL ORDER BY at DESC LIMIT 1", (conflict_id,)).fetchone()
    if not row:
        raise ValueError('没有可撤销的选择')
    applied = json.loads(row['applied'])
    store.patch_paper(_paper_of(store, conflict_id), {applied['field']: json.loads(row['previous'])}, force=True)
    with store.db() as db:
        db.execute('UPDATE conflict_resolutions SET undone_at=? WHERE id=?', (now(), row['id']))
        db.execute("UPDATE metadata_conflicts SET status='pending' WHERE id=?", (conflict_id,))
    return {'conflict_id': conflict_id, 'restored': json.loads(row['previous'])}


def _paper_of(store, conflict_id):
    with store.db() as db:
        return db.execute('SELECT paper_id FROM metadata_conflicts WHERE id=?', (conflict_id,)).fetchone()[0]
