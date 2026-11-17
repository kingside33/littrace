"""kbapp.workflows.tools.dump_text：由职责边界组织的知识库能力。"""
import argparse
import json
import os
import sys
from collections import defaultdict
BASE = str(__import__('kbapp.core.paths', fromlist=['Paths']).Paths.from_base().base)
SECTION_ORDER = ['Abstract', 'Introduction', 'Related', 'Method', 'Results', 'Discussion', 'Conclusion', 'Front']

def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument('citekeys', nargs='*')
    ap.add_argument('--index', default=str(__import__('kbapp.core.paths', fromlist=['Paths']).Paths.from_base().active_index()))
    ap.add_argument('--max-chars', type=int, default=20000)
    ap.add_argument('--sections', default='', help='逗号分隔，如 Abstract,Introduction,Conclusion')
    ap.add_argument('--list-core', action='store_true')
    ap.add_argument('--core', action='store_true', help='对每篇 Core-A 输出摘要+引言')
    args = ap.parse_args(argv)
    index = os.path.abspath(args.index)
    papers = [json.loads(line) for line in open(os.path.join(index, 'papers_all.jsonl'), encoding='utf-8') if line.strip()]
    by_key = {p['citekey']: p for p in papers}

    def is_core(p):
        tf = p.get('tagfields') or {}
        return 'A' in (tf.get('priority') or []) or 'Core-A' in (tf.get('reading_tier') or [])
    if args.list_core:
        core = [p for p in papers if is_core(p)]
        for p in sorted(core, key=lambda x: (x['primary_direction'], -(x.get('year') or 0))):
            print(f"{p['citekey']}\t{p.get('year')}\t{p['primary_direction']}\t{(p.get('title') or '')[:70]}")
        print(f'\n共 {len(core)} 篇', file=sys.stderr)
        return
    chunks = defaultdict(list)
    with open(os.path.join(index, 'chunks.jsonl'), encoding='utf-8') as fh:
        for line in fh:
            if not line.strip():
                continue
            c = json.loads(line)
            chunks[c['citekey']].append(c)
    want_sections = set((s.strip() for s in args.sections.split(',') if s.strip()))
    keys = args.citekeys
    if args.core:
        keys = [p['citekey'] for p in papers if is_core(p)]
    for ck in keys:
        p = by_key.get(ck)
        print(f"\n{'=' * 70}")
        if p:
            print(f"# {ck}  |  {p.get('year')}  |  {p['primary_direction']}")
            print(f"# {p.get('title')}")
            print(f"# 作者: {'; '.join(p.get('authors') or [])}")
            print(f"# 期刊: {p.get('venue') or '-'}   DOI: {p.get('doi') or '-'}")
        else:
            print(f'# {ck}（不在 papers_all 中）')
        print('=' * 70)
        cs = chunks.get(ck) or []
        if not cs:
            print('（无正文：PDF 未切块或不存在）')
            continue

        def sort_key(c):
            s = c.get('section') or 'Front'
            return (SECTION_ORDER.index(s) if s in SECTION_ORDER else 99, c.get('page_start') or 0)
        total = 0
        cur_sec = None
        for c in sorted(cs, key=sort_key):
            if want_sections and (c.get('section') or '') not in want_sections:
                continue
            if c.get('section') != cur_sec:
                cur_sec = c.get('section')
                print(f'\n--- [{cur_sec}] ---')
            text = c['text']
            if total + len(text) > args.max_chars:
                text = text[:max(0, args.max_chars - total)]
                print(text)
                print('\n…（已截断）')
                break
            print(text)
            total += len(text)
