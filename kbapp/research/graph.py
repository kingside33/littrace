"""kbapp.research.graph：由职责边界组织的知识库能力。"""
from __future__ import annotations
from collections import Counter, defaultdict, OrderedDict
import json
import re
import threading
import logging
from kbapp.core.io import atomic, digest, jsonl, now, read_json
from kbapp.research.viewmodels import METHOD_KINDS, display_relation, scoped_nodes, relation_grade
from kbapp.library.catalog import enriched_papers
from kbapp.research.rules import NAMES
from kbapp.research import method_taxonomy as taxonomy
THEMES = [('采样与最优规划', '\\brrt\\b|rrt\\*|sampling|probabilistic|batch informed'), ('海流与能耗', 'current|ocean flow|energy|海流|能耗'), ('安全与动力学约束', 'safe|collision|dynamic|kinematic|risk|避碰|约束'), ('覆盖与巡检', 'coverage|inspection|viewpoint|覆盖|巡检'), ('变构与协同', 'reconfig|modular|snake|变构|重构|构型'), ('感知与定位', 'perception|sensing|sonar|slam|localization|sensor|感知|定位')]

def theme(p):
    return next((name for name, rx in THEMES if re.search(rx, p.get('title', ''), re.I)), '其他研究')

def import_legacy(store, libraries):
    nodes = jsonl(store.paths.archive / 'legacy/citation/nodes.jsonl')
    keys = {p['citekey']: p['id'] for p in libraries}
    wid_map = {p.get('openalex_id'): p['id'] for p in libraries if p.get('openalex_id')}
    existing = store.papers()
    external = []
    for n in nodes:
        if n.get('kind') == 'library':
            continue
        wid = n.get('openalex_id') or n['id']
        if wid in wid_map:
            continue
        pid = 'oa:' + wid
        p = dict(n, id=pid, kind='external', openalex_id=wid, abstract_scope='cached')
        if pid in existing:
            p = {**p, **existing[pid]}
        p['theme'] = theme(p)
        external.append(p)
        keys[n['id']] = pid
    keys.update(wid_map)
    store.put_papers(external)
    mapped = {'method': 'inherits', 'baseline': 'compares', 'lineage': 'similar', 'background': 'cites', 'context': 'cites', 'cites': 'cites'}
    with store.db() as db:
        for e in jsonl(store.paths.archive / 'legacy/citation/edges.jsonl'):
            src = keys.get(e['src'])
            dst = keys.get(e['dst'])
            if not src or not dst or src == dst:
                continue
            kinds = ['cites'] + ([mapped[e['type']]] if mapped.get(e['type'], 'cites') != 'cites' else [])
            for kind in kinds:
                rid = digest(f'{src}|{dst}|{kind}')[:24]
                payload = {'id': rid, 'src': src, 'dst': dst, 'kind': kind, 'status': 'confirmed' if kind == 'cites' else 'candidate', 'level': 'citation' if kind == 'cites' else 'inference', 'explanation': '外部引用记录或库内参考文献记录' if kind == 'cites' else '旧版章节/关键词规则提供的关系线索，待原文核对', 'evidence': [{'quote': e.get('evidence', ''), 'ref_index': e.get('ref_index'), 'origin': 'legacy'}], 'previous_problem': '', 'change': '', 'conditions': ''}
                db.execute('INSERT OR IGNORE INTO relations VALUES(?,?,?,?,?,?,?,?,?)', (rid, src, dst, kind, payload['status'], payload['level'], 0, json.dumps(payload, ensure_ascii=False), now()))
    with store.db() as db:
        for p in libraries:
            if p.get('openalex_id'):
                for direction in ('upstream', 'downstream'):
                    db.execute('INSERT OR IGNORE INTO frontier(wid,direction,depth) VALUES(?,?,0)', (p['openalex_id'], direction))

