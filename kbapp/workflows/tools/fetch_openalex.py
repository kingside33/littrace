"""kbapp.workflows.tools.fetch_openalex：由职责边界组织的知识库能力。"""
import argparse
import difflib
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
import urllib.parse
from collections import Counter, defaultdict
import kbapp.core.config as C
from kbapp.core.io import append_jsonl, read_json, read_jsonl, write_json
from kbapp.core.progress import Progress
API = 'https://api.openalex.org'
SELECT_BASE = 'id,doi,title,publication_year,publication_date,type,cited_by_count,referenced_works_count,referenced_works,authorships,primary_location,language'
SELECT_ABSTRACT = SELECT_BASE + ',abstract_inverted_index'
BATCH_DOI = 40
BATCH_ID = 50
BATCH_CITES = 8
DOI_PREFIX_RE = re.compile('^(?:https?://(?:dx\\.)?doi\\.org/|doi:\\s*)', re.I)
CJK_RE = re.compile('[\\u4e00-\\u9fff]')

def short_id(x):
    """https://openalex.org/W123 -> W123"""
    if not x:
        return ''
    return str(x).rstrip('/').rsplit('/', 1)[-1]

def norm_doi(s):
    s = (s or '').strip().lower()
    s = DOI_PREFIX_RE.sub('', s)
    return s.rstrip('.,;)]}')

def norm_title(s):
    s = unicodedata.normalize('NFKC', s or '').lower()
    return re.sub('[^0-9a-z\\u4e00-\\u9fff]+', '', s)

def cite_dir(base):
    return C.citation_dir(base)

def oa_dir(base):
    return C.openalex_dir(base)

def works_dir(base):
    return os.path.join(oa_dir(base), 'works')

def _safe_write_work(base, wid, rec):
    write_json(os.path.join(works_dir(base), wid + '.json'), rec, indent=0)

def _read_work(base, wid):
    return read_json(os.path.join(works_dir(base), wid + '.json'))

def load_papers(base):
    return read_jsonl(os.path.join(C.index_dir(base), 'papers_all.jsonl'))

def doi_index(base):
    """扫一遍 works/ 缓存，建 DOI -> W_id 索引。用于解析阶段的断点续跑。"""
    idx = {}
    d = works_dir(base)
    if not os.path.isdir(d):
        return idx
    for name in os.listdir(d):
        if not name.endswith('.json'):
            continue
        rec = read_json(os.path.join(d, name))
        if rec and rec.get('doi'):
            idx.setdefault(rec['doi'], rec['id'])
    return idx

class BudgetExhausted(RuntimeError):
    pass

class KeyRejected(RuntimeError):
    """key 还没激活/失效。OpenAlex 会直接拒绝，此时要退回无 key 请求。"""
_KEY_STATE = {'rejected': False}

def _curl_json(url, timeout=45, retries=4, quiet=False, with_key=False):
    """用 curl 取 JSON。国内网络下 Python 的 SSL 常被重置，curl 稳定得多。"""
    last = ''
    for i in range(retries):
        proc = subprocess.run(['curl', '-sS', '-m', str(timeout), '-H', 'User-Agent: litgraph/1.0', '-w', '\n__HTTP__%{http_code}', url], capture_output=True)
        raw = proc.stdout.decode('utf-8', 'replace')
        body, _, code = raw.rpartition('\n__HTTP__')
        body = body.strip()
        code = code.strip()
        if code in ('401', '403') and with_key:
            raise KeyRejected(f'HTTP {code} {body[:80]}')
        if proc.returncode == 0 and body:
            try:
                data = json.loads(body)
            except Exception as exc:
                last = f'json: {exc} {body[:120]}'
            else:
                if isinstance(data, dict) and data.get('error'):
                    msg = str(data.get('error'))
                    low = msg.lower()
                    if 'budget' in low or 'rate' in low:
                        raise BudgetExhausted(msg)
                    if with_key and ('key' in low or 'unauthor' in low or 'forbidden' in low):
                        raise KeyRejected(msg)
                    last = msg
                else:
                    return data
        else:
            last = (proc.stderr.decode('utf-8', 'replace') or f'HTTP {code}')[:160]
        if not quiet:
            print(f'    retry {i + 1}/{retries}: {last}', flush=True)
        time.sleep(1.2 * 2 ** i)
    raise RuntimeError(f'curl 失败: {last}\n{url[:200]}')

