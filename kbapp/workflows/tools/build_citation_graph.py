"""kbapp.workflows.tools.build_citation_graph：由职责边界组织的知识库能力。"""
import argparse
import difflib
import json
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict
import kbapp.core.config as C
from kbapp.core.io import read_jsonl, write_json, write_jsonl
DOI_PREFIX_RE = re.compile('^(?:https?://(?:dx\\.)?doi\\.org/|doi:\\s*)', re.I)
INITIAL_RE = re.compile('^[A-Z]\\.?$')
TITLE_TOKEN_RE = re.compile('[a-z0-9]{3,}')
CJK_RE = re.compile('[\\u4e00-\\u9fff]')

def norm_doi(s):
    s = (s or '').strip().lower()
    s = DOI_PREFIX_RE.sub('', s)
    return s.rstrip('.,;)]}')

def norm_title(s):
    s = unicodedata.normalize('NFKC', s or '').lower()
    return re.sub('[^0-9a-z\\u4e00-\\u9fff]+', '', s)

def title_tokens(s):
    low = unicodedata.normalize('NFKC', s or '').lower()
    toks = set(TITLE_TOKEN_RE.findall(low))
    cjk = CJK_RE.findall(low)
    toks.update((cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)))
    return toks

def first_surname(authors):
    if not authors:
        return ''
    s = re.split('[,;，；]', authors.strip())[0].strip()
    if not s:
        return ''
    toks = s.replace('.', '. ').split()
    if len(toks) >= 2 and INITIAL_RE.match(toks[0]):
        return toks[-1]
    return toks[0]

def author_surname_list(authors):
    """库内 authors 是列表；取第一作者姓。"""
    if not authors:
        return ''
    return first_surname(authors[0])

class Matcher:

    def __init__(self, papers):
        self.by_doi = {}
        self.entries = {}
        self.token_index = defaultdict(set)
        self.by_year = defaultdict(list)
        self.meta = {}
        for p in papers:
            ck = p['citekey']
            self.meta[ck] = p
            d = norm_doi(p.get('doi'))
            if d and d not in self.by_doi:
                self.by_doi[d] = ck
            nt = norm_title(p.get('title'))
            toks = title_tokens(p.get('title'))
            entry = {'citekey': ck, 'title': nt, 'tokens': toks, 'surname': author_surname_list(p.get('authors')), 'year': p.get('year')}
            self.entries[ck] = entry
            for t in toks:
                self.token_index[t].add(ck)
            self.by_year[p.get('year')].append(entry)

    def _title_match(self, ref):
        nt = norm_title(ref.get('title'))
        ref_tokens = title_tokens(ref.get('title'))
        if len(nt) < 10 or len(ref_tokens) < 2:
            return None
        cand = set()
        for t in ref_tokens:
            cand |= self.token_index.get(t, set())
        if not cand:
            return None
        scored = []
        for ck in cand:
            e = self.entries[ck]
            shared = len(ref_tokens & e['tokens'])
            if shared >= 2:
                scored.append((shared, ck))
        if not scored:
            return None
        scored.sort(reverse=True)
        best_ck, best_score = (None, 0.0)
        for _shared, ck in scored[:80]:
            e = self.entries[ck]
            if not e['title']:
                continue
            ratio = difflib.SequenceMatcher(None, nt, e['title']).ratio()
            jac = _shared / max(1, len(ref_tokens | e['tokens']))
            score = max(ratio, jac)
            if score > best_score:
                best_ck, best_score = (ck, score)
        if best_ck and best_score >= 0.86:
            conf = 0.9 if best_score >= 0.92 else 0.8 if best_score >= 0.88 else 0.75
            return (best_ck, conf, f'title:{best_score:.2f}')
        return None

    def match(self, ref):
        doi = norm_doi(ref.get('doi'))
        if doi and doi in self.by_doi:
            return (self.by_doi[doi], 1.0, 'doi')
        hit = self._title_match(ref)
        if hit:
            return hit
        surname = first_surname(ref.get('authors'))
        year = ref.get('year')
        if surname and year:
            ref_tokens = title_tokens(ref.get('title'))
            best = None
            best_overlap = 0
            for cand in self.by_year.get(year, []) + self.by_year.get(year + 1, []):
                if cand['surname'].lower() != surname.lower():
                    continue
                ov = len(ref_tokens & cand['tokens'])
                if ov > best_overlap:
                    best_overlap = ov
                    best = cand
            if best and (best_overlap >= 3 or len(ref_tokens) <= 3):
                return (best['citekey'], 0.6, 'author-year')
        return (None, 0.0, '')