def graph_page(store, direction='', project=False, search='', paper='', offset=0, limit=180, candidates=False, method=''):
    papers = graph_papers(store, direction)
    pid = store.resolve(paper) if paper else None
    with store.db() as db:
        if pid:
            rows = db.execute("SELECT payload FROM relations WHERE (src=? OR dst=?) AND status!='rejected'", (pid, pid)).fetchall()
        else:
            rows = db.execute("SELECT payload FROM relations WHERE status!='rejected'").fetchall()
    edges = [json.loads(r[0]) for r in rows]
    if not candidates:
        edges = [r for r in edges if relation_grade(r) in {'citation', 'fulltext'}]
    pool = list(papers.values())
    if pid:
        neighborhood = {pid} | {r['src'] for r in edges} | {r['dst'] for r in edges}
        pool = [p for p in pool if p['id'] in neighborhood]
    if direction or project or search or method:
        pool = scoped_nodes(pool, edges, direction, project, search, method)
    semantic_nodes = {r['src'] for r in edges if r['kind'] != 'cites' and r['status'] == 'confirmed'} | {r['dst'] for r in edges if r['kind'] != 'cites' and r['status'] == 'confirmed'}

    def rank(p):
        tf = p.get('tagfields') or {}
        core = any((x in (tf.get('priority') or []) for x in ('A', 'B'))) or any((x in (tf.get('reading_tier') or []) for x in ('Core-A', 'Core-B')))
        return (p['id'] != pid, p['id'] not in semantic_nodes, not core, p.get('kind') != 'library', -(p.get('year') or 0), p.get('title', ''))
    pool.sort(key=rank)
    total = len(pool)
    page = pool[offset:offset + limit]
    selected = {p['id'] for p in page}
    edges = [display_relation(e) for e in edges if e['src'] in selected and e['dst'] in selected]
    return {'nodes': [dict(p, theme=p.get('theme') or theme(p)) for p in page], 'edges': edges, 'total': total, 'offset': offset, 'has_more': offset + limit < total, 'themes': dict(Counter((theme(p) for p in pool))), 'relation_names': NAMES}

_lock_guard = threading.Lock()


def _graph_lock(store):
    with _lock_guard:
        if not hasattr(store, '_graph_lock'):
            store._graph_lock = threading.RLock()
    return store._graph_lock


def _catalog_key(store):
    from kbapp.library.cards import title_rules_digest
    with store.db() as db:
        methods = [tuple(r) for r in db.execute('SELECT id,name,aliases,payload,updated FROM method_concepts ORDER BY id')]
        mappings = [tuple(r) for r in db.execute('SELECT paper_id,method_id FROM paper_methods ORDER BY paper_id,method_id')]
    return (taxonomy.VERSION, store.state('paper_revision', 0), digest(json.dumps([methods, mappings], ensure_ascii=False)), title_rules_digest())


def _summary_key(store):
    return (_catalog_key(store), store.state('relation_revision', 0), store.state('active_version'),
            digest(json.dumps(store.state('coverage', {}), sort_keys=True, ensure_ascii=False)))


def graph_papers(store, direction=''):
    """公共分类按文献/方法修订缓存，磁盘指纹支持重启复用与单篇增量。"""
    key = _catalog_key(store)
    with _graph_lock(store):
        cached = getattr(store, '_graph_catalog', None)
        if not cached or cached['key'] != key:
            papers = enriched_papers(store)
            with store.db() as db:
                concepts = [dict(r) for r in db.execute('SELECT id,name,aliases,payload FROM method_concepts')]
            path = store.paths.cache / 'graph-taxonomy.json'
            prior = read_json(path, {}) or {}
            if not isinstance(prior, dict):
                prior = {}
            records = {}
            taxonomy.annotate(papers, concepts, prior=prior.get('records', {}) if prior.get('version') == taxonomy.VERSION else {}, records=records)
            if _catalog_key(store) != key:
                raise ChainRevisionChanged('文献分类已更新，请刷新后重试')
            if records != prior.get('records') or prior.get('version') != taxonomy.VERSION:
                try:
                    atomic(path, {'version': taxonomy.VERSION, 'records': records})
                except OSError:
                    logging.getLogger(__name__).warning('谱系分类缓存暂未写入，当前结果保留在内存')
            cached = {'key': key, 'papers': papers, 'scopes': OrderedDict()}
            store._graph_catalog = cached
        if not direction:
            return cached['papers']
        scopes = cached['scopes']
        if direction not in scopes:
            scopes[direction] = taxonomy.scope(cached['papers'], direction)
            while len(scopes) > 4:
                scopes.popitem(last=False)
        scopes.move_to_end(direction)
        return scopes[direction]


def graph_methods(store, direction='', project=False, search=''):
    papers = graph_papers(store, direction)
    return {'items': taxonomy.method_options(papers, direction, project, search)}