def _query(base, params, select, per_page=50, timeout=45, quiet=False):
    mailto = getattr(C, 'OPENALEX_MAILTO', '') or ''
    if mailto:
        params = params + f'&mailto={urllib.parse.quote(mailto)}'
    tail = f'&select={select}&per-page={per_page}'
    key = get_api_key()
    if key and (not _KEY_STATE['rejected']):
        url = f'{API}/works?{params}&api_key={urllib.parse.quote(key)}{tail}'
        try:
            data = _curl_json(url, timeout=timeout, quiet=quiet, with_key=True)
        except KeyRejected as exc:
            _KEY_STATE['rejected'] = True
            print(f'    [key 未生效，本次改用无 key 请求] {str(exc)[:100]}', flush=True)
            data = _curl_json(f'{API}/works?{params}{tail}', timeout=timeout, quiet=quiet)
    else:
        data = _curl_json(f'{API}/works?{params}{tail}', timeout=timeout, quiet=quiet)
    meta = data.get('meta') or {}
    cost = float(meta.get('cost_usd') or 0.0)
    return (data, cost)

def get_api_key():
    from kbapp.storage.store import Store
    store = Store()
    key = os.environ.get('OPENALEX_API_KEY', '').strip() or store.settings().get('openalex_key', '')
    if key:
        return key
    legacy = store.paths.state / 'legacy-openalex.key'
    return legacy.read_text(encoding='utf-8').strip() if legacy.exists() else ''

def _key_paths():
    from kbapp.core.paths import Paths
    return [str(Paths.from_base().state / 'settings.json')]

def set_api_key(key):
    from kbapp.storage.store import Store
    store = Store()
    store.save_settings({'openalex_key': key.strip()})
    return str(store.paths.state / 'settings.json')

def _log_request(base, kind, n_results, cost, note=''):
    append_jsonl(os.path.join(oa_dir(base), 'fetch_log.jsonl'), [{'t': time.strftime('%Y-%m-%d %H:%M:%S'), 'kind': kind, 'n': n_results, 'cost_usd': cost, 'note': note}])

def _bump_state(base, cost=0.0, **kv):
    path = os.path.join(oa_dir(base), 'state.json')
    st = read_json(path, {}) or {}
    today = time.strftime('%Y-%m-%d')
    if st.get('cost_date') != today:
        st['cost_date'] = today
        st['cost_today'] = 0.0
    st['cost_today'] = round(float(st.get('cost_today', 0.0)) + cost, 6)
    st['cost_usd'] = round(float(st.get('cost_usd', 0.0)) + cost, 6)
    st['requests'] = int(st.get('requests', 0)) + 1
    st['last_run'] = time.strftime('%Y-%m-%d %H:%M:%S')
    st.update(kv)
    write_json(path, st)
    return st

def daily_budget():
    """有可用 key 是 $1/天，否则 $0.10/天（按 IP 共享）。"""
    if get_api_key() and (not _KEY_STATE['rejected']):
        return 0.95
    return 0.09

def check_budget(base, budget=None):
    st = read_json(os.path.join(oa_dir(base), 'state.json'), {}) or {}
    today = time.strftime('%Y-%m-%d')
    spent = float(st.get('cost_today', 0.0)) if st.get('cost_date') == today else 0.0
    cap = daily_budget() if budget is None else budget
    if spent >= cap:
        raise BudgetExhausted(f'今日已花 ${spent:.3f}（额度 ${cap}）。' + ('请等明日额度。' if get_api_key() else '可设置 OPENALEX_API_KEY 提到 $1/天：python -I fetch_openalex.py --set-key <你的key>'))

