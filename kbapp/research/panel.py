"""kbapp.research.panel：条目面板三 section 的只读聚合。

插件的条目面板一次只关心一篇文献的三件事实：它被索引了多少证据、它有哪些
已确认的研究关系、它的精读覆盖到哪一步。这三件事分散在语料、关系表和文档
解析表里，逐个接口取会让面板发出三四次请求、并各自失败。这里把它们聚合成
一份只读快照，供 ``GET /api/v2/paper/sections`` 使用。

边界：只读。本模块不写 papers/relations/documents，也不碰 Zotero。
"""
from __future__ import annotations

import json
import re
import threading
from collections import Counter

from kbapp.core.io import jsonl

# 语料 kind 的中文名：只影响面板展示，不改数据。
KIND_LABELS = {
    'paper_text': '段落',
    'figure': '图表',
    'formula': '公式',
    'metadata': '元数据',
    'external_abstract': '外部摘要',
}

# 关系 kind 的中文名，与 Web 版 core.js 的 relationNames 保持一致口径。
RELATION_LABELS = {
    'inherits': '方法继承',
    'improves': '具体改进',
    'combines': '方法融合',
    'transfers': '应用迁移',
    'cites': '引用',
}

EVIDENCE_KINDS = ('paper_text', 'figure', 'formula', 'metadata', 'external_abstract')
EXCERPT_LIMIT = 2
EXCERPT_CHARS = 140
RELATION_LIMIT = 5
GAP_LIMIT = 3
EXPLANATION_CHARS = 200

# {store.path: (stat key, {paper_id: {'counts': {...}, 'excerpts': [...]}})}
# 语料只随 active_version 发布变化，按文件 stat 失效即可，不必每个请求重解析。
_corpus_guard = threading.Lock()
_corpus_cache: dict[str, tuple[tuple, dict]] = {}


def _excerpt(text) -> str:
    return ' '.join(str(text or '').split())[:EXCERPT_CHARS]


def _int(value) -> int:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else 0


def corpus_index(store):
    """按 active_version 建立 {paper_id: counts/excerpts}；索引未发布时返回 None。

    统计对象是已发布语料（搜索真正使用的那一份），不是解析中间表，所以
    「已索引」与检索结果的口径一致。
    """
    version = store.state('active_version')
    if not version:
        return None
    path = store.paths.indexes / 'versions' / str(version) / 'corpus.jsonl'
    if not path.is_file():
        return None
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    with _corpus_guard:
        cached = _corpus_cache.get(str(store.path))
        if cached and cached[0] == key:
            return cached[1]
    index: dict[str, dict] = {}
    for row in jsonl(path):
        paper_id = row.get('paper_id') or ''
        if not paper_id:
            continue
        entry = index.setdefault(paper_id, {'counts': Counter(), 'excerpts': []})
        kind = str(row.get('kind') or 'unknown')
        entry['counts'][kind] += 1
        if kind in EVIDENCE_KINDS and len(entry['excerpts']) < EXCERPT_LIMIT:
            page = _int(row.get('page_start')) or _int(row.get('page'))
            entry['excerpts'].append({'kind': kind, 'page': page, 'excerpt': _excerpt(row.get('text'))})
    snapshot = {pid: {'counts': dict(entry['counts']), 'excerpts': entry['excerpts']} for pid, entry in index.items()}
    with _corpus_guard:
        _corpus_cache[str(store.path)] = (key, snapshot)
    return snapshot


def resolve(store, value):
    """把插件传来的条目身份解析成库内 paper 记录；找不到返回 None。

    插件只能拿到 Zotero 的 (libraryID, itemKey)，因此它按
    ``kbapp/library/sources.py`` 的 identity 规则拼出 ``z:<library_id>:<key>``。
    多数文献的 id 就是这个标识，但历史遗留条目可能保留 ``doi:`` 之类的 id，
    这时再用 zotero_key + library_id 兜底一次，避免把已索引文献误报成「未索引」。
    """
    papers = store.papers()
    try:
        pid = store.resolve(value)
    except ValueError:
        return None
    if pid in papers:
        return papers[pid]
    match = re.fullmatch(r'z:(\d+):(.+)', str(value or '').strip())
    if match:
        library_id, zotero_key = int(match.group(1)), match.group(2)
        for candidate in papers.values():
            if candidate.get('zotero_key') == zotero_key and int(candidate.get('library_id', 1)) == library_id:
                return candidate
    return None


def evidence(store, paper_id):
    """已索引证据：按 kind 计数 + 少量摘要；索引未发布时 indexed=False。

    只统计 ``EVIDENCE_KINDS``。语料里还有 Zotero 笔记/批注与 Obsidian 卡片笔记
    (``kbapp/search/indexing.py`` 会把 ``notes + note_sources`` 写进 corpus,
    ``kbapp/library/sources.py:64`` 让这些行的 ``paper_id`` 指向该文献),
    它们不是证据:把它们并进计数会让「已索引」总数虚高,而插件侧按
    ``evidence-kind-<kind>`` 查 FTL 查不到对应文案,会把原始 l10n key
    直接显示给用户。前端只认这五种 kind,所以口径也在这里收窄。
    """
    index = corpus_index(store)
    if index is None:
        return {'indexed': False, 'index_version': None, 'counts': {}, 'total': 0, 'excerpts': []}
    entry = index.get(paper_id) or {'counts': {}, 'excerpts': []}
    counts = {
        kind: int(entry['counts'][kind])
        for kind in EVIDENCE_KINDS
        if entry['counts'].get(kind)
    }
    total = sum(counts.values())
    return {
        'indexed': total > 0,
        'index_version': store.state('active_version'),
        'counts': counts,
        'total': total,
        'excerpts': list(entry['excerpts']),
    }


