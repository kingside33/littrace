"""kbapp.workflows.tools.clean_formula_latex：由职责边界组织的知识库能力。"""
import argparse
import re
import sys
import kbapp.core.config as C
from kbapp.core.io import read_jsonl, write_jsonl
WORD_RE = re.compile('[A-Za-z]{4,}')
INLINE_RE = re.compile('(?<!\\$)\\$([^$]{1,300})\\$(?!\\$)', re.S)

def is_prose(s):
    return len(WORD_RE.findall(s)) > 12 and s.count('\\') < 2

def pick_inline(s):
    best = ''
    for m in INLINE_RE.finditer(s):
        cand = m.group(1).strip()
        if not cand:
            continue
        if not re.search('[\\\\^_{}=<>≤≥]|\\d', cand):
            continue
        if len(cand) > len(best):
            best = cand
    return best

def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default=C.BASE)
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args(argv)
    path = C.formulas_jsonl(args.base)
    rows = read_jsonl(path)
    changed = cleared = 0
    samples = []
    for r in rows:
        lat = (r.get('latex') or '').strip()
        if not lat or not is_prose(lat):
            continue
        new = pick_inline(lat)
        if new == lat:
            continue
        if len(samples) < 8:
            samples.append((r['id'], lat[:80], new[:80]))
        r['latex'] = new
        changed += 1
        if not new:
            cleared += 1
    print(f'latex 清洗：修改 {changed} 条，其中清空 {cleared} 条')
    for cid, old, new in samples:
        print(f"- {cid}\n  old: {old}\n  new: {new or '(清空)'}")
    if args.dry_run:
        print('[dry-run] 不写盘')
        return
    if changed:
        write_jsonl(path, rows)
        print('已写回 formulas.jsonl；下一步：python kb.py data tool embed_extra --kind formulas')
