"""kbapp.workflows.backup：使用 SQLite backup API 生成一致性备份（O02/O03）。

运行中的数据库主文件不能作为唯一备份：直接复制会漏掉 WAL 中的已提交事务，
也可能得到撕裂快照。这里通过 backup API 生成一致副本，并记录完整性检查、
关键数据计数与保留策略。
"""
import json
import sqlite3
import time
from kbapp.core.io import now


def _counts(db):
    counts = {}
    for name, sql in [
        ('papers', 'SELECT COUNT(*) FROM papers'),
        ('relations', 'SELECT COUNT(*) FROM relations'),
        ('relations_confirmed', "SELECT COUNT(*) FROM relations WHERE status='confirmed'"),
        ('relations_manual', 'SELECT COUNT(*) FROM relations WHERE manual=1'),
        ('approvals', 'SELECT COUNT(*) FROM approvals'),
        ('usage_records', 'SELECT COUNT(*) FROM usage'),
        ('verification_budgets', 'SELECT COUNT(*) FROM verification_budgets'),
        ('agent_runs', 'SELECT COUNT(*) FROM agent_runs'),
    ]:
        try:
            counts[name] = db.execute(sql).fetchone()[0]
        except sqlite3.Error:
            counts[name] = None
    return counts


def create_backup(paths, keep=7, label='manual'):
    if not paths.database.is_file():
        raise ValueError('数据库不存在，无需备份')
    stamp = time.strftime('%Y%m%d-%H%M%S')
    folder = paths.backups / f'auto-{label}-{stamp}'
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / 'workbench.sqlite3'
    src = sqlite3.connect(paths.database.as_uri() + '?mode=ro', uri=True)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
        integrity = dst.execute('PRAGMA integrity_check').fetchone()[0]
        counts = _counts(dst)
    finally:
        dst.close()
        src.close()
    manifest = {'created': now(), 'label': label, 'source': str(paths.database), 'database': str(target),
                'size_mb': round(target.stat().st_size / 1e6, 1), 'integrity': integrity, 'counts': counts,
                'retention': f'保留最近 {keep} 份自动备份', 'external': {'notes': 'Obsidian 笔记与 Zotero 原件不在数据库内，需另行备份或依赖其自身同步'}}
    (folder / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding='utf-8')
    return dict(manifest, removed=prune(paths, keep))


def prune(paths, keep=7):
    """只清理自动备份目录；手工命名的备份不会被删除。"""
    folders = sorted((p for p in paths.backups.glob('auto-*') if p.is_dir()), key=lambda p: p.stat().st_mtime)
    removed = []
    for folder in folders[:-max(1, keep)]:
        for f in folder.iterdir():
            f.unlink()
        folder.rmdir()
        removed.append(folder.name)
    return removed