def _abstract_snippet(inv, limit=420):
    if not inv:
        return ''
    pos = []
    for word, idxs in inv.items():
        for i in idxs:
            pos.append((i, word))
    pos.sort()
    text = ' '.join((w for _, w in pos))
    return text[:limit]

def trim_work(w, with_abstract=False):
    loc = w.get('primary_location') or {}
    src = loc.get('source') or {}
    rec = {'id': short_id(w.get('id')), 'doi': norm_doi(w.get('doi')), 'title': (w.get('title') or '').strip(), 'year': w.get('publication_year'), 'date': w.get('publication_date'), 'type': w.get('type') or '', 'cited_by': int(w.get('cited_by_count') or 0), 'refs_count': int(w.get('referenced_works_count') or 0), 'refs': [short_id(x) for x in w.get('referenced_works') or []], 'venue': (src.get('display_name') or '').strip(), 'lang': w.get('language') or '', 'authors': [(a.get('author') or {}).get('display_name') or '' for a in w.get('authorships') or []][:12]}
    if with_abstract:
        rec['abstract'] = _abstract_snippet(w.get('abstract_inverted_index'))
    return rec

def resolve(base, limit=0, do_title=True):
    """库内文献 -> OpenAlex work。DOI 批量，标题检索兜底。"""
    papers = load_papers(base)
    out_path = os.path.join(oa_dir(base), 'resolve.jsonl')
    done = {r['citekey'] for r in read_jsonl(out_path)}
    rows = []
    todo = [p for p in papers if p.get('citekey') and p['citekey'] not in done]
    if limit:
        todo = todo[:limit]
    with_doi = [p for p in todo if norm_doi(p.get('doi'))]
    no_doi = [p for p in todo if not norm_doi(p.get('doi'))]
    print(f'resolve: {len(todo)} 篇待解析（有 DOI {len(with_doi)}，无 DOI {len(no_doi)}）', flush=True)
    cached = doi_index(base)
    need = [p for p in with_doi if norm_doi(p['doi']) not in cached]
    print(f'  其中 {len(with_doi) - len(need)} 篇已有缓存，实际请求 {len(need)} 篇', flush=True)
    by_doi = {}
    bar = Progress(len(need), prefix='  DOI ', enabled=bool(need))
    for i in range(0, len(need), BATCH_DOI):
        chunk = need[i:i + BATCH_DOI]
        check_budget(base)
        filt = '|'.join((norm_doi(p['doi']) for p in chunk))
        params = 'filter=doi:' + urllib.parse.quote(filt, safe='|:/')
        try:
            data, cost = _query(base, params, SELECT_BASE, per_page=len(chunk) + 10)
        except BudgetExhausted:
            raise
        for w in data.get('results', []):
            by_doi[norm_doi(w.get('doi'))] = trim_work(w)
        _log_request(base, 'resolve_doi', len(data.get('results', [])), cost, f'batch {len(chunk)}')
        _bump_state(base, cost)
        bar.update(len(chunk))
    bar.close()
    for p in with_doi:
        d = norm_doi(p.get('doi'))
        wid = by_doi.get(d) or cached.get(d)
        if wid:
            if d not in by_doi:
                w = _read_work(base, wid) or {}
            else:
                w = by_doi[d]
            rows.append({'citekey': p['citekey'], 'openalex_id': wid, 'method': 'doi', 'score': 1.0, 'year': w.get('year'), 'title': w.get('title') or ''})
        else:
            rows.append({'citekey': p['citekey'], 'openalex_id': '', 'method': 'none', 'score': 0.0, 'year': p.get('year'), 'title': p.get('title') or ''})
    if do_title:
        cand = [p for p in no_doi if len(norm_title(p.get('title'))) >= 12 and (not CJK_RE.search(p.get('title') or ''))]
        print(f'  标题检索兜底：{len(cand)} 篇', flush=True)
        bar = Progress(len(cand), prefix='  title ', enabled=bool(cand))
        for p in cand:
            check_budget(base)
            nt = norm_title(p.get('title'))
            q = re.sub('[*?]+', ' ', p['title']).strip()
            params = 'search=' + urllib.parse.quote(q[:180])
            if p.get('year'):
                params += f"&filter=publication_year:{int(p['year'])}"
            try:
                data, cost = _query(base, params, SELECT_BASE, per_page=5)
            except BudgetExhausted:
                bar.close('预算用尽')
                break
            except RuntimeError:
                rows.append({'citekey': p['citekey'], 'openalex_id': '', 'method': 'none', 'score': 0.0, 'year': p.get('year'), 'title': p.get('title') or ''})
                _log_request(base, 'resolve_title', 0, 0.0, f"{p['citekey']} ERR")
                bar.update(1)
                continue
            best, best_score = (None, 0.0)
            for w in data.get('results', []):
                sc = difflib.SequenceMatcher(None, nt, norm_title(w.get('title'))).ratio()
                if sc > best_score:
                    best, best_score = (w, sc)
            _log_request(base, 'resolve_title', len(data.get('results', [])), cost, p['citekey'])
            _bump_state(base, cost)
            if best and best_score >= 0.88:
                tw = trim_work(best)
                _safe_write_work(base, tw['id'], tw)
                rows.append({'citekey': p['citekey'], 'openalex_id': tw['id'], 'method': 'title', 'score': round(best_score, 3), 'year': tw['year'], 'title': tw['title']})
            else:
                rows.append({'citekey': p['citekey'], 'openalex_id': '', 'method': 'none', 'score': round(best_score, 3), 'year': p.get('year'), 'title': p.get('title') or ''})
            bar.update(1)
        bar.close()
    covered = {r['citekey'] for r in rows}
    for p in no_doi:
        if p['citekey'] not in covered:
            rows.append({'citekey': p['citekey'], 'openalex_id': '', 'method': 'none', 'score': 0.0, 'year': p.get('year'), 'title': p.get('title') or ''})
    append_jsonl(out_path, rows)
    hit = sum((1 for r in rows if r['openalex_id']))
    print(f'resolve 完成：新增 {len(rows)} 行，命中 {hit} ({100.0 * hit / max(1, len(rows)):.1f}%)', flush=True)
    return rows

