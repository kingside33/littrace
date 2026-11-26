"""kbapp.workflows.tools.build_cards：由职责边界组织的知识库能力。"""
import argparse
import json
import os
import sys
from collections import Counter
from kbapp.library.cards import BASE, DIR_NAME, base_stem, build_ctx, default_figures_block, extract_figure_block, extract_manual_fm, extract_persist, first_sentence, load_citation_maps, load_figures, load_template, make_citekey, merge_duplicate_records, norm_text, parse_filename, pick_directions_with_basis, render, render_citations, tag_fields, write_papers_all

def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default=BASE)
    ap.add_argument('--index', default=str(__import__('kbapp.core.paths', fromlist=['Paths']).Paths.from_base().active_index()))
    ap.add_argument('--lit-dir', default='_知识库/10_文献')
    ap.add_argument('--template', default='_知识库/笔记模板/文献卡片模板.md')
    ap.add_argument('--records-only', action='store_true', help='只刷新 papers_all.jsonl，不渲染卡片（供抽取步骤读取最新文献清单）')
    args = ap.parse_args(argv)
    base = os.path.abspath(args.base)
    index_dir = os.path.abspath(args.index)
    lit_root = os.path.join(base, args.lit_dir)
    tpl_path = os.path.join(base, args.template)
    if not os.path.exists(tpl_path):
        sys.exit(f'找不到模板文件：{tpl_path}')
    tpl = load_template(tpl_path)
    reading = {}
    rc_path = os.path.join(index_dir, 'reading_cards_all.jsonl')
    if os.path.exists(rc_path):
        for line in open(rc_path, encoding='utf-8'):
            if line.strip():
                r = json.loads(line)
                reading[r['citekey']] = r
    try:
        import kbapp.workflows.tools.apply_reading_cards as ARC
    except Exception:
        ARC = None
    papers = [json.loads(line) for line in open(os.path.join(index_dir, 'papers.jsonl'), encoding='utf-8') if line.strip()]
    pdfs = [json.loads(line) for line in open(os.path.join(index_dir, 'pdf_inventory.jsonl'), encoding='utf-8') if line.strip()]
    paper_pdf = json.load(open(os.path.join(index_dir, 'paper_pdf_map.json'), encoding='utf-8'))
    matched_rels = set(paper_pdf.values())
    old_meta = {}
    old_path = os.path.join(index_dir, 'old_meta.jsonl')
    if os.path.exists(old_path):
        for line in open(old_path, encoding='utf-8'):
            if line.strip():
                d = json.loads(line)
                old_meta[d['pdf']] = d
    records = []
    for p in papers:
        if p.get('type') == 'computerProgram' or (p.get('title') or '').strip() == 'Addon Item':
            continue
        rel = paper_pdf.get(p['key'])
        pdf_path, pdf_loc, pdf_ok = (None, None, False)
        if rel:
            pdf_path, pdf_loc, pdf_ok = (rel, 'vault', True)
        elif p.get('pdf') and p['pdf'].get('exists'):
            pdf_path = p['pdf']['abs_path']
            pdf_loc, pdf_ok = ('zotero-storage', True)
        tf, other = tag_fields(p.get('tags') or [])
        authors = p.get('authors') or []
        dirs, basis = pick_directions_with_basis(p, rel or '')
        records.append({'zotero_key': p['key'], 'title': p.get('title') or '', 'authors': authors, 'year': p.get('year'), 'venue': p.get('venue') or '', 'doi': p.get('doi') or '', 'abstract': p.get('abstract') or '', 'directions': dirs, 'direction_basis': basis, 'tagfields': tf, 'other_tags': other, 'metadata_source': 'zotero', 'pdf': pdf_path, 'pdf_location': pdf_loc, 'pdf_exists': pdf_ok, 'item_type': p.get('type'), 'collections': [c['path'] for c in p.get('collections') or []]})
    rel_to_paper = {v: k for k, v in paper_pdf.items()}
    by_qh = {}
    for p in pdfs:
        if p.get('qh'):
            by_qh.setdefault(p['qh'], []).append(p)
    valid_by_stem = {}
    for p in pdfs:
        if p.get('pages', 0) > 0:
            valid_by_stem.setdefault(base_stem(p['name']), []).append(p)

    def effective_pdf(p):
        if p.get('pages', 0) > 0:
            return p
        cands = valid_by_stem.get(base_stem(p['name']), [])
        if cands:
            return max(cands, key=lambda x: (x.get('pages', 0), x.get('size', 0)))
        return p
    orphans_raw = []
    seen_rel = set()
    for p in pdfs:
        if p['rel'] in matched_rels:
            continue
        e = effective_pdf(p)
        if e['rel'] in matched_rels or e['rel'] in seen_rel:
            continue
        seen_rel.add(e['rel'])
        orphans_raw.append(e)
    duplicates = []
    orphans = []
    kept_by_qh = {}
    for o in orphans_raw:
        qh = o.get('qh')
        if qh and qh in by_qh:
            group = by_qh[qh]
            matched_in_group = [x for x in group if x['rel'] in matched_rels]
            if matched_in_group:
                duplicates.append({'rel': o['rel'], 'duplicate_of': matched_in_group[0]['rel'], 'zotero_key': rel_to_paper.get(matched_in_group[0]['rel'])})
                continue
            if qh in kept_by_qh:
                duplicates.append({'rel': o['rel'], 'duplicate_of': kept_by_qh[qh]['rel'], 'zotero_key': None})
                continue
            kept_by_qh[qh] = o
        orphans.append(o)
    for o in orphans:
        parsed = parse_filename(o['name'])
        enrich = old_meta.get(o['rel']) or {}
        if enrich.get('title'):
            title = enrich['title']
            authors = enrich.get('authors') or parsed['authors']
            year = enrich.get('year') or parsed['year']
            source = 'old-zotero'
        else:
            title = parsed['title']
            authors = parsed['authors']
            year = parsed['year']
            source = 'filename'
        if isinstance(year, str) and year.isdigit():
            year = int(year)
        dirs, basis = pick_directions_with_basis({'title': title, 'collections': []}, o['rel'])
        records.append({'zotero_key': None, 'title': title, 'authors': authors, 'year': year, 'venue': enrich.get('venue') or '', 'doi': enrich.get('doi') or '', 'abstract': enrich.get('abstract') or '', 'directions': dirs, 'direction_basis': basis, 'tagfields': {}, 'other_tags': [], 'metadata_source': source, 'pdf': o['rel'], 'pdf_location': 'vault', 'pdf_exists': True, 'item_type': enrich.get('type') or None, 'collections': [], 'filename_parsed': parsed['parsed']})
    before = len(records)
    records, merged_dups = merge_duplicate_records(records)
    from kbapp.core.io import jsonl
    previous = jsonl(os.path.join(index_dir, 'papers_all.jsonl'))
    old_keys = {p.get('zotero_key'): p for p in previous if p.get('zotero_key')}
    old_dois = {p['doi'].lower(): p for p in previous if p.get('doi')}
    old_titles = {norm_text(p.get('title') or ''): p for p in previous if p.get('title')}
    used = {p['citekey'].lower() for p in previous}
    records.sort(key=lambda r: (r['year'] or 0, r['title']))
    for r in records:
        prior = old_keys.get(r.get('zotero_key')) or old_dois.get((r.get('doi') or '').lower()) or old_titles.get(norm_text(r.get('title') or ''))
        r['citekey'] = prior['citekey'] if prior else make_citekey(r['authors'], r['year'], r['title'], used)
        r['card'] = prior.get('card') if prior else None
        r['primary_direction'] = r['directions'][0]
        r['tldr'] = first_sentence(r['abstract'])
    if args.records_only:
        n = write_papers_all(records, index_dir)
        print(json.dumps({'mode': 'records-only', 'papers': n, 'cards_rendered': 0}, ensure_ascii=False, indent=2))
        return
    removed = 0
    cites, cited_by = load_citation_maps(index_dir)
    manifest = {}
    for r in records:
        d = r['primary_direction']
        outdir = os.path.join(lit_root, DIR_NAME[d])
        os.makedirs(outdir, exist_ok=True)
        path = os.path.join(base, r['card']) if r.get('card') else os.path.join(outdir, r['citekey'] + '.md')
        notes, manual, fig_block = ('', {}, '')
        if os.path.exists(path):
            old = open(path, encoding='utf-8').read()
            notes = extract_persist(old)
            manual = extract_manual_fm(old)
            fig_block = extract_figure_block(old)
        if not r.get('pdf'):
            manual_pdf = os.path.join(base, '_知识库', '_attachments', 'pdfs', r['citekey'] + '.pdf')
            if os.path.exists(manual_pdf):
                r['pdf'] = f"_知识库/_attachments/pdfs/{r['citekey']}.pdf"
                r['pdf_location'] = 'manual'
                r['pdf_exists'] = True
        figs = load_figures(index_dir, r['citekey'])
        content = render(tpl, build_ctx(r, manual, notes, fig_block if fig_block else default_figures_block(figs, r['citekey']), render_citations(r['citekey'], cites, cited_by, index_dir)))
        rc = reading.get(r['citekey'])
        if rc and ARC is not None:
            overlaid = ARC.overlay_text(content, rc, {f['id']: f for f in figs})
            if overlaid:
                content = overlaid
        from kbapp.library.generated import write_generated
        write_generated(path, content)
        manifest[r['citekey']] = {'path': os.path.relpath(path, base).replace(os.sep, '/'), 'direction': d, 'pdf': r['pdf'], 'metadata_source': r['metadata_source'], 'year': r['year'], 'title': r['title']}
    with open(os.path.join(index_dir, 'cards_manifest.json'), 'w', encoding='utf-8') as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(index_dir, 'papers_all.jsonl'), 'w', encoding='utf-8') as fh:
        for r in records:
            fh.write(json.dumps({'citekey': r['citekey'], 'zotero_key': r['zotero_key'], 'title': r['title'], 'authors': r['authors'], 'year': r['year'], 'venue': r['venue'], 'doi': r['doi'], 'abstract': r['abstract'], 'tldr': r['tldr'], 'directions': r['directions'], 'primary_direction': r['primary_direction'], 'tags': sorted([f'{k}:{v}' for k, vs in r['tagfields'].items() for v in vs] + r['other_tags']), 'metadata_source': r['metadata_source'], 'pdf': r['pdf'], 'pdf_exists': r['pdf_exists'], 'collections': r['collections'], 'tagfields': {k: sorted(set(v)) for k, v in r['tagfields'].items()}, 'card': manifest[r['citekey']]['path']}, ensure_ascii=False) + '\n')
    by_dir = Counter((r['primary_direction'] for r in records))
    stats = {'cards_total': len(records), 'from_zotero': sum((1 for r in records if r['metadata_source'] == 'zotero')), 'from_old_zotero': sum((1 for r in records if r['metadata_source'] == 'old-zotero')), 'from_filename': sum((1 for r in records if r['metadata_source'] == 'filename')), 'duplicate_pdfs_skipped': len(duplicates), 'duplicate_records_merged': len(merged_dups), 'records_before_merge': before, 'stale_cards_removed': removed, 'with_abstract': sum((1 for r in records if r['abstract'])), 'by_direction': {DIR_NAME[k]: v for k, v in sorted(by_dir.items())}, 'filename_unparsed': sum((1 for r in records if r['metadata_source'] == 'filename' and (not r.get('filename_parsed'))))}
    with open(os.path.join(index_dir, 'cards_stats.json'), 'w', encoding='utf-8') as fh:
        json.dump(stats, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(index_dir, 'duplicate_pdfs.json'), 'w', encoding='utf-8') as fh:
        json.dump(duplicates, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(index_dir, 'duplicate_cards.json'), 'w', encoding='utf-8') as fh:
        json.dump(merged_dups, fh, ensure_ascii=False, indent=2)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
