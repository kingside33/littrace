"""可审计、可恢复的数据布局迁移；不加载模型、不改写研究证据。"""
from contextlib import closing
import json
import shutil
from pathlib import Path
import sqlite3
import time
from kbapp.core.files import file_hash
from kbapp.core.io import atomic, now
from kbapp.core.paths import DIRECTORIES, LAYOUT_VERSION, legacy_target


def save_journal(journal, record):
    """不可变校验清单仅写一次，进度更新只写小型日志。"""
    if 'inventory' not in record:
        record['inventory'] = 'state/migrations/layout-' + str(time.time_ns()) + '.json'
        atomic(journal.parent.parent / record['inventory'],
               {o['source']: o['files'] for o in record['operations']})
    metadata = dict(record, operations=[{k:v for k,v in o.items() if k != 'files'} for o in record['operations']])
    atomic(journal, metadata)


def load_journal(journal):
    record = json.loads(journal.read_text(encoding='utf-8'))
    if 'inventory' in record:
        files = json.loads((journal.parent.parent / record['inventory']).read_text(encoding='utf-8'))
        for operation in record['operations']:
            operation['files'] = files[operation['source']]
    return record

def inventory(path):
    path = Path(path)
    files = [path] if path.is_file() else sorted((p for p in path.rglob('*') if p.is_file()))
    return [{'path': '.' if p == path else p.relative_to(path).as_posix(), 'size': p.stat().st_size, 'mtime_ns': p.stat().st_mtime_ns, 'sha256': file_hash(p)} for p in files]

def checked(root, relative):
    target = (root / relative).resolve()
    if target == root or not target.is_relative_to(root):
        raise ValueError('迁移路径超出数据目录')
    return target

def database_summary(path, *, immutable=False):
    if not Path(path).is_file():
        return {}
    with closing(sqlite3.connect(Path(path).as_uri() + '?mode=ro' + ('&immutable=1' if immutable else ''), uri=True)) as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        counts = {t: db.execute('SELECT COUNT(*) FROM "' + t + '"').fetchone()[0] for t in tables}
        state = dict(db.execute("SELECT key,value FROM state WHERE key IN ('active_version','last_success')")) if 'state' in tables else {}
        manual = [list(row) for row in db.execute('SELECT id,payload FROM relations WHERE manual=1 ORDER BY id')] if 'relations' in tables else []
        return {'counts': counts, 'state': state, 'manual_relations': manual}

def ensure_idle(database, *, immutable=False):
    if not Path(database).exists():
        return
    with closing(sqlite3.connect(Path(database).as_uri() + '?mode=ro' + ('&immutable=1' if immutable else ''), uri=True)) as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ('jobs', 'agent_runs', 'reference_jobs'):
            if table in tables and db.execute(f"SELECT 1 FROM {table} WHERE status IN ('running','queued','stopping') LIMIT 1").fetchone():
                raise ValueError('存在活动任务，请等待完成或停止任务后迁移')

def plan(paths):
    """严格只读；结果由 migrate 持久化。"""
    operations = []
    if not paths.data.exists():
        return {'layout_version': LAYOUT_VERSION, 'operations': [], 'files': 0, 'bytes': 0}
    for source in sorted(paths.data.iterdir()):
        if source.name in DIRECTORIES:
            continue
        children = sorted(source.iterdir()) if source.name == 'citation' and source.is_dir() else [source]
        for child in children:
            relative = child.relative_to(paths.data)
            target = legacy_target(relative)
            destination = checked(paths.data, target)
            if destination.exists():
                raise ValueError('目标已存在，未覆盖：' + str(target))
            files = inventory(child)
            operations.append({'source': relative.as_posix(), 'target': target.as_posix(), 'status': 'pending', 'files': files})
    return {'layout_version': LAYOUT_VERSION, 'created': now(), 'operations': operations, 'files': sum((len(o['files']) for o in operations)), 'bytes': sum((f['size'] for o in operations for f in o['files']))}

def translate(value, paths, operations, reverse=False):
    if not isinstance(value, str):
        return value
    pairs = [(o['target'], o['source']) if reverse else (o['source'], o['target']) for o in operations]
    for src, dst in sorted(pairs, key=lambda p: -len(p[0])):
        origin = str(paths.data / src)
        if value == origin or value.startswith(origin + '\\') or value.startswith(origin + '/'):
            return str(paths.data / dst) + value[len(origin):]
        relative = '_index/' + src
        normalized = value.replace('\\', '/')
        if paths.data == paths.base / '_index' and (normalized == relative or normalized.startswith(relative + '/')):
            return '_index/' + dst + normalized[len(relative):]
    return value

