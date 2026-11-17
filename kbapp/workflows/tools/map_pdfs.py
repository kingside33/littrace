"""kbapp.workflows.tools.map_pdfs：由职责边界组织的知识库能力。"""
import argparse
import hashlib
import json
import os
import re
import unicodedata
import pymupdf
DEFAULT_BASE = str(__import__('kbapp.core.paths', fromlist=['Paths']).Paths.from_base().base)
SKIP_DIRS = {'00_首页', '10_文献', '20_方向', '30_团队', '40_学者', '_index', '_tools', '.obsidian', '.git'}

def norm_path(p):
    return os.path.normcase(os.path.normpath(p))

def norm_text(s):
    """归一化用于模糊比较：去扩展名、去标点空格、小写、全半角统一"""
    if not s:
        return ''
    s = unicodedata.normalize('NFKC', s)
    s = os.path.splitext(s)[0]
    s = re.sub('[^\\w\\u4e00-\\u9fff]+', '', s, flags=re.UNICODE)
    return s.lower()

def quick_hash(path, chunk=1 << 20):
    """大小 + 首 1MB 的哈希，用于低成本查重"""
    try:
        size = os.path.getsize(path)
        h = hashlib.blake2b(digest_size=16)
        h.update(str(size).encode())
        with open(path, 'rb') as fh:
            h.update(fh.read(chunk))
        return h.hexdigest()
    except OSError:
        return None

def pdf_pages(path):
    """返回页数；0 表示 PDF 结构损坏/无页，-1 表示打不开。"""
    try:
        with pymupdf.open(path) as doc:
            return doc.page_count
    except Exception:
        return -1

def base_stem(name):
    """去掉 _1 / _2 / ' 3' / (1) 这类副本后缀，用于找同名有效副本。"""
    s = os.path.splitext(name or '')[0]
    s = re.sub('[\\s_\\-]*[\\(（]\\d{1,2}[\\)）]\\s*$', '', s)
    s = re.sub('[\\s_\\-]+\\d{1,2}\\s*$', '', s)
    return norm_text(s)

def scan_pdfs(base):
    out = []
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in files:
            if not fn.lower().endswith('.pdf'):
                continue
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, base)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = -1
            out.append({'rel': rel.replace(os.sep, '/'), 'abs': full, 'name': fn, 'size': size, 'mtime': os.path.getmtime(full) if size >= 0 else None, 'pages': pdf_pages(full) if size >= 0 else -1})
    out.sort(key=lambda x: x['rel'])
    return out

