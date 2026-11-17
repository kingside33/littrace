"""kbapp.library.chunk：由职责边界组织的知识库能力。"""
from collections import Counter
import hashlib
import json
import os
import re
from types import SimpleNamespace
import warnings
import kbapp.core.config as C
from kbapp.core.io import append_jsonl, read_json, read_jsonl, remove_if_exists, write_json, write_jsonl
from kbapp.core.chunking import chunk_paras
def pdf_rects(path, page, quote, limit=8):
    """C06：在 PDF 指定页定位摘录坐标；失败或找不到时返回空列表。"""
    import pymupdf
    try:
        with pymupdf.open(path) as doc:
            if not isinstance(page, int) or not 1 <= page <= len(doc):
                return []
            rects = doc[page - 1].search_for(quote)
            return [[round(r.x0, 1), round(r.y0, 1), round(r.x1, 1), round(r.y1, 1)] for r in rects[:limit]]
    except (OSError, ValueError, RuntimeError):
        return []

SECTION_PATTERNS = [('Abstract', '^\\s*(?:\\d+\\.?\\s*)?(abstract|summary|摘\\s*要)\\b'), ('Introduction', '^\\s*(?:\\d+\\.?\\s*)?(introduction|引\\s*言|绪\\s*论|前\\s*言)\\b'), ('Related', '^\\s*(?:\\d+\\.?\\s*)?(related\\s+work|literature\\s+review|background|相关工作|研究现状|文献综述)\\b'), ('Method', '^\\s*(?:\\d+\\.?\\s*)?(method(?:ology)?|proposed\\s+(?:method|approach|framework|algorithm)|approach|model|formulation|problem\\s+formulation|方\\s*法|算法|模型|问题描述|问题建模)\\b'), ('Results', '^\\s*(?:\\d+\\.?\\s*)?(results?|experiments?|evaluation|simulations?|case\\s+study|实验|仿真|结果|算例)\\b'), ('Discussion', '^\\s*(?:\\d+\\.?\\s*)?(discussion|分\\s*析|讨\\s*论)\\b'), ('Conclusion', '^\\s*(?:\\d+\\.?\\s*)?(conclusions?|concluding\\s+remarks|future\\s+work|结\\s*论|总结|展望)\\b'), ('References', '^\\s*(?:\\d+\\.?\\s*)?(references|bibliography|参考文献)\\b'), ('Acknowledgment', '^\\s*(?:\\d+\\.?\\s*)?(acknowledg\\w*|致\\s*谢)\\b')]
SECTION_RE = [(name, re.compile(pat, re.IGNORECASE)) for name, pat in SECTION_PATTERNS]
DROP_SECTIONS = {'References', 'Acknowledgment'}

def clean_text(t):
    t = t.replace('\xad', '')
    t = re.sub('-\\n(?=[a-z])', '', t)
    t = re.sub('[ \\t]+', ' ', t)
    t = re.sub('\\n{3,}', '\n\n', t)
    return t.strip()

def page_text(page):
    """双栏按栏读取，跨栏标题分隔上下区域，避免按 y 排序交错左右栏。"""
    import pymupdf
    data = page.get_text('dict', flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES)
    blocks = []
    for block in data.get('blocks', []):
        if block.get('type') != 0:
            continue
        for line in block.get('lines', []):
            bbox = line['bbox']
            text = ''.join((span.get('text', '') for span in line.get('spans', [])))
            if text.strip() and bbox[2] > page.rect.width * 0.08 and (bbox[0] < page.rect.width * 0.94):
                blocks.append((*bbox, text, 0, 0))
    mid = page.rect.width / 2
    left = [b for b in blocks if b[2] <= mid + 8]
    right = [b for b in blocks if b[0] >= mid - 8]
    if len(left) < 3 or len(right) < 3:
        return page.get_text('text')
    spanning = sorted([b for b in blocks if b[0] < mid - 30 and b[2] > mid + 30], key=lambda b: b[1])
    remaining = [b for b in blocks if b not in spanning]
    output = []
    for boundary in spanning + [None]:
        y = boundary[1] if boundary else float('inf')
        band = [b for b in remaining if (b[1] + b[3]) / 2 < y]
        output.extend((b[4] for b in sorted(band, key=lambda b: ((b[0] + b[2]) / 2 >= mid, b[1], b[0]))))
        remaining = [b for b in remaining if b not in band]
        if boundary:
            output.append(boundary[4])
    return '\n'.join(output)