def ancestor_candidates(base):
    """统计库外 work 被多少篇库内文献引用。纯本地计算，不花请求。"""
    papers = load_papers(base)
    lib = {p['citekey'] for p in papers}
    resolved = {r['citekey']: r['openalex_id'] for r in read_jsonl(os.path.join(oa_dir(base), 'resolve.jsonl')) if r.get('openalex_id')}
    lib_ids = set(resolved.values())
    counts = Counter()
    cited_by_lib = defaultdict(set)
    for ck, wid in resolved.items():
        w = _read_work(base, wid)
        if not w:
            continue
        for rid in w.get('refs', []):
            if rid in lib_ids:
                continue
            counts[rid] += 1
            cited_by_lib[rid].add(ck)
    return (counts, cited_by_lib, lib_ids, resolved, lib)

def fetch_ancestors(base, min_cites=2, limit=0):
    counts, cited_by_lib, lib_ids, resolved, lib = ancestor_candidates(base)
    keep = [wid for wid, n in counts.items() if n >= min_cites]
    keep.sort(key=lambda w: -counts[w])
    if limit:
        keep = keep[:limit]
    print(f'ancestors: 库外被引候选 {len(counts)} 个，≥{min_cites} 次者 {len(keep)} 个', flush=True)
    todo = [w for w in keep if not _read_work(base, w)]
    print(f'  已缓存 {len(keep) - len(todo)}，待取 {len(todo)}', flush=True)
    bar = Progress(len(todo), prefix='  fetch ', enabled=bool(todo))
    for i in range(0, len(todo), BATCH_ID):
        chunk = todo[i:i + BATCH_ID]
        check_budget(base)
        params = 'filter=openalex_id:' + '|'.join(chunk)
        try:
            data, cost = _query(base, params, SELECT_ABSTRACT, per_page=len(chunk) + 10)
        except BudgetExhausted:
            bar.close('预算用尽')
            break
        for w in data.get('results', []):
            _safe_write_work(base, short_id(w.get('id')), trim_work(w, with_abstract=True))
        _log_request(base, 'ancestor', len(data.get('results', [])), cost, f'batch {len(chunk)}')
        _bump_state(base, cost)
        bar.update(len(chunk))
    bar.close()
    rows = []
    for wid in keep:
        w = _read_work(base, wid)
        if not w:
            continue
        rows.append({'openalex_id': wid, 'title': w.get('title', ''), 'year': w.get('year'), 'venue': w.get('venue', ''), 'cited_by': w.get('cited_by', 0), 'lib_cites': counts[wid], 'cited_by_lib': sorted(cited_by_lib[wid]), 'authors': w.get('authors', []), 'abstract': w.get('abstract', '')})
    write_json(os.path.join(oa_dir(base), 'ancestors.json'), {'min_cites': min_cites, 'rows': rows})
    print(f'ancestors 完成：{len(rows)} 个库外祖先节点', flush=True)
    return rows

