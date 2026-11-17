"""kbapp.core.files：由职责边界组织的知识库能力。"""
import hashlib
from pathlib import Path
from functools import lru_cache

def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

@lru_cache(maxsize=4096)
def _source_hash(path, size, mtime, ctime):
    return file_hash(path)

def source_hash(path):
    """检索复用未改变文件的摘要；解析、证据展示仍重新检查完整内容。"""
    path = Path(path).resolve()
    st = path.stat()
    return _source_hash(str(path), st.st_size, st.st_mtime_ns, st.st_ctime_ns)