def pagerank(nodes, edges, damping=0.85, iters=60):
    n = len(nodes)
    if n == 0:
        return {}
    idx = {k: i for i, k in enumerate(nodes)}
    out_deg = [0] * n
    for src, dst, _ in edges:
        out_deg[idx[src]] += 1
    rank = [1.0 / n] * n
    for _ in range(iters):
        new = [(1.0 - damping) / n] * n
        dangling = sum((rank[i] for i in range(n) if out_deg[i] == 0))
        for src, dst, _ in edges:
            i, j = (idx[src], idx[dst])
            if out_deg[i]:
                new[j] += damping * rank[i] / out_deg[i]
        if dangling:
            share = damping * dangling / n
            for i in range(n):
                new[i] += share
        rank = new
    return {k: rank[idx[k]] for k in nodes}

def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default=C.BASE)
    ap.add_argument('--index', default=None)
    args = ap.parse_args(argv)
    base = os.path.abspath(args.base)
    index = os.path.abspath(args.index or C.index_dir(base))
    papers = read_jsonl(os.path.join(index, 'papers_all.jsonl'))
    refs = read_jsonl(C.references_jsonl(base))
    matcher = Matcher(papers)
    main_edges = {}
    low_edges = {}
    self_cites = 0
    unmatched = 0
    by_method = Counter()
    for ref in refs:
        src = ref.get('src_citekey')
        if not src:
            continue
        dst, conf, method = matcher.match(ref)
        if not dst:
            unmatched += 1
            continue
        if dst == src:
            self_cites += 1
            continue
        edge = {'src': src, 'dst': dst, 'confidence': conf, 'method': method, 'ref_index': ref.get('ref_index'), 'ref_title': ref.get('title', ''), 'ref_doi': ref.get('doi', ''), 'ref_year': ref.get('year')}
        target = main_edges if conf >= 0.75 else low_edges
        key = (src, dst)
        if key not in target or conf > target[key]['confidence']:
            target[key] = edge
            by_method[method.split(':')[0]] += 1
    nodes = [p['citekey'] for p in papers]
    node_set = set(nodes)
    edges_list = [(e['src'], e['dst'], e['confidence']) for e in main_edges.values() if e['src'] in node_set and e['dst'] in node_set]
    pr = pagerank(nodes, edges_list)
    in_deg = Counter()
    out_deg = Counter()
    for src, dst, _ in edges_list:
        out_deg[src] += 1
        in_deg[dst] += 1
    cited_by = defaultdict(list)
    cites = defaultdict(list)
    for src, dst, _ in edges_list:
        cited_by[dst].append(src)
        cites[src].append(dst)
    for k in cited_by:
        cited_by[k] = sorted(set(cited_by[k]))
    for k in cites:
        cites[k] = sorted(set(cites[k]))
    graph_nodes = []
    for p in papers:
        ck = p['citekey']
        graph_nodes.append({'citekey': ck, 'title': p.get('title', ''), 'year': p.get('year'), 'in_degree': in_deg.get(ck, 0), 'out_degree': out_deg.get(ck, 0), 'pagerank': round(pr.get(ck, 0.0), 8)})
    top_cited = sorted([x for x in graph_nodes if x['in_degree'] > 0], key=lambda x: (-x['in_degree'], x['citekey']))[:20]
    top_pr = sorted([x for x in graph_nodes if x['in_degree'] > 0 or x['out_degree'] > 0], key=lambda x: (-x['pagerank'], x['citekey']))[:20]
    write_jsonl(os.path.join(index, 'citation_edges.jsonl'), sorted(main_edges.values(), key=lambda e: (e['src'], e['dst'])))
    write_jsonl(os.path.join(index, 'citation_edges_lowconf.jsonl'), sorted(low_edges.values(), key=lambda e: (e['src'], e['dst'])))
    write_json(os.path.join(index, 'cited_by.json'), dict(cited_by))
    write_json(os.path.join(index, 'cites.json'), dict(cites))
    write_json(os.path.join(index, 'citation_graph.json'), {'nodes': graph_nodes, 'edges': [{'src': s, 'dst': d, 'confidence': c} for s, d, c in edges_list], 'stats': {'nodes': len(graph_nodes), 'edges': len(edges_list), 'refs': len(refs), 'matched': len(main_edges) + len(low_edges), 'unmatched': unmatched, 'self_cites': self_cites, 'low_confidence': len(low_edges)}})
    isolated = [x['citekey'] for x in graph_nodes if x['in_degree'] == 0 and x['out_degree'] == 0]
    report = {'papers': len(papers), 'refs': len(refs), 'papers_with_refs': len({r['src_citekey'] for r in refs if r.get('src_citekey')}), 'matched_main': len(main_edges), 'matched_lowconf': len(low_edges), 'unmatched': unmatched, 'match_rate': round(100 * (len(main_edges) + len(low_edges)) / max(1, len(refs)), 1), 'self_cites': self_cites, 'edges': len(edges_list), 'isolated': len(isolated), 'by_method': dict(by_method), 'top_cited': [{'citekey': x['citekey'], 'in_degree': x['in_degree']} for x in top_cited], 'top_pagerank': [{'citekey': x['citekey'], 'pagerank': x['pagerank']} for x in top_pr]}
    write_json(os.path.join(index, 'citation_report.json'), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
