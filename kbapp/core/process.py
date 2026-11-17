"""kbapp.core.process：进程身份与存活判断（I02）。

身份格式 `pid:token:created_ticks`：
- token 每个进程实例随机生成，避免同 pid 误判为同一执行者；
- created_ticks 在 Windows 上来自 GetProcessTimes，用于识别 pid 复用；
- 存活判断在 Windows 使用 OpenProcess（不使用 os.kill，避免误杀进程）。
"""
from __future__ import annotations
import os
import secrets

TOKEN = secrets.token_hex(6)


def _ticks(pid):
    if os.name == 'nt':
        from kbapp.core.win_runtime import process_info
        info = process_info(pid)
        return None if not info else int(info.get('created_ticks') or 0)
    return 0


def identity():
    pid = os.getpid()
    return f'{pid}:{TOKEN}:{_ticks(pid) or 0}'


def parse(owner):
    if not isinstance(owner, str):
        return None
    parts = owner.split(':')
    if len(parts) != 3 or not parts[0].isdigit():
        return None
    return int(parts[0]), parts[1], parts[2]


def alive(owner):
    parsed = parse(owner)
    if not parsed:
        return False
    pid, token, ticks = parsed
    if pid == os.getpid():
        return token == TOKEN
    if os.name == 'nt':
        from kbapp.core.win_runtime import process_info
        info = process_info(pid)
        if not info:
            return False
        return ticks == '0' or str(int(info.get('created_ticks') or 0)) == ticks
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
