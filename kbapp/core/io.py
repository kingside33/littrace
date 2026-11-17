"""kbapp.core.io：由职责边界组织的知识库能力。"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import time
from datetime import datetime, timezone, timedelta
import tempfile
TZ = timezone(timedelta(hours=8))

def now():
    return datetime.now(TZ).isoformat(timespec='seconds')

def digest(value):
    return hashlib.sha256(str(value).encode('utf-8')).hexdigest()

def read_json(path, default=None):
    for attempt in range(12):
        try:
            return json.loads(Path(path).read_text(encoding='utf-8'))
        except (FileNotFoundError, ValueError):
            return default
        except PermissionError:
            if attempt == 11:
                raise
            time.sleep(0.1 * (attempt + 1))

def jsonl(path):
    p = Path(path)
    for attempt in range(12):
        try:
            with p.open(encoding='utf-8') as f:
                return [json.loads(s) for s in f if s.strip()]
        except FileNotFoundError:
            return []
        except PermissionError:
            if attempt == 11:
                raise
            time.sleep(0.1 * (attempt + 1))

def atomic(path, value):
    write_text(path, value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2))

def _ensure_dir(path):
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)

def open_retry(path, mode='r', **kwargs):
    for attempt in range(12):
        try:
            return open(path, mode, **kwargs)
        except PermissionError:
            if attempt == 11:
                raise
            time.sleep(0.1 * (attempt + 1))

def write_text(path, text):
    """原子写文本：先写同目录临时文件，再 os.replace。"""
    _ensure_dir(path)
    d = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=d, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        for attempt in range(16):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 15:
                    raise
                time.sleep(0.05 * (attempt + 1))
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise

def write_json(path, obj, indent=1):
    write_text(path, json.dumps(obj, ensure_ascii=False, indent=indent))

def write_jsonl(path, rows):
    write_text(path, ''.join((json.dumps(r, ensure_ascii=False) + '\n' for r in rows)))

def read_jsonl(path, tolerant=True):
    """读 JSONL；tolerant=True 时跳过中断留下的半行。"""
    if not os.path.exists(path):
        return []
    out = []
    with open_retry(path, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                if not tolerant:
                    raise
    return out

def append_jsonl(path, rows):
    """追加写入并 fsync；崩溃最多损坏最后一行，读取时会被跳过。"""
    if not rows:
        return
    _ensure_dir(path)
    with open_retry(path, 'a', encoding='utf-8', newline='\n') as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + '\n')
        fh.flush()
        os.fsync(fh.fileno())

def group_by(rows, key):
    """按 key 分组，后出现的覆盖先出现的（便于 partial 去重）。"""
    out = {}
    for r in rows:
        k = r.get(key)
        if k is None:
            continue
        out.setdefault(k, []).append(r)
    return out

def remove_if_exists(path):
    try:
        os.remove(path)
    except OSError:
        pass
