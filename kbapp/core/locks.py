"""kbapp.core.locks：由职责边界组织的知识库能力。"""
from __future__ import annotations
from kbapp.core.paths import Paths
import contextlib
import os

@contextlib.contextmanager
def update_lock(root, filename='workbench-update.lock'):
    path = Paths.for_data(root).runtime / 'locks' / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as f:
        f.seek(0)
        f.write(b'0')
        f.flush()
        f.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError('已有更新任务运行中') from None
        try:
            yield
        finally:
            f.seek(0)
            if os.name == 'nt':
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
