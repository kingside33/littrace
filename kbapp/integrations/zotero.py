"""kbapp.integrations.zotero：由职责边界组织的知识库能力。"""
import os
import re
import sqlite3
DEFAULT_ZOTERO_DIR = os.environ.get('KB_ZOTERO_DIR', '')
DEFAULT_BASE = str(__import__('kbapp.core.paths', fromlist=['Paths']).Paths.from_base().base)
NON_PAPER_TYPES = {'attachment', 'note', 'annotation'}
FIELD_MAP = {'title': 'title', 'abstractNote': 'abstract', 'date': 'date', 'DOI': 'doi', 'url': 'url', 'language': 'language', 'volume': 'volume', 'issue': 'issue', 'pages': 'pages', 'publisher': 'publisher', 'place': 'place', 'ISBN': 'isbn', 'ISSN': 'issn', 'conferenceName': 'conference', 'university': 'university', 'thesisType': 'thesis_type', 'extra': 'extra', 'shortTitle': 'short_title'}
VENUE_FIELDS = ('publicationTitle', 'proceedingsTitle', 'bookTitle', 'repository', 'websiteTitle', 'blogTitle', 'forumTitle', 'encyclopediaTitle', 'dictionaryTitle')

def ro_connect(path):
    from pathlib import Path
    uri = Path(path).resolve().as_uri() + '?mode=ro'
    source = sqlite3.connect(uri, uri=True, timeout=10)
    conn = sqlite3.connect(':memory:')
    try:
        source.backup(conn)
    finally:
        source.close()
    conn.row_factory = sqlite3.Row
    return conn

def parse_year(date_str):
    if not date_str:
        return None
    m = re.search('(1[89]\\d{2}|20\\d{2})', date_str)
    return int(m.group(1)) if m else None

def load_collections(conn):
    """返回 {collectionID: {id, name, parent, path}}"""
    rows = conn.execute('SELECT collectionID, collectionName, parentCollectionID, libraryID, key, version FROM collections').fetchall()
    raw = {r['collectionID']: r for r in rows}
    deleted = {r[0] for r in conn.execute('SELECT collectionID FROM deletedCollections')}

    def build_path(cid):
        parts, seen = ([], set())
        while cid is not None and cid in raw and (cid not in seen):
            seen.add(cid)
            parts.append(raw[cid]['collectionName'])
            cid = raw[cid]['parentCollectionID']
        return '/'.join(reversed(parts))
    out = {}
    for cid, r in raw.items():
        out[cid] = {'id': cid, 'library_id': r['libraryID'], 'key': r['key'], 'version': r['version'], 'name': r['collectionName'], 'parent': r['parentCollectionID'], 'parent_key': raw[r['parentCollectionID']]['key'] if r['parentCollectionID'] in raw else None, 'path': build_path(cid), 'deleted': cid in deleted}
    return out

def load_items(conn):
    """顶层文献条目 -> list of dict"""
    deleted = {r[0] for r in conn.execute('SELECT itemID FROM deletedItems')}
    types = {r['itemTypeID']: r['typeName'] for r in conn.execute('SELECT itemTypeID, typeName FROM itemTypesCombined')}
    rows = conn.execute('\n        SELECT i.itemID, i.key, i.itemTypeID, i.dateAdded, i.dateModified,\n               i.libraryID, i.version, i.clientDateModified\n        FROM items i\n        ').fetchall()
    papers = []
    for r in rows:
        if r['itemID'] in deleted:
            continue
        tname = types.get(r['itemTypeID'], '?')
        if tname in NON_PAPER_TYPES:
            continue
        papers.append({'item_id': r['itemID'], 'key': r['key'], 'library_id': r['libraryID'], 'version': r['version'], 'client_date_modified': r['clientDateModified'], 'type': tname, 'date_added': r['dateAdded'], 'date_modified': r['dateModified'], 'fields': {}, 'creators': [], 'tags': [], 'collections': [], 'attachments': []})
    return papers

def fill_fields(conn, papers):
    by_id = {p['item_id']: p for p in papers}
    rows = conn.execute('\n        SELECT id.itemID, f.fieldName, idv.value\n        FROM itemData id\n        JOIN fieldsCombined f ON f.fieldID = id.fieldID\n        JOIN itemDataValues idv ON idv.valueID = id.valueID\n        ').fetchall()
    for r in rows:
        p = by_id.get(r['itemID'])
        if p is None:
            continue
        p['fields'][r['fieldName']] = r['value']

