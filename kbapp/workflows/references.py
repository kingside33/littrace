"""kbapp.workflows.references：由职责边界组织的知识库能力。"""
from __future__ import annotations
from datetime import datetime
import json
import os
import threading
import time
from urllib.parse import quote
from urllib.request import Request, urlopen
from kbapp.core.io import TZ, atomic, digest, now, read_json
from kbapp.library.sources import norm
from kbapp.integrations.openalex import OpenAlex, map_work
VERSION = 'references-v1'
TTL = 7 * 86400
CITATION_FIELDS = ('cited_by', 'citation_count', 'citation_source', 'citation_updated')
_threads = {}

def valid_count(value):
    return isinstance(value, int) and (not isinstance(value, bool)) and (value >= 0)

def citation_info(paper):
    count = paper.get('citation_count')
    if not valid_count(count):
        count = paper.get('cited_by')
    known = valid_count(count)
    return {'citation_count': count if known else None, 'citation_source': paper.get('citation_source') or ('OpenAlex 缓存' if known and paper.get('openalex_id') else ''), 'citation_updated': paper.get('citation_updated') or paper.get('metadata_updated') or '', 'citation_status': 'known' if known else 'unknown'}

def fingerprint(store, paper):
    from kbapp.library.recognition import pdf_path
    path = pdf_path(store, paper)
    try:
        stat = path.stat() if path else None
    except OSError:
        stat = None
    return digest(json.dumps([VERSION, paper.get('doi'), paper.get('openalex_id'), paper.get('title'), paper.get('year'), [str(path), stat.st_size, stat.st_mtime_ns] if stat else None], ensure_ascii=False))

def age(stamp):
    try:
        return time.time() - datetime.fromisoformat(stamp).timestamp()
    except (ValueError, TypeError):
        return float('inf')

def enqueue(store, paper_id, force=False, priority=0):
    pid = store.resolve(paper_id)
    paper = store.papers().get(pid)
    if not paper:
        raise ValueError('文献不存在')
    fp = fingerprint(store, paper)
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM reference_jobs WHERE paper_id=?', (pid,)).fetchone()
        cached = db.execute('SELECT * FROM reference_lists WHERE paper_id=?', (pid,)).fetchone()
        if row and row['status'] in {'queued', 'running'}:
            db.execute('UPDATE reference_jobs SET priority=MIN(priority,?) WHERE paper_id=?', (priority, pid))
            return dict(row)
        if not force:
            if row and row['fingerprint'] == fp and (row['retry_after'] > time.time()):
                return dict(row)
            if cached and cached['fingerprint'] == fp and (age(cached['updated']) < TTL) and (json.loads(cached['payload']).get('status') == 'complete'):
                return dict(row) if row else {'status': 'complete'}
        elif row and age(row['requested']) < 30:
            return dict(row)
        db.execute('INSERT OR REPLACE INTO reference_jobs VALUES(?,?,?,?,?,?,?,?,?)', (pid, fp, 'queued', priority, int(force), now(), now(), '', 0))
    return {'paper_id': pid, 'status': 'queued', 'fingerprint': fp}

def entry(ref, papers):
    doi = (ref.get('doi') or '').lower().removeprefix('https://doi.org/').strip()
    wid = (ref.get('openalex_id') or '').rsplit('/', 1)[-1]
    found = papers.get(ref.get('paper_id'))
    ordered = sorted(papers.values(), key=lambda p: p.get('kind') != 'library')
    if not found and wid:
        found = next((p for p in ordered if p.get('openalex_id') == wid), None)
    if not found and doi:
        found = next((p for p in ordered if (p.get('doi') or '').lower().removeprefix('https://doi.org/') == doi), None)
    title = norm(ref.get('title'))
    if not found and len(title) >= 20 and ref.get('year'):
        found = next((p for p in ordered if norm(p.get('title')) == title and p.get('year') == ref['year']), None)
    p = found or ref
    result = {k: p.get(k) for k in ('title', 'year', 'authors', 'doi', 'openalex_id', 'citekey', 'kind', 'abstract', 'zotero_key', 'attachment_key')}
    result.update(paper_id=found['id'] if found else None, raw=ref.get('raw', ''), ref_index=ref.get('ref_index'), origin=ref.get('origin', 'cached_citation'), url=ref.get('url') or ('https://doi.org/' + doi if doi else ''))
    result.update(citation_info(p))
    result['id'] = digest(str(result.get('paper_id') or doi or wid or ref.get('raw') or ref.get('title')))[:24]
    return result

