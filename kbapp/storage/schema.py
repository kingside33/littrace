"""kbapp.storage.schema：由职责边界组织的知识库能力。"""
import json
import sqlite3
from contextlib import closing
VERSION = 11
DDL = """
CREATE TABLE IF NOT EXISTS agent_units(
 id TEXT PRIMARY KEY, parent_id TEXT NOT NULL REFERENCES agent_tasks(id),run_id TEXT NOT NULL REFERENCES agent_runs(id),
 stage TEXT NOT NULL,role TEXT NOT NULL,subject TEXT NOT NULL,object_key TEXT NOT NULL,status TEXT NOT NULL,
 payload TEXT NOT NULL,result TEXT NOT NULL DEFAULT '{}',attempt INTEGER NOT NULL DEFAULT 0,error TEXT NOT NULL DEFAULT '',updated TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS agent_unit_run ON agent_units(run_id,status);
CREATE INDEX IF NOT EXISTS agent_unit_parent ON agent_units(parent_id,status);
CREATE TABLE IF NOT EXISTS agent_unit_dependencies(
 unit_id TEXT NOT NULL REFERENCES agent_units(id),dependency_id TEXT NOT NULL REFERENCES agent_units(id),
 PRIMARY KEY(unit_id,dependency_id));
CREATE INDEX IF NOT EXISTS task_event_unit ON task_events(task_id,step_id,seq);
CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS zotero_libraries(id INTEGER PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS zotero_items(library_id INTEGER NOT NULL REFERENCES zotero_libraries(id),item_key TEXT NOT NULL,
 paper_id TEXT NOT NULL REFERENCES papers(id),version INTEGER,payload TEXT NOT NULL,source TEXT NOT NULL,
 PRIMARY KEY(library_id,item_key));
CREATE INDEX IF NOT EXISTS zotero_paper ON zotero_items(paper_id);
CREATE TABLE IF NOT EXISTS zotero_collections(library_id INTEGER NOT NULL REFERENCES zotero_libraries(id),collection_key TEXT NOT NULL,
 parent_key TEXT,payload TEXT NOT NULL,PRIMARY KEY(library_id,collection_key));
CREATE TABLE IF NOT EXISTS zotero_memberships(library_id INTEGER NOT NULL,item_key TEXT NOT NULL,collection_key TEXT NOT NULL,
 PRIMARY KEY(library_id,item_key,collection_key),
 FOREIGN KEY(library_id,item_key) REFERENCES zotero_items(library_id,item_key),
 FOREIGN KEY(library_id,collection_key) REFERENCES zotero_collections(library_id,collection_key));
CREATE TABLE IF NOT EXISTS zotero_attachments(library_id INTEGER NOT NULL,attachment_key TEXT NOT NULL,item_key TEXT NOT NULL,
 payload TEXT NOT NULL,PRIMARY KEY(library_id,attachment_key),
 FOREIGN KEY(library_id,item_key) REFERENCES zotero_items(library_id,item_key));
CREATE TABLE IF NOT EXISTS zotero_notes(library_id INTEGER NOT NULL,note_key TEXT NOT NULL,paper_id TEXT REFERENCES papers(id),
 kind TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(library_id,note_key));
CREATE TABLE IF NOT EXISTS metadata_conflicts(id TEXT PRIMARY KEY,paper_id TEXT NOT NULL REFERENCES papers(id),field TEXT NOT NULL,
 authoritative TEXT,proposed TEXT,source TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS works(id TEXT PRIMARY KEY,payload TEXT NOT NULL,updated TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS paper_works(paper_id TEXT PRIMARY KEY REFERENCES papers(id),work_id TEXT NOT NULL,
 basis TEXT NOT NULL,confidence TEXT NOT NULL,evidence TEXT NOT NULL,updated TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS paper_work ON paper_works(work_id);
CREATE TABLE IF NOT EXISTS work_versions(paper_id TEXT PRIMARY KEY REFERENCES papers(id),work_id TEXT NOT NULL,
 version TEXT NOT NULL,venue TEXT,evidence TEXT NOT NULL,updated TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS work_version ON work_versions(work_id);
CREATE TABLE IF NOT EXISTS invalidations(id TEXT PRIMARY KEY,kind TEXT NOT NULL,subject TEXT NOT NULL,
 reason TEXT NOT NULL,affected TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'recorded',created TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS invalidation_kind ON invalidations(kind,created);
CREATE TABLE IF NOT EXISTS conflict_resolutions(id TEXT PRIMARY KEY,conflict_id TEXT NOT NULL REFERENCES metadata_conflicts(id),
 choice TEXT NOT NULL,previous TEXT NOT NULL,applied TEXT NOT NULL,at TEXT NOT NULL,undone_at TEXT);
CREATE TABLE IF NOT EXISTS citation_context_payload(context_id TEXT PRIMARY KEY REFERENCES citation_contexts(id),
 marker TEXT NOT NULL DEFAULT '',target_paper_id TEXT,payload TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS agent_unit_leases(unit_id TEXT PRIMARY KEY REFERENCES agent_units(id),owner TEXT NOT NULL,
 lease_until TEXT NOT NULL,updated TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS relation_kind_status ON relations(kind,status);
CREATE INDEX IF NOT EXISTS approval_status_created ON approvals(status,created);
CREATE INDEX IF NOT EXISTS approval_created ON approvals(created);
CREATE INDEX IF NOT EXISTS invalidation_created ON invalidations(created);
CREATE INDEX IF NOT EXISTS artifact_type_subject ON research_artifacts(type,subject,updated);
CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,paper_id TEXT NOT NULL REFERENCES papers(id),attachment_key TEXT,
 sha256 TEXT NOT NULL,path TEXT NOT NULL,page_count INTEGER NOT NULL,payload TEXT NOT NULL,updated TEXT NOT NULL,
 UNIQUE(paper_id,attachment_key,sha256));
CREATE INDEX IF NOT EXISTS document_hash ON documents(sha256);
CREATE TABLE IF NOT EXISTS document_pages(document_id TEXT NOT NULL REFERENCES documents(id),page INTEGER NOT NULL CHECK(page>0),
 classification TEXT NOT NULL CHECK(classification IN ('NATIVE_TEXT','SCANNED','MIXED','LOW_QUALITY','FAILED')),
 status TEXT NOT NULL,native_text TEXT NOT NULL,ocr_text TEXT NOT NULL DEFAULT '',payload TEXT NOT NULL,updated TEXT NOT NULL,
 PRIMARY KEY(document_id,page));
CREATE TABLE IF NOT EXISTS document_blocks(id TEXT PRIMARY KEY,document_id TEXT NOT NULL,page INTEGER NOT NULL,
 kind TEXT NOT NULL,text TEXT NOT NULL,payload TEXT NOT NULL,
 FOREIGN KEY(document_id,page) REFERENCES document_pages(document_id,page));
CREATE TABLE IF NOT EXISTS document_sections(id TEXT PRIMARY KEY,document_id TEXT NOT NULL REFERENCES documents(id),title TEXT NOT NULL,page INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS document_chunks(id TEXT PRIMARY KEY,document_id TEXT NOT NULL,page INTEGER NOT NULL,section_id TEXT REFERENCES document_sections(id),
 text TEXT NOT NULL,payload TEXT NOT NULL,FOREIGN KEY(document_id,page) REFERENCES document_pages(document_id,page));
CREATE TABLE IF NOT EXISTS relation_evidence(relation_id TEXT NOT NULL REFERENCES relations(id),ordinal INTEGER NOT NULL,
 paper_id TEXT REFERENCES papers(id),source_id TEXT,quote TEXT NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL,
 PRIMARY KEY(relation_id,ordinal));
CREATE TABLE IF NOT EXISTS document_references(id TEXT PRIMARY KEY,document_id TEXT NOT NULL REFERENCES documents(id),page INTEGER NOT NULL,
 label TEXT,raw TEXT NOT NULL,target_paper_id TEXT REFERENCES papers(id),payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS citation_contexts(id TEXT PRIMARY KEY,reference_id TEXT NOT NULL REFERENCES document_references(id),
 document_id TEXT NOT NULL,page INTEGER NOT NULL,section TEXT,text TEXT NOT NULL,purpose TEXT NOT NULL DEFAULT 'unknown',
 FOREIGN KEY(document_id,page) REFERENCES document_pages(document_id,page));
CREATE TABLE IF NOT EXISTS model_runs(id TEXT PRIMARY KEY,role TEXT NOT NULL,signature TEXT NOT NULL,status TEXT NOT NULL,
 payload TEXT NOT NULL,started TEXT NOT NULL,finished TEXT);
CREATE TABLE IF NOT EXISTS document_reviews(id TEXT PRIMARY KEY,document_id TEXT NOT NULL REFERENCES documents(id),page INTEGER NOT NULL,payload TEXT NOT NULL,created TEXT NOT NULL);
"""
# Revisions are transactionally updated even when workflows use direct SQL writes.
for _table, _key in [('papers','paper_revision'),('paper_classifications','paper_revision'),('works','paper_revision'),('paper_works','paper_revision'),('work_versions','paper_revision'),('document_references','source_revision'),('citation_contexts','source_revision'),('documents','source_revision'),('document_pages','source_revision'),('document_chunks','source_revision'),('document_blocks','source_revision'),('relations','relation_revision')]:
    for _action in ('INSERT','UPDATE','DELETE'):
        DDL += f"CREATE TRIGGER IF NOT EXISTS revision_{_table}_{_action.lower()} AFTER {_action} ON {_table} BEGIN INSERT INTO state(key,value) VALUES('{_key}','1') ON CONFLICT(key) DO UPDATE SET value=CAST(CAST(value AS INTEGER)+1 AS TEXT); END;\n"

