"""kbapp.search.indexing：由职责边界组织的知识库能力。"""
from __future__ import annotations
from collections import Counter, defaultdict
from contextlib import closing
import json
import hashlib
from time import perf_counter
from pathlib import Path
import pickle
import re
import sqlite3
import threading
import numpy as np
import kbapp.core.config as C
from kbapp.search.budget import split_source, split_sources, within_budget, embedding_policy
from kbapp.integrations.embedding import embed_texts, truncate_normalize
from kbapp.search.ranking import BM25, tokenize, rrf_multi
from kbapp.search.vector_cache import VectorStore
from kbapp.library.sources import note_sources, paper_url, approved_papers
from kbapp.core.io import atomic, digest, jsonl, read_json
LABELS = {'paper_text': '论文原文', 'metadata': '文献元数据与摘要', 'figure': '图表与图内文字', 'formula': '公式与上下文', 'zotero_note': 'Zotero笔记', 'zotero_annotation': 'Zotero批注', 'obsidian_note': 'Obsidian笔记', 'ai_analysis': 'AI分析', 'external_abstract': '库外元数据与摘要'}

def build_corpus(store, index, papers, notes):
    by_key = {p['citekey']: p for p in papers + approved_papers(store)}
    result = []

    def add(src, legacy_ids=True):
        for r in split_source(src, legacy_ids=legacy_ids):
            r.setdefault('source_label', LABELS.get(r['kind'], r['kind']))
            result.append(r)
    for p in papers:
        add({'id': 'metadata:' + p['id'], 'paper_id': p['id'], 'kind': 'metadata', 'origin': 'bibliographic', 'title': p['title'], 'text': '\n'.join((str(x) for x in [p['title'], '; '.join(p.get('authors') or []), p.get('abstract')] if x)), 'url': paper_url(p), 'citekey': p['citekey']})
    for r in jsonl(Path(index) / 'chunks.jsonl'):
        p = by_key.get(r['citekey'])
        if p:
            ocr_pages = [n for n in range(r.get('page_start', 1), r.get('page_end', r.get('page_start', 1)) + 1) if (Path(index) / 'ocr_pages' / r['citekey'] / f'p{n:04d}.txt').exists()]
            add(dict(r, id='text:' + r['chunk_id'], paper_id=p['id'], kind='paper_text', origin='paper', ocr_provenance={'model': 'GLM-OCR', 'pages': ocr_pages} if ocr_pages else None, title=p['title'], url=paper_url(p, r.get('page_start'))))
    from kbapp.library.context import table_context, formula_context
    for filename, kind in [('figures.jsonl', 'figure'), ('formulas.jsonl', 'formula')]:
        for r in jsonl(Path(index) / filename):
            p = by_key.get(r['citekey'])
            if not p:
                continue
            if kind == 'formula' and r.get('latex') and (not r.get('ocr_provenance')):
                r.setdefault('latex_status', 'legacy_unverified')
            parts = [r.get('caption'), r.get('inside_text'), r.get('table_text'), r.get('context')] if kind == 'figure' else [r.get('latex'), r.get('raw_text'), r.get('context')]
            if kind == 'figure' and (r.get('table_text') or r.get('kind') == 'table'):
                r['table_context'] = table_context(r.get('caption', ''), r.get('table_text', ''), r.get('context', ''))
            if kind == 'formula':
                r['formula_context'] = formula_context(r.get('latex', ''), r.get('context', ''))
            add(dict(r, id=kind + ':' + r['citekey'] + ':' + r['id'], paper_id=p['id'], kind=kind, origin='paper_ocr', text='\n'.join((x for x in parts if x)), title=p['title'], url=paper_url(p, r.get('page'))))
    for r in notes + note_sources(store, papers):
        add(r)
    from kbapp.library.documents import sources as document_sources
    for source in document_sources(store):
        add(source, legacy_ids=False)
    from kbapp.library.recognition import active_sources
    for src in active_sources(store):
        add(dict(src, source_label='GLM-OCR论文原文'), legacy_ids=False)
    for p in store.papers().values():
        if p.get('kind') == 'library':
            continue
        add({'id': 'external:' + p['id'], 'paper_id': p['id'], 'kind': 'external_abstract', 'origin': 'bibliographic', 'title': p.get('title', ''), 'text': '\n'.join((x for x in [p.get('title'), p.get('abstract')] if x)), 'url': p.get('doi_url') or 'https://openalex.org/' + p.get('openalex_id', '')})
    import pymupdf
    for a in store.approvals():
        if a['status'] != 'downloaded' or not Path(a['filepath']).exists():
            continue
        p = store.papers().get(a['paper_id'])
        if not p:
            continue
        if any((r.get('paper_id') == p['id'] and r['kind'] == 'paper_text' for r in result)):
            continue
        with pymupdf.open(a['filepath']) as doc:
            for n, page in enumerate(doc, 1):
                add({'id': f"external-pdf:{p['id']}:{n}", 'paper_id': p['id'], 'kind': 'paper_text', 'origin': 'paper', 'title': p['title'], 'page_start': n, 'page_end': n, 'text': page.get_text(sort=True), 'url': '/api/file?paper=' + __import__('urllib.parse', fromlist=['quote']).quote(p['id'], safe='') + f'#page={n}'})
    result = list({r['id']: r for r in result}.values())
    from kbapp.library.equivalence import annotate as annotate_equivalence
    annotate_equivalence(result)
    atomic(Path(index) / 'corpus.jsonl', ''.join((json.dumps(r, ensure_ascii=False) + '\n' for r in result)))
    return result