def combine(rows):
    output = []
    seen = {}
    for row in rows:
        keys = [str(x) for x in (row.get('paper_id'), row.get('doi'), row.get('openalex_id')) if x]
        if not keys:
            keys = ['text:' + norm(row.get('title') or row.get('raw'))]
        previous = next((seen[k] for k in keys if k in seen), None)
        if previous is not None:
            current = output[previous]
            for k, v in row.items():
                if v is not None and v != '' and (not current.get(k) or k in CITATION_FIELDS + ('citation_status',)):
                    current[k] = v
            for key in keys:
                seen[key] = previous
        else:
            for key in keys:
                seen[key] = len(output)
            output.append(row)
    return output

def local_rows(store, paper, papers):
    rows = []
    with store.db() as db:
        rels = [json.loads(r[0]) for r in db.execute("SELECT payload FROM relations WHERE src=? AND kind='cites' AND status!='rejected'", (paper['id'],))]
    for rel in rels:
        target = papers.get(rel['dst'])
        if target:
            evidence = next((e for e in rel.get('evidence', []) if e.get('origin') == 'pdf_reference'), {})
            rows.append(entry(dict(target, paper_id=target['id'], origin=evidence.get('origin', 'cached_citation'), raw=evidence.get('quote', ''), ref_index=evidence.get('ref_index')), papers))
    for wid in paper.get('refs', []):
        rows.append(entry({'openalex_id': wid, 'title': '待补全元数据 · ' + wid, 'origin': 'OpenAlex'}, papers))
    return combine(rows)

def view(store, paper_id, auto=True):
    pid = store.resolve(paper_id)
    papers = store.papers()
    paper = papers.get(pid)
    if not paper:
        raise ValueError('文献不存在')
    if auto:
        enqueue(store, pid)
    with store.db() as db:
        saved = db.execute('SELECT * FROM reference_lists WHERE paper_id=?', (pid,)).fetchone()
        task = db.execute('SELECT * FROM reference_jobs WHERE paper_id=?', (pid,)).fetchone()
        rejected = {r[0] for r in db.execute("SELECT dst FROM relations WHERE src=? AND kind='cites' AND manual=1 AND status='rejected'", (pid,))}
    payload = json.loads(saved['payload']) if saved else {'items': local_rows(store, paper, papers), 'status': 'cached', 'sources': ['已有引用记录']}
    items = [dict(row, **citation_info(papers[row['paper_id']])) if row.get('paper_id') in papers else row for row in payload['items']]
    items = [dict(row, association_status='manual_rejected') if row.get('paper_id') in rejected else row for row in items]
    return dict(payload, items=items, paper_id=pid, citation=citation_info(paper), updated=saved['updated'] if saved else '', stale=bool(saved and (saved['fingerprint'] != fingerprint(store, paper) or age(saved['updated']) >= TTL)), job={k: task[k] for k in ('status', 'updated', 'error', 'retry_after')} if task else {'status': 'cached'}, total=len(items), data_note='被引量来自标明的数据源；参考文献表与本库关联数均不作为被引量。')

def cache_raw(store, wid):
    path = store.paths.cache / 'references/openalex' / f'{wid}.json'
    saved = read_json(path, {})
    if saved.get('raw'):
        return saved
    legacy = store.paths.cache / 'openalex/works' / f'{wid}.json'
    raw = read_json(legacy, {})
    if raw.get('id') and 'publication_year' in raw:
        return {'raw': raw, 'updated': datetime.fromtimestamp(legacy.stat().st_mtime, TZ).isoformat(timespec='seconds')}
    return {}

def ingest(store, raw, stamp, papers=None):
    data = map_work(raw)
    papers = store.papers() if papers is None else papers
    same = [p for p in papers.values() if p.get('openalex_id') == data['openalex_id'] or (data.get('doi') and (p.get('doi') or '').lower().removeprefix('https://doi.org/') == data['doi'].lower())]
    same.sort(key=lambda p: p.get('kind') != 'library')
    fields = {k: data[k] for k in ('openalex_id', 'refs', 'oa_url', 'oa_landing_url', 'cited_by', 'citation_count', 'citation_source')}
    fields.update(citation_updated=stamp, metadata_updated=stamp)
    if same:
        old = same[0]
        for k in ('doi', 'authors', 'abstract'):
            if not old.get(k) and data.get(k):
                fields[k] = data[k]
        result = store.patch_paper(old['id'], fields)
        papers[result['id']] = result
        return result
    data.update(fields)
    store.put_papers([data])
    papers[data['id']] = data
    return data

def crossref(doi):
    url = 'https://api.crossref.org/works/' + quote(doi, safe='')
    with urlopen(Request(url, headers={'User-Agent': 'LiteratureWorkbench/1.0', 'Accept': 'application/json'}), timeout=25) as response:
        return json.load(response)['message']

