"""kbapp.library.sources：由职责边界组织的知识库能力。"""
from __future__ import annotations
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import quote
from kbapp.core.io import atomic, digest, jsonl
from kbapp.core.markers import AUTO_END, AUTO_START, PROJECT, STAGES

class Plain(HTMLParser):

    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, value):
        self.parts.append(value)

    def handle_endtag(self, tag):
        if tag in {'p', 'div', 'li', 'br', 'tr', 'h1', 'h2', 'h3'}:
            self.parts.append('\n')

def plaintext(value):
    p = Plain()
    p.feed(value or '')
    return html.unescape(''.join(p.parts)).strip()

def norm(value):
    return re.sub('[^\\w\\u4e00-\\u9fff]', '', (value or '').lower())

def identity(p):
    if p.get('id'):
        return p['id']
    if p.get('zotero_key'):
        return f"z:{int(p.get('library_id', 1))}:" + p['zotero_key']
    if p.get('doi'):
        return 'doi:' + p['doi'].lower().replace('https://doi.org/', '').strip()
    return 'local:' + digest(p.get('pdf') or norm(p.get('title')))[:24]

def snapshot_zotero(zdir):
    path = Path(zdir) / 'zotero.sqlite'
    src = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=10)
    dst = sqlite3.connect(':memory:')
    try:
        src.backup(dst)
    finally:
        src.close()
    dst.row_factory = sqlite3.Row
    return dst

def zotero_sources(db, key_map):
    result = []
    deleted = {r[0] for r in db.execute('SELECT itemID FROM deletedItems')}
    for r in db.execute('SELECT n.*,i.key,i.libraryID,i.version,i.dateModified FROM itemNotes n JOIN items i ON n.itemID=i.itemID'):
        if r['itemID'] in deleted:
            continue
        parent = db.execute('SELECT key FROM items WHERE itemID=?', (r['parentItemID'],)).fetchone()
        paper = key_map.get((r['libraryID'], parent[0])) if parent else None
        text = plaintext(r['note'])
        if text or r['note'] is not None:
            result.append({'id': 'zotero-note:' + ('' if r['libraryID'] == 1 else str(r['libraryID']) + ':') + r['key'], 'paper_id': paper['id'] if paper else '', 'library_id': r['libraryID'], 'zotero_key': r['key'], 'raw_zotero': dict(r), 'kind': 'zotero_note', 'text': text, 'title': paper['title'] if paper else 'Zotero 独立笔记', 'url': 'zotero://select/library/items/' + r['key'], 'origin': 'personal'})
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'itemAnnotations' not in tables:
        return result
    for row in db.execute('SELECT a.*,i.key,i.libraryID,i.version,i.dateModified FROM itemAnnotations a JOIN items i ON a.itemID=i.itemID'):
        r = dict(row)
        if r['itemID'] in deleted or r.get('parentItemID') in deleted:
            continue
        att = db.execute('SELECT i.key,a.parentItemID FROM itemAttachments a JOIN items i ON i.itemID=a.itemID WHERE a.itemID=?', (r.get('parentItemID'),)).fetchone()
        if not att:
            continue
        parent = db.execute('SELECT key FROM items WHERE itemID=?', (att['parentItemID'],)).fetchone()
        paper = key_map.get((r['libraryID'], parent[0])) if parent else None
        if not paper:
            continue
        pos = read_json_string(r.get('position'), {})
        page = int(pos.get('pageIndex', 0)) + 1
        text = '\n'.join((x for x in [r.get('text'), r.get('comment')] if x))
        if text or pos:
            result.append({'id': 'zotero-annotation:' + ('' if r['libraryID'] == 1 else str(r['libraryID']) + ':') + r['key'], 'paper_id': paper['id'], 'library_id': r['libraryID'], 'zotero_key': r['key'], 'raw_zotero': r, 'kind': 'zotero_annotation', 'text': text, 'title': paper['title'], 'page': page, 'page_label': r.get('pageLabel'), 'position': pos, 'origin': 'personal', 'url': f"zotero://open-pdf/library/items/{att['key']}?page={page}&annotation={r['key']}"})
    return result

def read_json_string(value, default):
    try:
        return json.loads(value or '')
    except ValueError:
        return default