def fetch_citers(base, min_lib=2, max_cited=6000, per_page=100):
    """库外后代：引用 ≥min_lib 篇库内文献的 work。

    对高被引的经典文献，其被引列表动辄上万条，翻页不现实，因此跳过
    cited_by_count > max_cited 的节点（它们的后代几乎不可能落在你的方向上）。
    """
    resolved = {r['citekey']: r['openalex_id'] for r in read_jsonl(os.path.join(oa_dir(base), 'resolve.jsonl')) if r.get('openalex_id')}
    targets = []
    for ck, wid in resolved.items():
        w = _read_work(base, wid)
        if w and 0 < w.get('cited_by', 0) <= max_cited:
            targets.append(wid)
    print(f'citers: {len(targets)} 篇库内文献待取被引（跳过 cited_by>{max_cited} 的经典文献）', flush=True)
    hit_by = defaultdict(set)
    bar = Progress(len(targets), prefix='  citers ', enabled=bool(targets))
    for i in range(0, len(targets), BATCH_CITES):
        chunk = targets[i:i + BATCH_CITES]
        check_budget(base)
        params = 'filter=cites:' + '|'.join(chunk) + '&sort=cited_by_count:desc'
        try:
            data, cost = _query(base, params, SELECT_BASE, per_page=per_page)
        except BudgetExhausted:
            bar.close('预算用尽')
            break
        for w in data.get('results', []):
            cid = short_id(w.get('id'))
            refs = set(w.get('referenced_works') or [])
            refs = {short_id(r) for r in refs}
            shared = refs & set(chunk)
            if shared:
                hit_by[cid] |= shared
                tw = trim_work(w)
                if not _read_work(base, cid):
                    _safe_write_work(base, cid, tw)
        _log_request(base, 'citers', len(data.get('results', [])), cost, f'batch {len(chunk)}')
        _bump_state(base, cost)
        bar.update(len(chunk))
    bar.close()
    rows = []
    for cid, libs in hit_by.items():
        if len(libs) < min_lib:
            continue
        w = _read_work(base, cid) or {}
        rows.append({'openalex_id': cid, 'title': w.get('title', ''), 'year': w.get('year'), 'venue': w.get('venue', ''), 'cited_by': w.get('cited_by', 0), 'lib_cites': len(libs), 'cites_lib': sorted(libs), 'authors': w.get('authors', [])})
    rows.sort(key=lambda r: (-r['lib_cites'], -(r['year'] or 0)))
    write_json(os.path.join(oa_dir(base), 'citers.json'), {'min_lib': min_lib, 'rows': rows})
    print(f'citers 完成：{len(rows)} 个库外后代节点', flush=True)
    return rows