def build_vectors(store, index, corpus, progress=lambda x: None, limit=None):
    from kbapp.core.io import now
    from kbapp.integrations.models import cancelled
    from kbapp.integrations.model_identity import embedding_signature
    # Also protects vector-only/resume callers which did not call build_corpus.
    # Mutate the list so their publication counts still match the saved matrix.
    corpus[:] = list({r['id']: r for r in split_sources(corpus)}.values())
    started = now()
    store.set_state('vector_build', {'version': Path(index).name, 'state': 'running', 'phase': 'preparing', 'total': len(corpus), 'started': started, 'updated': started})
    signature = embedding_signature()
    known_revision = signature.get('revision') not in (None, 'unknown')
    namespace = digest(json.dumps(signature, sort_keys=True))[:20]
    cache = VectorStore(str(store.paths.cache / 'vectors' / namespace), C.EMBED_MODEL, C.EMBED_DIMS)
    def strict_key(h):
        return 'full-input-v1:' + h
    seeds = {}
    published_index = store.active_dir()
    published_ready = set()

    def valid(v):
        return v is not None and v.shape == (C.EMBED_DIMS,) and np.isfinite(v).all() and np.linalg.norm(v) > 0

    for root in dict.fromkeys([published_index, Path(index)]):
        meta = read_json(root / 'corpus_meta.json', {})
        if root == published_index:
            published_ready = set(meta.get('vector_ids', []))
        if known_revision and meta.get('embedding') == signature and (root / 'corpus.npy').exists():
            saved = np.load(root / 'corpus.npy', mmap_mode='r')
            ready_ids = set(meta.get('vector_ids', []))
            try:
                for i, r in enumerate(jsonl(root / 'corpus.jsonl')):
                    if r['id'] not in ready_ids or i >= len(saved) or not within_budget(r['text']):
                        continue
                    h = digest(r['text'])
                    status = meta.get('vector_coverage', {}).get(r['id'], {}).get('status')
                    if valid(saved[i]):
                        key = strict_key(h) if status == 'full_input' else h
                        seeds[key] = np.array(saved[i], copy=True)
            finally:
                saved._mmap.close()
        legacy = read_json(root / 'vec/index_meta.json', {})
        if known_revision and legacy.get('embedding') == signature and (root / 'vec/chunks.npy').exists():
            saved = np.load(root / 'vec/chunks.npy', mmap_mode='r')
            try:
                for i, r in enumerate(jsonl(root / 'vec/chunks_meta.jsonl')):
                    if i < len(saved) and within_budget(r['text']) and valid(saved[i]):
                        seeds.setdefault(digest(r['text']), np.array(saved[i], copy=True))
            finally:
                saved._mmap.close()
    mat = np.zeros((len(corpus), C.EMBED_DIMS), dtype=np.float32)
    todo, ready = [], []
    positions = defaultdict(list)
    coverage = {}
    for i, r in enumerate(corpus):
        h = r['text_hash']
        key = strict_key(h)
        v = seeds.get(key)
        if v is None and known_revision:
            v = cache.get(key)
        status = 'full_input'
        # Old normal inputs retain their vectors. Legacy truncation cannot be
        # proven absent, so label the weaker guarantee instead of claiming full.
        if not valid(v):
            status = 'legacy_budget_checked'
            v = seeds.get(h)
            if v is None and known_revision:
                v = cache.get(h)
        coverage[r['id']] = {'status': 'pending', 'input_hash': h}
        if valid(v):
            mat[i] = v
            ready.append(r['id'])
            coverage[r['id']]['status'] = status
        else:
            if not positions[h]:
                todo.append(i)
            positions[h].append(i)
    warnings = [] if known_revision else ['无法确认Embedding权重版本，本轮保留关键词索引，待模型服务恢复后继续向量化']
    max_new = len(todo) if limit is None else max(0, limit)
    if not known_revision:
        max_new = 0
    reused, completed = len(ready), 0
    remaining = len(corpus) - reused

    def snapshot(state='running', phase='vectors'):
        available = set(ready)
        store.set_state('vector_build', {'version': Path(index).name, 'state': state, 'phase': phase, 'total': len(corpus), 'completed': len(ready), 'new_completed': completed, 'reused': reused, 'pending': len(corpus) - len(ready), 'cached_unpublished': len(available - published_ready), 'by_kind': dict(Counter((r['kind'] for r in corpus if r['id'] in available))), 'started': started, 'updated': now()})

    def compute(ids):
        nonlocal completed
        if cancelled(store, 'embedding'):
            return
        try:
            raw = np.asarray(embed_texts(C.EMBED_MODEL, [corpus[i]['text'] for i in ids], store=store), dtype=np.float32)
            if raw.ndim != 2 or raw.shape[0] != len(ids) or raw.shape[1] < C.EMBED_DIMS:
                raise ValueError('无效向量返回维度')
            arr = truncate_normalize(raw, C.EMBED_DIMS)
            if not all(valid(v) for v in arr):
                raise ValueError('无效向量返回（零值或非有限值）')
        except Exception as exc:
            if len(ids) > 1:
                mid = len(ids) // 2
                compute(ids[:mid])
                compute(ids[mid:])
            else:
                reason = f'{type(exc).__name__}: {exc}'[:300]
                for row in positions[corpus[ids[0]]['text_hash']]:
                    coverage[corpus[row]['id']].update(status='failed', reason=reason)
                warnings.append(f"向量来源 {corpus[ids[0]]['id']} 失败（{reason}）；未截断，保留关键词检索并可续算")
            return
        # Cache commits are outside model-error isolation; a disk failure must
        # surface rather than look like a failed input or corrupt ready coverage.
        cache.put_batch([strict_key(corpus[i]['text_hash']) for i in ids], arr)
        for j, i in enumerate(ids):
            for row in positions[corpus[i]['text_hash']]:
                mat[row] = arr[j]
                ready.append(corpus[row]['id'])
                coverage[corpus[row]['id']]['status'] = 'full_input'
                completed += 1
        snapshot()

    snapshot()
    for start in range(0, min(len(todo), max_new), 64):
        if cancelled(store, 'embedding'):
            warnings.append('向量模型已停止，已缓存的向量保留，未完成来源下次续算')
            break
        ids = todo[start:start + min(64, max_new - start)]
        progress(f'Qwen 向量化 {completed}/{remaining}；复用 {reused}')
        compute(ids)
        progress(f'Qwen 向量化 {completed}/{remaining}；复用 {reused}')
    snapshot(phase='publishing')
    atomic(Path(index) / 'corpus.jsonl', ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in corpus))
    np.save(Path(index) / 'corpus.npy', mat)
    counts = dict(Counter((r['kind'] for r in corpus)))
    atomic(Path(index) / 'corpus_meta.json', {'embedding': signature, 'embedding_policy': embedding_policy(), 'vector_coverage': coverage, 'sources': len(corpus), 'by_kind': counts, 'vector_ids': ready, 'vector_count': len(ready), 'vector_pending': len(corpus) - len(ready)})
    build_keyword_index(index, corpus)
    snapshot('complete' if len(ready) == len(corpus) else 'partial', 'awaiting_publication')
    return warnings

