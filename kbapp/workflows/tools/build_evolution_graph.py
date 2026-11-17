"""kbapp.workflows.tools.build_evolution_graph：由职责边界组织的知识库能力。"""
import argparse
import json
import math
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict
import kbapp.core.config as C
from kbapp.core.io import read_json, read_jsonl, write_json, write_jsonl
TYPE_WEIGHT = {'method': 3.0, 'baseline': 2.0, 'lineage': 2.0, 'background': 0.4, 'context': 0.3, 'cites': 1.0}
REVIEW_RE = re.compile('\\b(review|survey|overview|state[-\\s]?of[-\\s]?the[-\\s]?art|tutorial|taxonomy|research\\s+progress|advancements?)\\b|综述|述评|进展|研究现状', re.I)

def is_review(title):
    return bool(REVIEW_RE.search(title or ''))

def norm_title(s):
    s = unicodedata.normalize('NFKC', s or '').lower()
    return re.sub('[^0-9a-z\\u4e00-\\u9fff]+', '', s)

def norm_doi(s):
    s = (s or '').strip().lower()
    s = re.sub('^(?:https?://(?:dx\\.)?doi\\.org/|doi:\\s*)', '', s)
    return s.rstrip('.,;)]}')

def short_id(x):
    return str(x or '').rstrip('/').rsplit('/', 1)[-1]

class Store:

    def __init__(self, base):
        self.base = base
        self.oa = C.openalex_dir(base)
        self._cache = {}

    def work(self, wid):
        if wid in self._cache:
            return self._cache[wid]
        rec = read_json(os.path.join(self.oa, 'works', wid + '.json'))
        self._cache[wid] = rec
        return rec

def load_directions(base):
    rows = read_json(os.path.join(C.index_dir(base), 'directions.json'), []) or []
    return {r['id']: r['name'] for r in rows}

def load_card_folders(base):
    rows = read_json(os.path.join(C.index_dir(base), 'directions.json'), []) or []
    return {r['id']: r.get('card_folder', '') for r in rows}

