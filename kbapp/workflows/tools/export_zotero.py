"""kbapp.workflows.tools.export_zotero：由职责边界组织的知识库能力。"""
import argparse
import json
import os
import sys
from collections import Counter
from kbapp.integrations.zotero import DEFAULT_BASE, DEFAULT_ZOTERO_DIR, build_record, fill_attachments, fill_collections, fill_creators, fill_fields, fill_tags, load_collections, load_items, ro_connect

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--zotero-dir', default=DEFAULT_ZOTERO_DIR)
    ap.add_argument('--base', default=DEFAULT_BASE)
    ap.add_argument('--out', default=str(__import__('kbapp.core.paths', fromlist=['Paths']).Paths.from_base().active_index()))
    args = ap.parse_args(argv)
    zdir = args.zotero_dir
    db = os.path.join(zdir, 'zotero.sqlite')
    if not os.path.exists(db):
        sys.exit('找不到 zotero.sqlite: ' + db)
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)
    conn = ro_connect(db)
    collections = load_collections(conn)
    papers = load_items(conn)
    fill_fields(conn, papers)
    fill_creators(conn, papers)
    fill_tags(conn, papers)
    fill_collections(conn, papers, collections)
    fill_attachments(conn, papers, args.base, zdir)
    conn.close()
    records = [build_record(p) for p in papers]
    records.sort(key=lambda r: (r.get('year') or 0, r['key']))
    with open(os.path.join(out_dir, 'papers.jsonl'), 'w', encoding='utf-8') as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + '\n')
    colls = [c for c in collections.values() if not c['deleted']]
    colls.sort(key=lambda c: c['path'])
    with open(os.path.join(out_dir, 'collections.json'), 'w', encoding='utf-8') as fh:
        json.dump(colls, fh, ensure_ascii=False, indent=2)
    stats = {}
    stats['items_total'] = len(records)
    stats['by_type'] = dict(Counter((r['type'] for r in records)))
    stats['with_doi'] = sum((1 for r in records if r.get('doi')))
    stats['with_abstract'] = sum((1 for r in records if r.get('abstract')))
    stats['with_year'] = sum((1 for r in records if r.get('year')))
    stats['with_pdf'] = sum((1 for r in records if r.get('pdf')))
    stats['pdf_exists'] = sum((1 for r in records if r.get('pdf') and r['pdf']['exists']))
    stats['collections'] = len(colls)
    stats['tags_unique'] = len({t for r in records for t in r['tags']})
    stats['tag_prefixes'] = dict(Counter((t.split(':', 1)[0] for r in records for t in r['tags'] if ':' in t)))
    stats['authors_unique'] = len({a for r in records for a in r['authors']})
    stats['year_range'] = [min((r['year'] for r in records if r.get('year')), default=None), max((r['year'] for r in records if r.get('year')), default=None)]
    with open(os.path.join(out_dir, 'zotero_stats.json'), 'w', encoding='utf-8') as fh:
        json.dump(stats, fh, ensure_ascii=False, indent=2)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print('\n写出:')
    for n in ('papers.jsonl', 'collections.json', 'zotero_stats.json'):
        p = os.path.join(out_dir, n)
        print(f'  {p}  ({os.path.getsize(p):,} bytes)')
