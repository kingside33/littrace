"""kbapp.library.network：由职责边界组织的知识库能力。"""
from __future__ import annotations
import json
import time
from urllib.request import Request, build_opener
from kbapp.core.io import atomic, digest, now, read_json
from kbapp.integrations.openalex import OpenAlex, SafeRedirect, abstract, map_work, public_url

def hydrate_cached_metadata(store):
    """补齐旧图节点省略、但本机 OpenAlex 缓存已有的作者、DOI 和参考文献。"""
    changed = []
    for p in store.papers().values():
        if p.get('kind') == 'library':
            continue
        wid = p.get('openalex_id')
        if not wid or (p.get('authors') and p.get('doi') and ('refs' in p)):
            continue
        path = store.paths.cache / 'openalex/works' / f'{wid}.json'
        if not path.exists():
            continue
        stamp = path.stat().st_mtime_ns
        if p.get('metadata_cache_stamp') == stamp:
            continue
        raw = read_json(path, {})
        if not raw:
            continue
        data = map_work(raw) if 'publication_year' in raw else raw
        updated = dict(p)
        for key in ('authors', 'doi', 'abstract', 'refs', 'venue', 'oa_url', 'oa_landing_url'):
            if not updated.get(key) and data.get(key):
                updated[key] = data[key]
        if updated.get('doi'):
            updated['doi'] = updated['doi'].removeprefix('https://doi.org/')
        updated['metadata_cache_stamp'] = stamp
        if updated != p:
            updated['metadata_source'] = 'cached_openalex' if p.get('kind') != 'library' else p.get('metadata_source', 'zotero')
            changed.append(updated)
    store.put_papers(changed)
    return len(changed)

def ensure_research_metadata(store, paper):
    """精读任务按需补摘要；旧图的420字缓存明确标为片段，不伪装成完整摘要。"""
    if paper.get('kind') == 'library':
        return paper
    wid = paper.get('openalex_id')
    cached = store.paths.cache / 'metadata' / ((wid or digest(paper['id'])[:20]) + '.json')
    saved = read_json(cached, {})
    if saved:
        data = saved.get('paper', {})
        if data.get('abstract') and (not paper.get('abstract_manual')):
            updated = dict(paper, abstract=data['abstract'], abstract_scope='openalex_full', metadata_checked=saved.get('updated'))
            store.put_papers([updated])
            return updated
        return dict(paper, abstract_scope='unavailable' if not paper.get('abstract') else 'cached_excerpt')
    needs = not paper.get('abstract') or (paper.get('abstract_scope') in {'cached', 'cached_excerpt'} and len(paper.get('abstract', '')) <= 420)
    if needs and wid:
        # `settings()` 把「存了但解不开」表示成 None。若这里只按假值判断，
        # 那个状态会与「没配 key」走同一条路落到下面的兜底，于是整条谱系识别
        # 阶段拿不到摘要、也不留任何说明——把显式失败换成了静默半功能。
        # 所以两态分开：没配照旧安静降级（本来就没有可用动作），读不出则在
        # 返回值上带原因，并在状态里留一条，供设置页/状态页引导「重新填写」。
        key_state = store.secret_state('openalex_key')
        if key_state in ('unreadable', 'platform_unavailable'):
            message = store.secret_advice('openalex_key')
            store.set_state('openalex_key_unusable', {'reason': key_state, 'message': message, 'at': now()})
            return dict(paper, abstract_scope='key_unreadable',
                        abstract_scope_reason=message)
        if key_state == 'ok':
            try:
                from kbapp.core.execution import research_run
                budget_key = 'metadata-budget:' + research_run.get() + ':' + now()[:10]
                with store.db() as db:
                    db.execute('BEGIN IMMEDIATE')
                    row = db.execute('SELECT value FROM state WHERE key=?', (budget_key,)).fetchone()
                    used = json.loads(row[0]) if row else 0
                    if used >= store.settings()['external_requests_per_run']:
                        raise ValueError('本轮元数据请求上限已到')
                    db.execute('INSERT OR REPLACE INTO state VALUES(?,?)', (budget_key, json.dumps(used + 1)))
                data = map_work(OpenAlex(store).get('works/' + wid))
                atomic(cached, {'paper': data, 'updated': now()})
                updated = dict(paper)
                for field in ('abstract', 'doi', 'authors', 'refs', 'venue', 'oa_url', 'oa_landing_url'):
                    if data.get(field) and (not updated.get(field) or field == 'abstract') and (not (field == 'abstract' and paper.get('abstract_manual'))):
                        updated[field] = data[field]
                updated['abstract_scope'] = 'openalex_full' if data.get('abstract') else 'unavailable'
                store.put_papers([updated])
                return updated
            except Exception:
                return dict(paper, abstract_scope='cached_excerpt' if paper.get('abstract') else 'unavailable')
    return dict(paper, abstract_scope='cached_excerpt' if needs and paper.get('abstract') else paper.get('abstract_scope', 'unavailable'))

