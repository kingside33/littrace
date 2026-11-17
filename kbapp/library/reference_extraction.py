"""kbapp.workflows.tools.extract_refs：由职责边界组织的知识库能力。"""
import json
import os
import re
from types import SimpleNamespace
import pymupdf
import kbapp.core.config as C
from kbapp.library.chunk import extract_pdf
from kbapp.core.io import append_jsonl, read_json, read_jsonl, remove_if_exists, write_json, write_jsonl
HEAD_RE = re.compile('(?im)^\\s*(?:\\d+\\.?\\s*)?(references|bibliography|参考文献|引用文献)\\s*$')
APPENDIX_RE = re.compile('(?im)^\\s*(?:appendix|appendices|supplementary|acknowledg\\w*|作者简介|附录)\\b')
NUM_START_RE = re.compile('(?m)^\\s*(?:\\[(\\d{1,3})\\]|(\\d{1,3})[.)、]\\s|（(\\d{1,3})）)\\s*')
AUTHOR_START_RE = re.compile("\\n(?=[A-Z][A-Za-z'’\\-]{1,30},\\s*(?:[A-Z]\\.|[A-Z][a-z]+))")
DOI_RE = re.compile('10\\.\\d{4,9}/[-._;()/:A-Za-z0-9]+')
YEAR_RE = re.compile('\\b(19\\d{2}|20\\d{2})\\b')
PAREN_YEAR_RE = re.compile('[\\(（]\\s*((?:19|20)\\d{2})[a-z]?\\s*[\\)）]')
QUOTED_TITLE_RE = re.compile('[\\"“](.{8,300}?)[\\"”]')
CN_TITLE_RE = re.compile('[.．]\\s*(.{4,200}?)\\s*\\[[JCDMRSPN]\\]')
APA_TITLE_RE = re.compile('[\\(（]\\s*(?:19|20)\\d{2}[a-z]?\\s*[\\)）][.．]\\s*(.{8,300}?)(?:[.．]\\s|$)')
GENERIC_TITLE_RE = re.compile('(?:19|20)\\d{2}[a-z]?[.,)]\\s*(.{8,300}?)[.．]\\s')
MAX_REFS = 400

def is_core(p):
    tf = p.get('tagfields') or {}
    return 'A' in (tf.get('priority') or []) or 'Core-A' in (tf.get('reading_tier') or [])

def refs_text(pdf_path):
    """返回参考文献段原文；找不到返回空串。"""
    parts = []
    try:
        for _page, sec, text in extract_pdf(pdf_path):
            if sec == 'References':
                parts.append(text)
    except Exception:
        parts = []
    if not parts:
        try:
            with pymupdf.open(pdf_path) as doc:
                start = None
                for pno in range(doc.page_count - 1, max(-1, doc.page_count - 60), -1):
                    text = doc.load_page(pno).get_text('text')
                    m = HEAD_RE.search(text)
                    if m:
                        start = (pno, m.start())
                        break
                if start:
                    parts = [doc.load_page(start[0]).get_text('text')[start[1]:]]
                    for pno in range(start[0] + 1, doc.page_count):
                        parts.append(doc.load_page(pno).get_text('text'))
        except Exception:
            return ''
    text = '\n'.join(parts)
    m = APPENDIX_RE.search(text)
    if m:
        text = text[:m.start()]
    return text.strip()

def split_entries(text):
    if not text:
        return []
    text = text.replace('\r', '')
    text = re.sub('-\\n(?=[a-z])', '', text)
    marks = list(NUM_START_RE.finditer(text))
    if len(marks) >= 5:
        entries = []
        for i, m in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
            entries.append(text[m.start():end])
    else:
        entries = re.split('\\n\\s*\\n', text)
        if len(entries) < 5:
            entries = AUTHOR_START_RE.split(text)
    out = []
    for e in entries:
        e = re.sub('\\s+', ' ', e).strip()
        e = NUM_START_RE.sub('', e, count=1).strip()
        if len(e) < 30:
            continue
        if not YEAR_RE.search(e):
            continue
        out.append(e)
        if len(out) >= MAX_REFS:
            break
    return out