def detect_section(line):
    s = line.strip()
    if not s or len(s) > 90:
        return None
    s = re.sub('^(?:[IVXLC]{1,6}[.．]|[一二三四五六七八九十]+[、.．]|\\d+(?:\\.\\d+)*[.．]?)\\s*', '', s)
    if re.match('^(?:informed rrt|batch informed trees|algorithm design|the proposed|theoretical analysis)', s, re.I):
        return 'Method'
    for name, rx in SECTION_RE:
        if rx.match(s):
            return name
    return None

def split_paragraphs(text):
    return [p.strip() for p in re.split('\\n\\s*\\n', text) if p.strip()]

def extract_pdf(path, max_pages=None, citekey=None, index_dir=None):
    import pymupdf
    blocks = []
    with pymupdf.open(path) as doc:
        n = doc.page_count if max_pages is None else min(doc.page_count, max_pages)
        cur = 'Front'
        for i in range(n):
            try:
                raw = page_text(doc.load_page(i))
            except Exception as exc:
                warnings.warn(f'PDF第{i + 1}页文字提取失败：{type(exc).__name__}；其余页面继续处理', RuntimeWarning)
                raw = ''
            if citekey and index_dir:
                raw = read_ocr_page(index_dir, citekey, i + 1, pdf_path=path) or raw
            if not raw:
                continue
            raw = clean_text(raw)
            seg, seg_section = ([], cur)
            for line in raw.split('\n'):
                sec = detect_section(line)
                if sec:
                    if seg:
                        blocks.append((i + 1, seg_section, '\n'.join(seg)))
                        seg = []
                    seg_section = sec
                    cur = sec
                else:
                    seg.append(line)
            if seg:
                blocks.append((i + 1, seg_section, '\n'.join(seg)))
    return blocks

def text_hash(text):
    return hashlib.sha1((text or '').encode('utf-8')).hexdigest()[:12]

def read_ocr_page(index_dir, citekey, pno, pdf_path=None):
    """读取 ocr_pages.py 缓存的单页文本；没有则返回空串。"""
    if not index_dir or not citekey:
        return ''
    p = os.path.join(index_dir, 'ocr_pages', citekey, f'p{pno:04d}.txt')
    if not os.path.exists(p):
        return ''
    try:
        if pdf_path:
            st = os.stat(pdf_path)
            expected = hashlib.sha256((str(st.st_mtime_ns) + str(st.st_size)).encode('utf-8')).hexdigest()
            with open(os.path.splitext(p)[0] + '.json', encoding='utf-8') as stamp:
                metadata = json.load(stamp)
            if metadata.get('pdf_hash') != expected:
                return ''
            if metadata.get('content_sha256'):
                from kbapp.core.files import source_hash
                if source_hash(pdf_path) != metadata['content_sha256']:
                    return ''
        with open(p, encoding='utf-8') as fh:
            return fh.read().strip()
    except Exception:
        return ''

def build_chunks_for(paper, full_path, target, overlap, max_pages=None, index_dir=None):
    import pymupdf
    try:
        with pymupdf.open(full_path) as doc:
            if doc.page_count == 0:
                return (None, 'bad_pdf')
    except Exception as ex:
        return (None, f'open_error:{type(ex).__name__}')
    blocks = extract_pdf(full_path, max_pages, citekey=paper['citekey'], index_dir=index_dir)
    if not blocks:
        return (None, 'no_text')
    merged = []
    for page, sec, text in blocks:
        if sec in DROP_SECTIONS:
            continue
        text = text.strip()
        if not text:
            continue
        if merged and merged[-1][1] == sec:
            merged[-1][2].append((page, text))
        else:
            merged.append([page, sec, [(page, text)]])
    out = []
    idx = 0
    for _fp, sec, items in merged:
        for text, tok, ps, pe in chunk_paras(items, target, overlap):
            idx += 1
            out.append({'chunk_id': f"{paper['citekey']}#{idx:03d}", 'citekey': paper['citekey'], 'section': sec, 'page_start': ps, 'page_end': pe, 'tokens': tok, 'text': text, 'text_hash': text_hash(text)})
    return (out, None)

def fingerprint(path, ocr_dir=None):
    st = os.stat(path)
    fp = {'mtime': round(st.st_mtime, 3), 'size': st.st_size}
    m = 0.0
    if ocr_dir and os.path.isdir(ocr_dir):
        for fn in os.listdir(ocr_dir):
            if fn.endswith('.txt'):
                try:
                    m = max(m, os.path.getmtime(os.path.join(ocr_dir, fn)))
                except OSError:
                    pass
    fp['ocr_mtime'] = round(m, 3)
    return fp

