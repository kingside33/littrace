"""kbapp.workflows.tools.extract_formulas：由职责边界组织的知识库能力。"""
import json
import os
import re
from types import SimpleNamespace
import pymupdf
import kbapp.core.config as C
from kbapp.library.chunk import detect_section
from kbapp.core.io import append_jsonl, read_json, read_jsonl, remove_if_exists, write_json, write_jsonl
MATH_CHARS = set('=≤≥≈≠∈∉∑∫∬∂∇∀∃∞±×÷√∝⊂⊆∪∩→←↔⇒⇔')
MATH_WORDS = re.compile('\\b(arg\\s*min|arg\\s*max|minimize|maximize|subject\\s+to|s\\.t\\.)\\b', re.IGNORECASE)
EQ_NUM_RE = re.compile('[\\(（]\\s*(\\d+(?:\\.\\d+)?[a-z]?)\\s*[\\)）]\\s*$')
MATH_FONT_RE = re.compile('(cmmi|cmsy|cmex|symbol|mtmi|mtm-|stix|math|euler|lmmath)', re.IGNORECASE)
CAPTION_RE = re.compile('^\\s*(?:fig(?:ure)?\\.?|table|tab\\.?|图|表)\\s*[0-9]', re.IGNORECASE)
ZOOM = 3.0
MIN_W, MIN_H = (30, 8)
MAX_AREA_RATIO = 0.9
CONTEXT_CHARS = 700
DROP_SECTIONS = {'References', 'Acknowledgment'}

def is_core(p):
    tf = p.get('tagfields') or {}
    return 'A' in (tf.get('priority') or []) or 'Core-A' in (tf.get('reading_tier') or [])

def line_text(line):
    return ''.join((s.get('text', '') for s in line.get('spans', []))).strip()

def line_font_math(line):
    return any((MATH_FONT_RE.search(s.get('font') or '') for s in line.get('spans', [])))

def line_bbox(line):
    r = None
    for s in line.get('spans', []):
        b = s.get('bbox')
        if not b:
            continue
        sr = pymupdf.Rect(b)
        r = sr if r is None else r | sr
    return r

def classify_line(line):
    """返回候选信息 dict 或 None。"""
    text = line_text(line)
    if not text or len(text) < 2 or len(text) > 260:
        return None
    if CAPTION_RE.match(text):
        return None
    bbox = line_bbox(line)
    if bbox is None:
        return None
    has_sym = any((ch in MATH_CHARS for ch in text)) or bool(MATH_WORDS.search(text))
    font_math = line_font_math(line)
    m = EQ_NUM_RE.search(text)
    has_num = bool(m)
    words = len(re.findall('[A-Za-z]{3,}', text))
    if not has_num and (not font_math) and (words > 18):
        return None
    score = int(has_sym) + int(font_math) + int(has_num)
    if score < 2 and (not (has_num and has_sym)):
        return None
    return {'text': text, 'bbox': bbox, 'label': m.group(1) if m else '', 'has_num': has_num, 'has_sym': has_sym, 'font_math': font_math, 'y0': bbox.y0, 'y1': bbox.y1}

def merge_candidates(cands):
    """把同一公式的多行（含独立编号行）合并。"""
    if not cands:
        return []
    cands.sort(key=lambda c: (c['y0'], c['bbox'].x0))
    groups = []
    cur = [cands[0]]
    for c in cands[1:]:
        prev = cur[-1]
        gap = c['y0'] - prev['y1']
        overlap = min(prev['bbox'].x1, c['bbox'].x1) - max(prev['bbox'].x0, c['bbox'].x0)
        min_w = max(1.0, min(prev['bbox'].width, c['bbox'].width))
        same_block = gap <= 10 and (overlap / min_w > 0.15 or c['bbox'].x0 > prev['bbox'].x1 - 10)
        if same_block:
            cur.append(c)
        else:
            groups.append(cur)
            cur = [c]
    groups.append(cur)
    return groups

def group_record(group):
    text = ' '.join((c['text'] for c in group)).strip()
    text = re.sub('\\s+', ' ', text)
    bbox = group[0]['bbox']
    for c in group[1:]:
        bbox |= c['bbox']
    label = next((c['label'] for c in group if c['label']), '')
    return (text, bbox, label)

def page_headings(blocks):
    out = []
    for b in blocks:
        if b.get('type') != 0:
            continue
        lines = b.get('lines') or []
        if not lines:
            continue
        sec = detect_section(line_text(lines[0]))
        if sec:
            out.append((b['bbox'][1], sec))
    out.sort()
    return out

def section_at(headings, fallback, y):
    cur = fallback
    for hy, sec in headings:
        if hy <= y + 2:
            cur = sec
        else:
            break
    return cur

