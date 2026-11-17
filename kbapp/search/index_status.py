"""kbapp.search.index_status：由职责边界组织的知识库能力。"""
from collections import Counter
from pathlib import Path
import threading
import kbapp.core.config as C
from kbapp.core.io import digest, jsonl, now, read_json
_guard = threading.Lock()
_published = {}
_recognized = {}

def stamp(path):
    try:
        value = path.stat()
        return (value.st_mtime_ns, value.st_size)
    except FileNotFoundError:
        return None

def index_health(store, coverage, papers):
    version = store.state('active_version')
    root = store.paths.indexes / 'versions' / version if version else store.root
    signature = (str(root), stamp(root / 'corpus.jsonl'), stamp(root / 'corpus_meta.json'))
    with _guard:
        cached = _published.get(str(store.path))
        if not cached or cached[0] != signature:
            meta = read_json(root / 'corpus_meta.json', {})
            rows = {row['id']: row['kind'] for row in jsonl(root / 'corpus.jsonl')}
            ready = set(meta.get('vector_ids', [])) & rows.keys()
            cached = (signature, rows, Counter(rows.values()), Counter((rows[sid] for sid in ready)), meta)
            _published[str(store.path)] = cached
        _, published, counts, vectors, meta = cached
        paths = {pid: store.base / p['pdf'] for pid, p in papers.items() if p.get('kind') == 'library' and p.get('pdf')}
        paths.update({a['paper_id']: Path(a['filepath']) for a in store.approvals() if a['status'] == 'downloaded' and a.get('filepath') and (a['paper_id'] in papers)})
        live = {}
        files = list((store.paths.cache / 'recognized_sources').glob('*.json'))
        for file in files:
            key = str(file)
            signature = stamp(file)
            record = _recognized.get(key)
            if not record or record[0] != signature:
                data = read_json(file, {})
                record = (signature, data.get('paper_id'), data.get('pdf_hash'), tuple(((r['id'], r['kind']) for r in data.get('sources', []))))
                _recognized[key] = record
            path = paths.get(record[1])
            fingerprint = stamp(path) if path else None
            if fingerprint is None or digest(str(fingerprint[0]) + str(fingerprint[1])) != record[2]:
                continue
            live.update(((sid, kind) for sid, kind in record[3] if sid not in published))
        existing = {str(file) for file in files}
        for key in list(_recognized):
            if Path(key).parent == store.paths.cache / 'recognized_sources' and key not in existing:
                del _recognized[key]
    if not published:
        counts = Counter(coverage.get('by_kind', {}))
    live_counts = Counter(live.values())
    published_total = len(published) if published else int(coverage.get('sources') or 0)
    published_vectors = sum(vectors.values()) if published else int(coverage.get('vector_count') or 0)
    total = published_total + len(live)
    from kbapp.integrations.model_identity import embedding_signature
    current = embedding_signature()
    compatible = meta.get('embedding') == current and current.get('revision') not in (None, 'unknown')
    rows = [{'kind': kind, 'sources': counts[kind] + live_counts[kind], 'published_sources': counts[kind], 'vectors': vectors[kind], 'published_pending': max(0, counts[kind] - vectors[kind]), 'live_pending': live_counts[kind], 'pending': max(0, counts[kind] - vectors[kind]) + live_counts[kind]} for kind in sorted(counts.keys() | live_counts.keys())]
    return {'version': version, 'observed_at': now(), 'sources': total, 'vector_count': published_vectors, 'vector_pending': max(0, total - published_vectors), 'published_sources': published_total, 'published_pending': max(0, published_total - published_vectors), 'live_vector_pending': len(live), 'percent': round(100 * published_vectors / total, 2) if total else 0, 'by_kind': rows, 'embedding_compatible': compatible, 'usable_vector_count': published_vectors if compatible else 0, 'vector_warning': '' if compatible else '旧向量已保存，但模型权重版本无法与当前索引对应；当前使用关键词检索，可继续向量索引以更新。', 'embedding': meta.get('embedding', {'model': C.EMBED_MODEL, 'dims': C.EMBED_DIMS})}
