"""kbapp.workflows.tools.kb_search：由职责边界组织的知识库能力。"""
import argparse
import json
import os
import re
import sys
import kbapp.core.config as C
from kbapp.search.ranking import MODALITIES, allowed_indices, build_bm25, embed_query, format_figure, format_formula, format_text, load_papers_meta, load_records, rrf_multi, tokenize, vector_search

def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument('query')
    ap.add_argument('-k', type=int, default=8)
    ap.add_argument('--index', default=str(__import__('kbapp.core.paths', fromlist=['Paths']).Paths.from_base().active_index()))
    ap.add_argument('--model', default=C.EMBED_MODEL)
    ap.add_argument('--dims', type=int, default=C.EMBED_DIMS)
    ap.add_argument('--modality', default='text', choices=['text', 'figures', 'formulas', 'all'], help='检索哪一层；all 表示三路融合')
    ap.add_argument('--papers', action='store_true', help='按论文聚合输出')
    ap.add_argument('--bm25-only', action='store_true')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--year', default='', help='2020 / 2020-2026 / 2020,2022')
    ap.add_argument('--direction', default='', help='方向编号前缀，如 02')
    ap.add_argument('--section', default='', help='章节前缀，如 Method')
    ap.add_argument('--priority', default='', help='A / B / C')
    args = ap.parse_args(argv)
    if '--index' not in sys.argv:
        from kbapp.storage.store import Store
        store = Store()
        if store.state('active_version'):
            from kbapp.search.indexing import Search
            if not args.bm25_only:
                from kbapp.integrations.models import set_cancelled
                set_cancelled(store, ['embedding'], False)
            kind = {'figures': 'figure', 'formulas': 'formula'}.get(args.modality, '')
            found = Search(store).search(args.query, k=args.k * 3 if args.papers else args.k, kind=kind, direction=args.direction, year=args.year, section=args.section, vectors=not args.bm25_only, priority=args.priority, wait_vectors=not args.bm25_only)
            if args.papers:
                unique = {}
                for row in found['results']:
                    unique.setdefault(row.get('paper_id') or row['id'], row)
                found['results'] = list(unique.values())[:args.k]
            if args.json:
                print(json.dumps(found, ensure_ascii=False, indent=2))
            else:
                print(f"# 检索：{args.query} [{found['mode']}]")
                for row in found['results']:
                    print(f"\n## {row.get('citekey') or row.get('title')} · {row['source_label']}")
                    print(row.get('snippet') or row['text'][:900])
                    print('出处：' + row.get('url', ''))
            return
    index = os.path.abspath(args.index)
    mods = ['text', 'figures', 'formulas'] if args.modality == 'all' else [args.modality]
    papers_meta = load_papers_meta(index)
    records = {}
    texts = {}
    rank_lists = []
    modes = []
    for mod in mods:
        rs = load_records(index, mod)
        if not rs:
            continue
        ts = [MODALITIES[mod]['text'](r) for r in rs]
        records[mod] = rs
        texts[mod] = ts
        allowed = allowed_indices(rs, papers_meta, args)
        bm = build_bm25(index, mod, rs, ts)
        q_tokens = tokenize(args.query)
        phrases = [s.strip().lower() for s in re.split('[\\s,，、;；]+', args.query) if len(s.strip()) >= 2]
        bm_hits = bm.search(q_tokens, topk=50, phrases=phrases, texts=ts, allowed=allowed)
        lst = bm_hits
        mode = 'bm25'
        if not args.bm25_only and os.path.exists(os.path.join(index, 'vec', MODALITIES[mod]['vec'])):
            try:
                qv = embed_query(args.query, args.model, MODALITIES[mod]['instruction'])
                vh = vector_search(index, mod, qv, args.dims, topk=50, allowed=allowed)
                lst = bm_hits + vh
                mode = 'bm25+vector(rrf)'
            except Exception as ex:
                print(f'[warn] {mod} 向量检索不可用（{type(ex).__name__}），仅用 BM25', file=sys.stderr)
        if mode.startswith('bm25+vector'):
            inner = rrf_multi([(mod, bm_hits), (mod, vh)])
            lst = [(idx, s) for (_m, idx), s in inner]
        rank_lists.append((mod, lst))
        modes.append(f'{mod}:{mode}')
    if not rank_lists:
        sys.exit('没有可检索的索引；先运行 chunk/embed 与 extract_figures/extract_formulas')
    fused = rrf_multi(rank_lists)
    if args.papers:
        agg = {}
        for (mod, idx), s in fused:
            ck = records[mod][idx]['citekey']
            if ck not in agg or s > agg[ck]['score']:
                agg[ck] = {'citekey': ck, 'score': round(s, 5), 'modality': mod}
        out = sorted(agg.values(), key=lambda x: -x['score'])[:args.k]
        if args.json:
            print(json.dumps({'modality': args.modality, 'mode': modes, 'query': args.query, 'results': out}, ensure_ascii=False, indent=2))
        else:
            print(f"# 检索：{args.query}  [{', '.join(modes)}]")
            for r in out:
                print(f"- {r['citekey']}  ({r['modality']}, {r['score']})")
        return
    results = []
    for (mod, idx), s in fused[:args.k]:
        r = records[mod][idx]
        if mod == 'text':
            results.append(format_text(r, s, args.query))
        elif mod == 'figures':
            results.append(format_figure(r, s))
        else:
            results.append(format_formula(r, s, args.query))
    if args.json:
        print(json.dumps({'modality': args.modality, 'mode': modes, 'query': args.query, 'results': results}, ensure_ascii=False, indent=2))
        return
    print(f'# 检索：{args.query}')
    print(f"# 模式：{', '.join(modes)}  命中 {len(results)} 条\n")
    for r in results:
        if r['modality'] == 'text':
            print(f"## {r['citekey']}  ·  {r['section']}  ·  p.{r['pages']}  ·  score {r['score']}")
            print(r['snippet'])
        elif r['modality'] == 'figures':
            kind = '图' if r.get('kind') == 'figure' else '表'
            print(f"## {r['citekey']}  ·  {kind} {r.get('label') or ''}  ·  p.{r.get('page')}  ·  score {r['score']}")
            print((r.get('caption') or '')[:500])
            print(f"文件：{r.get('file')}")
        else:
            print(f"## {r['citekey']}  ·  式 {r.get('label') or '?'}  ·  p.{r.get('page')}  ·  score {r['score']}")
            if r.get('latex'):
                print(f"LaTeX: {r['latex'][:500]}")
            else:
                print(f"原文: {(r.get('raw_text') or '')[:500]}")
            if r.get('context'):
                print(f"上下文: {r['context']}")
            print(f"文件：{r.get('file')}")
        print()
