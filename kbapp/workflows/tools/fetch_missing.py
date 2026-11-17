"""kbapp.workflows.tools.fetch_missing：由职责边界组织的知识库能力。"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import kbapp.core.config as C
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'
STAGE = os.path.join(C.BASE, '_index', 'pdf_fetch')

def get_json(url, timeout=25):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8', 'replace'))

def openalex(doi):
    try:
        d = get_json(f'https://api.openalex.org/works/doi:{urllib.parse.quote(doi)}')
    except Exception:
        return []
    urls = []
    for loc in d.get('locations') or []:
        u = loc.get('pdf_url')
        if u:
            urls.append(('openalex', u))
    b = (d.get('best_oa_location') or {}).get('pdf_url')
    if b:
        urls.insert(0, ('openalex-best', b))
    for loc in d.get('locations') or []:
        lp = loc.get('landing_page_url')
        if lp and 'arxiv.org/abs/' in lp:
            urls.append(('arxiv-abs', lp))
    return urls

def unpaywall(doi, email='kb@example.org'):
    try:
        d = get_json(f'https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}?email={email}')
    except Exception:
        return []
    urls = []
    for loc in d.get('oa_locations') or []:
        for k in ('url_for_pdf', 'url'):
            if loc.get(k):
                urls.append((f"unpaywall-{loc.get('host_type', '?')}", loc[k]))
    return urls

def semanticscholar(doi):
    try:
        d = get_json('https://api.semanticscholar.org/graph/v1/paper/DOI:' + urllib.parse.quote(doi) + '?fields=openAccessPdf,externalIds')
    except Exception:
        return []
    out = []
    p = (d.get('openAccessPdf') or {}).get('url')
    if p:
        out.append(('s2', p))
    ax = (d.get('externalIds') or {}).get('ArXiv')
    if ax:
        out.append(('arxiv-id', f'https://arxiv.org/pdf/{ax}'))
    return out

def arxiv_search(title):
    q = urllib.parse.quote(f'ti:"{title[:120]}"')
    try:
        req = urllib.request.Request(f'http://export.arxiv.org/api/query?search_query={q}&max_results=3', headers={'User-Agent': UA})
        with urllib.request.urlopen(req, timeout=25) as r:
            xml = r.read().decode('utf-8', 'replace')
    except Exception:
        return []
    ids = re.findall('<id>http://arxiv.org/abs/([^<]+)</id>', xml)
    return [('arxiv-search', f'https://arxiv.org/pdf/{i}') for i in ids[:2]]

def try_download(url, dest):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/pdf,*/*'})
        with urllib.request.urlopen(req, timeout=60) as r:
            head = r.read(5)
            if head[:4] != b'%PDF':
                return (False, f'not pdf ({head[:4]!r})')
            data = head + r.read()
            if len(data) < 20000:
                return (False, f'too small ({len(data)}B)')
            with open(dest, 'wb') as fh:
                fh.write(data)
            return (True, f'{len(data) // 1024} KB')
    except urllib.error.HTTPError as e:
        return (False, f'HTTP {e.code}')
    except Exception as e:
        return (False, type(e).__name__)

def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument('--download', action='store_true')
    ap.add_argument('--only', default='')
    args = ap.parse_args(argv)
    os.makedirs(STAGE, exist_ok=True)
    rows = [json.loads(line) for line in open(os.path.join(C.index_dir(C.BASE), 'reading_cards_all.jsonl'), encoding='utf-8') if line.strip()]
    papers = {p['citekey']: p for p in (json.loads(line) for line in open(os.path.join(C.index_dir(C.BASE), 'papers_all.jsonl'), encoding='utf-8'))}
    todo = [r['citekey'] for r in rows if r['summary'].startswith(('（摘要级）', '（题录级）'))]
    if args.only:
        todo = [k for k in todo if k in args.only.split(',')]
    report = {}
    for ck in todo:
        p = papers.get(ck, {})
        doi = (p.get('doi') or '').strip()
        title = p.get('title') or ''
        cands = []
        if doi:
            cands += openalex(doi)
            time.sleep(0.3)
            cands += semanticscholar(doi)
            time.sleep(0.3)
            cands += unpaywall(doi)
            time.sleep(0.3)
        if not cands and title:
            cands += arxiv_search(title)
        seen, uniq = (set(), [])
        for src, u in cands:
            if u not in seen:
                seen.add(u)
                uniq.append((src, u))
        entry = {'doi': doi, 'title': title[:80], 'candidates': uniq, 'downloaded': None, 'tried': []}
        if args.download:
            dest = os.path.join(STAGE, ck + '.pdf')
            for src, u in uniq:
                ok, msg = try_download(u, dest)
                entry['tried'].append({'src': src, 'url': u[:120], 'ok': ok, 'msg': msg})
                if ok:
                    entry['downloaded'] = dest
                    break
                time.sleep(0.5)
        report[ck] = entry
        status = 'OK' if entry['downloaded'] else f'{len(uniq)} 个候选'
        print(f'{ck:26} {status}', flush=True)
    out = os.path.join(C.index_dir(C.BASE), 'pdf_fetch_report.json')
    with open(out, 'w', encoding='utf-8') as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    ok = sum((1 for v in report.values() if v['downloaded']))
    print(json.dumps({'total': len(report), 'downloaded': ok, 'report': out}, ensure_ascii=False))