def migrate(store):
    with closing(sqlite3.connect(store.path, timeout=30)) as db:
        present = db.execute("SELECT 1 FROM sqlite_master WHERE name='schema_migrations'").fetchone()
        if present and db.execute('SELECT 1 FROM schema_migrations WHERE version=?', (VERSION,)).fetchone():
            return
        backup = store.paths.backups / f'pre-schema-{VERSION}.sqlite3'
        backup.parent.mkdir(parents=True, exist_ok=True)
        if not backup.exists():
            with closing(sqlite3.connect(backup)) as dest:
                db.backup(dest)
        from kbapp.core.io import now
        db.executescript('BEGIN IMMEDIATE;' + DDL)
        db.execute('INSERT OR IGNORE INTO schema_migrations VALUES(?,?)', (VERSION, now()))
        db.commit()

def sync_zotero(store, papers, notes, collections_path=None):
    """调用者在 put_papers 后写入来源快照；删除来源不删除历史记录。"""
    def dumps(v):
        return json.dumps(v, ensure_ascii=False)
    with store.db() as db:
        from kbapp.core.io import read_json
        for c in read_json(collections_path or store.active_dir() / 'zotero_collections.json', []):
            lib = int(c['library_id'])
            db.execute('INSERT OR IGNORE INTO zotero_libraries VALUES(?,?)', (lib, dumps({'id': lib, 'source': 'local_sqlite'})))
            db.execute('INSERT INTO zotero_collections VALUES(?,?,?,?) ON CONFLICT(library_id,collection_key) DO UPDATE SET parent_key=excluded.parent_key,payload=excluded.payload', (lib, c['key'], c.get('parent_key'), dumps(c)))
        for p in papers:
            raw = p.get('raw_zotero')
            if not raw or not p.get('zotero_key'):
                continue
            lib = int(p['library_id'])
            key = p['zotero_key']
            db.execute('INSERT OR IGNORE INTO zotero_libraries VALUES(?,?)', (lib, dumps({'id': lib, 'source': 'local_sqlite'})))
            db.execute('INSERT INTO zotero_items VALUES(?,?,?,?,?,?) ON CONFLICT(library_id,item_key) DO UPDATE SET paper_id=excluded.paper_id,version=excluded.version,payload=excluded.payload,source=excluded.source', (lib, key, p['id'], p.get('zotero_version'), dumps(raw), 'local_sqlite_snapshot'))
            db.execute('DELETE FROM zotero_memberships WHERE library_id=? AND item_key=?', (lib, key))
            collections = raw.get('collections', [])
            by_id = {c['id']: c for c in collections}
            for c in collections:
                if not c.get('key'):
                    continue
                db.execute('INSERT INTO zotero_collections VALUES(?,?,?,?) ON CONFLICT(library_id,collection_key) DO UPDATE SET parent_key=excluded.parent_key,payload=excluded.payload', (lib, c['key'], c.get('parent_key') or by_id.get(c.get('parent'), {}).get('key'), dumps(c)))
                db.execute('INSERT OR IGNORE INTO zotero_memberships VALUES(?,?,?)', (lib, key, c['key']))
            for a in raw.get('attachments', []):
                db.execute('INSERT INTO zotero_attachments VALUES(?,?,?,?) ON CONFLICT(library_id,attachment_key) DO UPDATE SET item_key=excluded.item_key,payload=excluded.payload', (lib, a['key'], key, dumps(a)))
        for n in notes:
            lib = int(n.get('library_id', 1))
            db.execute('INSERT INTO zotero_notes VALUES(?,?,?,?,?) ON CONFLICT(library_id,note_key) DO UPDATE SET paper_id=excluded.paper_id,kind=excluded.kind,payload=excluded.payload', (lib, n.get('zotero_key') or n['id'], n.get('paper_id') or None, n['kind'], dumps(n)))
