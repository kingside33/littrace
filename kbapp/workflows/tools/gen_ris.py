"""kbapp.workflows.tools.gen_ris：由职责边界组织的知识库能力。"""
import argparse
import csv
import json
import os
import re
import sqlite3
import sys
import unicodedata
from kbapp.library.cards import parse_filename
BASE = str(__import__('kbapp.core.paths', fromlist=['Paths']).Paths.from_base().base)
OLD_ZOTERO = os.path.expanduser('~\\Zotero\\zotero.sqlite')
RIS_TYPE = {'journalArticle': 'JOUR', 'conferencePaper': 'CONF', 'thesis': 'THES', 'book': 'BOOK', 'bookSection': 'CHAP', 'preprint': 'GEN', 'report': 'RPRT', 'webpage': 'ELEC'}

def norm_text(s):
    if not s:
        return ''
    s = unicodedata.normalize('NFKC', s)
    s = os.path.splitext(os.path.basename(s))[0]
    s = re.sub('[^\\w\\u4e00-\\u9fff]+', '', s, flags=re.UNICODE)
    return s.lower()

def ro(path):
    c = sqlite3.connect('file:' + path.replace('\\', '/') + '?mode=ro&immutable=1', uri=True)
    c.row_factory = sqlite3.Row
    return c

def load_old_library(path):
    """返回 {归一化文件名: 条目字典}"""
    conn = ro(path)
    types = {r['itemTypeID']: r['typeName'] for r in conn.execute('SELECT itemTypeID, typeName FROM itemTypesCombined')}
    deleted = {r[0] for r in conn.execute('SELECT itemID FROM deletedItems')}
    fields = {}
    for r in conn.execute('\n        SELECT id.itemID, f.fieldName, idv.value\n        FROM itemData id\n        JOIN fieldsCombined f ON f.fieldID = id.fieldID\n        JOIN itemDataValues idv ON idv.valueID = id.valueID\n        '):
        fields.setdefault(r['itemID'], {})[r['fieldName']] = r['value']
    creators = {}
    for r in conn.execute('\n        SELECT ic.itemID, ic.orderIndex, c.firstName, c.lastName, c.fieldMode, ct.creatorType\n        FROM itemCreators ic\n        JOIN creators c ON c.creatorID = ic.creatorID\n        JOIN creatorTypes ct ON ct.creatorTypeID = ic.creatorTypeID\n        ORDER BY ic.itemID, ic.orderIndex\n        '):
        creators.setdefault(r['itemID'], []).append({'last': (r['lastName'] or '').strip(), 'first': (r['firstName'] or '').strip(), 'single': r['fieldMode'] == 1, 'type': r['creatorType']})
    meta = {}
    rows = conn.execute('\n        SELECT ia.parentItemID, ia.path, i.itemTypeID\n        FROM itemAttachments ia\n        JOIN items i ON i.itemID = ia.parentItemID\n        WHERE ia.parentItemID IS NOT NULL AND ia.path IS NOT NULL\n        ').fetchall()
    for r in rows:
        pid = r['parentItemID']
        if pid in deleted:
            continue
        raw = r['path'] or ''
        fname = raw.split(':')[-1].replace('\\', '/').split('/')[-1]
        if not fname.lower().endswith('.pdf'):
            continue
        f = fields.get(pid, {})
        meta[norm_text(fname)] = {'item_id': pid, 'type': types.get(r['itemTypeID'], 'journalArticle'), 'title': f.get('title') or '', 'date': f.get('date') or '', 'abstract': f.get('abstractNote') or '', 'doi': f.get('DOI') or '', 'venue': f.get('publicationTitle') or f.get('proceedingsTitle') or f.get('bookTitle') or f.get('university') or f.get('publisher') or '', 'volume': f.get('volume') or '', 'issue': f.get('issue') or '', 'pages': f.get('pages') or '', 'url': f.get('url') or '', 'language': f.get('language') or '', 'creators': creators.get(pid, [])}
    conn.close()
    return meta

def ris_escape(s):
    return re.sub('\\s+', ' ', (s or '').replace('\n', ' ')).strip()

def to_ris(rec, pdf_rel):
    L = [f"TY  - {RIS_TYPE.get(rec.get('type'), 'GEN')}"]
    L.append(f"TI  - {ris_escape(rec.get('title'))}")
    for c in rec.get('creators') or []:
        if c['type'] != 'author':
            continue
        if c['single'] or not c['first']:
            name = c['last']
        else:
            name = f"{c['last']}, {c['first']}"
        L.append(f'AU  - {ris_escape(name)}')
    if rec.get('date'):
        L.append(f"PY  - {ris_escape(rec['date'])}")
    if rec.get('venue'):
        key = 'T2' if rec.get('type') in ('journalArticle', 'conferencePaper') else 'PB'
        L.append(f"{key}  - {ris_escape(rec['venue'])}")
    if rec.get('volume'):
        L.append(f"VL  - {ris_escape(rec['volume'])}")
    if rec.get('issue'):
        L.append(f"IS  - {ris_escape(rec['issue'])}")
    if rec.get('pages'):
        L.append(f"SP  - {ris_escape(rec['pages'])}")
    if rec.get('doi'):
        L.append(f"DO  - {ris_escape(rec['doi'])}")
    if rec.get('url'):
        L.append(f"UR  - {ris_escape(rec['url'])}")
    if rec.get('abstract'):
        L.append(f"AB  - {ris_escape(rec['abstract'])}")
    L.append(f'N1  - PDF: {pdf_rel}')
    L.append('ER  - ')
    L.append('')
    return '\n'.join(L)

