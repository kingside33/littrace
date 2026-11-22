"""pytest 仓库根夹具：把未显式配置的测试隔离到临时文献库。

只改 KB_BASE / KB_DATA_DIR 的默认值：两个变量都已设置时原样保留；只设置
其中一个时，仅补齐缺的那个。测试因此不会读写用户真实的文献库。
"""
from __future__ import annotations

import os
import shutil
import socket
import tempfile
from pathlib import Path

_fallback_root: Path | None = None
_socket_connect_original = None
COMMERCIAL_PORT = 8766
COMMERCIAL_HOSTS = {"127.0.0.1", "localhost"}


def _is_commercial_address(address):
    if not isinstance(address, tuple) or len(address) < 2:
        return False
    return address[1] == COMMERCIAL_PORT and str(address[0]) in COMMERCIAL_HOSTS


def _install_socket_guard():
    """整套测试期间禁止连接本机商业层 8766。

    默认解析器零 I/O 与 pro 路径全部用假 transport，这条守卫兜底：任何
    意外真实探测都会立刻失败，而不是安静地读到本机商业层。
    """
    global _socket_connect_original
    if _socket_connect_original is not None:
        return
    original = socket.socket.connect
    _socket_connect_original = original

    def guarded(self, address):
        if _is_commercial_address(address):
            raise AssertionError("测试套件不得连接 127.0.0.1:8766 商业层")
        return original(self, address)

    socket.socket.connect = guarded


def _restore_socket_guard():
    global _socket_connect_original
    if _socket_connect_original is not None:
        socket.socket.connect = _socket_connect_original
        _socket_connect_original = None


def pytest_sessionstart(session):
    """在收集、导入测试模块之前设置环境变量。

    空字符串按“未设置”处理：paths.py 会把空值当成假值并回退到仓库根，
    那样运行数据就会落进真实工作副本。
    """
    global _fallback_root
    _install_socket_guard()
    missing_base = not os.environ.get("KB_BASE")
    missing_data = not os.environ.get("KB_DATA_DIR")
    if not (missing_base or missing_data):
        return
    factory = getattr(session.config, "_tmp_path_factory", None)
    if factory is not None:
        root = factory.mktemp("littrace-kb")
    else:
        root = Path(tempfile.mkdtemp(prefix="littrace-kb-"))
        _fallback_root = root
    if missing_base:
        os.environ["KB_BASE"] = str(root)
    if missing_data:
        # 与 paths.py 的默认布局一致：<base>/_index。
        os.environ["KB_DATA_DIR"] = str(root / "_index")


def pytest_unconfigure(config):
    """只清理自建的 fallback 目录；pytest 的 tmp_path 由 pytest 自己管理。"""
    global _fallback_root
    _restore_socket_guard()
    if _fallback_root is not None:
        shutil.rmtree(_fallback_root, ignore_errors=True)
        _fallback_root = None