def fill_creators(conn, papers):
    by_id = {p['item_id']: p for p in papers}
    rows = conn.execute('\n        SELECT ic.itemID, ic.orderIndex, ic.creatorTypeID,\n               c.firstName, c.lastName, c.fieldMode,\n               ct.creatorType\n        FROM itemCreators ic\n        JOIN creators c ON c.creatorID = ic.creatorID\n        JOIN creatorTypes ct ON ct.creatorTypeID = ic.creatorTypeID\n        ORDER BY ic.itemID, ic.orderIndex\n        ').fetchall()
    for r in rows:
        p = by_id.get(r['itemID'])
        if p is None:
            continue
        last = (r['lastName'] or '').strip()
        first = (r['firstName'] or '').strip()
        name = last if r['fieldMode'] == 1 else last + ', ' + first if first else last
        p['creators'].append({'last': last, 'first': first, 'name': name, 'type': r['creatorType'], 'order': r['orderIndex'], 'field_mode': r['fieldMode']})

def fill_tags(conn, papers):
    by_id = {p['item_id']: p for p in papers}
    rows = conn.execute('\n        SELECT it.itemID, t.name, it.type\n        FROM itemTags it\n        JOIN tags t ON t.tagID = it.tagID\n        ').fetchall()
    for r in rows:
        p = by_id.get(r['itemID'])
        if p is not None:
            p['tags'].append(r['name'])
            p.setdefault('tags_raw', []).append({'tag': r['name'], 'type': r['type']})

def fill_collections(conn, papers, collections):
    by_id = {p['item_id']: p for p in papers}
    rows = conn.execute('SELECT collectionID, itemID FROM collectionItems').fetchall()
    for r in rows:
        p = by_id.get(r['itemID'])
        c = collections.get(r['collectionID'])
        if p is not None and c is not None and (not c['deleted']):
            p['collections'].append(dict(c))

def resolve_attachment_path(raw_path, base_dir, zotero_dir, att_key):
    """Zotero path 前缀: attachments: 相对 baseAttachmentPath; storage: 相对 storage/<key>"""
    if not raw_path:
        return None
    if raw_path.startswith('attachments:'):
        rel = raw_path[len('attachments:'):].lstrip('/\\')
        return os.path.normpath(os.path.join(base_dir, rel.replace('/', os.sep)))
    if raw_path.startswith('storage:'):
        fname = raw_path[len('storage:'):].lstrip('/\\')
        return os.path.normpath(os.path.join(zotero_dir, 'storage', att_key, fname.replace('/', os.sep)))
    if re.match('^[A-Za-z]:[\\\\/]', raw_path):
        return os.path.normpath(raw_path)
    return os.path.normpath(os.path.join(base_dir, raw_path))

def fill_attachments(conn, papers, base_dir, zotero_dir):
    by_id = {p['item_id']: p for p in papers}
    rows = conn.execute('\n        SELECT ia.itemID, ia.parentItemID, ia.linkMode, ia.contentType, ia.path,\n               i.key, i.libraryID, i.version, i.dateModified\n        FROM itemAttachments ia\n        JOIN items i ON i.itemID = ia.itemID\n        WHERE ia.parentItemID IS NOT NULL AND ia.itemID NOT IN (SELECT itemID FROM deletedItems)\n        ').fetchall()
    for r in rows:
        p = by_id.get(r['parentItemID'])
        if p is None:
            continue
        abs_path = resolve_attachment_path(r['path'], base_dir, zotero_dir, r['key'])
        p['attachments'].append({'key': r['key'], 'library_id': r['libraryID'], 'version': r['version'], 'date_modified': r['dateModified'], 'raw_path': r['path'], 'abs_path': abs_path, 'exists': bool(abs_path and os.path.exists(abs_path)), 'content_type': r['contentType'], 'link_mode': r['linkMode']})

def pick_venue(fields):
    for k in VENUE_FIELDS:
        v = fields.get(k)
        if v:
            return v
    return fields.get('publisher') or ''

def build_record(p):
    f = p['fields']
    out = {k: f.get(src) for src, k in FIELD_MAP.items() if f.get(src)}
    out['venue'] = pick_venue(f)
    out['year'] = parse_year(f.get('date'))
    out['item_id'] = p['item_id']
    out['key'] = p['key']
    out['type'] = p['type']
    out['creators'] = p['creators']
    out['authors'] = [c['name'] for c in p['creators'] if c['type'] == 'author']
    out['tags'] = sorted(p['tags'])
    out['collections'] = p['collections']
    out['attachments'] = p['attachments']
    for key in ('library_id', 'version', 'date_added', 'date_modified', 'client_date_modified'):
        out[key] = p.get(key)
    out['raw_zotero'] = dict(p)
    out['raw_zotero_source'] = 'local_sqlite_snapshot'
    pdfs = [a for a in p['attachments'] if (a['content_type'] or '').endswith('pdf')]
    pdfs.sort(key=lambda a: (not a['exists'], a['raw_path'] is None))
    out['pdf'] = pdfs[0] if pdfs else None
    return out