def chains(store):
    """专题页的具体方法证据；全库关系使用分页谱系接口，避免一次渲染全部引用。"""
    index = _chain_index(store)
    cached = getattr(store, '_chains_cache', None)
    if cached and cached[0] == index['revision']:
        return cached[1]
    with store.db() as db:
        payloads = {r['id']: display_relation(json.loads(r['payload'])) for r in
                    db.execute("SELECT id,payload FROM relations WHERE status!='rejected' AND kind IN ('inherits','improves','combines','transfers')")}
    result = []
    for branch in index['groups'].values():
        edges = [payloads[e['id']] for e in branch['edges'] if e['id'] in payloads]
        if not edges:
            continue
        ids = {e[k] for e in edges for k in ('src', 'dst')}
        result.append(dict(id=branch['id'], name=branch['name'], family_id=branch['family_id'],
                           nodes=[index['papers'][pid] for pid in sorted(ids)], edges=edges))
    store._chains_cache = (index['revision'], result)
    return result


CARD_FIELDS = ('id', 'title', 'year', 'kind', 'citekey', 'authors', 'directions', 'project', 'methods',
               'method_families', 'primary_method_family', 'selection_reason', 'theme', 'tagfields',
               'cited_by', 'citation_count', 'citation_source', 'citation_updated', 'zotero_key', 'attachment_key', 'doi')
RELATION_CARD_FIELDS = ('id', 'src', 'dst', 'kind', 'status', 'level', 'explanation', 'change', 'conditions',
                        'evidence_grade', 'evidence_label', 'evidence_stale', 'manual', 'verification_origin')


def paper_card(paper):
    return {key: paper[key] for key in CARD_FIELDS if key in paper}


def relation_card(edge):
    pages = list(dict.fromkeys(e.get('page') or e.get('page_start') for e in edge.get('evidence') or []
                               if e.get('page') or e.get('page_start')))
    return dict({key: edge[key] for key in RELATION_CARD_FIELDS if key in edge}, evidence_pages=pages)


def compact_chains(items):
    return [dict(branch, nodes=[paper_card(p) for p in branch['nodes']],
                 edges=[relation_card(e) for e in branch['edges']]) for branch in items]


def branch_summary(branch):
    edges = branch.get('edges') or []
    fulltext = sum(e.get('evidence_grade') == 'fulltext' and e['kind'] in METHOD_KINDS for e in edges)
    citations = sum(e['kind'] == 'cites' for e in edges)
    method_edges = sum(e['kind'] in METHOD_KINDS for e in edges)
    pending = sum(e.get('evidence_grade') not in {'fulltext', 'citation'} for e in edges)
    updated = max((str(e.get('updated') or '') for e in edges), default='')
    return {'id': branch['id'], 'name': branch['name'], 'family_id': branch.get('family_id'),
            'nodes': len(branch.get('nodes') or []), 'edges': len(edges), 'citations': citations,
            'method_edges': method_edges, 'other_edges': len(edges) - citations - method_edges,
            'unlinked': len(branch.get('unlinked_nodes') or []),
            'fulltext': fulltext, 'confirmed': fulltext, 'pending': pending, 'updated': updated,
            'value': fulltext * 5 + method_edges * 2 + len(edges)}


def _branch_id(name):
    return 'branch-' + digest(name)[:24]


class ChainRevisionChanged(ValueError):
    pass


def _chain_index(store, direction=''):
    with _graph_lock(store):
        return _build_chain_index(store, direction)


