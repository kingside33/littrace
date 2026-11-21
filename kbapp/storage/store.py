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

# Stored secrets: DPAPI ciphertext bound to the current Windows user. A blob
# copied from another machine/account (or after a profile rebuild) may exist but
# be undecryptable, which is a state callers must be able to tell apart from
# "no key stored". See `settings()`.
SECRET_KEYS = ('api_key', 'openalex_key')

class Store:

    def __init__(self, base=None, *, data_dir=None, paths=None):
        from kbapp.core.paths import Paths
        self.paths = paths or Paths.from_base(base, data_dir)
        self.base = self.paths.base
        self.root = self.paths.data
        self.paths.state.mkdir(parents=True, exist_ok=True)
        self.path = self.paths.database
        self.apply_cache_dir()
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

    def apply_cache_dir(self):
        """Point `paths.cache` at the configured directory, if any.

        Called at construction and again after settings change, so a saved
        choice takes effect without restarting the service. An empty setting
        means "use `<data>/cache`", which is what a fresh install has always
        done. The directory is created on demand; a path that cannot be created
        or written raises, and the caller turns that into a visible error
        instead of silently falling back (a silent fallback would scatter
        caches across two disks with no way to tell which one is in use).
        """
        from kbapp.core.paths import Paths
        configured = str(self.settings().get('cache_dir') or '').strip()
        if not configured:
            if self.paths.cache_root is None:
                return self.paths.cache
            self.paths = Paths(self.paths.base, self.paths.data)
            return self.paths.cache
        from pathlib import Path
        target = Path(configured).expanduser()
        if not target.is_absolute():
            raise ValueError(f'缓存目录需为绝对路径：{configured}')
        target.mkdir(parents=True, exist_ok=True)
        probe = target / '.littrace-write-probe'
        try:
            probe.write_text('ok', encoding='utf-8')
        finally:
            probe.unlink(missing_ok=True)
        self.paths = Paths(self.paths.base, self.paths.data, target.resolve())
        return self.paths.cache

    def cache_info(self):
        """What the settings page shows: where the cache is and how much is in it."""
        root = self.paths.cache
        files = 0
        size = 0
        if root.is_dir():
            for entry in root.rglob('*'):
                if entry.is_file():
                    files += 1
                    with contextlib.suppress(OSError):
                        size += entry.stat().st_size
        return {
            'path': str(root),
            'default_path': str(self.paths.default_cache),
            'overridden': self.paths.cache_overridden,
            'exists': root.is_dir(),
            'files': files,
            'bytes': size,
        }

    def settings(self, public=False):
        """Read settings, never raising because a stored secret cannot be decrypted.

        DPAPI ciphertext is bound to the Windows user that wrote it, so a data
        directory restored from a backup, copied from another machine/account,
        or read after a profile rebuild can hold a secret this process cannot
        open. In that case the non-public view returns `None` for that key: a
        stored-but-unreadable secret is distinguishable both from `''`
        (nothing stored) and from plaintext. `None` is falsy, so callers that
        only ask "is there a usable key?" keep working.

        The public view (booleans only) still reports `True`, because a secret
        *is* stored; `save_settings` never needs to reveal it to decide what to
        do with an untouched key (it writes the stored bytes back verbatim).
        """
        result = dict(DEFAULTS)
        data = read_json(self.paths.state / 'settings.json', {})
        for key, value in data.items():
            if key in SECRET_KEYS:
                result[key] = bool(value) if public else self._reveal(value)
            elif key in DEFAULTS:
                result[key] = value
        if public:
            for key in SECRET_KEYS:
                result[key] = bool(result[key])
        return result

    @staticmethod
    def _reveal(value):
        """Decrypt one stored secret; `None` means stored but unreadable."""
        if not value:
            return ''
        try:
            return protect(value, decrypt=True)
        except Exception:  # DPAPI/base64 failures are environmental, not caller errors
            return None

    def save_settings(self, values):
        from urllib.parse import urlparse
        import math
        # The raw map is what an untouched secret is written back from: it may
        # hold ciphertext this process cannot decrypt, which must survive a
        # save of unrelated fields byte for byte.
        raw = read_json(self.paths.state / 'settings.json', {})
        current = self.settings()
        # The settings page posts the whole form on every save, so its
        # `cloud_enabled` is the current checkbox state and may be `True` on a
        # save whose only real change is an unrelated field. Only an explicit
        # off→on transition with an unreadable key is refused; otherwise the
        # user would be re-locked out of every settings change (the state #81
        # exists to remove).
        was_cloud_enabled = current['cloud_enabled']
        for key, value in values.items():
            if key not in DEFAULTS:
                raise ValueError(f'未知配置：{key}')
            if key in SECRET_KEYS and value is None:
                continue
            if key == 'network_depth':
                # 追溯层数在通用数值分支之前做键级校验：布尔与非数值显式拒绝
                # （通用分支会把 True 变 1；float(None) 抛 TypeError，经 web 层
                # 会变成 500）；小数拒绝而不是静默截断；数值字符串沿用旧行为
                # 接受。「不限层数」不走这里，由 network_depth_unlimited 承载。
                if isinstance(value, bool):
                    raise ValueError('层数应为数字')
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    raise ValueError('层数应为数字') from None
                if not math.isfinite(number):
                    raise ValueError('层数应为数字')
                if not number.is_integer():
                    raise ValueError('层数应为整数')
                if not 1 <= number <= 100:
                    raise ValueError('层数应在 1 至 100 之间；不限层数请选择「不限层数」')
                value = int(number)
            elif isinstance(DEFAULTS[key], bool):
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
        # network_depth 的约束已前移为键级校验（1–100 的正整数；「不限层数」由
        # network_depth_unlimited 布尔承载），存量非 2 的层数不再锁死与层数无关
        # 的保存；daily_time 保持原规则，语义不变。
        if current['daily_time'] != '09:00':
            raise ValueError('当前版本固定北京时间 09:00')
        if current['budget_mode'] not in {'limited', 'unlimited'}:
            raise ValueError('费用模式无效')
        if current['relation_extractor'] not in {'local', 'cloud'}:
            raise ValueError('关系提取方式无效')
        if not 2048 <= current['local_text_context'] <= 32768:
            raise ValueError('本地核验上下文应在 2048 至 32768 之间')
        if current['cloud_enabled']:
            # Check the non-secret fields first so the normal-path message is
            # unchanged. A `None` api_key means "stored but unreadable": it
            # cannot be used, so an explicit request to enable cloud calls gets
            # a message naming the fix instead of echoing a DPAPI/base64
            # exception. Saves that do not touch the switch keep working (the
            # ciphertext is written back verbatim below), so an unrelated
            # settings change cannot re-lock the page.
            if not all((current[k] for k in ('api_base', 'model'))):
                raise ValueError('启用前需填写 API、模型与密钥')
            if current['api_key'] is None:
                if not was_cloud_enabled and values.get('cloud_enabled') is True:
                    raise ValueError('保存的密钥无法读取，请重新填写')
            elif not current['api_key']:
                raise ValueError('启用前需填写 API、模型与密钥')
            if current['budget_mode'] == 'limited' and (not all((current[k] for k in ('daily_budget', 'input_price', 'output_price')))):
                raise ValueError('限额模式需填写输入/输出价格与每日限额；暂不限制需明确选择不限额模式')
        encrypted = {}
        for key, value in current.items():
            if key in SECRET_KEYS:
                # A value supplied in this request (including '' to clear) is
                # encrypted fresh, without ever opening the old ciphertext.
                # An untouched key is written back exactly as stored: decrypt
                # + re-encrypt would fail outright when the blob is unreadable.
                encrypted[key] = protect(value) if key in values and values[key] is not None else raw.get(key, '')
            else:
                encrypted[key] = value
        atomic(self.paths.state / 'settings.json', encrypted)
        # A cache location change must take effect now, not on the next restart.
        # A path that cannot be created or written raises here, after the other
        # settings were already saved: the caller reports it, and the stored
        # value is the one the user asked for (so a fixed path retries cleanly
        # instead of half-applying).
        if 'cache_dir' in values:
            self.apply_cache_dir()
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