def sync_library(store, index, source_base=None):
    import kbapp.library.cards as cards
    import kbapp.integrations.zotero as exporter
    old = jsonl(store.active_dir() / 'papers_all.jsonl')
    by_key = {(int(p.get('library_id', 1)), p['zotero_key']): p for p in old if p.get('zotero_key')}
    registered = store.papers()
    for p in registered.values():
        if p.get('kind') == 'library' and p.get('zotero_key'):
            key = (int(p.get('library_id', 1)), p['zotero_key'])
            by_key[key] = dict(by_key.get(key, {}), **p)
    legacy_by_key = {p['citekey']: p for p in registered.values() if p.get('citekey')}
    records = {identity(p): dict(p, id=identity(p), kind='library') for p in old}
    used = {p['citekey'].lower() for p in [*old, *by_key.values()] if p.get('citekey')}
    warnings = []
    notes = []
    try:
        zdir = store.settings()['zotero_dir']
        db = snapshot_zotero(zdir)
        try:
            collections = exporter.load_collections(db)
            data = exporter.load_items(db)
            exporter.fill_fields(db, data)
            exporter.fill_creators(db, data)
            exporter.fill_tags(db, data)
            exporter.fill_collections(db, data, collections)
            exporter.fill_attachments(db, data, str(source_base or store.base), zdir)
            raw = [exporter.build_record(p) for p in data]
            raw = [p for p in raw if p.get('title') and p.get('type') != 'computerProgram' and (p['title'].strip() != 'Addon Item')]
            for p in raw:
                prev = by_key.get((p['library_id'], p['key']))
                r = dict(prev or {})
                for field in ('openalex_id', 'refs', 'cited_by', 'citation_count', 'citation_source', 'citation_updated'):
                    if field in registered.get(r.get('id') or identity(r), {}):
                        r[field] = registered[r.get('id') or identity(r)][field]
                r.update({k: p.get(k) for k in ('title', 'authors', 'year', 'venue', 'doi', 'abstract')})
                r.update(zotero_key=p['key'], kind='library', metadata_source='zotero', source_status='active')
                r.update(library_id=p['library_id'], zotero_version=p.get('version'), date_added=p.get('date_added'), date_modified=p.get('date_modified'), raw_zotero=p['raw_zotero'], raw_zotero_source=p['raw_zotero_source'], creators=p.get('creators', []), attachments=p.get('attachments', []), tags=p.get('tags', []))
                for field in ('type', 'volume', 'issue', 'pages', 'extra', 'url', 'date', 'language', 'publisher', 'conference', 'isbn', 'issn'):
                    r[field] = p.get(field)
                r['id'] = identity(prev) if prev else identity(r)
                r['citekey'] = (prev or {}).get('citekey') or cards.make_citekey(p.get('authors') or [], p.get('year'), p['title'], used)
                r['directions'] = r.get('directions') or cards.pick_directions(p, '')
                r['primary_direction'] = r.get('primary_direction') or r['directions'][0]
                r['tagfields'], _ = cards.tag_fields(p.get('tags') or [])
                r['collections'] = [c['path'] for c in p.get('collections', [])]
                if p.get('pdf') and p['pdf'].get('exists'):
                    r['pdf_aliases'] = list(dict.fromkeys((x for x in (r.get('pdf_aliases') or []) + [r.get('pdf'), legacy_by_key.get(r['citekey'], {}).get('pdf')] if x)))
                    r['pdf'] = p['pdf']['abs_path']
                    r['attachment_key'] = p['pdf']['key']
                r['pdf_exists'] = bool(r.get('pdf') and (store.base / r['pdf']).exists())
                r['card'] = r.get('card') or f"_知识库/10_文献/{cards.DIR_NAME[r['primary_direction']]}/{r['citekey']}.md"
                records[r['id']] = r
            active_keys = {(p['library_id'], p['key']) for p in raw}
            for r in records.values():
                if r.get('zotero_key') and (int(r.get('library_id', 1)), r['zotero_key']) not in active_keys:
                    r['source_status'] = 'removed_from_zotero'
            key_map = {(int(p.get('library_id', 1)), p['zotero_key']): p for p in records.values() if p.get('zotero_key')}
            notes = zotero_sources(db, key_map)
            live_note_ids = {n['id'] for n in notes}
            notes.extend((dict(n, source_status='removed_from_zotero') for n in jsonl(store.active_dir() / 'zotero_sources.jsonl') if n['id'] not in live_note_ids))
        finally:
            db.close()
    except (sqlite3.Error, OSError) as exc:
        warnings.append(f'Zotero 快照读取失败，保留上一版本：{type(exc).__name__}')
        notes = jsonl(store.active_dir() / 'zotero_sources.jsonl')
    resolved = {p['citekey']: p['openalex_id'] for p in jsonl(store.paths.cache / 'openalex/resolve.jsonl') if p.get('openalex_id')}
    field_state = store.state('card_fields', {})
    for r in records.values():
        r['openalex_id'] = r.get('openalex_id') or registered.get(r['id'], {}).get('openalex_id') or resolved.get(r['citekey'], '')
        r['project'] = PROJECT if any((d in STAGES for d in r.get('directions', []))) else ''
        r['project_stages'] = [STAGES[d] for d in r.get('directions', []) if d in STAGES]
        card = store.base / r['card']
        if card.is_file():
            import yaml
            match = re.match('^---\\s*\\n(.*?)\\n---\\s*\\n', card.read_text(encoding='utf-8'), re.S)
            try:
                fm = yaml.safe_load(match.group(1)) if match else {}
            except yaml.YAMLError:
                fm = {}
            prior = field_state.get(r['id'], {})
            for key in ('project', 'project_stages'):
                if isinstance(fm, dict) and key in fm and (r.get('project_assignment') == 'manual' or key not in prior or fm[key] != prior[key]):
                    r[key] = fm[key]
                    r['project_assignment'] = 'manual'
    atomic(Path(index) / 'papers_all.jsonl', ''.join((json.dumps(p, ensure_ascii=False) + '\n' for p in records.values())))
    atomic(Path(index) / 'zotero_sources.jsonl', ''.join((json.dumps(p, ensure_ascii=False) + '\n' for p in notes)))
    if 'collections' in locals():
        atomic(Path(index) / 'zotero_collections.json', list(collections.values()))
    return (list(records.values()), notes, warnings)

