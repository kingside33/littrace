"""kbapp.storage.store：由职责边界组织的知识库能力。"""
from __future__ import annotations
import contextlib
import json
import sqlite3
import time
from datetime import datetime
from kbapp.core.secrets import protect
from kbapp.core.io import TZ, atomic, digest, now, read_json
from kbapp.core.config import DEFAULTS, KINDS

class Store:

    def __init__(self, base=None, *, data_dir=None, paths=None):
        from kbapp.core.paths import Paths
        self.paths = paths or Paths.from_base(base, data_dir)
        self.base = self.paths.base
        self.root = self.paths.data
        self.paths.state.mkdir(parents=True, exist_ok=True)
        self.path = self.paths.database
        with self.db() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS papers(id TEXT PRIMARY KEY, payload TEXT NOT NULL, library INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS aliases(alias TEXT PRIMARY KEY, paper_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS relations(id TEXT PRIMARY KEY,src TEXT,dst TEXT,kind TEXT,status TEXT,
              level TEXT,manual INTEGER DEFAULT 0,payload TEXT,updated TEXT);
            CREATE INDEX IF NOT EXISTS relation_src ON relations(src);
            CREATE INDEX IF NOT EXISTS relation_dst ON relations(dst);
            CREATE TABLE IF NOT EXISTS relation_history(id INTEGER PRIMARY KEY,relation_id TEXT,payload TEXT,at TEXT);
            CREATE TABLE IF NOT EXISTS approvals(paper_id TEXT PRIMARY KEY,status TEXT,reason TEXT,question TEXT,
              created TEXT,updated TEXT,filepath TEXT DEFAULT '',error TEXT DEFAULT '');
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,kind TEXT,status TEXT,pid INTEGER,payload TEXT,
              progress TEXT,started TEXT,finished TEXT,error TEXT);
            CREATE TABLE IF NOT EXISTS frontier(wid TEXT,direction TEXT,depth INTEGER,cursor TEXT DEFAULT '*',
              status TEXT DEFAULT 'pending',updated TEXT DEFAULT '',error TEXT DEFAULT '',
              PRIMARY KEY(wid,direction));
            CREATE TABLE IF NOT EXISTS usage(id TEXT PRIMARY KEY,day TEXT,reserved REAL,actual REAL,
              purpose TEXT,status TEXT,at TEXT);
            CREATE TABLE IF NOT EXISTS analyses(id TEXT PRIMARY KEY,fingerprint TEXT,payload TEXT,updated TEXT);
            CREATE TABLE IF NOT EXISTS token_usage(usage_id TEXT PRIMARY KEY,input_tokens INTEGER,output_tokens INTEGER,cost_known INTEGER,at TEXT);
            CREATE TABLE IF NOT EXISTS agent_runs(id TEXT PRIMARY KEY,kind TEXT,status TEXT,input TEXT,
              fingerprint TEXT,priority INTEGER,stop INTEGER DEFAULT 0,result TEXT DEFAULT '{}',error TEXT DEFAULT '',created TEXT,updated TEXT);
            CREATE TABLE IF NOT EXISTS agent_tasks(id TEXT PRIMARY KEY,run_id TEXT,role TEXT,subject TEXT,
              fingerprint TEXT,status TEXT,payload TEXT,result TEXT DEFAULT '{}',attempt INTEGER DEFAULT 0,error TEXT DEFAULT '',updated TEXT);
            CREATE INDEX IF NOT EXISTS agent_task_run ON agent_tasks(run_id,status);
            CREATE TABLE IF NOT EXISTS task_events(seq INTEGER PRIMARY KEY AUTOINCREMENT,task_id TEXT NOT NULL,
              step_id TEXT,role TEXT,stage TEXT,status TEXT,message TEXT,payload TEXT,created TEXT);
            CREATE INDEX IF NOT EXISTS task_event_cursor ON task_events(task_id,seq);
            CREATE TABLE IF NOT EXISTS task_activity(task_id TEXT PRIMARY KEY,payload TEXT,updated TEXT);
            CREATE TABLE IF NOT EXISTS task_usage(usage_id TEXT PRIMARY KEY,task_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS verification_budgets(subject TEXT PRIMARY KEY,payload TEXT,updated TEXT);
            CREATE TABLE IF NOT EXISTS verification_attempts(cache_key TEXT PRIMARY KEY,subject TEXT,
              fingerprint TEXT,status TEXT,payload TEXT,updated TEXT);
            CREATE TABLE IF NOT EXISTS research_artifacts(id TEXT PRIMARY KEY,type TEXT,subject TEXT,
              fingerprint TEXT,payload TEXT,status TEXT,version TEXT,updated TEXT);
            CREATE INDEX IF NOT EXISTS artifact_subject ON research_artifacts(subject,type);
            CREATE TABLE IF NOT EXISTS method_concepts(id TEXT PRIMARY KEY,name TEXT,aliases TEXT,payload TEXT,manual INTEGER DEFAULT 0,updated TEXT);
            CREATE TABLE IF NOT EXISTS paper_methods(paper_id TEXT,method_id TEXT,payload TEXT,PRIMARY KEY(paper_id,method_id));
            CREATE TABLE IF NOT EXISTS paper_classifications(paper_id TEXT PRIMARY KEY,payload TEXT,manual INTEGER DEFAULT 0,updated TEXT);
            CREATE TABLE IF NOT EXISTS note_sync(paper_id TEXT PRIMARY KEY,base_text TEXT,proposed TEXT,status TEXT,updated TEXT);
            CREATE TABLE IF NOT EXISTS query_vectors(key TEXT PRIMARY KEY,signature TEXT,vector TEXT,status TEXT,error TEXT DEFAULT '',updated TEXT);
            CREATE TABLE IF NOT EXISTS reference_lists(paper_id TEXT PRIMARY KEY,fingerprint TEXT,payload TEXT,updated TEXT);
            CREATE TABLE IF NOT EXISTS reference_jobs(paper_id TEXT PRIMARY KEY,fingerprint TEXT,status TEXT,
              priority INTEGER DEFAULT 10,force INTEGER DEFAULT 0,requested TEXT,updated TEXT,error TEXT DEFAULT '',retry_after REAL DEFAULT 0);
            """)
        from kbapp.storage.schema import migrate
        migrate(self)

    @contextlib.contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def state(self, key, default=None):
        with self.db() as db:
            row = db.execute('SELECT value FROM state WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_state(self, key, value):
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO state VALUES(?,?)', (key, json.dumps(value, ensure_ascii=False)))

    def settings(self, public=False):
        result = dict(DEFAULTS)
        data = read_json(self.paths.state / 'settings.json', {})
        for key, value in data.items():
            if key in {'api_key', 'openalex_key'}:
                result[key] = bool(value) if public else protect(value, decrypt=True)
            elif key in DEFAULTS:
                result[key] = value
        if public:
            for key in ('api_key', 'openalex_key'):
                result[key] = bool(result[key])
        return result

    def save_settings(self, values):
        from urllib.parse import urlparse
        import math
        current = self.settings()
        for key, value in values.items():
            if key not in DEFAULTS:
                raise ValueError(f'未知配置：{key}')
            if key in {'api_key', 'openalex_key'} and value is None:
                continue
            if isinstance(DEFAULTS[key], bool):
                if not isinstance(value, bool):
                    raise ValueError(f'{key} 应为布尔值')
            elif isinstance(DEFAULTS[key], (int, float)):
                value = float(value)
                if not math.isfinite(value) or value < 0:
                    raise ValueError(f'{key} 应为非负有限数值')
                if isinstance(DEFAULTS[key], int):
                    value = int(value)
            else:
                value = str(value).strip()
            current[key] = value
        u = urlparse(current['api_base'])
        if current['api_base'] and (u.scheme not in {'https', 'http'} or not u.hostname or u.username or (u.scheme == 'http' and u.hostname not in {'localhost', '127.0.0.1'})):
            raise ValueError('模型地址应为 HTTPS；本机地址可使用 HTTP')
        proxy = urlparse(current['cloud_proxy'])
        if current['cloud_proxy'] and (proxy.scheme != 'http' or proxy.hostname not in {'localhost','127.0.0.1','::1'} or proxy.username or proxy.password or proxy.path not in {'','/'} or proxy.query or proxy.fragment or not proxy.port):
            raise ValueError('云端代理应为本机 HTTP 地址及端口，例如 http://127.0.0.1:7897')
        if current['network_depth'] != 2 or current['daily_time'] != '09:00':
            raise ValueError('当前版本固定北京时间 09:00、上下游两层')
        if current['budget_mode'] not in {'limited', 'unlimited'}:
            raise ValueError('费用模式无效')
        if current['relation_extractor'] not in {'local', 'cloud'}:
            raise ValueError('关系提取方式无效')
        if not 2048 <= current['local_text_context'] <= 32768:
            raise ValueError('本地核验上下文应在 2048 至 32768 之间')
        if current['cloud_enabled']:
            if not all((current[k] for k in ('api_base', 'model', 'api_key'))):
                raise ValueError('启用前需填写 API、模型与密钥')
            if current['budget_mode'] == 'limited' and (not all((current[k] for k in ('daily_budget', 'input_price', 'output_price')))):
                raise ValueError('限额模式需填写输入/输出价格与每日限额；暂不限制需明确选择不限额模式')
        encrypted = {k: protect(v) if k in {'api_key', 'openalex_key'} else v for k, v in current.items()}
        atomic(self.paths.state / 'settings.json', encrypted)
        return self.settings(public=True)

    def active_dir(self):
        version = self.state('active_version')
        return self.paths.indexes / 'versions' / version if version else self.paths.indexes / 'unpublished'

    def resolve(self, key):
        from kbapp.core.metadata import normalize_doi
        doi = normalize_doi(key)
        if doi:
            with self.db() as db:
                matches = [r['id'] for r in db.execute("SELECT id,payload FROM papers WHERE json_extract(payload,'$.doi') IS NOT NULL") if normalize_doi(json.loads(r['payload']).get('doi')) == doi]
            if len(matches) > 1:
                raise ValueError('DOI对应多个独立条目，请通过文献列表选择具体条目')
            if matches:
                return matches[0]
        with self.db() as db:
            row = db.execute('SELECT paper_id FROM aliases WHERE alias=?', (key,)).fetchone()
        return row[0] if row else key

    def papers(self):
        with self.db() as db:
            return {r['id']: json.loads(r['payload']) for r in db.execute('SELECT * FROM papers')}

    def put_papers(self, papers):
        with self.db() as db:
            for p in papers:
                db.execute('INSERT INTO papers VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,library=excluded.library', (p['id'], json.dumps(p, ensure_ascii=False), int(p.get('kind') == 'library')))
                for alias in [p['id'], p.get('citekey'), p.get('openalex_id'), p.get('doi')]:
                    if alias:
                        db.execute('INSERT OR IGNORE INTO aliases VALUES(?,?)', (alias, p['id']))

    def patch_paper(self, paper_id, fields, force=False):
        """在事务内合并后台元数据，保留并发发生的人工字段修改。"""
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT payload FROM papers WHERE id=?', (paper_id,)).fetchone()
            if not row:
                raise ValueError('文献不存在')
            paper = json.loads(row[0])
            fields = dict(fields)
            if paper.get('metadata_source') == 'zotero' and (not force):
                for key in ('title', 'authors', 'creators', 'doi', 'year', 'venue', 'abstract', 'tags', 'collections', 'type', 'volume', 'issue', 'pages', 'extra', 'url'):
                    if key in fields and fields[key] != paper.get(key):
                        cid = digest(json.dumps([paper_id, key, paper.get(key), fields[key]], ensure_ascii=False, sort_keys=True))
                        db.execute('INSERT OR IGNORE INTO metadata_conflicts(id,paper_id,field,authoritative,proposed,source,created) VALUES(?,?,?,?,?,?,?)', (cid, paper_id, key, json.dumps(paper.get(key), ensure_ascii=False), json.dumps(fields[key], ensure_ascii=False), 'background_enrichment', now()))
                        fields.pop(key)
            paper.update(fields)
            db.execute('UPDATE papers SET payload=? WHERE id=?', (json.dumps(paper, ensure_ascii=False), paper_id))
            for alias in (paper.get('openalex_id'), paper.get('doi')):
                if alias:
                    db.execute('INSERT OR IGNORE INTO aliases VALUES(?,?)', (alias, paper_id))
        return paper

    def request_fulltext(self, paper_id, reason, question):
        paper_id = self.resolve(paper_id)
        with self.db() as db:
            if not db.execute('SELECT 1 FROM papers WHERE id=?', (paper_id,)).fetchone():
                raise ValueError('文献不存在')
            db.execute('INSERT OR IGNORE INTO approvals(paper_id,status,reason,question,created,updated) VALUES(?,?,?,?,?,?)', (paper_id, 'pending', reason, question, now(), now()))
            pending = db.execute('SELECT status FROM approvals WHERE paper_id=?', (paper_id,)).fetchone()[0] == 'pending'
        if pending:
            from kbapp.storage.activity import emit
            emit(self, 'fulltext', 'waiting_approval', '关键全文需要审批', paper_id=paper_id)

    def decide(self, paper_ids, decision):
        if decision not in {'approved', 'rejected', 'pending'}:
            raise ValueError('无效审批状态')
        with self.db() as db:
            for pid in paper_ids:
                db.execute("UPDATE approvals SET status=?,updated=?,error='' WHERE paper_id=? AND status!='downloaded'", (decision, now(), self.resolve(pid)))

    def relation(self, item, manual=False):
        item = dict(item)
        item['src'] = self.resolve(item['src'])
        item['dst'] = self.resolve(item['dst'])
        if item['kind'] not in KINDS or item['src'] == item['dst']:
            raise ValueError('无效关系类型或自引用')
        item.setdefault('status', 'candidate')
        item.setdefault('level', 'inference')
        rid = item.get('id') or digest(f"{item['src']}|{item['dst']}|{item['kind']}")[:24]
        item['id'] = rid
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM relations WHERE id=?', (rid,)).fetchone()
            if old and old['manual'] and (not manual):
                return rid
            if item['status'] not in {'confirmed', 'candidate', 'rejected'}:
                raise ValueError('无效关系状态')
            if old and old['status'] == 'confirmed' and (item['status'] != 'confirmed') and (not manual) and (not item.get('evidence_stale')) and (not item.get('semantic_recheck_failed')):
                db.execute('INSERT INTO relation_history(relation_id,payload,at) VALUES(?,?,?)', (rid, json.dumps(dict(item, history_event='automatic_candidate_not_applied'), ensure_ascii=False), now()))
                return rid
            if old and (not manual) and (json.loads(old['payload']) != item):
                db.execute('INSERT INTO relation_history(relation_id,payload,at) VALUES(?,?,?)', (rid, old['payload'], now()))
            if manual:
                item['level'] = 'human'
                item['manual'] = True
                db.execute('INSERT INTO relation_history(relation_id,payload,at) VALUES(?,?,?)', (rid, old['payload'] if old else '{}', now()))
            db.execute('INSERT INTO relations VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET src=excluded.src,dst=excluded.dst,kind=excluded.kind,status=excluded.status,level=excluded.level,manual=excluded.manual,payload=excluded.payload,updated=excluded.updated', (rid, item['src'], item['dst'], item['kind'], item['status'], item['level'], int(manual), json.dumps(item, ensure_ascii=False), now()))
            db.execute('DELETE FROM relation_evidence WHERE relation_id=?', (rid,))
            for n, e in enumerate(item.get('evidence', [])):
                pid = e.get('paper_id')
                if pid and (not db.execute('SELECT 1 FROM papers WHERE id=?', (pid,)).fetchone()):
                    pid = None
                db.execute('INSERT INTO relation_evidence VALUES(?,?,?,?,?,?,?)', (rid, n, pid, e.get('source_id'), e.get('quote', ''), e.get('verification_status', 'unverified'), json.dumps(e, ensure_ascii=False)))
        return rid

    def relations(self, status=None):
        with self.db() as db:
            rows = db.execute('SELECT payload,manual FROM relations' + (' WHERE status=?' if status else ''), (status,) if status else ()).fetchall()
        return [dict(json.loads(r[0]), manual=bool(r[1])) for r in rows]

    def reserve(self, amount, purpose):
        cfg = self.settings()
        if not cfg['cloud_enabled']:
            raise ValueError('云端调用尚未启用，请先配置模型、价格及每日限额')
        uid = digest(f'{time.time_ns()}|{purpose}')[:24]
        day = datetime.now(TZ).date().isoformat()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            spent = db.execute('SELECT COALESCE(SUM(COALESCE(actual,reserved)),0) FROM usage WHERE day=?', (day,)).fetchone()[0]
            if cfg['budget_mode'] == 'limited' and spent + amount > cfg['daily_budget']:
                raise ValueError('今日模型费用额度不足，任务保留至次日')
            db.execute('INSERT INTO usage VALUES(?,?,?,?,?,?,?)', (uid, day, amount, None, purpose, 'reserved', now()))
        return uid

    def settle(self, uid, cost=None, status='complete', usage=None, cost_known=True):
        with self.db() as db:
            db.execute('UPDATE usage SET actual=?,status=? WHERE id=?', (cost, status, uid))
            if usage is not None:
                db.execute('INSERT OR REPLACE INTO token_usage VALUES(?,?,?,?,?)', (uid, int(usage.get('prompt_tokens') or 0), int(usage.get('completion_tokens') or 0), int(cost_known), now()))

    def approvals(self):
        with self.db() as db:
            return [dict(r) for r in db.execute('SELECT * FROM approvals ORDER BY created DESC')]
