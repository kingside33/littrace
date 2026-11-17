"""kbapp.workflows.tools.ocr_pages：由职责边界组织的知识库能力。"""
import argparse
import json
import os
import sys
import time
import pymupdf
import kbapp.core.config as C
from kbapp.core.io import read_json, read_jsonl, write_json, write_text
from kbapp.integrations.ocr import GLMOCR, GLMOCRError
MIN_CHARS = 20

def page_has_text(page):
    try:
        from kbapp.library.documents import classify
        return not classify(page)['needs_ocr']
    except Exception:
        return False

def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default=C.BASE)
    ap.add_argument('--index', default=None)
    ap.add_argument('--citekey', default='')
    ap.add_argument('--only-failed', action='store_true', default=False)
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--force', action='store_true')
    ap.add_argument('--max-pages-per-paper', type=int, default=0, help='0表示检查全部页面')
    ap.add_argument('--limit-papers', type=int, default=0)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--require', action='store_true')
    ap.add_argument('--url', default=None)
    ap.add_argument('--model', default=None)
    ap.add_argument('--api', default=None, choices=['openai', 'ollama'])
    args = ap.parse_args(argv)
    base = os.path.abspath(args.base)
    index = os.path.abspath(args.index or C.index_dir(base))
    out_root = os.path.join(index, 'ocr_pages')
    os.makedirs(out_root, exist_ok=True)
    papers = read_jsonl(os.path.join(index, 'papers_all.jsonl'))
    by_key = {p['citekey']: p for p in papers}
    if args.citekey:
        targets = [by_key[args.citekey]] if args.citekey in by_key else []
    elif args.all:
        targets = papers
    else:
        failed = read_json(os.path.join(index, 'chunk_failures.json'), []) or []
        keys = {f['citekey'] for f in failed if f.get('error') == 'no_text'}
        vr = read_json(os.path.join(index, 'verify_report.json'), {}) or {}
        keys.update(vr.get('no_chunks') or [])
        targets = [p for p in papers if p['citekey'] in keys]
    targets = [p for p in targets if p.get('pdf')]
    if args.limit_papers:
        targets = targets[:args.limit_papers]
    client = GLMOCR(args.url, args.model, args.api)
    from kbapp.integrations.model_identity import signature
    model_identity = signature(client.model, client.url, client.api, refresh=True)
    probe = client.probe()
    print(f"[GLM-OCR] {probe['url']} api={probe['api']} model={probe['model']} ok={probe['ok']} ({probe['detail']})")
    if not probe['ok']:
        if args.require:
            sys.exit(1)
        print('[skip] OCR 服务不可用；先启动 GLM-OCR 再重跑')
        return
    if args.dry_run:
        for p in targets:
            print(f"- {p['citekey']}  {p.get('pdf')}")
        return
    from kbapp.core.progress import Progress
    total_pages = ocr_done = skipped = failed = 0
    t0 = time.time()
    bar = Progress(len(targets), '扫描页 OCR ')
    for p in targets:
        ck = p['citekey']
        pdf = p['pdf']
        full = pdf if os.path.isabs(pdf) else os.path.join(base, pdf)
        if not os.path.exists(full):
            bar.update(1, note=f'{ck} 缺失')
            continue
        out_dir = os.path.join(out_root, ck)
        os.makedirs(out_dir, exist_ok=True)
        page_map = {}
        try:
            with pymupdf.open(full) as doc:
                if doc.page_count == 0:
                    print(f'\n[warn] {ck} PDF 0 页（文件损坏），跳过')
                    bar.update(1, note=f'{ck} bad_pdf')
                    continue
                n = min(doc.page_count, args.max_pages_per_paper) if args.max_pages_per_paper > 0 else doc.page_count
                for pno in range(1, n + 1):
                    total_pages += 1
                    page = doc.load_page(pno - 1)
                    cache_path = os.path.join(out_dir, f'p{pno:04d}.txt')
                    if os.path.exists(cache_path) and (not args.force):
                        from kbapp.library.chunk import read_ocr_page
                        stamp = read_json(os.path.splitext(cache_path)[0] + '.json', {}) or {}
                        cached = read_ocr_page(index, ck, pno, pdf_path=full)
                        if cached and model_identity['revision'] != 'unknown' and (stamp.get('model_identity') == model_identity):
                            page_map[pno] = cached
                            skipped += 1
                            continue
                    if page_has_text(page) and (not args.force):
                        skipped += 1
                        continue
                    pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2))
                    png = pix.tobytes('png')
                    try:
                        text = client.page_to_text(png)
                    except GLMOCRError as ex:
                        failed += 1
                        if failed <= 3:
                            print(f'\n[warn] {ck} p.{pno}: {ex}')
                        continue
                    write_text(cache_path, text)
                    import hashlib
                    from kbapp.core.files import file_hash
                    from kbapp.integrations.model_identity import signature
                    st = os.stat(full)
                    write_json(os.path.splitext(cache_path)[0] + '.json', {'pdf_hash': hashlib.sha256((str(st.st_mtime_ns) + str(st.st_size)).encode()).hexdigest(), 'content_sha256': file_hash(full), 'model': client.model, 'model_identity': signature(client.model, client.url, client.api), 'page': pno})
                    page_map[pno] = text
                    ocr_done += 1
        except Exception as ex:
            print(f'\n[warn] {ck} 打开失败：{type(ex).__name__}: {ex}')
            bar.update(1, note=f'{ck} 失败')
            continue
        if page_map:
            write_json(os.path.join(out_root, ck + '.json'), page_map)
        bar.update(1, note=f'{ck} {len(page_map)}页')
    bar.close(f'OCR {ocr_done} 页')
    print(json.dumps({'papers': len(targets), 'pages_scanned': total_pages, 'ocr_done': ocr_done, 'skipped': skipped, 'failed': failed, 'seconds': round(time.time() - t0, 1)}, ensure_ascii=False, indent=2))
    print('下一步：python kb.py data tool chunk   # 会自动读取 OCR 文本并重切这些页')
