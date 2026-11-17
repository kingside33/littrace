"""kbapp.workflows.consistency：数据库列与 JSON 载荷的一致性检查（C09）。

唯一权威约定：
- relations：列为权威（id/src/dst/kind/status/level/manual/updated），payload 为记录快照；
- papers：payload 为权威（kind 决定 library 列）；
- documents：列为权威（id/paper_id/attachment_key/sha256/path/page_count），payload 只存解析元数据；
- paper_works / work_versions：由 works.rebuild 生成，列为权威。
"""
from __future__ import annotations
import json
from pathlib import Path

AUTHORITATIVE = {
    'relations': 'columns',
    'papers': 'payload',
    'documents': 'columns',
    'paper_works': 'columns',
    'work_versions': 'columns',
}


def check(store, limit=10):
    checks = {name: {'mismatches': 0, 'samples': []} for name in ('relations', 'papers', 'documents', 'paper_works', 'work_versions')}

    def bucket(name):
        return checks.setdefault(name, {'mismatches': 0, 'samples': []})

    def sample(name, row):
        item = bucket(name)
        item['mismatches'] += 1
        if len(item['samples']) < limit:
            item['samples'].append(row)

    with store.db() as db:
        for row in db.execute('SELECT id,src,dst,kind,status,level,manual,payload FROM relations'):
            payload = json.loads(row['payload'] or '{}')
            diffs = {key: (payload.get(key), row[key]) for key in ('src', 'dst', 'kind', 'status', 'level') if payload.get(key) != row[key]}
            if diffs:
                sample('relations', {'id': row['id'], 'fields': diffs})
        for row in db.execute('SELECT id,payload,library FROM papers'):
            payload = json.loads(row['payload'] or '{}')
            if int(row['library']) != int(payload.get('kind') == 'library'):
                sample('papers', {'id': row['id'], 'library': row['library'], 'kind': payload.get('kind')})
        known = {r[0] for r in db.execute('SELECT id FROM papers')}
        missing_files = 0
        for row in db.execute('SELECT id,paper_id,path FROM documents'):
            if row['paper_id'] not in known:
                sample('documents', {'id': row['id'], 'reason': 'paper_missing'})
            if not Path(row['path']).is_file():
                missing_files += 1
        checks.setdefault('documents', {'mismatches': 0, 'samples': []})
        checks['documents']['missing_files'] = missing_files
        for table in ('paper_works', 'work_versions'):
            for row in db.execute('SELECT paper_id FROM ' + table):
                if row[0] not in known:
                    sample(table, {'paper_id': row[0], 'reason': 'paper_missing'})
    ok = all(item['mismatches'] == 0 for item in checks.values())
    return {'ok': ok, 'authoritative': AUTHORITATIVE, 'checks': checks}