def page_context(blocks, rect, limit=CONTEXT_CHARS):
    texts = []
    for b in blocks:
        if b.get('type') != 0:
            continue
        t = ' '.join((line_text(ln) for ln in b.get('lines') or [])).strip()
        if len(t) < 20:
            continue
        texts.append((pymupdf.Rect(b['bbox']), t))
    texts.sort(key=lambda x: (x[0].y0, x[0].x0))
    before = [t for r, t in texts if r.y1 <= rect.y0 + 2][-2:]
    after = [t for r, t in texts if r.y0 >= rect.y1 - 2][:2]
    ctx = re.sub('\\s+', ' ', ' '.join(before + after)).strip()
    return ctx[:limit]

def render_group(page, rect):
    pw, ph = (page.rect.width, page.rect.height)
    m = 4
    clip = pymupdf.Rect(max(0, rect.x0 - m), max(0, rect.y0 - m), min(pw, rect.x1 + m), min(ph, rect.y1 + m))
    if clip.width < MIN_W or clip.height < MIN_H:
        return None
    if clip.width * clip.height > pw * ph * MAX_AREA_RATIO:
        return None
    return page.get_pixmap(matrix=pymupdf.Matrix(ZOOM, ZOOM), clip=clip)

def process_pdf(pdf_path, out_dir, citekey, max_formulas=30, attach_rel='_知识库/_attachments'):
    os.makedirs(out_dir, exist_ok=True)
    records = []
    accepted = []
    n = 0
    cur_section = 'Front'

    def overlaps(r, pno):
        for p2, r2 in accepted:
            if p2 != pno:
                continue
            inter = pymupdf.Rect(r) & r2
            if inter.is_empty:
                continue
            a = inter.width * inter.height
            if a <= 0:
                continue
            if a / min(r.width * r.height, r2.width * r2.height) > 0.7:
                return True
        return False
    with pymupdf.open(pdf_path) as doc:
        for pno in range(doc.page_count):
            if n >= max_formulas:
                break
            page = doc.load_page(pno)
            blocks = page.get_text('dict').get('blocks') or []
            headings = page_headings(blocks)
            cands = []
            for b in blocks:
                if b.get('type') != 0:
                    continue
                for ln in b.get('lines') or []:
                    c = classify_line(ln)
                    if c:
                        cands.append(c)
            if not cands:
                if headings:
                    cur_section = headings[-1][1]
                continue
            for group in merge_candidates(cands):
                if n >= max_formulas:
                    break
                text, bbox, label = group_record(group)
                if len(text) < 3:
                    continue
                if overlaps(bbox, pno):
                    continue
                pix = render_group(page, bbox)
                if pix is None:
                    continue
                n += 1
                fid = f'eq-{n:02d}'
                out_png = os.path.join(out_dir, fid + '.png')
                try:
                    pix.save(out_png)
                except Exception:
                    n -= 1
                    continue
                accepted.append((pno, pymupdf.Rect(bbox)))
                section = section_at(headings, cur_section, bbox.y0)
                if section in DROP_SECTIONS:
                    try:
                        os.remove(out_png)
                    except OSError:
                        pass
                    continue
                records.append({'id': fid, 'label': label, 'raw_text': text, 'latex': '', 'context': page_context(blocks, bbox), 'section': section, 'page': pno + 1, 'file': f'{attach_rel}/equations/{citekey}/{fid}.png', 'w': int(pix.width), 'h': int(pix.height)})
            if headings:
                cur_section = headings[-1][1]
    return records

def load_existing(index, out_jsonl, cache):
    """统一索引 + 本次 partial，后写的覆盖先写的；缓存里 count=0 表示已完成但无公式。"""
    existing = {}
    for r in read_jsonl(out_jsonl):
        ck = r.get('citekey')
        if not ck:
            continue
        rr = dict(r)
        rr['id'] = str(rr.get('id', '')).split('#', 1)[-1]
        existing.setdefault(ck, []).append(rr)
    cur = None
    partial_keys = set()
    for r in read_jsonl(os.path.join(index, 'formulas.partial.jsonl')):
        ck = r.get('citekey')
        if not ck:
            continue
        if ck != cur:
            existing[ck] = []
            cur = ck
        existing[ck].append(dict(r))
        partial_keys.add(ck)
    for ck, meta in (cache.get('papers') or {}).items():
        if int(meta.get('count', 0)) == 0 and ck not in partial_keys:
            existing[ck] = []
    return existing

DEFAULT_OPTIONS = {'base': C.BASE, 'index': None, 'only_core': False, 'citekey': '', 'limit': 0, 'max_formulas': C.FORMULA_MAX, 'full': False, 'reset_latex': False}