def expand(store, progress=lambda x: None):
    client = OpenAlex(store)
    if not client.cfg['openalex_key']:
        # Two causes, two messages: "no key yet" and "a key is stored but this
        # process cannot read it". Both stop the run, but the second one must
        # not tell the user to go apply for a key they already have.
        if client.cfg['openalex_key'] is None:
            return {'state': 'awaiting_openalex_key', 'reason': 'unreadable',
                    'message': store.secret_advice('openalex_key'), 'requests': 0}
        return {'state': 'awaiting_openalex_key', 'reason': 'absent',
                'message': '', 'requests': 0}
    with store.db() as db:
        db.execute("UPDATE frontier SET status='pending',cursor='*' WHERE status='complete' AND direction='downstream' AND updated<?", (time.strftime('%Y-%m-%d', time.gmtime(time.time() - 7 * 86400)),))
    papers = store.papers()
    widmap = {p['openalex_id']: p['id'] for p in papers.values() if p.get('openalex_id')}
    doi_map = {p['doi'].lower().removeprefix('https://doi.org/'): p['id'] for p in papers.values() if p.get('kind') == 'library' and p.get('doi')}

    def ingest(raws):
        out = []
        for raw in raws:
            p = map_work(raw)
            if p.get('doi') and p['doi'].lower() in doi_map:
                widmap[p['openalex_id']] = doi_map[p['doi'].lower()]
            if p['openalex_id'] in widmap:
                previous = papers.get(widmap[p['openalex_id']], {})
                p = {**p, **previous, 'openalex_id': p['openalex_id'], 'refs': p['refs'], 'oa_url': p['oa_url'], **{k: p[k] for k in ('cited_by', 'citation_count', 'citation_source', 'citation_updated')}, 'metadata_updated': now()}
                if not p.get('abstract'):
                    p['abstract'] = abstract(raw.get('abstract_inverted_index'))
            out.append(p)
            widmap[p['openalex_id']] = p['id']
            papers[p['id']] = p
        store.put_papers(out)
        return out
    for p in sorted(list(papers.values()), key=lambda x: not bool(x.get('project'))):
        if p.get('kind') != 'library' or p.get('openalex_id'):
            continue
        resolve_id = 'openalex-resolve:' + p['id']
        resolve_fp = digest(str(p.get('doi')) + str(p.get('title')) + str(p.get('year')))
        previous = store.state(resolve_id, {})
        if previous.get('fingerprint') == resolve_fp and time.time() - previous.get('attempt', 0) < 7 * 86400:
            continue
        if client.calls >= min(25, client.cfg['external_requests_per_run']):
            break
        try:
            query = {'filter': 'doi:' + p['doi'], 'per_page': 5} if p.get('doi') else {'search': p.get('title', ''), 'per_page': 5}
            hits = client.get('works', query).get('results', [])
            hit = next((x for x in hits if p.get('doi') and (x.get('doi') or '').lower().removeprefix('https://doi.org/') == p['doi'].lower()), None)
            if not hit:
                from kbapp.library.sources import norm
                hit = next((x for x in hits if norm(x.get('title')) == norm(p.get('title')) and x.get('publication_year') == p.get('year')), None)
            if hit:
                data = map_work(hit)
                data = {**data, **p, 'openalex_id': data['openalex_id'], 'refs': data['refs'], 'oa_url': data['oa_url'], **{k: data[k] for k in ('cited_by', 'citation_count', 'citation_source', 'citation_updated')}}
                store.put_papers([data])
                papers[data['id']] = data
                widmap[data['openalex_id']] = data['id']
                for direction in ('upstream', 'downstream'):
                    enqueue(store, data['openalex_id'], direction, 0)
            store.set_state(resolve_id, {'fingerprint': resolve_fp, 'attempt': time.time(), 'matched': bool(hit)})
        except Exception:
            break
    while True:
        with store.db() as db:
            row = db.execute("SELECT * FROM frontier WHERE status='pending' AND depth<2 ORDER BY depth,updated,wid LIMIT 1").fetchone()
        if not row:
            break
        r = dict(row)
        wid = r['wid']
        depth = r['depth']
        progress(f"库外{r['direction']}第{depth + 1}层：{wid}，请求 {client.calls}")
        try:
            if r['direction'] == 'upstream':
                known = papers.get(widmap.get(wid), {})
                if 'refs' not in known:
                    known = ingest([client.get('works/' + wid)])[0]
                refs = known.get('refs', [])
                start = int(r['cursor']) if r['cursor'] != '*' else 0
                batch = refs[start:start + 50]
                missing = [x for x in batch if x not in widmap]
                if missing:
                    ingest(client.get('works', {'filter': 'openalex_id:' + '|'.join(missing), 'per_page': 100}).get('results', []))
                seed = widmap.get(wid)
                for ref in batch:
                    target = widmap.get(ref)
                    if target and seed:
                        store.relation({'src': seed, 'dst': target, 'kind': 'cites', 'status': 'confirmed', 'level': 'citation', 'explanation': 'OpenAlex referenced_works 引用记录', 'evidence': []})
                    if depth + 1 < 2:
                        enqueue(store, ref, r['direction'], depth + 1)
                nextcursor = str(start + len(batch)) if start + len(batch) < len(refs) else None
            else:
                data = client.get('works', {'filter': 'cites:' + wid, 'per_page': 100, 'cursor': r['cursor']})
                works = ingest(data.get('results', []))
                seed = widmap.get(wid)
                for p in works:
                    if seed:
                        store.relation({'src': p['id'], 'dst': seed, 'kind': 'cites', 'status': 'confirmed', 'level': 'citation', 'explanation': 'OpenAlex 被引分页记录', 'evidence': []})
                    if depth + 1 < 2:
                        enqueue(store, p['openalex_id'], r['direction'], depth + 1)
                nextcursor = data.get('meta', {}).get('next_cursor') if works else None
            with store.db() as db:
                db.execute("UPDATE frontier SET status=?,cursor=?,updated=?,error='' WHERE wid=? AND direction=?", ('pending' if nextcursor else 'complete', nextcursor or '*', now(), wid, r['direction']))
        except Exception as exc:
            message = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            with store.db() as db:
                db.execute('UPDATE frontier SET error=?,updated=? WHERE wid=? AND direction=?', (message, now(), wid, r['direction']))
            return {'state': 'deferred', 'requests': client.calls, 'reason': message}
    return {'state': 'complete', 'requests': client.calls}