def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default=C.BASE)
    ap.add_argument('--no-citers', action='store_true')
    ap.add_argument('--min-display-cites', type=int, default=C.EVOLUTION_MIN_LIB_CITES)
    args = ap.parse_args(argv)
    base = os.path.abspath(args.base)
    cdir = C.citation_dir(base)
    oa = C.openalex_dir(base)
    os.makedirs(cdir, exist_ok=True)
    store = Store(base)
    dir_names = load_directions(base)
    card_folders = load_card_folders(base)
    papers = read_jsonl(os.path.join(C.index_dir(base), 'papers_all.jsonl'))
    resolved = {r['citekey']: r['openalex_id'] for r in read_jsonl(os.path.join(oa, 'resolve.jsonl')) if r.get('openalex_id')}
    wid2ck = {w: ck for ck, w in resolved.items()}
    ancestors = (read_json(os.path.join(oa, 'ancestors.json'), {}) or {}).get('rows', [])
    citers = []
    if not args.no_citers:
        citers = (read_json(os.path.join(oa, 'citers.json'), {}) or {}).get('rows', [])
    contexts = {}
    for r in read_jsonl(os.path.join(cdir, 'contexts.jsonl')):
        contexts.setdefault(r['src'], {})[int(r['ref_index'])] = r
    refs_by_src = defaultdict(dict)
    for r in read_jsonl(C.references_jsonl(base)):
        if r.get('src_citekey') and r.get('ref_index') is not None:
            refs_by_src[r['src_citekey']][int(r['ref_index'])] = r
    nodes = {}
    for p in papers:
        ck = p['citekey']
        wid = resolved.get(ck, '')
        w = store.work(wid) if wid else None
        dirs = p.get('directions') or []
        nodes[ck] = {'id': ck, 'kind': 'library', 'citekey': ck, 'openalex_id': wid, 'title': p.get('title') or '', 'year': p.get('year'), 'venue': p.get('venue') or '', 'author': (p.get('authors') or [''])[0] if p.get('authors') else '', 'direction': dirs[0] if dirs else '', 'primary_direction': p.get('primary_direction') or (dirs[0] if dirs else ''), 'directions': dirs, 'cited_by': (w or {}).get('cited_by', 0), 'lang': (w or {}).get('lang', ''), 'resolved': bool(wid), 'card': card_folders.get(dirs[0], '') + '/' + ck + '.md' if dirs and card_folders.get(dirs[0]) else ''}
    for r in ancestors:
        wid = r['openalex_id']
        if wid in wid2ck:
            continue
        nodes[wid] = {'id': wid, 'kind': 'ancestor', 'citekey': '', 'openalex_id': wid, 'title': r.get('title') or '', 'year': r.get('year'), 'venue': r.get('venue') or '', 'author': (r.get('authors') or [''])[0] if r.get('authors') else '', 'direction': '', 'directions': [], 'cited_by': r.get('cited_by', 0), 'lib_cites': r.get('lib_cites', 0), 'abstract': r.get('abstract', ''), 'resolved': True}
    for r in citers:
        wid = r['openalex_id']
        if wid in wid2ck or wid in nodes:
            continue
        nodes[wid] = {'id': wid, 'kind': 'citer', 'citekey': '', 'openalex_id': wid, 'title': r.get('title') or '', 'year': r.get('year'), 'venue': r.get('venue') or '', 'author': (r.get('authors') or [''])[0] if r.get('authors') else '', 'direction': '', 'directions': [], 'cited_by': r.get('cited_by', 0), 'lib_cites': r.get('lib_cites', 0), 'resolved': True}
    lib_in = Counter()
    for a in ancestors:
        for ck in a.get('cited_by_lib', []):
            lib_in[wid2ck.get(a['openalex_id'], a['openalex_id'])] += 1
    edges = {}
    id2nid = {}
    for wid, ck in wid2ck.items():
        id2nid[wid] = ck

    def add_edge(src, dst, etype='cites', weight=1.0, evidence='', ref_index=None):
        if src == dst or src not in nodes or dst not in nodes:
            return
        key = (src, dst)
        old = edges.get(key)
        w = TYPE_WEIGHT.get(etype, 1.0)
        if old is None or w > old['weight']:
            edges[key] = {'src': src, 'dst': dst, 'type': etype, 'weight': w, 'evidence': evidence, 'ref_index': ref_index}

    def nid_of_wid(wid):
        return id2nid.get(wid, wid)
    for ck, wid in resolved.items():
        w = store.work(wid)
        if not w:
            continue
        for rid in w.get('refs', []):
            dst = nid_of_wid(rid)
            if dst in nodes:
                add_edge(ck, dst)
    for a in ancestors:
        wid = a['openalex_id']
        w = store.work(wid)
        if not w:
            continue
        src = nid_of_wid(wid)
        for rid in w.get('refs', []):
            dst = nid_of_wid(rid)
            if dst in nodes:
                add_edge(src, dst)
    for r in citers:
        wid = r['openalex_id']
        w = store.work(wid)
        if not w:
            continue
        src = nid_of_wid(wid)
        for rid in w.get('refs', []):
            dst = nid_of_wid(rid)
            if dst in nodes:
                add_edge(src, dst)
    for src_ck, per_idx in refs_by_src.items():
        if src_ck not in resolved:
            continue
        ctx = contexts.get(src_ck, {})
        if not ctx:
            continue
        by_doi, by_title = ({}, {})
        for idx, r in per_idx.items():
            d = norm_doi(r.get('doi'))
            if d:
                by_doi.setdefault(d, idx)
            t = norm_title(r.get('title'))
            if len(t) >= 12:
                by_title.setdefault(t, idx)
        w = store.work(resolved[src_ck])
        if not w:
            continue
        for rid in w.get('refs', []):
            dst = nid_of_wid(rid)
            if dst not in nodes or nodes[dst]['kind'] != 'library':
                continue
            dw = store.work(rid) or {}
            idx = None
            d = norm_doi(dw.get('doi'))
            if d and d in by_doi:
                idx = by_doi[d]
            else:
                t = norm_title(dw.get('title'))
                if t and t in by_title:
                    idx = by_title[t]
            if idx is not None and idx in ctx:
                c = ctx[idx]
                add_edge(src_ck, dst, c['type'], c['weight'], c.get('snippet', ''), idx)
    old_edges = read_jsonl(os.path.join(C.index_dir(base), 'citation_edges.jsonl'))
    for e in old_edges:
        s, d = (e.get('src'), e.get('dst'))
        if s not in nodes or d not in nodes:
            continue
        ctx = contexts.get(s, {})
        idx = e.get('ref_index')
        if idx is not None and int(idx) in ctx:
            c = ctx[int(idx)]
            add_edge(s, d, c['type'], c['weight'], c.get('snippet', ''), int(idx))
        else:
            add_edge(s, d, 'cites', 1.0)
    out_deg = Counter()
    in_deg = Counter()
    for s, d in edges:
        out_deg[s] += 1
        in_deg[d] += 1
    ids = list(nodes)
    idx_of = {k: i for i, k in enumerate(ids)}
    n = len(ids)
    pr = [1.0 / n] * n
    for _ in range(40):
        new = [0.15 / n] * n
        dangling = sum((pr[i] for i in range(n) if out_deg[ids[i]] == 0))
        for s, d in edges:
            i, j = (idx_of[s], idx_of[d])
            if out_deg[s]:
                new[j] += 0.85 * pr[i] / out_deg[s]
        if dangling:
            share = 0.85 * dangling / n
            for i in range(n):
                new[i] += share
        pr = new
    for k, v in nodes.items():
        v['pagerank'] = round(pr[idx_of[k]], 8)
        v['in_deg'] = in_deg.get(k, 0)
        v['out_deg'] = out_deg.get(k, 0)
        if v['kind'] == 'library':
            v['lib_cites'] = lib_in.get(k, 0)
    cites_map = defaultdict(list)
    for (s, d), e in edges.items():
        ys, yd = (nodes[s]['year'], nodes[d]['year'])
        if ys and yd and (ys <= yd):
            continue
        w = e['weight']
        if is_review(nodes[d]['title']):
            w *= 0.35
        if not (nodes[d]['title'] or '').strip():
            w *= 0.05
        cites_map[s].append((d, w))
    order = sorted(nodes, key=lambda k: (nodes[k]['year'] or 3000, k))
    best = {}
    prev = {}
    for u in order:
        base_w, base_p = (0.0, None)
        for v, w in cites_map.get(u, []):
            if v not in best:
                continue
            cand = best[v] + w
            if cand > base_w:
                base_w, base_p = (cand, v)
        best[u] = base_w
        if base_p is not None:
            prev[u] = base_p

    def chain_of(node, max_len=14):
        out = []
        cur = node
        seen = set()
        while cur and cur not in seen and (len(out) < max_len):
            seen.add(cur)
            if (nodes[cur]['title'] or '').strip():
                out.append(cur)
            cur = prev.get(cur)
        out.reverse()
        return out
    lineage = {}
    for k, v in nodes.items():
        if v['kind'] != 'library':
            continue
        ch = chain_of(k)
        if len(ch) >= 2:
            lineage[k] = [{'id': x, 'title': nodes[x]['title'], 'year': nodes[x]['year'], 'kind': nodes[x]['kind']} for x in ch]
    by_dir = defaultdict(list)
    for k, v in nodes.items():
        if v['kind'] != 'library':
            continue
        d = v.get('primary_direction') or (v['directions'][0] if v['directions'] else '')
        if d:
            by_dir[d].append(k)
    dir_support = {}
    for d, libs in by_dir.items():
        sup = Counter()
        for ck in libs:
            w = store.work(resolved.get(ck, ''))
            if not w:
                continue
            for rid in set(w.get('refs', [])):
                sup[nid_of_wid(rid)] += 1
        dir_support[d] = sup
    backbone = {}
    on_chain = set()
    for d, libs in by_dir.items():
        sup = dir_support.get(d) or Counter()
        cands = []
        for ck in libs:
            if ck not in best:
                continue
            ch = chain_of(ck)
            if len(ch) < 3:
                continue
            score = sum((math.log(1 + sup.get(x, 0)) for x in ch)) + 0.15 * len(ch)
            cands.append((score, ck, ch))
        cands.sort(reverse=True, key=lambda x: x[0])
        chosen, used = ([], set())
        for score, ck, ch in cands:
            if len(set(ch) & used) > len(ch) * 0.6:
                continue
            chosen.append({'score': round(score, 2), 'end': ck, 'nodes': ch, 'years': [nodes[x]['year'] for x in ch], 'titles': [nodes[x]['title'][:90] for x in ch], 'kinds': [nodes[x]['kind'] for x in ch], 'support': [sup.get(x, 0) for x in ch]})
            used |= set(ch)
            if len(chosen) >= 5:
                break
        if chosen:
            backbone[d] = {'direction': d, 'name': dir_names.get(d, d), 'chains': chosen}
            for c in chosen:
                on_chain |= set(c['nodes'])
    cited_by_map = defaultdict(list)
    for s, d in edges:
        cited_by_map[d].append(s)
    forks = []
    for k in on_chain:
        succ = [s for s in cited_by_map.get(k, []) if s in on_chain]
        if len(succ) >= 2:
            forks.append({'id': k, 'title': nodes[k]['title'][:100], 'year': nodes[k]['year'], 'successors': [{'id': s, 'title': nodes[s]['title'][:80], 'year': nodes[s]['year']} for s in succ[:8]]})
    forks.sort(key=lambda f: -(f['year'] or 0))
    gaps = {}
    for d, libs in by_dir.items():
        if d not in backbone:
            continue
        support = defaultdict(set)
        for ck in libs:
            w = store.work(resolved.get(ck, ''))
            if not w:
                continue
            for rid in w.get('refs', []):
                dst = nid_of_wid(rid)
                if dst in nodes:
                    support[dst].add(ck)
        for ck in libs:
            w = store.work(resolved.get(ck, ''))
            if not w:
                continue
            cited = {nid_of_wid(r) for r in w.get('refs', [])}
            miss = []
            for cand, supporters in support.items():
                if cand == ck or cand in cited:
                    continue
                others = supporters - {ck}
                if len(others) < 3:
                    continue
                if nodes[cand]['kind'] == 'library' and nodes[cand]['direction'] not in ('', d):
                    continue
                miss.append({'id': cand, 'title': nodes[cand]['title'][:110], 'year': nodes[cand]['year'], 'support': len(others), 'kind': nodes[cand]['kind']})
            if miss:
                miss.sort(key=lambda x: -x['support'])
                gaps[ck] = miss[:8]
    for k, v in nodes.items():
        if v['kind'] == 'library':
            v['lane'] = v.get('primary_direction') or '90'
        else:
            c = Counter()
            for s in cited_by_map.get(k, []):
                if nodes[s]['kind'] == 'library':
                    c[nodes[s].get('primary_direction') or '90'] += 1
            v['lane'] = c.most_common(1)[0][0] if c else 'ext'
    for k, v in nodes.items():
        if v['kind'] == 'library':
            v['show'] = True
        else:
            v['show'] = v.get('lib_cites', 0) >= args.min_display_cites or k in on_chain
        v['on_chain'] = k in on_chain
    shown = {k for k, v in nodes.items() if v['show']}
    kept_edges = [(s, d) for s, d in edges if s in shown and d in shown]
    node_rows = sorted(nodes.values(), key=lambda v: (v['kind'], v['id']))
    edge_rows = [edges[s, d] for s, d in sorted(kept_edges)]
    write_jsonl(os.path.join(cdir, 'nodes.jsonl'), node_rows)
    write_jsonl(os.path.join(cdir, 'edges.jsonl'), edge_rows)
    stats = {'nodes_total': len(nodes), 'nodes_shown': len(shown), 'by_kind': dict(Counter((v['kind'] for v in nodes.values()))), 'edges_total': len(edges), 'edges_shown': len(edge_rows), 'by_edge_type': dict(Counter((e['type'] for e in edge_rows))), 'library_resolved': len(resolved), 'library_unresolved': len(papers) - len(resolved), 'library_with_upstream': sum((1 for k, v in nodes.items() if v['kind'] == 'library' and out_deg.get(k, 0) > 0)), 'library_isolated': sum((1 for k, v in nodes.items() if v['kind'] == 'library' and out_deg.get(k, 0) == 0 and (in_deg.get(k, 0) == 0))), 'directions_with_backbone': len(backbone), 'forks': len(forks), 'papers_with_gaps': len(gaps)}
    write_json(os.path.join(cdir, 'graph.json'), {'nodes': [{k: v for k, v in n.items() if k not in ('abstract',)} for n in node_rows if n['show']], 'edges': [{'s': e['src'], 'd': e['dst'], 't': e['type'], 'w': e['weight']} for e in edge_rows], 'typed': [{'s': e['src'], 'd': e['dst'], 't': e['type'], 'ev': (e.get('evidence') or '')[:220]} for e in edge_rows if e['type'] != 'cites'], 'stats': stats, 'directions': dir_names})
    write_json(os.path.join(cdir, 'backbone.json'), {'directions': backbone, 'forks': forks})
    write_json(os.path.join(cdir, 'lineage.json'), lineage)
    write_json(os.path.join(cdir, 'gaps.json'), gaps)
    write_json(os.path.join(cdir, 'evolution_stats.json'), stats)
    print(json.dumps(stats, ensure_ascii=False, indent=2), flush=True)