def _build_chain_index(store, direction=''):
    """全库拓扑缓存：每条关系只归属一个大类，孤立文献也纳入覆盖。"""
    key = (_summary_key(store), direction)
    indexes = getattr(store, '_chain_indexes', None)
    if indexes is None:
        indexes = store._chain_indexes = OrderedDict()
    if key in indexes:
        indexes.move_to_end(key)
        return indexes[key]
    papers = graph_papers(store, direction)
    topology_key = key[0][1]  # 只有关系修订改变拓扑/证据分级，文献元数据变化只更新端点视图。
    topology = getattr(store, '_graph_topology', None)
    if topology and topology[0] == topology_key:
        rows = topology[1]
    else:
        with store.db() as db:
            rows = db.execute("""SELECT id,src,dst,kind,status,level,manual,updated,
            json_extract(payload,'$.evidence_stale') AS evidence_stale,
            json_extract(payload,'$.pipeline_legacy') AS pipeline_legacy,
            json_extract(payload,'$.reviewed_by') AS reviewed_by,
            json_extract(payload,'$.assessment_verified') AS assessment_verified,
            json_array_length(json_extract(payload,'$.evidence')) AS evidence
            FROM relations WHERE status!='rejected' ORDER BY
            CASE WHEN kind IN ('inherits','improves','combines','transfers') THEN 0
                 WHEN kind='cites' THEN 2 ELSE 1 END, id""").fetchall()
        rows = [dict(row, evidence_grade=relation_grade(dict(row))) for row in rows]
        store._graph_topology = (topology_key, rows)
    groups = {}
    def group(family):
        name = taxonomy.FAMILIES[family]
        bid = _branch_id(name)
        return groups.setdefault(bid, {'id': bid, 'name': name, 'family_id': family, 'edges': [], 'unlinked_ids': []})
    connected = set()
    for row in rows:
        edge = row
        if edge['src'] not in papers or edge['dst'] not in papers:
            continue
        family = papers[edge['src']]['primary_method_family']
        if family == taxonomy.OTHER:
            family = papers[edge['dst']]['primary_method_family']
        group(family)['edges'].append(edge)
        connected.update((edge['src'], edge['dst']))
    for pid in sorted(papers.keys() - connected):
        group(papers[pid]['primary_method_family'])['unlinked_ids'].append(pid)
    result = {'papers': papers, 'groups': groups, 'revision': digest(json.dumps(key, ensure_ascii=False))[:24]}
    if (_summary_key(store), direction) != key:
        raise ChainRevisionChanged('研究链已更新，请刷新后重试')
    indexes[key] = result
    while len(indexes) > 4:
        indexes.popitem(last=False)
    return result


def _scoped_branch(index, branch, direction, project, search, method, candidates):
    if method.startswith('family:') and method != branch['family_id']:
        return dict(branch, nodes=[], edges=[], unlinked_nodes=[])
    edges = [e for e in branch['edges'] if candidates or e['evidence_grade'] in {'fulltext', 'citation'}]
    ids = {e[k] for e in edges for k in ('src', 'dst')} | set(branch['unlinked_ids'])
    nodes = [index['papers'][pid] for pid in sorted(ids)]
    if direction or project or search or method:
        nodes = scoped_nodes(nodes, edges, direction, project, search, method)
        keep = {p['id'] for p in nodes}
        edges = [e for e in edges if e['src'] in keep and e['dst'] in keep]
    unlinked = set(branch['unlinked_ids'])
    return dict(branch, nodes=nodes, edges=edges, unlinked_nodes=[p for p in nodes if p['id'] in unlinked])


def _check_chain_revision(index, revision):
    if revision and revision != index['revision']:
        raise ChainRevisionChanged('研究链已更新，请刷新后重试')


def chains_page(store, offset=0, limit=20, sort='value', direction='', project=False, search='', method='', candidates=True, revision=''):
    if sort not in {'value', 'pending', 'recent', 'name'}:
        raise ValueError('排序方式无效')
    index = _chain_index(store, direction)
    _check_chain_revision(index, revision)
    summaries, visible_ids, linked_ids = [], set(), set()
    for branch in index['groups'].values():
        scoped = _scoped_branch(index, branch, direction, project, search, method, candidates)
        if scoped['nodes']:
            summaries.append(branch_summary(scoped))
            visible_ids.update(p['id'] for p in scoped['nodes'])
            linked_ids.update(e[k] for e in scoped['edges'] for k in ('src', 'dst'))
    summaries.sort(key=lambda b: (b['name'], b['id']))
    if sort != 'name':
        field = {'value': 'value', 'pending': 'pending', 'recent': 'updated'}[sort]
        summaries.sort(key=lambda b: b[field], reverse=True)
    coverage = {'library_papers': len(index['papers']), 'papers': len(visible_ids), 'connected': len(linked_ids),
                'unlinked': len(visible_ids - linked_ids),
                **{field: sum(b[field] for b in summaries) for field in ('edges', 'citations', 'method_edges', 'other_edges', 'confirmed', 'pending')}}
    start, size = max(0, int(offset)), min(max(int(limit), 1), 100)
    return {'items': summaries[start:start + size], 'total': len(summaries), 'offset': start,
            'limit': size, 'has_more': start + size < len(summaries), 'sort': sort,
            'coverage': coverage, 'revision': index['revision']}


