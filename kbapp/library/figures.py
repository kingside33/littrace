"""kbapp.workflows.tools.extract_figures：由职责边界组织的知识库能力。"""
import json
import os
import re
from types import SimpleNamespace
import pymupdf
import kbapp.core.config as C
from kbapp.library.chunk import detect_section
from kbapp.core.io import append_jsonl, read_json, read_jsonl, remove_if_exists, write_json, write_jsonl
CAP_RE = re.compile('^\\s*(?:(fig(?:ure)?\\.?)|(table|tab\\.?)|(图)|(表))\\s*([0-9]+(?:[-–.][0-9]+)?)\\s*[.:：、]?\\s*', re.IGNORECASE)
MIN_W, MIN_H = (90, 60)
MAX_FIG_AREA_RATIO = 0.92
ZOOM = 2.0
CONTEXT_CHARS = 700

def is_core(p):
    tf = p.get('tagfields') or {}
    return 'A' in (tf.get('priority') or []) or 'Core-A' in (tf.get('reading_tier') or [])

def caption_kind(m):
    if m.group(1) or m.group(3):
        return 'figure'
    return 'table'

def _first_line(block):
    lines = block.get('lines') or []
    if not lines:
        return ''
    return ''.join((s.get('text', '') for s in lines[0].get('spans', [])))

def find_captions(page):
    """返回 [(kind, label, caption_text, rect)]"""
    out = []
    d = page.get_text('dict')
    for block in d.get('blocks', []):
        if block.get('type') != 0:
            continue
        lines = block.get('lines') or []
        if not lines:
            continue
        first = _first_line(block)
        m = CAP_RE.match(first)
        if not m:
            continue
        text = ' '.join((''.join((s.get('text', '') for s in ln.get('spans', []))) for ln in lines)).strip()
        if len(text) < 8 or len(text) > 600:
            continue
        kind = caption_kind(m)
        if kind == 'table' and (len(lines) > 2 or len(text) > 120):
            continue
        if kind == 'figure' and len(lines) > 8:
            continue
        out.append((kind, m.group(0).strip(), re.sub('\\s+', ' ', text), pymupdf.Rect(block['bbox'])))
    out.sort(key=lambda x: (x[3].y0, x[3].x0))
    return out

def _near(r, cap, kind):
    """r 是否紧邻标题（figure 在上方，table 在下方），且水平有重叠"""
    if not (r.x1 > cap.x0 - 30 and r.x0 < cap.x1 + 30):
        return False
    if kind == 'figure':
        return r.y1 <= cap.y0 + 16 and cap.y0 - r.y1 < 150
    return r.y0 >= cap.y1 - 16 and r.y0 - cap.y1 < 60

def find_region(page, cap_rect, kind):
    """返回最可能的图/表区域矩形；找不到返回 None。"""
    pw, ph = (page.rect.width, page.rect.height)
    page_area = pw * ph
    cands = []
    try:
        for img in page.get_images(full=True):
            try:
                rects = page.get_image_rects(img[0])
            except Exception:
                continue
            for r in rects:
                if r.width < MIN_W or r.height < MIN_H:
                    continue
                if r.width * r.height > page_area * MAX_FIG_AREA_RATIO:
                    continue
                if _near(r, cap_rect, kind):
                    cands.append(r)
    except Exception:
        pass
    drs = []
    try:
        for d in page.get_drawings():
            r = d.get('rect')
            if r is None:
                continue
            if r.width < 3 or r.height < 3:
                continue
            if r.width * r.height > page_area * MAX_FIG_AREA_RATIO:
                continue
            if _near(r, cap_rect, kind):
                drs.append(r)
    except Exception:
        pass
    if drs:
        u = pymupdf.Rect(drs[0])
        for r in drs[1:]:
            u |= r
        if u.width >= MIN_W and u.height >= MIN_H:
            cands.append(u)
    if cands:
        best = max(cands, key=lambda r: r.width * r.height)
        return pymupdf.Rect(best)
    if kind == 'table':
        return None
    try:
        d = page.get_text('dict')
        tops = []
        for b in d.get('blocks', []):
            if b.get('type') != 0:
                continue
            r = pymupdf.Rect(b['bbox'])
            if not (r.x1 > cap_rect.x0 - 30 and r.x0 < cap_rect.x1 + 30):
                continue
            if kind == 'figure' and r.y1 <= cap_rect.y0 - 2:
                tops.append(r.y1)
            if kind == 'table' and r.y0 >= cap_rect.y1 + 2:
                tops.append(r.y0)
        if tops:
            if kind == 'figure':
                top = max(tops) + 4
                bot = cap_rect.y0 - 2
            else:
                top = cap_rect.y1 + 2
                bot = min(tops) - 4
            if bot - top >= MIN_H:
                return pymupdf.Rect(cap_rect.x0 - 10, top, cap_rect.x1 + 10, bot)
    except Exception:
        pass
    return None

