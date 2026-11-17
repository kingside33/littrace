"""kbapp.workflows.tools.ocr_formulas：由职责边界组织的知识库能力。"""
import argparse
import os
import re
import sys
import time
from collections import defaultdict
import kbapp.core.config as C
from kbapp.core.io import append_jsonl, read_jsonl, remove_if_exists, write_jsonl
from kbapp.integrations.ocr import GLMOCR, GLMOCRError
SECTION_PRIORITY = {'Method': 0, 'Related': 1, 'Results': 2, 'Discussion': 3, 'Introduction': 4, 'Conclusion': 5, 'Front': 9}
CAPTION_RE = re.compile('^\\s*(?:fig(?:ure)?\\.?|table|tab\\.?|图|表)\\s*[0-9]', re.IGNORECASE)

def priority(r):
    p = SECTION_PRIORITY.get(r.get('section'), 8)
    if not r.get('label'):
        p += 10
    return p

def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default=C.BASE)
    ap.add_argument('--index', default=None)
    ap.add_argument('--citekey', default='')
    ap.add_argument('--max-per-paper', type=int, default=10)
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--full', action='store_true', help='已有 latex 的也重跑')
    ap.add_argument('--only-labeled', action='store_true', help='只 OCR 有编号的公式')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--require', action='store_true', help='OCR 不可用时返回非零')
    ap.add_argument('--url', default=None)
    ap.add_argument('--model', default=None)
    ap.add_argument('--api', default=None, choices=['openai', 'ollama'])
    args = ap.parse_args(argv)
    base = os.path.abspath(args.base)
    index = os.path.abspath(args.index or C.index_dir(base))
    src = os.path.join(index, 'formulas.jsonl')
    partial_path = os.path.join(index, 'formula_latex.partial.jsonl')
    records = read_jsonl(src)
    if not records:
        sys.exit('formulas.jsonl 为空，先运行 extract_formulas.py')
    client = GLMOCR(args.url, args.model, args.api)
    probe = client.probe()
    print(f"[GLM-OCR] {probe['url']} api={probe['api']} model={probe['model']} ok={probe['ok']} ({probe['detail']})")
    if not probe['ok']:
        if args.require:
            sys.exit(1)
        print('[skip] OCR 服务不可用；先启动 GLM-OCR 再重跑，本次不动 formulas.jsonl')
        return
    done = {}
    for r in read_jsonl(partial_path):
        if r.get('id') and r.get('citekey'):
            done[r['citekey'], r['id'], r.get('raw_text')] = r.get('latex', '')
    for r in records:
        key = (r.get('citekey'), r['id'], r.get('raw_text'))
        if key in done and (not r.get('latex')):
            r['latex'] = done[key]
    if args.citekey:
        records_sel = [r for r in records if r.get('citekey') == args.citekey]
    else:
        records_sel = records
    by_paper = defaultdict(list)
    for r in records_sel:
        by_paper[r['citekey']].append(r)
    todo = []
    for ck, rs in by_paper.items():
        rs.sort(key=priority)
        picked = 0
        for r in rs:
            if not args.full and r.get('latex'):
                continue
            if r.get('section') in ('References', 'Acknowledgment'):
                continue
            if CAPTION_RE.match(r.get('raw_text', '')):
                continue
            if args.only_labeled and (not r.get('label')):
                continue
            todo.append(r)
            picked += 1
            if picked >= args.max_per_paper:
                break
    if args.limit:
        todo = todo[:args.limit]
    print(f'待 OCR：{len(todo)} 条（{len(by_paper)} 篇，每篇上限 {args.max_per_paper}）')
    if args.dry_run:
        for r in todo[:20]:
            print(f"  - {r['id']} p.{r.get('page')} {r.get('section')} {r.get('label')}")
        return
    from kbapp.core.progress import Progress
    bar = Progress(max(1, len(todo)), '公式 OCR ')
    ok = failed = missing = 0
    t0 = time.time()
    for r in todo:
        path = r.get('file', '')
        full = path if os.path.isabs(path) else os.path.join(base, path)
        if not os.path.exists(full):
            missing += 1
            bar.update(1, note=f"{r['id']} 缺图")
            continue
        with open(full, 'rb') as fh:
            png = fh.read()
        try:
            latex = client.formula_to_latex(png)
        except GLMOCRError as ex:
            failed += 1
            bar.update(1, note=f"{r['id']} 失败")
            if failed <= 3:
                print(f"\n[warn] {r['id']}: {ex}")
            continue
        r['latex'] = latex
        append_jsonl(partial_path, [{'id': r['id'], 'citekey': r['citekey'], 'raw_text': r.get('raw_text'), 'latex': latex}])
        ok += 1
        bar.update(1, note=r['id'])
    bar.close(f'OCR {ok} 条')
    write_jsonl(src, records)
    remove_if_exists(partial_path)
    print(f'\n完成：OCR {ok}，失败 {failed}，缺图 {missing}，耗时 {time.time() - t0:.1f}s')
    print('下一步：python kb.py data tool embed_extra --kind formulas')