def research_tokens(text):
    tokens = tokenize(text)
    tokens.extend((x.lower() for x in re.findall('\\b[A-Za-z][A-Za-z0-9-]*\\*', text)))
    return tokens

KEYWORD_POLICY_VERSION = 'research-tokens-star-fts5-v1'
KEYWORD_BATCH_ROWS = 500


def build_keyword_index(index, corpus):
    """Resume a durable rowid prefix only for the exact corpus/tokenizer policy.

    The checkpoint and each FTS batch share one SQLite transaction. The old
    published index is untouched until optimize + complete commit and close.
    Bump KEYWORD_POLICY_VERSION when research_tokens/tokenize semantics change.
    """
    from kbapp.search.ranking import CJK, LATIN, STOP
    strategy = json.dumps([KEYWORD_POLICY_VERSION, CJK.pattern, LATIN.pattern, sorted(STOP),
                           sqlite3.sqlite_version, 'fts5(text,title,content=empty)'], sort_keys=True)
    fingerprint = hashlib.sha256(strategy.encode('utf-8'))
    for r in corpus:
        fingerprint.update(json.dumps([r['id'], r['text'], r.get('title', '')],
                                      ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
        fingerprint.update(b'\n')
    fingerprint = fingerprint.hexdigest()
    path = Path(index) / 'corpus-search.sqlite3'
    tmp = path.with_suffix('.building.sqlite3')
    db = sqlite3.connect(tmp)
    try:
        db.execute('PRAGMA journal_mode=DELETE')
        db.execute('PRAGMA synchronous=FULL')
        db.execute('CREATE TABLE IF NOT EXISTS build_checkpoint (id INTEGER PRIMARY KEY CHECK(id=1), fingerprint TEXT NOT NULL, strategy TEXT NOT NULL, last_rowid INTEGER NOT NULL, complete INTEGER NOT NULL)')
        checkpoint = db.execute('SELECT fingerprint,last_rowid,complete FROM build_checkpoint WHERE id=1').fetchone()
        if not checkpoint or checkpoint[0] != fingerprint:
            with db:
                db.execute('DROP TABLE IF EXISTS docs')
                db.execute("CREATE VIRTUAL TABLE docs USING fts5(text,title,content='')")
                db.execute('INSERT OR REPLACE INTO build_checkpoint VALUES(1,?,?,0,0)', (fingerprint, strategy))
            checkpoint = (fingerprint, 0, 0)
        last, complete = checkpoint[1:]
        if not 0 <= last <= len(corpus):
            raise ValueError('FTS checkpoint rowid outside corpus')
        for start in range(last, len(corpus), KEYWORD_BATCH_ROWS):
            rows = []
            for i in range(start, min(start + KEYWORD_BATCH_ROWS, len(corpus))):
                r = corpus[i]
                text = ' '.join(t.replace('*', 'star') for t in research_tokens(r['text']))
                title = ' '.join(t.replace('*', 'star') for t in research_tokens(r.get('title', '')))
                rows.append((i + 1, text, title))
            with db:
                db.executemany('INSERT INTO docs(rowid,text,title) VALUES(?,?,?)', rows)
                db.execute('UPDATE build_checkpoint SET last_rowid=? WHERE id=1', (rows[-1][0],))
        if not complete:
            with db:
                db.execute("INSERT INTO docs(docs) VALUES('optimize')")
                db.execute('UPDATE build_checkpoint SET complete=1 WHERE id=1')
    finally:
        db.close()
    tmp.replace(path)


def vector_topk(scores, allowed, topk):
    """Exact positive-score top-k; boundary ties use ascending corpus rowid.

    Apply the complete scope before partitioning, then sort only selected rows.
    Equal boundary scores cannot be left to argpartition's arbitrary ordering.
    """
    if topk <= 0 or not allowed:
        return []
    rows = np.fromiter(allowed, dtype=np.intp, count=len(allowed))
    values = np.asarray(scores)[rows]
    keep = values > 0
    rows, values = rows[keep], values[keep]
    if len(rows) > topk:
        boundary = np.partition(values, len(values) - topk)[len(values) - topk]
        above = rows[values > boundary]
        tied = rows[values == boundary]
        count = topk - len(above)
        if count < len(tied):
            tied = np.partition(tied, count - 1)[:count]
        rows = np.concatenate((above, tied))
    return rows[np.lexsort((rows, -np.asarray(scores)[rows]))].tolist()

class DiskBM25:

    def __init__(self, path):
        self.path = Path(path)
        self.excluded = frozenset()

    def search(self, tokens, topk=100, allowed=None):
        terms = list(dict.fromkeys((t.replace('*', 'star').replace('"', '') for t in tokens)))[:32]
        if not terms or (allowed is not None and (not allowed)):
            return []
        expression = ' OR '.join(('"' + t + '"' for t in terms if t))
        base = 'SELECT rowid,bm25(docs,1.0,4.0) AS score FROM docs WHERE docs MATCH ? ORDER BY score LIMIT ?'
        with closing(sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True)) as db:
            if allowed is None:
                # Snapshot exclusions are sparse and immutable. Fetch enough to
                # cover every excluded row without building/copying a 100k mask.
                rows = db.execute(base, [expression, topk + len(self.excluded)]).fetchall()
                return [(r[0] - 1, -float(r[1])) for r in rows if r[0] - 1 not in self.excluded][:topk]
            # E02：允许集合较大时不做逐 rowid/json 掩码探测（实测 13k 值掩码约 340ms/查询）。
            # 分数全局有序：按批取回无掩码候选，在 Python 内按允许集合过滤；
            # 命中数达到 topk 时，其前缀即允许集合内的全局前 topk，结果与掩码 SQL 精确等价。
            keep = allowed if isinstance(allowed, (set, frozenset)) else set(allowed)
            limit = max(topk * 16, 1200)
            while True:
                rows = db.execute(base, [expression, limit]).fetchall()
                hits = [(r[0] - 1, -float(r[1])) for r in rows if r[0] - 1 in keep and r[0] - 1 not in self.excluded]
                if len(hits) >= topk or len(rows) < limit:
                    return hits[:topk]
                limit *= 8

class Search:

    def __init__(self, store):
        self.store = store
        self.version = None
        self.query_cache = {}
        self.corpus = None
        self.live_files = []
        self._load_lock = threading.Lock()
        self._papers_cache = None

    def source_token(self, root):
        """E04：用事务性修订号与文件 stat 作为来源快照 token。

        修订号由数据库触发器维护（schema.revision_*），覆盖 papers、documents、
        document_pages/chunks/blocks 等直接 SQL 写入；识别结果 JSON 与已登记
        PDF/附件的修改由 stat 捕获。token 只读取少量状态，不再反序列化全部来源。
        """
        meta = root / 'corpus_meta.json'
        cache_dir = self.store.paths.cache / 'recognized_sources'
        files = sorted((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in cache_dir.glob('*.json')) if cache_dir.is_dir() else []
        watched = []
        for path in self.live_files:
            try:
                stat = Path(path).stat()
                watched.append((str(path), stat.st_mtime_ns, stat.st_size))
            except OSError:
                watched.append((str(path), None, None))
        with self.store.db() as db:
            revisions = [tuple(r) for r in db.execute("SELECT key,value FROM state WHERE key IN ('source_revision','paper_revision') ORDER BY key")]
            document_paths = sorted({r[0] for r in db.execute('SELECT DISTINCT path FROM documents')})
        return digest(str(root) + str(meta.stat().st_mtime_ns if meta.exists() else 0) + str(revisions) + str(files) + str(watched) + str(document_paths))

    def load(self):
        with self._load_lock:
            self._load_locked()

    def _load_locked(self):
        root = self.store.active_dir()
        frozen = getattr(self, 'source_snapshot', None)
        if frozen:
            token, live, _papers = frozen
            if token == self.version and self.corpus is not None:
                return
        else:
            token = self.source_token(root)
            if token == self.version and self.corpus is not None:
                return
            from kbapp.library.recognition import active_sources
            live = active_sources(self.store)
            from kbapp.library.documents import sources as document_sources
            live += document_sources(self.store)
        corpus = jsonl(root / 'corpus.jsonl')
        if not corpus:
            raise ValueError('统一索引尚未发布，请查看任务状态')
        if (root / 'corpus-search.sqlite3').exists():
            bm = DiskBM25(root / 'corpus-search.sqlite3')
        else:
            with (root / 'corpus_bm25.pkl').open('rb') as f:
                bm = pickle.load(f)
        mat = np.load(root / 'corpus.npy', mmap_mode='r') if (root / 'corpus.npy').exists() else None
        meta = read_json(root / 'corpus_meta.json', {})
        live = split_sources(live)
        current = {s['id']: s for s in live}
        valid_documents = {s['id'] for s in live if s.get('document_id')}
        self.invalid_documents = {r['id'] for r in corpus if r.get('document_id') and r['id'] not in valid_documents}
        self.invalid_rows = set()
        retained = set()
        migrated = []
        for i, r in enumerate(corpus):
            replacement = current.get(r['id'])
            if r['id'] in self.invalid_documents:
                self.invalid_rows.add(i)
            elif replacement is not None:
                # A stable source ID alone does not make old FTS/vector content
                # valid. Changed live text gets its own keyword-only row.
                if r['text'] != replacement['text'] or r.get('title', '') != replacement.get('title', ''):
                    self.invalid_rows.add(i)
                else:
                    corpus[i] = replacement
                    retained.add(r['id'])
            elif not within_budget(r['text']):
                self.invalid_rows.add(i)
                migrated.extend(split_source(r))
            else:
                retained.add(r['id'])
        extra = list({r['id']: r for r in migrated + live if r['id'] not in retained}.values())
        self.live_start = len(corpus)
        self.live_bm = BM25([research_tokens(r['text'] + ' ' + r.get('title', '')) for r in extra]) if extra else None
        corpus.extend(extra)
        if extra:
            from kbapp.library.equivalence import annotate as annotate_equivalence
            annotate_equivalence(extra)
        self.corpus, self.bm, self.mat, self.meta = (corpus, bm, mat, meta)
        # Scope facts depend only on this source snapshot, not on the query.
        self.valid_rows = frozenset(range(len(corpus))) - self.invalid_rows
        self._ready_ids = None
        if isinstance(bm, DiskBM25):
            bm.excluded = frozenset(self.invalid_rows)
        self.by_id = {r['id']: r for i, r in enumerate(corpus) if i not in self.invalid_rows}
        self.source_parts = defaultdict(list)
        for r in self.by_id.values():
            for identity in {r.get('parent_id'), r.get('source_id'), r['id']} - {None}:
                self.source_parts[identity].append(r['id'])
        if not frozen:
            # 记录本次构建实际校验过的附件路径，下次 token 只 stat 这些文件
            with self.store.db() as db:
                document_paths = {r[0] for r in db.execute('SELECT DISTINCT path FROM documents')}
                pdf_paths = {r[0] for r in db.execute("SELECT DISTINCT json_extract(payload,'$.pdf') FROM papers WHERE json_extract(payload,'$.pdf') IS NOT NULL")}
            self.live_files = sorted(p for p in document_paths | pdf_paths if p)
            # 新发现的路径纳入 token，避免下一次查询因 token 变化而重复构建
            token = self.source_token(root)
        self.version = token

    def papers_map(self):
        """E05：papers 派生数据按修订号缓存；分类与方法映射变更同样使其失效。"""
        from kbapp.library.catalog import enriched_papers
        with self.store.db() as db:
            revision = db.execute("SELECT value FROM state WHERE key='paper_revision'").fetchone()
            methods = db.execute('SELECT COUNT(*),COALESCE(MAX(rowid),0) FROM paper_methods').fetchone()
        key = (revision[0] if revision else 0, tuple(methods))
        if self._papers_cache and self._papers_cache[0] == key:
            return self._papers_cache[1]
        papers = enriched_papers(self.store)
        self._papers_cache = (key, papers)
        return papers

    def search(self, query, k=12, kind='', direction='', year='', project=False, paper='', section='', vectors=True, priority='', wait_vectors=False, paper_ids=None, collection='', tag='', document='', level='passage'):
        started = perf_counter()
        timings = {name: 0.0 for name in ('load', 'filter', 'fts', 'query_vector', 'dot', 'topk', 'ranking')}

        def timed(name, function, *args, **kwargs):
            before = perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                timings[name] += (perf_counter() - before) * 1000

        timed('load', self.load)
        filter_started = perf_counter()
        frozen = getattr(self, 'source_snapshot', None)
        papers = frozen[2] if frozen else self.papers_map()
        pid = self.store.resolve(paper) if paper else ''
        filters = bool(kind or direction or year or project or paper or paper_ids is not None or section or priority or collection or tag or document)
        full_range = not filters and (not self.invalid_rows or isinstance(self.bm, DiskBM25))
        allowed = set()
        if not filters:
            # Validity was checked at load and follows the source revision. Disk
            # FTS applies its sparse exclusions even when allowed=None.
            allowed = self.valid_rows
        else:
            for i, r in enumerate(self.corpus):
                if i in self.invalid_rows:
                    continue
                p = papers.get(r.get('paper_id'), {})
                if kind and r['kind'] != kind:
                    continue
                if collection and collection not in p.get('collections', []):
                    continue
                if tag and tag not in p.get('tags', []):
                    continue
                if document and r.get('document_id') != document:
                    continue
                if direction and direction not in p.get('directions', []):
                    continue
                if project and (not p.get('project')):
                    continue
                if priority and priority not in (p.get('tagfields') or {}).get('priority', []):
                    continue
                if paper and r.get('paper_id') != pid:
                    continue
                if paper_ids is not None and r.get('paper_id') not in paper_ids:
                    continue
                if section and (not (r.get('section') or '').lower().startswith(section.lower())):
                    continue
                if year:
                    if ',' in year or '，' in year:
                        if int(p.get('year') or 0) not in {int(y.strip()) for y in re.split('[,，]', year)}:
                            continue
                    else:
                        limits = year.split('-')
                        low = int(limits[0])
                        high = int(limits[-1])
                        if not low <= int(p.get('year') or 0) <= high:
                            continue
                allowed.add(i)
        terms = research_tokens(query)
        from kbapp.core.metadata import normalize_doi
        exact_doi = normalize_doi(query)
        if exact_doi:
            matching = {pid for pid, p in papers.items() if normalize_doi(p.get('doi')) == exact_doi}
            allowed = {i for i in allowed if self.corpus[i].get('paper_id') in matching}
            full_range = False
            vectors = False
        timings['filter'] = (perf_counter() - filter_started) * 1000
        bh = timed('fts', self.bm.search, terms, topk=max(k * 8, 100), allowed=None if full_range else allowed)
        if self.live_bm:
            bh.extend(((i + self.live_start, score) for i, score in timed('fts', self.live_bm.search, terms, topk=max(k * 8, 100), allowed=None if full_range else {i - self.live_start for i in allowed if i >= self.live_start})))
        qnorm = re.sub('\\s+', ' ', query.lower()).strip()
        boosted = {i: s for i, s in bh}
        from kbapp.core.metadata import normalize_doi
        doi = normalize_doi(query)
        if doi:
            for i in allowed:
                if normalize_doi(papers.get(self.corpus[i].get('paper_id'), {}).get('doi')) == doi:
                    boosted[i] = boosted.get(i, 0) + 1000
        if len(qnorm) >= 3:
            for i in allowed:
                title = self.corpus[i].get('title', '').lower()
                if qnorm in title:
                    record = self.corpus[i]
                    boosted[i] = boosted.get(i, 0) + 80 + (20 if papers.get(record.get('paper_id'), {}).get('kind') == 'library' else 0) + (40 if title.startswith(qnorm) else 0) + {'metadata': 30, 'paper_text': 10}.get(record['kind'], 0)
        bh = sorted(boosted.items(), key=lambda x: -x[1])[:max(k * 8, 100)]
        lists = [('all', bh)]
        mode = 'bm25'
        warning = ''
        scores = None
        ready = ()
        vector_rows = set()
        vector_status = 'disabled' if not vectors else 'unavailable'
        if vectors:
            if self._ready_ids is None:
                self._ready_ids = frozenset(self.meta.get('vector_ids', []))
            ready = self._ready_ids
            from kbapp.integrations.model_identity import embedding_signature
            current_signature = timed('query_vector', embedding_signature)
            if self.meta.get('embedding') == current_signature and (current_signature or {}).get('revision') != 'unknown':
                vector_rows = {i for i in allowed if i < self.live_start and self.corpus[i]['id'] in ready}
        if vectors and self.mat is not None and vector_rows:
            from kbapp.search.query import query_vector
            try:
                vector, vector_status = timed('query_vector', query_vector, self.store, query, wait=wait_vectors)
                if vector is None:
                    raise ValueError('向量查询等待中' if vector_status == 'queued' else '向量不可用')
                scores = timed('dot', np.matmul, self.mat, vector)
                order = timed('topk', vector_topk, scores, vector_rows, max(100, k * 8))
                lists.append(('all', [(i, float(scores[i])) for i in order]))
                mode = 'bm25+vector(rrf)'
            except Exception as exc:
                if vector_status in {'ready', 'cached'}:
                    vector_status = 'unavailable'
                warning = '查询超过向量输入预算，未截断；请缩短问题，当前使用关键词检索' if vector_status == 'too_long' else '查询已进入交互优先队列，当前先显示关键词结果' if vector_status == 'queued' else '向量模型已被手动停止，当前使用关键词结果' if vector_status == 'stopped' else f'向量检索不可用（{type(exc).__name__}），当前结果使用关键词检索'
        elif vectors:
            vector_status = 'no_index'
            warning = '当前筛选范围尚无可用向量，暂用关键词检索'
        from kbapp.search.query import source_quality
        metadata_rows = {i for i in allowed if self.corpus[i]['kind'] in {'metadata', 'external_abstract'}}
        coarse_keyword = timed('fts', self.bm.search, terms, topk=120, allowed=metadata_rows)
        coarse_lists = [('all', coarse_keyword)]
        if scores is not None:
            order = timed('topk', vector_topk, scores, metadata_rows & vector_rows, 120)
            coarse_lists.append(('all', [(i, float(scores[i])) for i in order]))
        paper_scores = {}
        for (_, i), score in rrf_multi(coarse_lists):
            r = self.corpus[i]
            identity = r.get('paper_id') or r['source_id']
            paper_scores[identity] = max(score, paper_scores.get(identity, 0))
        passage_papers = {}
        for (_, i), score in rrf_multi(lists):
            r = self.corpus[i]
            identity = r.get('paper_id') or r['source_id']
            passage_papers[identity] = max(score * source_quality(r), passage_papers.get(identity, 0))
        paper_lists = [('paper', sorted(ranking.items(), key=lambda item: -item[1])) for ranking in (paper_scores, passage_papers)]
        paper_scores = {identity: score for (_, identity), score in rrf_multi(paper_lists)}
        cues = [c.strip().lower() for c in re.findall('[A-Za-z][A-Za-z0-9*+\\- ]*', query) if c.strip()]
        cues = [c for c in cues if len(c) >= 3 and (any((ch in c for ch in ('*', '-', ' '))) or c.upper() in {'REACT', 'BIT', 'RRT', 'PRM', 'FMT', 'CHOMP', 'STOMP'})]
        focus = set()
        available_papers = {self.corpus[i].get('paper_id') for i in allowed}
        for identity in available_papers:
            p = papers.get(identity, {})
            title = (p.get('title') or '').lower()
            abstract = (p.get('abstract') or '').lower()
            matched = sum((1 for cue in cues if re.search('(?<![a-z0-9])' + re.escape(cue) + '(?![a-z0-9*])', title)))
            author_match = bool(p.get('year') and re.search('\\b(?:19|20)\\d{2}\\b', query) and (str(p['year']) in query) and any((len(a) >= 4 and a.lower() in str(p.get('authors', [])).lower() for a in re.findall('[A-Za-z]+', query))))
            if matched or author_match:
                focus.add(identity)
                paper_scores[identity] = paper_scores.get(identity, 0) + 0.15 * matched + 0.2 * author_match
            elif any(('*' in cue and cue in abstract for cue in cues)):
                paper_scores[identity] = paper_scores.get(identity, 0) + 0.03
        if not paper_scores:
            for (_, i), score in rrf_multi(lists):
                r = self.corpus[i]
                identity = r.get('paper_id') or r['source_id']
                paper_scores[identity] = max(score, paper_scores.get(identity, 0))
        ranked_papers = sorted(paper_scores, key=paper_scores.get, reverse=True)
        semantic_papers = []
        if scores is not None:
            for i, _ in lists[1][1]:
                r = self.corpus[i]
                identity = r.get('paper_id') or r['source_id']
                if source_quality(r) > 0.18 and identity not in semantic_papers:
                    semantic_papers.append(identity)
                if len(semantic_papers) == 8:
                    break
        selected_papers = set(list(dict.fromkeys([p for p in ranked_papers if p in focus] + semantic_papers + ranked_papers))[:20])
        passage_rows = {i for i in allowed if (self.corpus[i].get('paper_id') or self.corpus[i]['source_id']) in selected_papers}
        passage_keyword = timed('fts', self.bm.search, terms, topk=max(k * 12, 160), allowed=passage_rows)
        if self.live_bm:
            passage_keyword.extend(((i + self.live_start, score) for i, score in timed('fts', self.live_bm.search, terms, topk=100, allowed={i - self.live_start for i in passage_rows if i >= self.live_start})))
        passage_lists = [('all', passage_keyword)]
        if exact_doi:
            passage_keyword = [(i, 1000) for i in sorted(passage_rows, key=lambda i: (self.corpus[i]['kind'] != 'metadata', self.corpus[i].get('page_start') or 0))[:max(k * 2, 20)]]
            passage_lists = [('all', passage_keyword)]
        if scores is not None:
            order = timed('topk', vector_topk, scores, passage_rows & vector_rows, max(k * 12, 160))
            passage_lists.append(('all', [(i, float(scores[i])) for i in order]))
        fused = rrf_multi(passage_lists)
        keyword_ranks = {i: rank for rank, (i, _) in enumerate(passage_keyword, 1)}
        vector_ranks = {i: rank for rank, (i, _) in enumerate(passage_lists[1][1], 1)} if scores is not None else {}

        def passage_score(hit):
            src = self.corpus[hit[0][1]]
            identity = src.get('paper_id') or src['source_id']
            # C04：人工指定的主附件版本优先，但不排除其他版本证据
            return hit[1] * source_quality(src) * (3 if identity in focus else 1) * (1.15 if src.get('primary') else 1)
        fused = sorted(fused, key=lambda hit: -passage_score(hit))
        results = []
        per_paper = Counter()
        seen_content = set()
        for (_, i), score in fused:
            r = self.corpus[i]
            identity = r.get('paper_id') or r['source_id']
            if identity not in selected_papers or source_quality(r) == 0:
                continue
            quota = 1 if level == 'paper' else max(8, k // max(1, len(focus))) if identity in focus else 8 if k >= 40 else 3
            if per_paper[identity] >= quota and (not pid):
                continue
            # E07：同一论文同一页面同一规范化内容只保留得分最高的一条；不同附件版本内容不同时保留
            content_key = (identity, r.get('page_start') or r.get('page'), r.get('content_group') or r.get('text_hash') or r['id'])
            if content_key in seen_content:
                continue
            seen_content.add(content_key)
            per_paper[identity] += 1
            p = papers.get(r.get('paper_id'), {})
            results.append(dict(r, score=round(score, 6), year=p.get('year'), authors=p.get('authors', []), directions=p.get('directions', []), project_stages=p.get('project_stages', []), retrieval={'keyword_rank': keyword_ranks.get(i), 'vector_rank': vector_ranks.get(i), 'vector_similarity': round(float(scores[i]), 6) if i in vector_ranks else None}, snippet=r['text'][:900]))
            if len(results) >= k:
                break
        live_pending = len(self.corpus) - self.live_start
        if vectors and live_pending and (mode == 'bm25'):
            warning = '；'.join((x for x in (warning, f'已发布的{len(ready):,}条向量可用；另有{live_pending:,}条新识别原文暂用关键词补充，待增量向量发布') if x))
        total = (perf_counter() - started) * 1000
        timings['ranking'] = max(0.0, total - sum(timings.values()))
        timings['total'] = total
        return {'query': query, 'mode': mode, 'version': self.version, 'warning': warning, 'results': results, 'vector_status': vector_status, 'timings_ms': {key: round(value, 3) for key, value in timings.items()}, 'papers': [dict({field: papers.get(pid, {}).get(field) for field in ('id', 'title', 'year', 'kind', 'abstract', 'citekey', 'methods', 'directions')}, score=paper_scores[pid]) for pid in sorted(selected_papers, key=paper_scores.get, reverse=True)], 'coverage': {'sources': len(self.corpus), 'published_sources': self.meta.get('sources'), 'vectors': self.meta.get('vector_count'), 'pending': (self.meta.get('vector_pending') or 0) + live_pending, 'live_vector_pending': live_pending}}