def load_papers(index_dir):
    path = os.path.join(index_dir, 'papers.jsonl')
    with open(path, encoding='utf-8') as fh:
        return [json.loads(line) for line in fh if line.strip()]

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default=DEFAULT_BASE)
    ap.add_argument('--index', default=str(__import__('kbapp.core.paths', fromlist=['Paths']).Paths.from_base().active_index()))
    ap.add_argument('--no-hash', action='store_true')
    ap.add_argument('--no-progress', action='store_true')
    args = ap.parse_args(argv)
    base = os.path.abspath(args.base)
    index_dir = os.path.abspath(args.index)
    pdfs = scan_pdfs(base)
    papers = load_papers(index_dir)
    by_abs = {norm_path(p['abs']): p for p in pdfs}
    by_normname = {}
    for p in pdfs:
        by_normname.setdefault(norm_text(p['name']), []).append(p)
    matched_pdf = {}
    paper_pdf = {}
    method_count = {'abs': 0, 'name': 0, 'none': 0}
    for paper in papers:
        hit = None
        att = paper.get('pdf')
        if att and att.get('abs_path'):
            hit = by_abs.get(norm_path(att['abs_path']))
            if hit:
                method_count['abs'] += 1
        if hit is None:
            cands = []
            for a in paper.get('attachments', []):
                raw = a.get('raw_path') or ''
                fname = raw.split(':')[-1].replace('\\', '/').split('/')[-1]
                if fname.lower().endswith('.pdf'):
                    cands.extend(by_normname.get(norm_text(fname), []))
            if len(cands) == 1:
                hit = cands[0]
                method_count['name'] += 1
        if hit:
            paper_pdf[paper['key']] = hit['rel']
            matched_pdf[hit['rel']] = paper['key']
        else:
            method_count['none'] += 1
    orphans = [p for p in pdfs if p['rel'] not in matched_pdf]
    dup_groups = []
    if not args.no_hash:
        from kbapp.core.progress import Progress
        cache_path = os.path.join(index_dir, 'hash_cache.json')
        hcache = {}
        if os.path.exists(cache_path):
            try:
                hcache = json.load(open(cache_path, encoding='utf-8'))
            except Exception:
                hcache = {}
        bar = Progress(len(pdfs), '哈希 ', enabled=not args.no_progress)
        buckets = {}
        new_cache = {}
        for p in pdfs:
            key = p['rel']
            st = hcache.get(key)
            if st and st.get('size') == p['size'] and (st.get('mtime') == p.get('mtime')):
                h = st.get('qh')
            else:
                h = quick_hash(p['abs'])
            if h:
                p['qh'] = h
                buckets.setdefault(h, []).append(p['rel'])
                new_cache[key] = {'size': p['size'], 'mtime': p.get('mtime'), 'qh': h}
            bar.update(1, note=key)
        bar.close(f'已哈希 {len(new_cache)} / {len(pdfs)}')
        dup_groups = [v for v in buckets.values() if len(v) > 1]
        with open(cache_path, 'w', encoding='utf-8') as fh:
            json.dump(new_cache, fh, ensure_ascii=False)
        dup_groups.sort(key=lambda g: (-len(g), g[0]))
    rel_to_pdf = {p['rel']: p for p in pdfs}
    qh_to_valid = {}
    by_stem = {}
    for p in pdfs:
        if p.get('pages', 0) > 0:
            if p.get('qh'):
                qh_to_valid.setdefault(p['qh'], p['rel'])
            by_stem.setdefault(base_stem(p['name']), []).append(p)
    for key, rel in list(paper_pdf.items()):
        p = rel_to_pdf.get(rel)
        if not p or p.get('pages', 0) > 0:
            continue
        new_rel = None
        if p.get('qh') in qh_to_valid:
            new_rel = qh_to_valid[p['qh']]
        else:
            cands = by_stem.get(base_stem(p['name']), [])
            if cands:
                new_rel = max(cands, key=lambda x: (x.get('pages', 0), x.get('size', 0)))['rel']
        if new_rel:
            paper_pdf[key] = new_rel
            matched_pdf.pop(rel, None)
            matched_pdf[new_rel] = key
    orphans = [p for p in pdfs if p['rel'] not in matched_pdf]
    os.makedirs(index_dir, exist_ok=True)
    with open(os.path.join(index_dir, 'pdf_inventory.jsonl'), 'w', encoding='utf-8') as fh:
        for p in pdfs:
            fh.write(json.dumps({k: v for k, v in p.items() if k != 'abs'}, ensure_ascii=False) + '\n')
    with open(os.path.join(index_dir, 'paper_pdf_map.json'), 'w', encoding='utf-8') as fh:
        json.dump(paper_pdf, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(index_dir, 'orphan_pdfs.json'), 'w', encoding='utf-8') as fh:
        json.dump([{'rel': p['rel'], 'size': p['size']} for p in orphans], fh, ensure_ascii=False, indent=2)
    report = {'pdfs_on_disk': len(pdfs), 'papers_in_zotero': len(papers), 'matched': len(paper_pdf), 'match_method': method_count, 'orphan_pdfs': len(orphans), 'duplicate_groups': len(dup_groups), 'duplicate_files': sum((len(g) - 1 for g in dup_groups)), 'orphan_by_top_dir': {}}
    for p in orphans:
        top = p['rel'].split('/')[0] if '/' in p['rel'] else '(根目录)'
        report['orphan_by_top_dir'][top] = report['orphan_by_top_dir'].get(top, 0) + 1
    with open(os.path.join(index_dir, 'map_report.json'), 'w', encoding='utf-8') as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print('\n重复组示例（前 5）:')
    for g in dup_groups[:5]:
        print('  -', ' | '.join(g))
    print('\n孤儿 PDF 示例（前 10）:')
    for p in orphans[:10]:
        print('  -', p['rel'])