def render_region(page, rect):
    pw, ph = (page.rect.width, page.rect.height)
    m = 6
    clip = pymupdf.Rect(max(m, rect.x0 - m), max(m, rect.y0 - m), min(pw - m, rect.x1 + m), min(ph - m, rect.y1 + m))
    if clip.width < MIN_W or clip.height < MIN_H:
        return None
    return page.get_pixmap(matrix=pymupdf.Matrix(ZOOM, ZOOM), clip=clip)

def label_num(label):
    m = re.search('([0-9]+(?:[-–.][0-9]+)?)', label or '')
    return m.group(1) if m else ''

def page_headings(blocks):
    """[(y0, section)]，按 y 排序。"""
    out = []
    for b in blocks:
        if b.get('type') != 0:
            continue
        sec = detect_section(_first_line(b))
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
    """取图注上方最近 2 块、下方最近 2 块正文，拼成检索上下文。"""
    texts = []
    for b in blocks:
        if b.get('type') != 0:
            continue
        t = ' '.join((''.join((s.get('text', '') for s in ln.get('spans', []))) for ln in b.get('lines') or [])).strip()
        if len(t) < 20:
            continue
        texts.append((pymupdf.Rect(b['bbox']), t))
    texts.sort(key=lambda x: (x[0].y0, x[0].x0))
    before = [t for r, t in texts if r.y1 <= rect.y0 + 2][-2:]
    after = [t for r, t in texts if r.y0 >= rect.y1 - 2][:2]
    ctx = ' '.join(before + after)
    ctx = re.sub('\\s+', ' ', ctx).strip()
    return ctx[:limit]

def process_pdf(pdf_path, out_dir, citekey, max_figures=12, attach_rel='_知识库/_attachments'):
    os.makedirs(out_dir, exist_ok=True)
    records = []
    accepted = []
    seen_labels = set()
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
            page = doc.load_page(pno)
            caps = find_captions(page)
            if not caps:
                continue
            blocks = page.get_text('dict').get('blocks') or []
            headings = page_headings(blocks)
            for kind, label, text, rect in caps:
                if n >= max_figures:
                    break
                lk = (pno, kind, label_num(label))
                if lk[2] and lk in seen_labels:
                    continue
                region = find_region(page, rect, kind)
                if region is None:
                    continue
                if overlaps(region, pno):
                    continue
                pix = render_region(page, region)
                if pix is None:
                    continue
                n += 1
                fid = f'fig-{n:02d}'
                out_png = os.path.join(out_dir, fid + '.png')
                try:
                    pix.save(out_png)
                except Exception:
                    n -= 1
                    continue
                accepted.append((pno, pymupdf.Rect(region)))
                if lk[2]:
                    seen_labels.add(lk)
                section = section_at(headings, cur_section, rect.y0)
                records.append({'id': fid, 'kind': kind, 'label': label, 'caption': text, 'page': pno + 1, 'section': section, 'context': page_context(blocks, rect), 'file': f'{attach_rel}/figures/{citekey}/{fid}.png', 'w': int(pix.width), 'h': int(pix.height)})
            if n >= max_figures:
                break
            if headings:
                cur_section = headings[-1][1]
    return records