def chain_detail(store, branch_id, offset=0, limit=40, direction='', project=False, search='', method='', candidates=True, revision='', paper_offset=0, paper_limit=40):
    index = _chain_index(store, direction)
    _check_chain_revision(index, revision)
    branch = index['groups'].get(branch_id)
    if branch is None:
        return None
    scoped = _scoped_branch(index, branch, direction, project, search, method, candidates)
    start, size = max(0, int(offset)), min(max(int(limit), 1), 100)
    page = scoped['edges'][start:start + size]
    ids = [e['id'] for e in page]
    edges = []
    if ids:
        with store.db() as db:
            payloads = {r['id']: json.loads(r['payload']) for r in db.execute(
                'SELECT id,payload FROM relations WHERE id IN (' + ','.join('?' for _ in ids) + ')', ids)}
        edges = [display_relation(dict(payloads[e['id']], updated=e['updated'])) for e in page if e['id'] in payloads]
    if _chain_index(store, direction)['revision'] != index['revision']:
        raise ChainRevisionChanged('研究链已更新，请刷新后重试')
    node_ids = {e[k] for e in edges for k in ('src', 'dst')}
    nodes = sorted((p for p in scoped['nodes'] if p['id'] in node_ids), key=lambda p: (p.get('year') or 9999, p['id']))
    ps, pn = max(0, int(paper_offset)), min(max(int(paper_limit), 1), 100)
    unlinked = scoped['unlinked_nodes']
    return {'id': branch_id, 'name': branch['name'], 'nodes': [paper_card(p) for p in nodes], 'edges': edges,
            'total': len(scoped['edges']), 'offset': start, 'limit': size,
            'has_more': start + size < len(scoped['edges']), 'revision': index['revision'],
            'unlinked_nodes': [paper_card(p) for p in unlinked[ps:ps + pn]], 'unlinked_total': len(unlinked),
            'paper_offset': ps, 'paper_has_more': ps + pn < len(unlinked)}


def sync_references(store, index, papers):
    """新增附件的参考文献以精确 DOI 或题名关联，引用编号可用于句内证据核对。"""
    from kbapp.library.sources import norm, paper_url
    by_key = {p['citekey']: p for p in papers}
    registered = store.papers()
    registered.update({p['id']: p for p in papers})
    canonical = sorted(registered.values(), key=lambda p: (p.get('kind') == 'library', bool(p.get('zotero_key'))))
    by_doi = {p['doi'].lower().removeprefix('https://doi.org/'): p for p in canonical if p.get('doi')}
    by_title = {norm(p.get('title')): p for p in canonical if len(norm(p.get('title'))) >= 20}
    count = 0
    for ref in jsonl(index / 'references.jsonl'):
        src = by_key.get(ref.get('src_citekey'))
        dst = by_doi.get((ref.get('doi') or '').lower().removeprefix('https://doi.org/')) or by_title.get(norm(ref.get('title')))
        if not src or not dst or src['id'] == dst['id']:
            continue
        store.relation({'src': src['id'], 'dst': dst['id'], 'kind': 'cites', 'status': 'confirmed', 'level': 'citation', 'explanation': 'PDF 参考文献中 DOI 或题名的精确匹配', 'evidence': [{'quote': ref.get('raw', ''), 'ref_index': ref.get('ref_index'), 'origin': 'pdf_reference', 'url': paper_url(src)}]})
        count += 1
    return count

def overview(store):
    from kbapp.library.cards import DIR_NAME
    key = _summary_key(store)
    cached = getattr(store, '_overview_cache', None)
    if cached and cached[0] == key:
        return cached[1]
    papers = graph_papers(store)
    directions = {d: {'direction': d, 'name': name[3:], 'papers': 0, 'confirmed': 0, 'candidates': 0, 'citations': 0} for d, name in DIR_NAME.items()}
    for p in papers.values():
        if p.get('kind') == 'library':
            for d in p.get('directions', []):
                if d in directions:
                    directions[d]['papers'] += 1
    with store.db() as db:
        rows = [json.loads(r[0]) for r in db.execute("SELECT payload FROM relations WHERE status!='rejected'").fetchall()]
    total_confirmed = total_candidates = 0
    for r in rows:
        if r['kind'] not in METHOD_KINDS | {'cites'}:
            continue
        if r['kind'] == 'cites' and r['status'] != 'confirmed':
            continue
        verified = relation_grade(r) == 'fulltext'
        field = 'citations' if r['kind'] == 'cites' else 'confirmed' if verified else 'candidates'
        if r['kind'] in METHOD_KINDS:
            total_confirmed += verified
            total_candidates += not verified
        ds = set(papers.get(r['src'], {}).get('directions') or []) | set(papers.get(r['dst'], {}).get('directions') or [])
        for d in ds:
            if d in directions:
                directions[d][field] += 1
    result = {'directions': list(directions.values()), 'confirmed': total_confirmed, 'candidates': total_candidates, 'directions_with_methods': sum((d['confirmed'] > 0 for d in directions.values())), 'coverage': store.state('coverage', {})}
    store._overview_cache = (key, result)
    return result

