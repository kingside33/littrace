"""kbapp.search.query：由职责边界组织的知识库能力。"""
import json
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
import numpy as np
import kbapp.core.config as C
from kbapp.core.io import digest, now
from kbapp.core.execution import priority
INSTRUCTION_VERSION = 'scholarly-evidence-v2'
INSTRUCTION = 'Instruct: Retrieve scholarly evidence, research notes, figures or equations relevant to the research question.\nQuery: '
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='interactive-vector')
_guard = threading.Lock()
_pending = {}

def query_vector(store, query, wait=False):
    from kbapp.search.budget import within_budget
    # Check before legacy cache lookup: old entries may represent a prefix.
    if not within_budget(INSTRUCTION + query):
        return (None, 'too_long')
    from kbapp.integrations.model_identity import embedding_signature
    model = embedding_signature()
    if model.get('revision') == 'unknown':
        return (None, 'unavailable')
    signature = json.dumps([model, INSTRUCTION_VERSION], sort_keys=True, separators=(',', ':'))
    key = digest(signature + '\n' + query)
    with store.db() as db:
        row = db.execute("SELECT vector FROM query_vectors WHERE key=? AND signature=? AND status='ready'", (key, signature)).fetchone()
    if row:
        value = np.asarray(json.loads(row[0]), dtype=np.float32)
        if value.shape == (C.EMBED_DIMS,) and np.isfinite(value).all() and (np.linalg.norm(value) > 0):
            return (value, 'cached')
    from kbapp.integrations.models import cancelled
    if cancelled(store, 'embedding'):
        return (None, 'stopped')

    def compute():
        from kbapp.integrations.embedding import embed_texts, truncate_normalize
        token = priority.set(0)
        try:
            with store.db() as db:
                db.execute('INSERT OR REPLACE INTO query_vectors VALUES(?,?,?,?,?,?)', (key, signature, '[]', 'queued', '', now()))
            arr = np.asarray(embed_texts(C.EMBED_MODEL, [INSTRUCTION + query], timeout=60, store=store), dtype=np.float32)
            if arr.shape != (1, C.EMBED_DIMS) or not np.isfinite(arr).all() or np.linalg.norm(arr[0]) == 0:
                raise ValueError('查询向量维度或数值无效')
            value = truncate_normalize(arr, C.EMBED_DIMS)[0]
            with store.db() as db:
                db.execute('UPDATE query_vectors SET vector=?,status=?,error=?,updated=? WHERE key=?', (json.dumps(value.tolist()), 'ready', '', now(), key))
            return value
        except Exception as exc:
            with store.db() as db:
                db.execute('UPDATE query_vectors SET status=?,error=?,updated=? WHERE key=?', ('unavailable', type(exc).__name__, now(), key))
            raise
        finally:
            priority.reset(token)
    with _guard:
        future = _pending.get((str(store.path), key))
        if future is not None and future.done():
            try:
                return (future.result(), 'cached')
            except Exception:
                future = None
        if future is None:
            future = _pool.submit(compute)
            _pending[str(store.path), key] = future
        for previous in list(_pending):
            if previous != (str(store.path), key) and _pending[previous].done():
                del _pending[previous]
    try:
        return (future.result(timeout=245 if wait else 0.2), 'ready')
    except TimeoutError:
        return (None, 'queued')
    except Exception:
        return (None, 'stopped' if cancelled(store, 'embedding') else 'unavailable')

def source_quality(source):
    text = source.get('text', '')
    section = (source.get('section') or '').lower()
    if source.get('derived') or source.get('kind') == 'ai_analysis':
        return 0.0
    if 'reference' in section or 'bibliograph' in section or '参考文献' in section:
        return 0.18
    if text.lstrip().startswith(('{', '[')) and source.get('kind', '').endswith('note'):
        return 0.12
    return {'paper_text': 1.0, 'figure': 0.9, 'formula': 0.85, 'metadata': 0.7, 'external_abstract': 0.65, 'obsidian_note': 0.35, 'zotero_note': 0.4, 'zotero_annotation': 0.5}.get(source.get('kind'), 0.4)