def load_existing(index, fig_jsonl, cache):
    """统一索引 + 本次 partial，后写的覆盖先写的；缓存里 count=0 表示已完成但无图。"""
    existing = {}
    for r in read_jsonl(fig_jsonl):
        ck = r.get('citekey')
        if not ck:
            continue
        rr = dict(r)
        rr['id'] = str(rr.get('id', '')).split('#', 1)[-1]
        existing.setdefault(ck, []).append(rr)
    cur = None
    partial_keys = set()
    for r in read_jsonl(os.path.join(index, 'figures.partial.jsonl')):
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

DEFAULT_OPTIONS = {'base': C.BASE, 'index': None, 'only_core': False, 'citekey': '', 'limit': 0, 'max_figures': C.FIGURE_MAX, 'full': False}

def process_collection(options):
    args = SimpleNamespace(**dict(DEFAULT_OPTIONS, **options))
    base = os.path.abspath(args.base)
    index = os.path.abspath(args.index or C.index_dir(base))
    fig_index = os.path.join(index, 'figures')
    fig_jsonl = C.figures_jsonl(base)
    cache_path = os.path.join(index, 'figures_cache.json')
    partial_path = os.path.join(index, 'figures.partial.jsonl')
    att_root = os.path.join(base, C.ATTACH_DIR, 'figures')
    os.makedirs(fig_index, exist_ok=True)
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
    if args.full or cache.get('_version') != C.FIGURE_VERSION or cache.get('max_figures') != args.max_figures:
        cache = {'_version': C.FIGURE_VERSION, 'max_figures': args.max_figures, 'papers': {}}
        remove_if_exists(partial_path)
    existing = load_existing(index, fig_jsonl, cache)
    from kbapp.core.progress import Progress
    bar = Progress(len(papers), '图表 ')
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
            revision = hashlib.sha256(str((fp, C.FIGURE_VERSION)).encode()).hexdigest()[:12]
            out_dir = os.path.join(att_root, ck, revision)
            try:
                recs = process_pdf(full, out_dir, ck, args.max_figures, attach_rel=C.ATTACH_DIR)
                for rec in recs:
                    rec['file'] = f"{C.ATTACH_DIR}/figures/{ck}/{revision}/{rec['id']}.png"
                    rec['image_revision'] = revision
            except Exception as ex:
                failed.append({'citekey': ck, 'error': f'{type(ex).__name__}: {ex}'})
                bar.update(1, note=f'{ck} 失败')
                continue
            rebuilt += 1
            write_json(os.path.join(fig_index, ck + '.json'), recs)
            append_jsonl(partial_path, [{'citekey': ck, **r} for r in recs])
            existing[ck] = recs
            cache.setdefault('papers', {})[ck] = {**fp, 'count': len(recs)}
            write_json(cache_path, cache)
        if ck in existing and (not os.path.exists(os.path.join(fig_index, ck + '.json'))):
            write_json(os.path.join(fig_index, ck + '.json'), existing[ck])
        total += len(existing.get(ck, []))
        bar.update(1, note=f'{ck} {len(existing.get(ck, []))}图')
    bar.close(f'{total} 张')
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
    write_jsonl(fig_jsonl, rows)
    remove_if_exists(partial_path)
    write_json(cache_path, cache)
    report = {'papers': len(papers), 'papers_with_figures': sum((1 for ck in existing if existing[ck])), 'figures': total, 'figures_total_index': len(rows), 'reused': reused, 'rebuilt': rebuilt, 'failed': len(failed), 'cache_papers': len(cache['papers'])}
    print(json.dumps(report, ensure_ascii=False, indent=2))
