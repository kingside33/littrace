"""kbapp.workflows.tools.kb_graph：由职责边界组织的知识库能力。"""
import argparse
import json
import os
import sys
from collections import deque
import kbapp.core.config as C
from kbapp.core.io import read_json

def load(index):
    graph = read_json(os.path.join(index, 'citation_graph.json'), {}) or {}
    nodes = {n['citekey']: n for n in graph.get('nodes', [])}
    edges = graph.get('edges', [])
    cited_by = {}
    cites = {}
    for e in edges:
        cited_by.setdefault(e['dst'], []).append(e['src'])
        cites.setdefault(e['src'], []).append(e['dst'])
    for d in (cited_by, cites):
        for k in d:
            d[k] = sorted(set(d[k]))
    return (graph, nodes, cited_by, cites)

def label(nodes, ck):
    n = nodes.get(ck) or {}
    t = (n.get('title') or '')[:70]
    y = n.get('year') or '----'
    return f'{ck} ({y}) {t}'.strip()

def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default=C.BASE)
    ap.add_argument('--index', default=None)
    ap.add_argument('--cited-by', default='')
    ap.add_argument('--cites', default='')
    ap.add_argument('--top-cited', type=int, default=0)
    ap.add_argument('--neighbors', default='')
    ap.add_argument('--depth', type=int, default=1)
    ap.add_argument('--stats', action='store_true')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args(argv)
    index = os.path.abspath(args.index or C.index_dir(args.base))
    graph, nodes, cited_by, cites = load(index)
    if not graph:
        sys.exit('citation_graph.json 不存在，先运行 build_citation_graph.py')
    out = {}
    if args.cited_by:
        out['cited_by'] = {'citekey': args.cited_by, 'papers': cited_by.get(args.cited_by, [])}
    if args.cites:
        out['cites'] = {'citekey': args.cites, 'papers': cites.get(args.cites, [])}
    if args.top_cited:
        ranked = sorted(nodes.values(), key=lambda n: (-n['in_degree'], n['citekey']))[:args.top_cited]
        out['top_cited'] = [{'citekey': n['citekey'], 'in_degree': n['in_degree']} for n in ranked]
    if args.neighbors:
        start = args.neighbors
        seen = {start}
        q = deque([(start, 0)])
        while q:
            cur, d = q.popleft()
            if d >= args.depth:
                continue
            for nb in cited_by.get(cur, []) + cites.get(cur, []):
                if nb not in seen:
                    seen.add(nb)
                    q.append((nb, d + 1))
        seen.discard(start)
        out['neighbors'] = {'citekey': start, 'depth': args.depth, 'papers': sorted(seen)}
    if args.stats or not out:
        out['stats'] = graph.get('stats', {})
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    for key, val in out.items():
        if key == 'stats':
            print('# 引文图统计')
            for k, v in val.items():
                print(f'- {k}: {v}')
            print()
            continue
        if key == 'top_cited':
            print(f'# 高被引 Top {len(val)}')
            for i, r in enumerate(val, 1):
                print(f"{i:2}. {label(nodes, r['citekey'])}  ← {r['in_degree']}")
            print()
            continue
        ck = val.get('citekey')
        papers = val.get('papers', [])
        title = '被谁引用' if key == 'cited_by' else '引用了谁' if key == 'cites' else '邻域'
        print(f'# {ck} · {title}（{len(papers)} 篇）')
        for p in papers:
            print(f'- {label(nodes, p)}')
        print()