def managed(old, block):
    text = AUTO_START + '\n' + block.rstrip() + '\n' + AUTO_END
    if AUTO_START in old and AUTO_END in old:
        a = old.index(AUTO_START)
        b = old.index(AUTO_END, a) + len(AUTO_END)
        return old[:a] + text + old[b:]
    return old.rstrip() + '\n\n' + text + '\n'

def repair_links(text, paper=None):
    text = text.replace('FROM "_知识库/', 'FROM "').replace('[[ _知识库/', '[[ ')
    text = re.sub('\\[\\[_知识库/', '[[', text)
    import kbapp.library.cards as cards
    for name in cards.DIR_NAME.values():
        text = text.replace('[[' + name + ']]', '[[20_方向/' + name + '|' + name + ']]')
    if paper and paper.get('pdf'):
        url = paper_url(paper)
        for raw in [paper['pdf']] + paper.get('pdf_aliases', []):
            text = text.replace('[[' + raw + ']]', '[阅读原文](' + url + ')')
    return text

def paper_url(paper, page=None, port=None):
    if paper.get('attachment_key'):
        return 'zotero://open-pdf/library/items/' + paper['attachment_key'] + ('?page=' + str(page) if page else '')
    if paper.get('zotero_key'):
        return 'zotero://select/library/items/' + paper['zotero_key']
    from kbapp.core.config import server_base
    return server_base(port) + '/api/file?paper=' + quote(paper['id'], safe='') + ('#page=' + str(page) if page else '')

def approved_papers(store):
    papers = store.papers()
    out = []
    for approval in store.approvals():
        if approval['status'] != 'downloaded' or not Path(approval['filepath']).is_file():
            continue
        p = papers.get(approval['paper_id'])
        if not p or p.get('kind') == 'library':
            continue
        out.append(dict(p, citekey=p.get('citekey') or 'External' + digest(p['id'])[:16], pdf=approval['filepath'], pdf_exists=True, directions=p.get('directions') or [], tagfields=p.get('tagfields') or {}))
    return out

def note_sources(store, papers):
    by_path = {(store.base / p['card']).resolve(): p for p in papers}
    result = []
    for path in (store.base / '_知识库').rglob('*.md'):
        if any((x in path.parts for x in ('.obsidian', '_attachments', '笔记模板'))):
            continue
        text = path.read_text(encoding='utf-8')
        text = re.sub(re.escape(AUTO_START) + '.*?' + re.escape(AUTO_END), '', text, flags=re.S)
        from kbapp.core.markers import START as READING_START, END as READING_END
        text = re.sub(re.escape(READING_START) + '.*?' + re.escape(READING_END), '', text, flags=re.S)
        text = re.sub('^---\\s*\\n.*?\\n---\\s*\\n', '', text, flags=re.S)
        text = re.sub('<!--.*?-->', '', text, flags=re.S)
        text = re.sub('```dataview.*?```', '', text, flags=re.S)
        paper = by_path.get(path.resolve())
        if paper:
            kind, origin = ('obsidian_note', 'legacy_mixed')
        else:
            kind, origin = ('obsidian_note', 'personal')
        if len(text.strip()) < 30:
            continue
        rel = path.relative_to(store.base / '_知识库').as_posix()
        result.append({'id': 'note:' + rel, 'paper_id': paper['id'] if paper else '', 'kind': kind, 'origin': origin, 'text': text, 'title': path.stem, 'path': str(path), 'url': 'obsidian://open?vault=' + quote('_知识库') + '&file=' + quote(rel)})
    return result