def pdf_rows(store, paper, papers):
    from kbapp.library.recognition import pdf_path
    from kbapp.library.reference_extraction import process_pdf
    path = pdf_path(store, paper)
    if not path or not path.is_file():
        return []
    refs = process_pdf(str(path), paper.get('citekey') or paper['id'])
    return [entry(dict(r, origin='pdf_reference'), papers) for r in refs]

def collect(store, paper_id, force=False):
    papers = store.papers()
    paper = papers[paper_id]
    client = OpenAlex(store, allow_anonymous=True)
    with store.db() as db:
        previous = db.execute('SELECT * FROM reference_lists WHERE paper_id=?', (paper_id,)).fetchone()
    changed = bool(previous and previous['fingerprint'] != fingerprint(store, paper))
    rows = []
    sources = []
    errors = []
    raw = None
    stamp = ''
    wid = paper.get('openalex_id')
    cached = cache_raw(store, wid) if wid else {}
    if cached and (not force) and (age(cached['updated']) < TTL):
        raw = cached['raw']
        stamp = cached['updated']
    else:
        try:
            if wid:
                raw = client.get('works/' + wid)
            elif paper.get('doi'):
                raw = client.get('works/https://doi.org/' + quote(paper['doi'].removeprefix('https://doi.org/'), safe='/'))
            else:
                hits = client.get('works', {'search': paper['title'], 'per_page': 5}).get('results', [])
                raw = next((h for h in hits if norm(h.get('title')) == norm(paper['title']) and h.get('publication_year') == paper.get('year')), None)
                if not raw:
                    raise ValueError('题名与年份没有精确匹配，保留PDF与已有引用记录')
            if not isinstance(raw, dict) or not raw.get('id'):
                raise ValueError('返回的文献元数据无效')
            if wid and raw['id'].rsplit('/', 1)[-1] != wid:
                raise ValueError('返回的 OpenAlex ID 不一致')
            doi = (paper.get('doi') or '').lower().removeprefix('https://doi.org/')
            if not wid and doi and ((raw.get('doi') or '').lower().removeprefix('https://doi.org/') != doi):
                raise ValueError('返回的 DOI 不一致，未关联该文献')
            stamp = now()
            wid = raw['id'].rsplit('/', 1)[-1]
            atomic(store.paths.cache / 'references/openalex' / f'{wid}.json', {'raw': raw, 'updated': stamp})
        except Exception as exc:
            raw = cached.get('raw')
            stamp = cached.get('updated', '')
            errors.append('OpenAlex：' + (str(exc) if isinstance(exc, ValueError) else type(exc).__name__))
    if raw:
        paper = ingest(store, raw, stamp, papers)
        sources.append('OpenAlex')
        refs = paper.get('refs', [])
        missing = []
        for ref in refs:
            saved = cache_raw(store, ref)
            if saved and (not force) and (age(saved['updated']) < TTL):
                ingest(store, saved['raw'], saved['updated'], papers)
            else:
                missing.append(ref)
        for start in range(0, len(missing), 100):
            try:
                batch = client.get('works', {'filter': 'openalex_id:' + '|'.join(missing[start:start + 100]), 'per_page': 100}).get('results', [])
                for hit in batch:
                    ref = hit['id'].rsplit('/', 1)[-1]
                    updated = now()
                    atomic(store.paths.cache / 'references/openalex' / f'{ref}.json', {'raw': hit, 'updated': updated})
                    ingest(store, hit, updated, papers)
            except Exception as exc:
                errors.append('参考文献元数据：' + (str(exc) if isinstance(exc, ValueError) else type(exc).__name__))
                break
        papers = store.papers()
        rows = [entry({'openalex_id': ref, 'title': '待补全元数据 · ' + ref, 'origin': 'OpenAlex'}, papers) for ref in refs]
    elif paper.get('doi') and client.calls < client.cfg['external_requests_per_run']:
        try:
            data = crossref(paper['doi'].removeprefix('https://doi.org/'))
            if data.get('DOI', '').lower() != paper['doi'].lower().removeprefix('https://doi.org/'):
                raise ValueError('Crossref DOI 不一致')
            stamp = now()
            count = data.get('is-referenced-by-count')
            if valid_count(count):
                paper = store.patch_paper(paper_id, {'citation_count': count, 'citation_source': 'Crossref', 'citation_updated': stamp})
            from kbapp.library.reference_extraction import parse_entry
            for index, ref in enumerate(data.get('reference', []), 1):
                parsed = parse_entry(ref.get('unstructured', ''))
                parsed.update(doi=ref.get('DOI') or parsed.get('doi'), title=ref.get('article-title') or parsed.get('title'), year=int(ref['year']) if str(ref.get('year', '')).isdigit() else parsed.get('year'), origin='Crossref', ref_index=None)
                rows.append(entry(parsed, store.papers()))
            sources.append('Crossref')
        except Exception as exc:
            errors.append('Crossref：' + (str(exc) if isinstance(exc, ValueError) else type(exc).__name__))
    if not rows:
        parsed = pdf_rows(store, paper, store.papers())
        if parsed:
            rows = parsed
            sources.append('PDF参考文献解析')
    local = local_rows(store, paper, store.papers())
    if changed and rows:
        local = []
    if changed and (not rows) and local:
        errors.append('来源版本已变化，以下已有引用记录尚待重新核对')
        local = [dict(r, origin='旧引用缓存，待重核') for r in local]
    rows = combine(rows + local)
    if not sources and (not rows):
        raise ValueError('；'.join(errors) or '未找到可解析的参考文献，可能缺少PDF、为扫描件或来源未收录')
    if errors:
        with store.db() as db:
            old = db.execute('SELECT payload FROM reference_lists WHERE paper_id=?', (paper_id,)).fetchone()
        if old:
            previous_rows = json.loads(old[0])['items']
            if changed:
                current_ids = {r['id'] for r in rows}
                previous_rows = [dict(r, origin='旧引用缓存，待重核') for r in previous_rows if r['id'] not in current_ids]
            rows = combine(rows + previous_rows)
    papers = store.papers()
    unknown = sum((not row.get('paper_id') for row in rows))
    for row in rows:
        target = row.get('paper_id')
        if target and target != paper_id:
            store.relation({'src': paper_id, 'dst': target, 'kind': 'cites', 'status': 'confirmed', 'level': 'citation', 'explanation': row['origin'] + ' 参考文献记录，仅证明引用事实', 'evidence': [{'origin': row['origin'], 'quote': row.get('raw', ''), 'ref_index': row.get('ref_index')}]})
    if unknown:
        errors.append(f'{unknown} 条参考文献尚未精确匹配到元数据，被引量可能未知')
    payload = {'items': rows, 'status': 'partial' if errors else 'complete', 'sources': sources or ['已有引用记录'], 'gaps': list(dict.fromkeys(errors)), 'order_note': 'PDF序号仅在PDF解析来源提供；OpenAlex／Crossref列表按元数据顺序显示'}
    with store.db() as db:
        db.execute('INSERT OR REPLACE INTO reference_lists VALUES(?,?,?,?)', (paper_id, fingerprint(store, papers.get(paper_id, paper)), json.dumps(payload, ensure_ascii=False), now()))
    store.set_state('reference_graph_dirty', True)
    return payload