def process_collection(options):
    args = SimpleNamespace(**dict(DEFAULT_OPTIONS, **options))
    base = os.path.abspath(args.base)
    index = os.path.abspath(args.index or C.index_dir(base))
    out_index = os.path.join(index, 'formulas')
    out_jsonl = C.formulas_jsonl(base)
    cache_path = os.path.join(index, 'formulas_cache.json')
    partial_path = os.path.join(index, 'formulas.partial.jsonl')
    att_root = os.path.join(base, C.ATTACH_DIR, 'equations')
    os.makedirs(out_index, exist_ok=True)
    os.makedirs(att_root, exist_ok=True)
    papers = read_jsonl(os.path.join(index, 'papers_all.jsonl'))
    valid_keys = {p['citekey'] for p in papers}
    if args.citekey:
        papers = [p for p in papers if p['citekey'] == args.citekey]
    elif args.only_core:
        papers = [p for p in papers if is_core(p)]
    if args.limit:
        papers = papers[:args.limit]
    cache = read_json(cache_path, {}) or {}
    if args.full or cache.get('_version') != C.FORMULA_VERSION or cache.get('max_formulas') != args.max_formulas:
        cache = {'_version': C.FORMULA_VERSION, 'max_formulas': args.max_formulas, 'papers': {}}
        remove_if_exists(partial_path)
    existing = load_existing(index, out_jsonl, cache)
    prev_latex = {}
    if not args.reset_latex:
        for r in read_jsonl(out_jsonl):
            if r.get('id') and r.get('latex'):
                prev_latex[r.get('citekey'), r['id'], r.get('raw_text')] = r['latex']
    from kbapp.core.progress import Progress
    bar = Progress(len(papers), '公式 ')
    total = reused = rebuilt = 0
    failed = []
    for p in papers:
        ck = p['citekey']
        pdf = p.get('pdf')
        if not pdf:
            bar.update(1, note=f'{ck} 无PDF')
            continue
        full = pdf if os.path.isabs(pdf) else os.path.join(base, pdf)
        if not os.path.exists(full):
            bar.update(1, note=f'{ck} 缺失')
            continue
        st = os.stat(full)
        fp = {'mtime': round(st.st_mtime, 3), 'size': st.st_size}
        prev = (cache.get('papers') or {}).get(ck)
        can_reuse = not args.full and prev and (prev.get('mtime') == fp['mtime']) and (prev.get('size') == fp['size']) and (ck in existing or int(prev.get('count', 0)) == 0)
        if can_reuse:
            recs = existing.get(ck, [])
            reused += 1
        else:
            import hashlib
            revision = hashlib.sha256(str((fp, C.FORMULA_VERSION)).encode()).hexdigest()[:12]
            out_dir = os.path.join(att_root, ck, revision)
            try:
                recs = process_pdf(full, out_dir, ck, args.max_formulas, attach_rel=C.ATTACH_DIR)
                for rec in recs:
                    rec['file'] = f"{C.ATTACH_DIR}/equations/{ck}/{revision}/{rec['id']}.png"
                    rec['image_revision'] = revision
            except Exception as ex:
                failed.append({'citekey': ck, 'error': f'{type(ex).__name__}: {ex}'})
                bar.update(1, note=f'{ck} 失败')
                continue
            rebuilt += 1
            if prev_latex:
                for rec in recs:
                    old_key = (ck, rec['id'], rec.get('raw_text'))
                    if not rec.get('latex') and old_key in prev_latex:
                        rec['latex'] = prev_latex[old_key]
                        rec['latex_status'] = 'legacy_unverified'
            write_json(os.path.join(out_index, ck + '.json'), recs)
            append_jsonl(partial_path, [{'citekey': ck, **r} for r in recs])
            existing[ck] = recs
            cache.setdefault('papers', {})[ck] = {**fp, 'count': len(recs)}
            write_json(cache_path, cache)
        if ck in existing and (not os.path.exists(os.path.join(out_index, ck + '.json'))):
            write_json(os.path.join(out_index, ck + '.json'), existing[ck])
        total += len(existing.get(ck, []))
        bar.update(1, note=f'{ck} {len(existing.get(ck, []))}式')
    bar.close(f'{total} 条')
    for ck in list(existing):
        if ck not in valid_keys:
            existing.pop(ck, None)
    rows = []
    for ck in sorted(existing):
        for r in existing[ck]:
            row = dict(r)
            row['id'] = f"{ck}#{r['id']}"
            row['citekey'] = ck
            rows.append(row)
    write_jsonl(out_jsonl, rows)
    remove_if_exists(partial_path)
    write_json(cache_path, cache)
    report = {'papers': len(papers), 'papers_with_formulas': sum((1 for ck in existing if existing[ck])), 'formulas': total, 'formulas_total_index': len(rows), 'reused': reused, 'rebuilt': rebuilt, 'failed': len(failed), 'cache_papers': len(cache['papers'])}
    print(json.dumps(report, ensure_ascii=False, indent=2))
