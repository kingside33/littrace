"""kbapp.integrations.scheduling：由职责边界组织的知识库能力。"""
from kbapp.core.paths import Paths
from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid
from kbapp.core.execution import _held, priority

@contextmanager
def file_slot(root, name, timeout=30):
    from kbapp.core.locks import update_lock
    started = time.monotonic()
    while True:
        guard = update_lock(root, name)
        try:
            guard.__enter__()
            break
        except ValueError:
            if time.monotonic() - started >= timeout:
                raise ValueError('笔记同步队列仍忙；已保存产物等待续跑') from None
            time.sleep(0.05)
    try:
        yield
    finally:
        guard.__exit__(None, None, None)

def _alive(pid, tid):
    if pid == os.getpid():
        return any((t.ident == tid and t.is_alive() for t in threading.enumerate()))
    if os.name == 'nt':
        from kbapp.core.win_runtime import process_info
        return bool(process_info(pid))
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True

@contextmanager
def model_slot(root, resource='local', timeout=180, should_stop=lambda: False):
    root = Path(root).resolve()
    key = (str(root), resource)
    held = getattr(_held, 'keys', set())
    if key in held:
        if should_stop():
            raise ValueError('任务已停止；已完成结果保留')
        yield
        return
    root.mkdir(parents=True, exist_ok=True)
    path = Paths.for_data(root).runtime / 'model-queue.sqlite3'
    path.parent.mkdir(parents=True, exist_ok=True)
    ticket = uuid.uuid4().hex

    @contextmanager
    def connect():
        db = sqlite3.connect(path, timeout=30)
        try:
            db.execute('CREATE TABLE IF NOT EXISTS queue(id TEXT PRIMARY KEY,resource TEXT,priority INTEGER,pid INTEGER,tid INTEGER,status TEXT,created REAL)')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()
    with connect() as db:
        db.execute('INSERT INTO queue VALUES(?,?,?,?,?,?,?)', (ticket, resource, priority.get(), os.getpid(), threading.get_ident(), 'waiting', time.time()))
    from kbapp.storage.activity import queue_event
    queue_event(root, resource, 'waiting_resource')
    started = time.monotonic()
    try:
        while True:
            if should_stop():
                raise ValueError('任务已停止；已完成结果保留')
            with connect() as db:
                db.execute('BEGIN IMMEDIATE')
                for row in db.execute('SELECT id,pid,tid FROM queue').fetchall():
                    if not _alive(row[1], row[2]):
                        db.execute('DELETE FROM queue WHERE id=?', (row[0],))
                active = db.execute("SELECT COUNT(*) FROM queue WHERE resource=? AND status='running'", (resource,)).fetchone()[0]
                first = db.execute("SELECT id FROM queue WHERE resource=? AND status='waiting' ORDER BY priority,created LIMIT 1", (resource,)).fetchone()
                capacity = 1 if resource == 'local' else 2
                acquired = active < capacity and first and (first[0] == ticket)
                if acquired:
                    db.execute("UPDATE queue SET status='running' WHERE id=?", (ticket,))
            if acquired:
                queue_event(root, resource, 'running')
                break
            if time.monotonic() - started >= timeout:
                raise ValueError('模型队列仍忙；查询已保留，可在当前请求安全结束后重试')
            time.sleep(0.1)
        _held.keys = held | {key}
        yield
    finally:
        _held.keys = held
        with connect() as db:
            db.execute('DELETE FROM queue WHERE id=?', (ticket,))
