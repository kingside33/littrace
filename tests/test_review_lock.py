"""评审请求锁对瞬时 PermissionError 的容忍与上限（issue #97）。

CI 与本地压测中，两线程并发评审时锁文件上的 PermissionError(13) 会裸逃逸。
这里锚定三处防线：锁文件打开经 open_retry 重试、解锁失败不外逃（句柄关闭
即释放字节锁）、获取循环把 PermissionError 与「队列忙」同样处理且有 180 秒上限。
"""
import builtins
import contextlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import kbapp.core.locks as locks
import kbapp.research.review as review
from kbapp.core.locks import update_lock
from kbapp.research.review import request_lock


class ReviewLock(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = self.temp.name

    def tearDown(self):
        self.temp.cleanup()

    def store_stub(self):
        return SimpleNamespace(root=self.root)

    def test_transient_permission_error_on_lock_open_is_retried(self):
        """锁文件被外部句柄短暂占用：open 的 PermissionError 按竞争重试后拿锁。"""
        real_open = builtins.open
        state = {'failed': False}

        def flaky_open(file, mode='r', *args, **kwargs):
            if Path(file).name == 'flaky-open.lock' and not state['failed']:
                state['failed'] = True
                raise PermissionError(13, 'Permission denied')
            return real_open(file, mode, *args, **kwargs)

        with mock.patch('builtins.open', flaky_open):
            with update_lock(self.root, 'flaky-open.lock'):
                inside = True
        self.assertTrue(inside)
        self.assertTrue(state['failed'], '注入的瞬时 PermissionError 必须真实发生')

    def test_unlock_failure_does_not_escape_and_reacquire_succeeds(self):
        """解锁失败不外逃；句柄关闭释放字节锁后，下一个获取者仍能立即拿锁。"""
        if os.name == 'nt':
            import msvcrt
            real_locking = msvcrt.locking

            def flaky(fd, mode, size):
                if mode == msvcrt.LK_UNLCK:
                    raise PermissionError(13, 'Permission denied')
                return real_locking(fd, mode, size)

            patcher = mock.patch.object(msvcrt, 'locking', flaky)
        else:
            import fcntl
            real_flock = fcntl.flock

            def flaky(fd, op):
                if op == fcntl.LOCK_UN:
                    raise PermissionError(13, 'Permission denied')
                return real_flock(fd, op)

            patcher = mock.patch.object(fcntl, 'flock', flaky)
        with patcher, update_lock(self.root, 'flaky-unlock.lock'):
            pass
        with update_lock(self.root, 'flaky-unlock.lock'):
            reacquired = True
        self.assertTrue(reacquired)

    def test_request_lock_treats_permission_error_as_busy(self):
        """获取循环把 PermissionError 与「队列忙」同样处理：重试直至拿到锁。"""
        real_update_lock = locks.update_lock
        attempts = []

        @contextlib.contextmanager
        def flaky(root, filename):
            attempts.append(filename)
            if len(attempts) <= 2:
                raise PermissionError(13, 'Permission denied')
            with real_update_lock(root, filename):
                yield

        with mock.patch.object(locks, 'update_lock', flaky):
            with request_lock(self.store_stub(), 'flaky-key', lambda: False):
                inside = True
        self.assertTrue(inside)
        self.assertEqual(len(attempts), 3)

    def test_request_lock_permission_error_is_bounded_by_queue_busy_cap(self):
        """持续 PermissionError 在 180 秒上限处转成「队列忙」的可续跑错误。

        attempts 计数与 FakeTime 终值把「180 秒」钉进断言（PR #123 评审）：
        上限改小会因尝试次数不足 1801 转红；上限检查被删会触发 FakeTime
        安全阀转红，而不是无限自旋挂死。
        """
        attempts = []

        class BusyLock:
            def __enter__(self):
                attempts.append(1)
                raise PermissionError(13, 'Permission denied')

            def __exit__(self, *exc):
                return False

        class FakeTime:
            def __init__(self):
                self.value = 0.0

            def monotonic(self):
                return self.value

            def sleep(self, seconds):
                self.value += seconds
                if self.value > 1000:
                    raise AssertionError('上限检查未生效：重试循环未在 180 秒处停止')

        fake_time = FakeTime()
        with mock.patch.object(locks, 'update_lock', lambda root, filename: BusyLock()), \
                mock.patch.object(review, 'time', fake_time):
            with self.assertRaisesRegex(ValueError, '核验请求队列仍忙'):
                with request_lock(self.store_stub(), 'busy-key', lambda: False):
                    pass
        # 获取循环「先检查上限、再睡眠」：180 上限时浮点累加落在 1801~1802 次
        # 尝试、终值约 180.1。任何更小的上限（如 0、90）都到不了 1801 次。
        self.assertGreaterEqual(len(attempts), 1801)
        self.assertLessEqual(len(attempts), 1802)
        self.assertGreater(fake_time.value, 180)
        self.assertLess(fake_time.value, 180.5)