def relocate_database(path, paths, operations, reverse=False):
    """只更新实际文件定位字段；原始快照、证据、人工判断及 ID 不变。"""
    if not path.exists():
        return
    with closing(sqlite3.connect(path)) as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, column, key in [('documents', 'path', 'id'), ('approvals', 'filepath', 'paper_id')]:
            if table not in tables:
                continue
            for identity, value in db.execute(f'SELECT {key},{column} FROM {table}').fetchall():
                new = translate(value, paths, operations, reverse)
                if new != value:
                    db.execute(f'UPDATE {table} SET {column}=? WHERE {key}=?', (new, identity))
        if 'papers' in tables:
            for pid, payload in db.execute('SELECT id,payload FROM papers').fetchall():
                paper = json.loads(payload)
                changed = False
                for field in ('pdf',):
                    new = translate(paper.get(field), paths, operations, reverse)
                    if new != paper.get(field):
                        paper[field] = new
                        changed = True
                if 'pdf_aliases' in paper:
                    aliases = [translate(v, paths, operations, reverse) for v in paper['pdf_aliases']]
                    if aliases != paper['pdf_aliases']:
                        paper['pdf_aliases'] = aliases
                        changed = True
                if changed:
                    db.execute('UPDATE papers SET payload=? WHERE id=?', (json.dumps(paper, ensure_ascii=False), pid))
        db.commit()

def verify_move(operation, path):
    for record in operation['files']:
        file = path if record['path'] == '.' else path / record['path']
        if not file.is_file() or file.stat().st_size != record['size'] or file_hash(file) != record['sha256']:
            raise ValueError('文件校验不一致：' + str(file))

def migrate(paths, *, after_move=None):
    from kbapp.core.locks import update_lock
    with update_lock(paths.data, 'layout-migration.lock'):
        journal = paths.state / 'layout-migration.json'
        if journal.exists():
            record = load_journal(journal)
            if record['status'] == 'complete':
                return record
            if record['status'] == 'rolled_back':
                record = None
        else:
            record = None
        if record is None:
            database = paths.data / 'workbench.sqlite3'
            ensure_idle(database)
            if database.exists():
                with closing(sqlite3.connect(database)) as db:
                    checkpoint = db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
                    if checkpoint and checkpoint[0]:
                        raise ValueError('数据库仍在使用，请关闭服务后迁移')
            record = dict(plan(paths), status='moving', before=database_summary(database, immutable=True))
            snapshot = paths.backups / ('layout-' + str(time.time_ns())) / 'workbench.sqlite3'
            if database.exists():
                snapshot.parent.mkdir(parents=True, exist_ok=True)
                with closing(sqlite3.connect(database.as_uri() + '?mode=ro&immutable=1', uri=True)) as src, closing(sqlite3.connect(snapshot)) as dst:
                    src.backup(dst)
            record['database_backup'] = str(snapshot)
            save_journal(journal, record)
        ensure_idle(paths.database if paths.database.exists() else paths.data / 'workbench.sqlite3', immutable=True)
        for operation in record['operations']:
            if operation['status'] == 'moved':
                continue
            source = checked(paths.data, operation['source'])
            target = checked(paths.data, operation['target'])
            if source.exists() and target.exists():
                raise ValueError('目标冲突，未覆盖：' + operation['target'])
            if source.exists():
                verify_move(operation, source)
                target.parent.mkdir(parents=True, exist_ok=True)
                source.rename(target)
            elif not target.exists():
                raise ValueError('源与目标均不存在：' + operation['source'])
            verify_move(operation, target)
            operation['status'] = 'moved'
            save_journal(journal, record)
            if after_move:
                after_move(operation)
        record['status'] = 'relocating'
        save_journal(journal, record)
        relocate_database(paths.database, paths, record['operations'])
        after = database_summary(paths.database, immutable=True)
        if after != record['before']:
            raise ValueError('数据库业务记录核对不一致，未完成切换')
        paths.ensure_layout()
        from kbapp.workflows.publication import publish_manifest
        if 'derived_backups' not in record:
            folder = paths.backups / ('layout-derived-' + str(time.time_ns()))
            saved = {}
            for name in ('current.json', 'llms.txt'):
                original = paths.exports / name
                if original.exists():
                    folder.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(original, folder / name)
                    saved[name] = str(folder / name)
                else:
                    saved[name] = None
            record['derived_backups'] = saved
            save_journal(journal, record)
        publish_manifest(paths)
        citation = paths.data / 'citation'
        if citation.is_dir() and (not any(citation.iterdir())):
            citation.rmdir()
        record.update(status='complete', completed=now(), after=after)
        save_journal(journal, record)
        return record

def rollback(paths):
    from kbapp.core.locks import update_lock
    with update_lock(paths.data, 'layout-migration.lock'):
        journal = paths.state / 'layout-migration.json'
        if not journal.exists():
            raise ValueError('未发现迁移记录')
        record = load_journal(journal)
        if record['status'] == 'rolled_back':
            return record
        ensure_idle(paths.database)
        relocate_database(paths.database, paths, record['operations'], reverse=True)
        backups = record.get('derived_backups') or {'current.json': record.get('derived_backup')}
        for name, backup in backups.items():
            if backup:
                shutil.copy2(backup, paths.exports / name)
            elif name in record.get('derived_backups', {}):
                (paths.exports / name).unlink(missing_ok=True)
        for operation in reversed(record['operations']):
            source = checked(paths.data, operation['source'])
            target = checked(paths.data, operation['target'])
            if source.exists() and target.exists():
                raise ValueError('回滚目标冲突：' + operation['source'])
            if target.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                target.rename(source)
            operation['status'] = 'pending'
            save_journal(journal, record)
        record.update(status='rolled_back', rolled_back=now())
        save_journal(journal, record)
        return record