def work_one(store):
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute("SELECT * FROM reference_jobs WHERE status='queued' ORDER BY priority,requested LIMIT 1").fetchone()
        if not row:
            return False
        job = dict(row)
        db.execute("UPDATE reference_jobs SET status='running',updated=? WHERE paper_id=?", (now(), job['paper_id']))
    try:
        result = collect(store, job['paper_id'], bool(job['force']))
        status = result['status']
        error = '；'.join(result['gaps'])
        retry = time.time() + 3600 if status == 'partial' else 0
    except Exception as exc:
        status = 'deferred'
        error = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        retry = time.time() + 3600
    with store.db() as db:
        db.execute('UPDATE reference_jobs SET status=?,updated=?,error=?,retry_after=?,fingerprint=? WHERE paper_id=?', (status, now(), error, retry, fingerprint(store, store.papers()[job['paper_id']]), job['paper_id']))
    return True

def recover_jobs(store):
    with store.db() as db:
        db.execute("UPDATE reference_jobs SET status='queued' WHERE status='running'")

def start_worker(store, stop):
    if os.environ.get('KB_NO_REFERENCE_WORKER'):
        return
    key = str(store.path)
    if key in _threads and _threads[key].is_alive():
        return

    def loop():
        from kbapp.core.locks import update_lock
        from kbapp.workflows.export import export_static
        try:
            with update_lock(store.root, 'workbench-references.lock'):
                recover_jobs(store)
                last_export = 0
                while not stop.is_set():
                    worked = work_one(store)
                    if store.state('reference_graph_dirty') and (not worked or time.monotonic() - last_export >= 60):
                        try:
                            export_static(store)
                            store.set_state('reference_graph_dirty', False)
                            last_export = time.monotonic()
                            store.set_state('reference_graph_sync', {'status': 'complete', 'updated': now()})
                        except Exception as exc:
                            store.set_state('reference_graph_sync', {'status': 'deferred', 'updated': now(), 'error': type(exc).__name__})
                    stop.wait(0.2 if worked else 1)
        except ValueError:
            pass
    thread = threading.Thread(target=loop, daemon=True, name='reference-collector')
    _threads[key] = thread
    thread.start()

def attach_citations(store, result):
    papers = store.papers()
    for key in ('papers', 'results'):
        for row in result.get(key, []):
            row.update(citation_info(papers.get(row.get('paper_id') or row.get('id'), {})))
    return result