def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default=BASE)
    ap.add_argument('--index', default=str(__import__('kbapp.core.paths', fromlist=['Paths']).Paths.from_base().active_index()))
    ap.add_argument('--old', default=OLD_ZOTERO)
    ap.add_argument('--limit', type=int, default=0)
    args = ap.parse_args(argv)
    base = os.path.abspath(args.base)
    index = os.path.abspath(args.index)
    out_dir = os.path.join(index, 'import')
    os.makedirs(out_dir, exist_ok=True)
    pdfs = [json.loads(line) for line in open(os.path.join(index, 'pdf_inventory.jsonl'), encoding='utf-8') if line.strip()]
    paper_pdf = json.load(open(os.path.join(index, 'paper_pdf_map.json'), encoding='utf-8'))
    matched = set(paper_pdf.values())
    by_qh = {}
    for p in pdfs:
        if p.get('qh'):
            by_qh.setdefault(p['qh'], []).append(p)
    orphans = []
    seen_qh = set()
    for o in pdfs:
        if o['rel'] in matched:
            continue
        qh = o.get('qh')
        if qh:
            group = by_qh.get(qh) or []
            if any((x['rel'] in matched for x in group)):
                continue
            if qh in seen_qh:
                continue
            seen_qh.add(qh)
        parsed = parse_filename(o['name'])
        orphans.append({'citekey': os.path.splitext(o['name'])[0][:48], 'title': parsed['title'], 'authors': parsed['authors'], 'year': parsed['year'], 'pdf': o['rel']})
    orphans.sort(key=lambda r: r['pdf'])
    if args.limit:
        orphans = orphans[:args.limit]
    old_meta = {}
    if os.path.exists(args.old):
        try:
            old_meta = load_old_library(args.old)
            print(f'旧库条目（含 PDF 附件）：{len(old_meta)}', file=sys.stderr)
        except Exception as ex:
            print(f'[warn] 旧库读取失败：{ex}', file=sys.stderr)
    ris_lines = []
    rows = []
    recovered = 0
    filename_only = 0
    for p in orphans:
        key = norm_text(p.get('pdf') or p.get('title') or '')
        rec = old_meta.get(key)
        if rec and rec.get('title'):
            recovered += 1
            src = 'old-zotero'
        else:
            filename_only += 1
            src = 'filename'
            rec = {'type': 'journalArticle', 'title': p.get('title') or os.path.splitext(os.path.basename(p.get('pdf') or ''))[0], 'date': str(p.get('year') or ''), 'creators': [{'last': a, 'first': '', 'single': True, 'type': 'author'} for a in p.get('authors') or []], 'abstract': '', 'doi': '', 'venue': ''}
        ris_lines.append(to_ris(rec, p.get('pdf') or ''))
        rows.append({'citekey': p['citekey'], 'source': src, 'title': rec.get('title', ''), 'authors': '; '.join((c['last'] if c['single'] or not c['first'] else f"{c['last']}, {c['first']}" for c in rec.get('creators') or [])), 'year': p.get('year') or '', 'doi': rec.get('doi', ''), 'pdf': p.get('pdf') or ''})
    ris_path = os.path.join(out_dir, 'orphans.ris')
    with open(ris_path, 'w', encoding='utf-8', newline='\r\n') as fh:
        fh.write(''.join(ris_lines))
    csv_path = os.path.join(out_dir, 'orphans_manifest.csv')
    with open(csv_path, 'w', encoding='utf-8-sig', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=['citekey', 'source', 'title', 'authors', 'year', 'doi', 'pdf'])
        w.writeheader()
        w.writerows(rows)
    enrich = {}
    for p in orphans:
        key = norm_text(p.get('pdf') or p.get('title') or '')
        rec = old_meta.get(key)
        if not (rec and rec.get('title')):
            continue
        enrich[p.get('pdf') or ''] = {'title': rec.get('title'), 'year': (re.search('(1[89]\\d{2}|20\\d{2})', rec.get('date') or '') or [None])[0], 'doi': rec.get('doi'), 'abstract': rec.get('abstract'), 'venue': rec.get('venue'), 'type': rec.get('type'), 'authors': [c['last'] if c['single'] or not c['first'] else f"{c['last']}, {c['first']}" for c in rec.get('creators') or [] if c['type'] == 'author']}
    with open(os.path.join(index, 'old_meta.jsonl'), 'w', encoding='utf-8') as fh:
        for k, v in enrich.items():
            fh.write(json.dumps({'pdf': k, **v}, ensure_ascii=False) + '\n')
    report = {'orphans': len(orphans), 'recovered_from_old_library': recovered, 'filename_only': filename_only, 'ris': os.path.relpath(ris_path, base).replace(os.sep, '/'), 'manifest': os.path.relpath(csv_path, base).replace(os.sep, '/'), 'next_step': '在 Zotero 中：文件 -> 导入 -> 选择 orphans.ris -> 导入到新集合「待整理」。导入后需手动为每条挂载链接文件（N1 字段记录了 PDF 路径）。'}
    with open(os.path.join(out_dir, 'orphans_report.json'), 'w', encoding='utf-8') as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