def timeline(store, direction='', project=False, search=''):
    key = (_summary_key(store), direction, bool(project), search)
    cached = getattr(store, '_timeline_cache', None)
    if cached and cached[0] == key:
        return cached[1]
    result = _build_timeline(store, direction, project, search)
    store._timeline_cache = (key, result)
    return result


def _build_timeline(store, direction='', project=False, search=''):
    """沿用旧 HTML 的等距分行布局数据；引用路径明确保留为待解释线索。"""
    papers = graph_papers(store)
    stats = overview(store)
    with store.db() as db:
        aliases = {r['alias']: r['paper_id'] for r in db.execute('SELECT * FROM aliases')}
        citations = {(r['src'], r['dst']): dict(r, level='citation', explanation='引用路径，尚未核实方法关系') for r in db.execute("SELECT id,src,dst,kind,status FROM relations WHERE kind='cites' AND status='confirmed'")}
        semantic = [display_relation(json.loads(r[0])) for r in db.execute("SELECT payload FROM relations WHERE status!='rejected' AND kind IN ('inherits','improves','combines','transfers')")]
    methods = {(r['src'], r['dst']): r for r in semantic}
    out = defaultdict(list)
    incoming = set()
    for r in semantic:
        out[r['dst']].append(r['src'])
        incoming.add(r['src'])
    verified = []

    def walk(path):
        choices = [x for x in out.get(path[-1], []) if x not in path]
        if not choices or len(path) >= 18:
            if len(path) > 1:
                verified.append(path)
            return
        for x in choices:
            if len(verified) < 100:
                walk(path + [x])
    for root in out:
        if root not in incoming:
            walk([root])
    if not verified:
        verified = [[r['dst'], r['src']] for r in semantic]

    def matches(ids, d):
        nodes = [papers[x] for x in ids if x in papers]
        return bool(nodes) and any((d in (p.get('directions') or []) for p in nodes)) and (not project or any((p.get('project') for p in nodes))) and (not search or any((search.lower() in (p.get('title', '') + ' ' + p.get('citekey', '') + ' ' + str(p.get('authors') or '')).lower() for p in nodes)))

    def row(ids, kind):
        edges = []
        for a, b in zip(ids, ids[1:]):
            r = methods.get((b, a)) or citations.get((b, a)) or methods.get((a, b)) or citations.get((a, b))
            if r:
                edges.append(r)
        fields = ('id', 'title', 'year', 'kind', 'citekey', 'directions', 'project', 'authors')
        return {'kind': kind, 'nodes': [{k: papers[x].get(k) for k in fields} for x in ids], 'edges': edges}
    legacy = read_json(store.paths.archive / 'legacy/citation/backbone.json', {}).get('directions', {})
    groups = []
    for stat in stats['directions']:
        d = stat['direction']
        if direction and d != direction:
            continue
        paths = []
        seen = set()
        for ids in verified:
            if matches(ids, d):
                paths.append(row(ids, 'method'))
                seen.add(tuple(ids))
        for c in legacy.get(d, {}).get('chains', []):
            ids = [aliases.get(x, x) for x in c.get('nodes', [])]
            segment = []
            segments = []
            for pid in ids:
                if pid not in papers:
                    continue
                if segment and (not any((pair in citations or pair in methods for pair in ((pid, segment[-1]), (segment[-1], pid))))):
                    if len(segment) > 1:
                        segments.append(segment)
                    segment = []
                segment.append(pid)
            if len(segment) > 1:
                segments.append(segment)
            for segment in segments:
                if tuple(segment) not in seen and matches(segment, d):
                    paths.append(row(segment, 'citation'))
                    seen.add(tuple(segment))
        groups.append(dict(stat, paths=paths[:30], total_paths=len(paths)))
    return {'groups': groups, 'overview': stats, 'method_families': chains(store), 'gaps': ['引用路径只表示引用事实；题目线索不构成已核方法继承']}
