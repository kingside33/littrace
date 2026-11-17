"""kbapp.research.evidence：由职责边界组织的知识库能力。"""
import json
from kbapp.core.io import digest, jsonl, now
from kbapp.core.text import locate_quote
VERSION = 'research-evidence-v1'
PRIMARY = {'paper_text', 'figure', 'formula', 'metadata', 'external_abstract'}

def artifact(store, kind, subject, fingerprint, payload, status='verified'):
    aid = digest('|'.join((kind, subject, fingerprint, VERSION)))[:32]
    record = dict(payload, id=aid, type=kind, subject=subject, input_fingerprint=fingerprint, generation_version=VERSION, verification_status=status, updated=now())
    with store.db() as db:
        db.execute('INSERT OR REPLACE INTO research_artifacts VALUES(?,?,?,?,?,?,?,?)', (aid, kind, subject, fingerprint, json.dumps(record, ensure_ascii=False), status, VERSION, now()))
    return record

def latest(store, kind, subject):
    with store.db() as db:
        row = db.execute('SELECT payload FROM research_artifacts WHERE type=? AND subject=? ORDER BY updated DESC,rowid DESC LIMIT 1', (kind, subject)).fetchone()
    return json.loads(row[0]) if row else None

def latest_many(store, kind, subjects):
    """H05：批量取最新产物，替代逐篇查询；返回 {subject: record}。"""
    subjects = list(dict.fromkeys(subjects))
    if not subjects:
        return {}
    result = {}
    with store.db() as db:
        for start in range(0, len(subjects), 400):
            chunk = subjects[start:start + 400]
            placeholders = ','.join('?' * len(chunk))
            rows = db.execute('SELECT subject,payload FROM research_artifacts WHERE type=? AND subject IN (' + placeholders + ') ORDER BY updated DESC,rowid DESC', (kind, *chunk)).fetchall()
            for row in rows:
                result.setdefault(row[0], json.loads(row[1]))
    return result

def all_sources(store):
    from kbapp.library.recognition import active_sources
    sources = jsonl(store.active_dir() / 'corpus.jsonl') + active_sources(store)
    from kbapp.library.documents import sources as document_sources
    sources += document_sources(store)
    with store.db() as db:
        rows = db.execute("SELECT payload FROM research_artifacts WHERE type='RawEvidence'").fetchall()
    for row in rows:
        src = json.loads(row[0])
        sources.append(src)
    return {s['id']: s for s in sources}

def evidence_ref(ref, sources, paper_id=None, allow_personal=False, pdf_path=None, *, store=None):
    src = sources.get(ref.get('source_id'))
    quote = ref.get('quote')
    if not src or not isinstance(quote, str) or (not quote.strip()):
        raise ValueError('结论含无法定位的原文摘录')
    quote = locate_quote(src.get('text', ''), quote)
    if not quote:
        raise ValueError('结论含无法定位的原文摘录')
    if paper_id and src.get('paper_id') != paper_id:
        raise ValueError('证据所属论文与结论不一致')
    personal = src.get('kind') in {'obsidian_note', 'zotero_note', 'zotero_annotation'} and allow_personal
    if src.get('derived') or (src.get('kind') not in PRIMARY and (not personal)):
        raise ValueError('论文事实不能以派生笔记或个人笔记作为独立证据')
    start = src.get('page_start', src.get('page'))
    end = src.get('page_end', start)
    url = src.get('url')
    for page in src.get('page_texts', []):
        if locate_quote(page['text'], quote):
            start = end = page['page']
            break
    span = None
    from kbapp.core.text import locate_span
    if src.get('text'):
        span = locate_span(src['text'], quote)
    bbox = []
    if store is not None and src.get('kind') in {'paper_text', 'figure', 'formula'}:
        from kbapp.library.source_documents import resolve_source
        pdf_path, provenance = resolve_source(store, src)
        src = dict(src, **provenance)
    elif src.get('document_id'):
        # A caller-supplied primary PDF is not proof of this attachment's identity.
        pdf_path = None
    if pdf_path and isinstance(start, int) and start > 0:
        from kbapp.library.chunk import pdf_rects
        bbox = pdf_rects(pdf_path, start, quote)
    if start and url:
        from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
        parts = urlsplit(url)
        if url.startswith('zotero://open-pdf/'):
            params = dict(parse_qsl(parts.query))
            params['page'] = str(start)
            url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), parts.fragment))
        elif '/api/file?' in url or '/api/documents/' in url:
            url = urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, 'page=' + str(start)))
    return {'id': 'evidence:' + digest(src['id'] + quote + str(start) + str(end))[:24], 'type': 'EvidenceRef', 'input_fingerprint': digest(src.get('text', '')), 'generation_version': VERSION, 'verification_status': 'located', 'source_id': src['id'], 'paper_id': src.get('paper_id'), 'quote': quote, 'page_start': start, 'page_end': end, 'location_status': 'exact_page' if start and start == end else 'page_range' if start else 'abstract_or_note', 'kind': src['kind'], 'url': url, 'span': span, 'sentence_id': (span['sentence_ids'][0] if span and span['sentence_ids'] else None), 'bbox': bbox, 'bbox_status': 'located' if bbox else ('not_checked' if not pdf_path else 'not_found'), 'source_hash': digest(src.get('text', '')), 'pdf_hash': src.get('pdf_hash'), 'document_id': src.get('document_id'), 'sha256': src.get('sha256'), 'document_version': src.get('document_version'), 'evidence_level': ('legacy_mixed' if src.get('origin') == 'legacy_mixed' else 'personal') if personal else 'abstract' if src['kind'] in {'metadata', 'external_abstract'} else 'fulltext'}

def valid_claim(claim, sources, paper_id=None):
    if not isinstance(claim, dict) or not isinstance(claim.get('text'), str) or (not claim['text'].strip()):
        raise ValueError('结论文本缺失')
    refs = [evidence_ref(r, sources, paper_id, claim.get('claim_type') == 'personal_judgment') for r in claim.get('evidence', []) if isinstance(r, dict)]
    if not refs:
        raise ValueError('结论缺少原始证据')
    numbers = claim.get('numbers', [])
    if not isinstance(numbers, list):
        raise ValueError('数字条件格式错误')
    import re
    scientific_numbers = re.findall('(?<![A-Za-z])\\d+(?:\\.\\d+)?\\s*(?:%|ms\\b|s\\b|m\\b|Hz\\b|次|秒|米)', claim['text'])
    if scientific_numbers and (not numbers):
        raise ValueError('数值结论没有绑定指标、单位、基线和实验条件')
    for number in numbers:
        if not isinstance(number, dict) or any((not str(number.get(k, '')).strip() for k in ('value', 'metric', 'unit', 'baseline', 'conditions', 'source_id'))):
            raise ValueError('数字缺少指标、单位、基线或实验条件')
        matching = [r for r in refs if r['source_id'] == number['source_id']]
        if not matching or not any((str(number['value']) in r['quote'] for r in matching)):
            raise ValueError('数字未出现在绑定的摘录中')
    return dict(claim, evidence=refs, numbers=numbers, id=claim.get('id') or digest(claim['text'] + json.dumps(refs, sort_keys=True))[:24])