def _direction(paper, peer):
    paper_year, peer_year = paper.get('year'), peer.get('year')
    if not isinstance(paper_year, int) or not isinstance(peer_year, int) or paper_year == peer_year:
        return 'unknown'
    return 'previous' if peer_year < paper_year else 'following'


def lineage(store, paper, papers):
    """研究关系：已确认列表 + 待核计数；引用事实单独计数，不混进方法关系。"""
    from kbapp.research.viewmodels import METHOD_KINDS, display_relation
    with store.db() as db:
        rows = [json.loads(row[0]) for row in db.execute('SELECT payload FROM relations WHERE src=? OR dst=?', (paper['id'], paper['id']))]
    verified = pending = citations = 0
    items = []
    for raw in rows:
        if raw.get('status') == 'rejected':
            continue
        edge = display_relation(raw)
        kind = edge.get('kind')
        if kind == 'cites':
            citations += 1
            continue
        if kind not in METHOD_KINDS:
            continue
        grade = edge.get('evidence_grade') or 'title'
        if grade == 'fulltext':
            verified += 1
        else:
            pending += 1
        peer_id = edge.get('dst') if edge.get('src') == paper['id'] else edge.get('src')
        peer = papers.get(peer_id) or {}
        items.append({
            'id': edge.get('id'),
            'kind': kind,
            'label': RELATION_LABELS.get(kind, str(kind or '')),
            'status': edge.get('status'),
            'grade': grade,
            'grade_label': edge.get('evidence_label') or '',
            'peer_id': peer_id,
            'peer_title': peer.get('title') or '',
            'peer_year': peer.get('year') if isinstance(peer.get('year'), int) else None,
            'direction': _direction(paper, peer),
            'explanation': str(edge.get('explanation') or '')[:EXPLANATION_CHARS],
        })
    # 已确认的排前面，其余按前作/后作、对端题目稳定排序，面板只取前几条。
    items.sort(key=lambda item: (item['grade'] != 'fulltext', item['direction'] != 'previous', item['peer_title'], str(item['id'])))
    return {'verified': verified, 'pending': pending, 'citations': citations, 'items': items[:RELATION_LIMIT]}


def reading(store, paper):
    """精读：understanding 覆盖度 + 页分类摘要 + 是否可提交解析。"""
    from kbapp.research.evidence import latest
    from kbapp.research.reading import understanding_fingerprint
    understanding = latest(store, 'PaperUnderstanding', paper['id'])
    stale = bool(understanding and understanding.get('input_fingerprint') != understanding_fingerprint(store, paper))
    coverage = (understanding or {}).get('coverage') or {}
    read_pages = coverage.get('read_pages') or []
    missing = coverage.get('missing_pages') or []
    gaps = [str(item) for item in ((understanding or {}).get('gaps') or [])][:GAP_LIMIT]
    if stale and 'PDF、模型或提示版本已变化，需重新理解' not in gaps:
        gaps.insert(0, 'PDF、模型或提示版本已变化，需重新理解')
        gaps = gaps[:GAP_LIMIT]
    with store.db() as db:
        rows = db.execute(
            'SELECT pg.classification,COUNT(*) FROM document_pages pg JOIN documents d ON d.id=pg.document_id '
            'WHERE d.paper_id=? GROUP BY pg.classification', (paper['id'],)).fetchall()
        documents = db.execute('SELECT COUNT(*) FROM documents WHERE paper_id=?', (paper['id'],)).fetchone()[0]
    classification = {str(row[0]): int(row[1]) for row in rows}
    if stale:
        state = 'stale'
    elif understanding and coverage.get('complete'):
        state = 'complete'
    elif understanding:
        state = 'partial'
    else:
        state = 'unparsed'
    return {
        'state': state,
        'total_pages': _int(coverage.get('total_pages')),
        'read_pages': len(read_pages) if isinstance(read_pages, list) else 0,
        'missing_pages': missing[:GAP_LIMIT] if isinstance(missing, list) else [],
        'gaps': gaps,
        'classification': classification,
        'documents': int(documents),
        'can_parse': paper.get('kind') == 'library',
    }


def panel_snapshot(store, value):
    """聚合三 section 的快照；文献不在库内时抛 ValueError（路由转 404）。"""
    paper = resolve(store, value)
    if not paper:
        raise ValueError('文献不存在')
    papers = store.papers()
    return {
        'paper_id': paper['id'],
        'title': paper.get('title') or '',
        'evidence': evidence(store, paper['id']),
        'lineage': lineage(store, paper, papers),
        'reading': reading(store, paper),
    }
