"""kbapp.research.dependencies：来源变化到索引、候选、关系、问答与总结的失效记录（C07）。"""
from __future__ import annotations
import json
from kbapp.core.io import digest, now

KINDS = {'index', 'candidate', 'relation', 'answer', 'summary'}


def record(store, kind, subject, reason, affected=None):
    """记录一次依赖失效；同一 kind+subject+reason 幂等。"""
    if kind not in KINDS:
        raise ValueError('无效的失效类型：' + str(kind))
    stamp = now()
    rid = digest('|'.join((kind, str(subject), reason)))[:32]
    with store.db() as db:
        db.execute('INSERT OR IGNORE INTO invalidations VALUES(?,?,?,?,?,?,?)',
                   (rid, kind, str(subject), reason, json.dumps(affected or [], ensure_ascii=False), 'recorded', stamp))
        db.execute('UPDATE invalidations SET created=? WHERE id=? AND created IS NULL', (stamp, rid))
    return rid


def recent(store, limit=20):
    with store.db() as db:
        rows = db.execute('SELECT * FROM invalidations ORDER BY created DESC, rowid DESC LIMIT ?', (max(1, min(200, limit)),)).fetchall()
    return [dict(r, affected=json.loads(r['affected'])) for r in rows]


def resolve(store, invalidation_id, reason=''):
    with store.db() as db:
        db.execute("UPDATE invalidations SET status='resolved', reason=CASE WHEN ?='' THEN reason ELSE ? END WHERE id=?", (reason, reason, invalidation_id))