def load_existing(index):
    """返回 {citekey: [chunks]}；chunks.jsonl 打底，partial 覆盖（后写的为准）。"""
    out = {}
    for c in read_jsonl(os.path.join(index, 'chunks.jsonl')):
        ck = c.get('citekey')
        if not ck:
            continue
        c.setdefault('text_hash', text_hash(c.get('text', '')))
        out.setdefault(ck, []).append(c)
    cur = None
    for c in read_jsonl(os.path.join(index, 'chunks.partial.jsonl')):
        ck = c.get('citekey')
        if not ck:
            continue
        if ck != cur:
            out[ck] = []
            cur = ck
        if c.get('_reset'):
            continue
        out[ck].append(c)
    return out

DEFAULT_OPTIONS = {'base': C.BASE, 'index': None, 'full': False, 'limit': 0, 'only_core': False, 'max_pages': 0, 'quiet': False}

def process_collection(options):
    args = SimpleNamespace(**dict(DEFAULT_OPTIONS, **options))
    base = os.path.abspath(args.base)
    index = os.path.abspath(args.index or C.index_dir(base))
    target_tok = C.CHUNK_TARGET_TOKENS
    overlap_tok = C.CHUNK_OVERLAP_TOKENS
    papers = read_jsonl(os.path.join(index, 'papers_all.jsonl'))

    def is_core(p):
        tf = p.get('tagfields') or {}
        return 'A' in (tf.get('priority') or []) or 'Core-A' in (tf.get('reading_tier') or [])
    if args.only_core:
        papers = [p for p in papers if is_core(p)]
    targets = []
    for p in papers:
        pdf = p.get('pdf')
        if not pdf:
            continue
        full = pdf if os.path.isabs(pdf) else os.path.join(base, pdf)
        if os.path.exists(full):
            targets.append((p, full))
    if args.limit:
        targets = targets[:args.limit]
    cache_path = os.path.join(index, 'chunk_cache.json')
    partial_path = os.path.join(index, 'chunks.partial.jsonl')
    cache = {} if args.full else read_json(cache_path, {}) or {}
    if cache.get('_version') != C.CHUNKER_VERSION:
        cache = {}
    if args.full:
        remove_if_exists(partial_path)
    existing = {} if args.full else load_existing(index)
    seed_mode = False
    from kbapp.core.progress import Progress
    bar = Progress(len(targets), '切块 ', enabled=not args.quiet)
    new_cache = {'_version': C.CHUNKER_VERSION, 'papers': dict(cache.get('papers') or {})}
    failures = []
    section_counter = Counter()
    reused = rebuilt = 0
    out_rows = []
    for n, (p, full) in enumerate(targets, 1):
        ck = p['citekey']
        fp = fingerprint(full, os.path.join(index, 'ocr_pages', ck))
        prev = (cache.get('papers') or {}).get(ck)
        can_reuse = not args.full and existing.get(ck) and (seed_mode or (prev and prev.get('mtime') == fp['mtime'] and (prev.get('size') == fp['size']) and (prev.get('ocr_mtime', 0) == fp['ocr_mtime'])))
        if can_reuse:
            rows = existing[ck]
            reused += 1
        else:
            append_jsonl(partial_path, [{'citekey': ck, '_reset': True}])
            rows, err = build_chunks_for(p, full, target_tok, overlap_tok, args.max_pages or None, index)
            if err:
                failures.append({'citekey': ck, 'pdf': p.get('pdf'), 'error': err})
                existing.pop(ck, None)
                bar.update(1, note=f'跳过 {ck}')
                continue
            rebuilt += 1
            append_jsonl(partial_path, rows)
            existing[ck] = rows
            new_cache['papers'][ck] = {**fp, 'chunks': len(rows)}
            write_json(cache_path, new_cache)
        out_rows.extend(rows)
        new_cache['papers'][ck] = {**fp, 'chunks': len(rows)}
        for r in rows:
            section_counter[r['section']] += 1
        bar.update(1, note=ck)
    bar.close(f'重切 {rebuilt} · 复用 {reused}')
    write_jsonl(os.path.join(index, 'chunks.jsonl'), out_rows)
    write_json(cache_path, new_cache)
    remove_if_exists(partial_path)
    report = {'mode': 'full' if args.full else 'incremental', 'papers_with_pdf': len(targets), 'reused': reused, 'rebuilt': rebuilt, 'papers_failed': len(failures), 'chunks': len(out_rows), 'sections': dict(section_counter.most_common())}
    with open(os.path.join(index, 'chunk_report.json'), 'w', encoding='utf-8') as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(index, 'chunk_failures.json'), 'w', encoding='utf-8') as fh:
        json.dump(failures, fh, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