def enqueue(store, wid, direction, depth):
    with store.db() as db:
        db.execute('INSERT INTO frontier(wid,direction,depth) VALUES(?,?,?) ON CONFLICT(wid,direction) DO UPDATE SET depth=MIN(depth,excluded.depth)', (wid, direction, depth))

def fetch_approved(store, progress=lambda x: None):
    import pymupdf
    papers = store.papers()
    done = []
    failed = []
    for approval in store.approvals():
        if approval['status'] != 'approved':
            continue
        pid = approval['paper_id']
        p = papers.get(pid, {})
        path = store.paths.files / 'external-pdfs' / (digest(pid)[:24] + '.pdf')
        tmp = path.with_suffix('.partial')
        try:
            url = p.get('oa_url')
            if not url:
                raise ValueError('元数据没有可直接获取的开放 PDF 地址，等待补充来源')
            public_url(url)
            path.parent.mkdir(parents=True, exist_ok=True)
            progress('获取已批准全文：' + p.get('title', pid)[:60])
            opener = build_opener(SafeRedirect())
            with opener.open(Request(url, headers={'User-Agent': 'LiteratureWorkbench/1.0'}), timeout=45) as resp, tmp.open('wb') as f:
                size = 0
                while True:
                    block = resp.read(65536)
                    if not block:
                        break
                    size += len(block)
                    if size > 100 * 1024 * 1024:
                        raise ValueError('全文文件超过100MB，请手动处理')
                    f.write(block)
            with tmp.open('rb') as f:
                if not f.read(1024).lstrip().startswith(b'%PDF-'):
                    raise ValueError('来源返回的内容不是PDF')
            with pymupdf.open(tmp, filetype='pdf') as doc:
                if doc.page_count < 1:
                    raise ValueError('PDF无法读取')
            with store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                live = db.execute('SELECT status FROM approvals WHERE paper_id=?', (pid,)).fetchone()
                if not live or live[0] != 'approved':
                    raise ValueError('审批状态已变化')
                tmp.replace(path)
                db.execute("UPDATE approvals SET status='downloaded',filepath=?,updated=?,error='' WHERE paper_id=?", (str(path), now(), pid))
            done.append(pid)
        except Exception as exc:
            message = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
            with store.db() as db:
                db.execute('UPDATE approvals SET error=?,updated=? WHERE paper_id=?', (message, now(), pid))
            failed.append({'paper_id': pid, 'error': message})
        finally:
            if tmp.exists():
                tmp.unlink()
    return {'downloaded': done, 'failed': failed}
