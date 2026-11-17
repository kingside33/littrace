"""pytest 仓库根夹具：把未显式配置的测试隔离到临时文献库。

只改 KB_BASE / KB_DATA_DIR 的默认值：两个变量都已设置时原样保留；只设置
其中一个时，仅补齐缺的那个。测试因此不会读写用户真实的文献库。
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

_fallback_root: Path | None = None


def pytest_sessionstart(session):
    """在收集、导入测试模块之前设置环境变量。

    空字符串按“未设置”处理：paths.py 会把空值当成假值并回退到仓库根，
    那样运行数据就会落进真实工作副本。
    """
    global _fallback_root
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
    if _fallback_root is not None:
        shutil.rmtree(_fallback_root, ignore_errors=True)
        _fallback_root = None
