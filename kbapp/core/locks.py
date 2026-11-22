"""kbapp.core.locks：由职责边界组织的知识库能力。"""
from __future__ import annotations
from kbapp.core.io import open_retry
from kbapp.core.paths import Paths
import contextlib
import os

@contextlib.contextmanager
def update_lock(root, filename='workbench-update.lock'):
    path = Paths.for_data(root).runtime / 'locks' / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    # Windows 上锁文件会被实时扫描等外部句柄短暂占用，open 抛出的
    # PermissionError 是瞬时共享冲突而非权限配置问题；与其他共享文件一致，
    # 经 open_retry 限次重试（kbapp/core/io.py），真权限问题重试耗尽后原样抛出。
    with open_retry(path, 'a+b') as f:
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
            try:
                if os.name == 'nt':
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except OSError:
                # 解锁失败不改变锁的归属：本句柄关闭时即释放其全部字节锁
                # （Windows LockFile 与 POSIX flock 均如此），等待方下一轮重试
                # 必然拿到锁；让异常逃逸只会毁掉一次已完成的工作（issue #97）。
                pass