def probe(base):
    print('== OpenAlex 探测 ==', flush=True)
    key = get_api_key()
    print(f"API key: {('已配置（$1/天）' if key else '未配置（$0.10/天，按 IP 共享）')}", flush=True)
    t0 = time.time()
    data, cost = _query(base, 'filter=doi:10.1109/tssc.1968.300136', SELECT_BASE, per_page=1)
    dt = time.time() - t0
    notice = (data.get('meta') or {}).get('notice') or ''
    key_ok = bool(key) and 'No API key' not in notice
    print(f"连通性: OK  {dt:.2f}s  cost=${cost:.5f}  hit={len(data.get('results', []))}  key生效={key_ok}", flush=True)
    papers = load_papers(base)
    sample = [p for p in papers if norm_doi(p.get('doi'))][:30]
    if not sample:
        print('没有带 DOI 的文献，无法评估命中率', flush=True)
        return
    filt = '|'.join((norm_doi(p['doi']) for p in sample))
    params = 'filter=doi:' + urllib.parse.quote(filt, safe='|:/')
    data, cost = _query(base, params, SELECT_BASE, per_page=60)
    got = {norm_doi(w.get('doi')) for w in data.get('results', [])}
    hit = sum((1 for p in sample if norm_doi(p['doi']) in got))
    print(f'DOI 命中率: {hit}/{len(sample)} ({100.0 * hit / len(sample):.0f}%)', flush=True)
    st = _bump_state(base, cost, last_probe=time.strftime('%Y-%m-%d %H:%M:%S'))
    cap = daily_budget()
    print(f"今日花费: ${st.get('cost_today', 0):.4f} / 额度 ${cap:.2f}", flush=True)
    if not key:
        print('提示: python -I fetch_openalex.py --set-key <key> 可提到 $1/天。', flush=True)
    write_json(os.path.join(cite_dir(base), 'probe.json'), {'ok': True, 'latency_s': round(dt, 2), 'key': key_ok, 'doi_hit': hit, 'doi_sample': len(sample), 'cost_today': st.get('cost_today', 0), 'budget': cap, 't': time.strftime('%Y-%m-%d %H:%M:%S')})

def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument('--base', default=C.BASE)
    ap.add_argument('--probe', action='store_true')
    ap.add_argument('--resolve', action='store_true')
    ap.add_argument('--ancestors', action='store_true')
    ap.add_argument('--citers', action='store_true')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--limit', type=int, default=0)
    ap.add_argument('--no-title', action='store_true', help='跳过标题检索兜底')
    ap.add_argument('--min-cites', type=int, default=None)
    ap.add_argument('--max-cited', type=int, default=6000)
    ap.add_argument('--set-key', default='', metavar='KEY', help='把 OpenAlex API key 写入本机设置（加密保存）')
    ap.add_argument('--show-key', action='store_true', help='显示 key 来源（打码）')
    args = ap.parse_args(argv)
    base = os.path.abspath(args.base)
    if args.set_key:
        p = set_api_key(args.set_key)
        k = args.set_key.strip()
        print(f'已写入 {p}（{k[:4]}…{k[-4:]}，共 {len(k)} 字符）')
        print('验证：python -I fetch_openalex.py --probe')
        return
    if args.show_key:
        k = get_api_key()
        if not k:
            print('未配置 API key（免费 $0.10/天，按 IP 共享）')
        else:
            src = '环境变量 OPENALEX_API_KEY'
            if os.environ.get('OPENALEX_API_KEY', '').strip() != k:
                src = _key_paths()[0]
            print(f'已配置：{k[:4]}…{k[-4:]}（{len(k)} 字符，来源：{src}）')
            print(f'今日额度：${daily_budget():.2f}')
        return
    for d in (cite_dir(base), oa_dir(base), works_dir(base)):
        os.makedirs(d, exist_ok=True)
    if args.probe:
        probe(base)
        return
    if args.all or args.resolve:
        resolve(base, limit=args.limit, do_title=not args.no_title)
    if args.all or args.ancestors:
        mc = args.min_cites if args.min_cites is not None else C.EVOLUTION_MIN_LIB_CITES
        fetch_ancestors(base, min_cites=mc, limit=args.limit)
    if args.all or args.citers:
        fetch_citers(base, max_cited=args.max_cited)
    if not any([args.probe, args.resolve, args.ancestors, args.citers, args.all]):
        ap.print_help()