def parse_entry(raw):
    doi = ''
    m = DOI_RE.search(raw)
    if m:
        doi = m.group(0).rstrip('.,;)]}')
    year = None
    m = PAREN_YEAR_RE.search(raw)
    if m:
        year = int(m.group(1))
    else:
        m = YEAR_RE.search(raw)
        if m:
            year = int(m.group(1))
    cut = m.start() if m else len(raw)
    authors = raw[:cut]
    authors = re.sub('^\\s*(?:\\[[0-9]+\\]|\\d+[.)、])\\s*', '', authors).strip(' .,;')
    if len(authors) > 300:
        authors = authors[:300]
    title = ''
    for rx in (QUOTED_TITLE_RE, CN_TITLE_RE, APA_TITLE_RE, GENERIC_TITLE_RE):
        m = rx.search(raw)
        if m:
            cand = m.group(1).strip(' .,;')
            if 6 <= len(cand) <= 300:
                title = cand
                break
    if not title and authors:
        tail = raw[len(authors):]
        tail = re.sub('^[\\s.,;:()\\d]+', '', tail)
        cand = re.split('[.．]\\s', tail, maxsplit=1)[0].strip()
        if 6 <= len(cand) <= 300:
            title = cand
    venue = ''
    if title and title in raw:
        rest = raw.split(title, 1)[1]
        rest = re.sub('^[\\s.,;:]+', '', rest)
        venue = rest[:120]
    return {'doi': doi.lower(), 'year': year, 'authors': authors, 'title': title, 'venue': venue, 'raw': raw}

def process_pdf(pdf_path, citekey):
    text = refs_text(pdf_path)
    entries = split_entries(text)
    out = []
    for i, raw in enumerate(entries, 1):
        rec = parse_entry(raw)
        rec['ref_index'] = i
        rec['src_citekey'] = citekey
        out.append(rec)
    return out

def load_existing(index, out_jsonl, cache):
    existing = {}
    for r in read_jsonl(out_jsonl):
        ck = r.get('src_citekey')
        if ck:
            existing.setdefault(ck, []).append(dict(r))
    cur = None
    partial_keys = set()
    for r in read_jsonl(os.path.join(index, 'references.partial.jsonl')):
        ck = r.get('src_citekey')
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

DEFAULT_OPTIONS = {'base': C.BASE, 'index': None, 'only_core': False, 'citekey': '', 'limit': 0, 'full': False}

def process_collection(options):
    args = SimpleNamespace(**dict(DEFAULT_OPTIONS, **options))
    base = os.path.abspath(args.base)
    index = os.path.abspath(args.index or C.index_dir(base))
    out_index = os.path.join(index, 'references')
    out_jsonl = C.references_jsonl(base)
    cache_path = os.path.join(index, 'refs_cache.json')
    partial_path = os.path.join(index, 'references.partial.jsonl')
    os.makedirs(out_index, exist_ok=True)
    papers = read_jsonl(os.path.join(index, 'papers_all.jsonl'))
    valid_keys = {p['citekey'] for p in papers}
    if args.citekey:
        papers = [p for p in papers if p['citekey'] == args.citekey]
    elif args.only_core:
        papers = [p for p in papers if is_core(p)]
    if args.limit:
        papers = papers[:args.limit]
    cache = read_json(cache_path, {}) or {}
    if args.full or cache.get('_version') != C.REF_VERSION:
        cache = {'_version': C.REF_VERSION, 'papers': {}}
        remove_if_exists(partial_path)
    existing = load_existing(index, out_jsonl, cache)
    from kbapp.core.progress import Progress
    bar = Progress(len(papers), '参考文献 ')
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
            try:
                recs = process_pdf(full, ck)
            except Exception as ex:
                failed.append({'citekey': ck, 'error': f'{type(ex).__name__}: {ex}'})
                bar.update(1, note=f'{ck} 失败')
                continue
            rebuilt += 1
            write_json(os.path.join(out_index, ck + '.json'), recs)
            append_jsonl(partial_path, recs)
            existing[ck] = recs
            cache.setdefault('papers', {})[ck] = {**fp, 'count': len(recs)}
            write_json(cache_path, cache)
        if ck in existing and (not os.path.exists(os.path.join(out_index, ck + '.json'))):
            write_json(os.path.join(out_index, ck + '.json'), existing[ck])
        total += len(existing.get(ck, []))
        bar.update(1, note=f'{ck} {len(existing.get(ck, []))}条')
    bar.close(f'{total} 条')
    for ck in list(existing):
        if ck not in valid_keys:
            existing.pop(ck, None)
    rows = []
    for ck in sorted(existing):
        rows.extend(existing[ck])
    write_jsonl(out_jsonl, rows)
    remove_if_exists(partial_path)
    write_json(cache_path, cache)
    with_doi = sum((1 for r in rows if r.get('doi')))
    with_title = sum((1 for r in rows if r.get('title')))
    report = {'papers': len(papers), 'papers_with_refs': sum((1 for ck in existing if existing[ck])), 'refs': total, 'refs_total_index': len(rows), 'with_doi': with_doi, 'with_title': with_title, 'reused': reused, 'rebuilt': rebuilt, 'failed': len(failed), 'cache_papers': len(cache['papers'])}
    print(json.dumps(report, ensure_ascii=False, indent=2))
