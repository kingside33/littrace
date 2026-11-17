"""kbapp.library.documents：由职责边界组织的知识库能力。"""
import json
from pathlib import Path
import re
from urllib.parse import quote
from kbapp.core.io import atomic, digest, now
from kbapp.core.files import file_hash, source_hash
PARSER_VERSION = 2
EXCLUDED_SECTIONS = {'References', 'Acknowledgment'}

def classify(page):
    """文字和图像覆盖共同判定；分类、质量和空白标志各自保存。"""
    import pymupdf
    text = page.get_text()
    area = max(1, page.rect.get_area())
    images = [pymupdf.Rect(i['bbox']) & page.rect for i in page.get_image_info()]
    image_area = min(1, sum((r.get_area() for r in images)) / area)
    chars = len(text.strip())
    bad = text.count('�') / max(chars, 1)
    if chars < 30 and image_area > 0.35:
        kind = 'SCANNED'
    elif bad > 0.02:
        kind = 'LOW_QUALITY'
    elif image_area > 0.35:
        kind = 'MIXED'
    elif chars < 30:
        kind = 'LOW_QUALITY'
    else:
        kind = 'NATIVE_TEXT'
    from kbapp.library.page_ocr import blank_page
    blank = chars < 30 and blank_page(page)
    return {'classification': kind, 'native_chars': chars, 'image_coverage': round(image_area, 4), 'replacement_ratio': bad, 'rotation': page.rotation, 'blank': blank, 'needs_ocr': kind != 'NATIVE_TEXT' and (not blank)}

def register(store, paper, path, attachment_key=''):
    import pymupdf
    path = Path(path).resolve()
    sha = file_hash(path)
    identity = 'doc:' + digest(paper['id'] + '|' + attachment_key + '|' + sha)[:32]
    with pymupdf.open(path) as pdf:
        count = pdf.page_count
    payload = {'primary': str(path) == str((store.base / paper.get('pdf', '')).resolve()), 'parser_version': PARSER_VERSION}
    with store.db() as db:
        # C08：同一附件键换路径时身份不变，原地更新路径并复用已解析页面；内容变化（sha 不同）才产生新文档版本
        db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET path=excluded.path,payload=excluded.payload', (identity, paper['id'], attachment_key, sha, str(path), count, json.dumps(payload), now()))
    return get_document(store, identity)

def get_document(store, identity):
    with store.db() as db:
        row = db.execute('SELECT * FROM documents WHERE id=?', (identity,)).fetchone()
    if not row:
        raise ValueError('文档不存在')
    return dict(row)

def page_record(store, identity, number):
    with store.db() as db:
        row = db.execute('SELECT * FROM document_pages WHERE document_id=? AND page=?', (identity, number)).fetchone()
    if not row:
        return None
    result = dict(row)
    result['payload'] = json.loads(result['payload'])
    return result

SECTION_VALUE = {'Abstract': 20, 'Introduction': 12, 'Related': 8, 'Method': 22, 'Results': 22, 'Discussion': 18, 'Conclusion': 14, 'References': -60, 'Acknowledgment': -40}

def ocr_priority(store, document, numbers):
    """D09：按页面质量与研究价值排序 OCR 顺序；失败/待处理与关键章节优先，参考文献后置。"""
    scored = []
    for number in numbers:
        record = page_record(store, document['id'], number)
        score = 0
        if record:
            if record['status'] == 'failed':
                score += 100
            elif record['status'] == 'pending':
                score += 80
            if record.get('classification') in {'SCANNED', 'LOW_QUALITY', 'MIXED'}:
                score += 30
            score += SECTION_VALUE.get(record.get('payload', {}).get('section', ''), 0)
        scored.append((-score, number))
    return [number for _score, number in sorted(scored)]

def process(store, document, pages=None, client=None, budget=0, force=False, progress=lambda x: None, should_stop=lambda: False):
    import pymupdf
    from kbapp.library.chunk import page_text, detect_section
    from kbapp.core.chunking import split_long
    from kbapp.library.page_ocr import recognize_page
    from kbapp.integrations.model_identity import signature
    path = Path(document['path'])
    if file_hash(path) != document['sha256']:
        raise ValueError('PDF已变化，请先同步附件新版本')
    model = signature(client.model, client.url, client.api) if client else None
    complete = failed = pending = used = 0
    section = 'Front'
    with pymupdf.open(path) as pdf:
        selected = ocr_priority(store, document, range(1, len(pdf) + 1)) if pages is None else sorted(set(pages))
        if any((not isinstance(n, int) or n < 1 or n > len(pdf) for n in selected)):
            raise ValueError('页码超出文档范围')
        for n in selected:
            if should_stop():
                break
            old = page_record(store, document['id'], n)
            if old:
                section = old['payload'].get('section', section)
            if not force and old and (old['status'] == 'complete') and (old['payload'].get('parser_version') == PARSER_VERSION):
                if not old['ocr_text'] or model is None or old['payload'].get('model') == model:
                    complete += 1
                    continue
            native = ''
            ocr = ''
            blocks = []
            info = {}
            status = 'pending'
            run_id = None
            try:
                page = pdf[n - 1]
                info = classify(page)
                native = page_text(page)
                if info['needs_ocr']:
                    if not force and old and old['ocr_text'] and (model is None or old['payload'].get('model') == model):
                        ocr = old['ocr_text']
                        info.update({k: old['payload'][k] for k in ('model', 'regions', 'review_status') if k in old['payload']})
                        status = 'complete'
                    elif client and used < budget:
                        used += 1
                        run_id = digest(document['id'] + str(n) + now())
                        with store.db() as db:
                            db.execute('INSERT OR REPLACE INTO model_runs VALUES(?,?,?,?,?,?,?)', (run_id, 'ocr', json.dumps(model), 'running', json.dumps({'document_id': document['id'], 'page': n}), now(), None))
                        client.should_stop = should_stop
                        ocr, regions = recognize_page(client, page, progress)
                        if not ocr.strip():
                            raise ValueError('非空白页面 OCR 返回空文本')
                        info.update(model=model, regions=regions, review_status='pending')
                        status = 'complete'
                    else:
                        pending += 1
                else:
                    status = 'complete'
                text = ocr or native
                for b in page.get_text('dict', flags=pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES)['blocks']:
                    if b['type'] != 0:
                        continue
                    value = '\n'.join((''.join((s['text'] for s in line['spans'])) for line in b['lines']))
                    kind = 'formula' if re.search('[=∑∫≤≥]|\\\\(?:frac|sum|int)\\b', value) else 'text'
                    blocks.append({'kind': kind, 'text': value, 'bbox': list(b['bbox']), 'review_status': 'pending' if kind == 'formula' else 'unreviewed', 'origin': 'native'})
                for image in page.get_image_info():
                    blocks.append({'kind': 'image', 'text': '', 'bbox': list(image['bbox']), 'origin': 'pdf'})
                for match in re.finditer('\\$\\$(.*?)\\$\\$|\\\\\\[(.*?)\\\\\\]', ocr, re.S):
                    blocks.append({'kind': 'formula', 'text': match.group(1) or match.group(2), 'bbox': list(page.rect), 'origin': 'ocr', 'review_status': 'pending'})
                for match in re.finditer('(?<!\\$)\\$(?!\\$)([^$\\n]+)\\$(?!\\$)', ocr):
                    blocks.append({'kind': 'formula', 'text': match.group(1), 'bbox': list(page.rect), 'origin': 'ocr', 'inline': True, 'review_status': 'pending'})
                for match in re.finditer('(?:^\\s*\\|[^\\n]+\\|\\s*$\\n?){2,}', ocr, re.M):
                    blocks.append({'kind': 'table', 'text': match.group(0), 'bbox': list(page.rect), 'origin': 'ocr', 'review_status': 'pending'})
                try:
                    for table in page.find_tables().tables:
                        blocks.append({'kind': 'table', 'text': table.to_markdown(), 'bbox': list(table.bbox), 'cells': table.extract(), 'origin': 'native', 'review_status': 'pending'})
                except Exception as exc:
                    info['table_warning'] = type(exc).__name__
                if ocr:
                    blocks.append({'kind': 'ocr', 'text': ocr, 'bbox': list(page.rect), 'origin': 'ocr', 'review_status': 'pending'})
                info.update(parser_version=PARSER_VERSION, markdown=text, quality='needs_review' if info['needs_ocr'] else 'native', page_label=page.get_label())
                section = next((detect_section(line) for line in text.splitlines()[:15] if detect_section(line)), section)
                info['section'] = section
                if status == 'complete':
                    complete += 1
            except Exception as exc:
                status = 'failed'
                failed += 1
                info.update(error=str(exc), parser_version=PARSER_VERSION)
                info.setdefault('classification', 'FAILED')
            with store.db() as db:
                db.execute('INSERT INTO document_pages VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(document_id,page) DO UPDATE SET classification=excluded.classification,status=excluded.status,native_text=excluded.native_text,ocr_text=excluded.ocr_text,payload=excluded.payload,updated=excluded.updated', (document['id'], n, info.get('classification', 'FAILED'), status, native, ocr, json.dumps(info, ensure_ascii=False), now()))
                if run_id:
                    db.execute('UPDATE model_runs SET status=?,finished=?,payload=? WHERE id=?', (status, now(), json.dumps({'document_id': document['id'], 'page': n, 'error': info.get('error')}), run_id))
                if status == 'failed':
                    continue
                previous_blocks = {r['id']: dict(r) for r in db.execute('SELECT * FROM document_blocks WHERE document_id=? AND page=?', (document['id'], n))}
                for previous in previous_blocks.values():
                    details = json.loads(previous['payload'])
                    if details.get('review_history'):
                        db.execute('INSERT OR IGNORE INTO document_reviews VALUES(?,?,?,?,?)', (digest(previous['id'] + previous['payload']), document['id'], n, previous['payload'], now()))
                db.execute('DELETE FROM document_blocks WHERE document_id=? AND page=?', (document['id'], n))
                db.execute('DELETE FROM document_chunks WHERE document_id=? AND page=?', (document['id'], n))
                sid = digest(document['id'] + '|section|' + section)
                db.execute('INSERT OR IGNORE INTO document_sections VALUES(?,?,?,?)', (sid, document['id'], section, n))
                for i, b in enumerate(blocks):
                    bid = digest(document['id'] + f'|p{n}|b{i}')
                    previous = previous_blocks.get(bid)
                    if previous and previous['text'] == b['text']:
                        details = json.loads(previous['payload'])
                        if details.get('bbox') == b.get('bbox') and details.get('review_history'):
                            b.update(review_history=details['review_history'], review_status=details['review_status'])
                    db.execute('INSERT INTO document_blocks VALUES(?,?,?,?,?,?)', (bid, document['id'], n, b['kind'], b['text'], json.dumps(b, ensure_ascii=False)))
                for i, piece in enumerate(split_long(ocr or native, 768)):
                    cid = 'document:' + digest(document['id'] + f'|p{n}|c{i}|' + piece)
                    db.execute('INSERT INTO document_chunks VALUES(?,?,?,?,?,?)', (cid, document['id'], n, sid, piece, json.dumps({'section': section, 'parser_version': PARSER_VERSION, 'origin': 'paper_ocr' if ocr else 'paper', 'quality': status})))
                if n == 1:
                    from kbapp.core.metadata import normalize_doi
                    paper = json.loads(db.execute('SELECT payload FROM papers WHERE id=?', (document['paper_id'],)).fetchone()[0])
                    for match in re.finditer('10\\.\\d{4,9}/[^\\s<>]+', ocr or native, re.I):
                        proposed = normalize_doi(match[0])
                        if proposed and proposed != normalize_doi(paper.get('doi')):
                            cid = digest(document['id'] + '|doi|' + proposed)
                            db.execute('INSERT OR IGNORE INTO metadata_conflicts(id,paper_id,field,authoritative,proposed,source,created) VALUES(?,?,?,?,?,?,?)', (cid, document['paper_id'], 'doi', json.dumps(paper.get('doi')), json.dumps(proposed), 'pdf_first_page:' + document['id'], now()))
            progress(f"页面解析 {n}/{document['page_count']}：{status}")
    index_citations(store, document)
    return {'document_id': document['id'], 'complete': complete, 'failed': failed, 'pending': pending, 'ocr_calls': used, 'selected': len(selected)}

def _hash_cache(store):
    cache = getattr(store, '_document_hash_cache', None)
    if cache is None:
        from kbapp.core.io import read_json
        cache = read_json(store.paths.cache / 'source_hashes.json', {})
        store._document_hash_cache = cache
    return cache

def _validated_hash(store, path, cache):
    """按 (size, mtime, ctime) 复用上次校验的摘要；文件变化时才重新计算完整哈希。"""
    try:
        st = Path(path).stat()
    except OSError:
        return None
    entry = cache.get(str(path))
    if entry and entry.get('size') == st.st_size and entry.get('mtime') == st.st_mtime_ns and entry.get('ctime') == st.st_ctime_ns:
        return entry.get('sha')
    sha = source_hash(path)
    cache[str(path)] = {'size': st.st_size, 'mtime': st.st_mtime_ns, 'ctime': st.st_ctime_ns, 'sha': sha}
    return sha

def sources(store):
    with store.db() as db:
        rows = db.execute("SELECT c.*,d.paper_id,d.path,d.sha256,p.payload AS paper_payload FROM document_chunks c JOIN documents d ON d.id=c.document_id JOIN papers p ON p.id=d.paper_id JOIN document_pages pg ON pg.document_id=c.document_id AND pg.page=c.page WHERE pg.status!='failed'").fetchall()
        block_rows = db.execute("SELECT b.*,d.paper_id,d.path,d.sha256,p.payload AS paper_payload FROM document_blocks b JOIN documents d ON d.id=b.document_id JOIN papers p ON p.id=d.paper_id JOIN document_pages pg ON pg.document_id=b.document_id AND pg.page=b.page WHERE pg.status!='failed' AND b.kind IN ('formula','table') AND b.text!=''").fetchall()
    result = []
    hashes = {}
    cache = _hash_cache(store)
    primaries = {}
    for row in rows:
        r = dict(row)
        p = json.loads(r['paper_payload'])
        payload = json.loads(r['payload'])
        path = Path(r['path'])
        if r['path'] not in hashes:
            try:
                hashes[r['path']] = _validated_hash(store, path, cache)
            except OSError:
                hashes[r['path']] = None
        if hashes[r['path']] != r['sha256']:
            continue
        if payload.get('section') in EXCLUDED_SECTIONS:
            continue
        primary_document = primaries.setdefault(r['paper_id'], p.get('primary_document') or '')
        result.append({'id': r['id'], 'source_id': r['id'], 'paper_id': r['paper_id'], 'document_id': r['document_id'], 'primary': bool(primary_document and r['document_id'] == primary_document), 'kind': 'paper_text', 'origin': payload['origin'], 'title': p.get('title', ''), 'citekey': p.get('citekey'), 'text': r['text'], 'text_hash': digest(r['text']), 'page_start': r['page'], 'page_end': r['page'], 'section': payload['section'], 'quality': payload['quality'], 'url': f"/api/documents/{quote(r['document_id'], safe='')}/file#page={r['page']}"})
    if cache:
        try:
            atomic(store.paths.cache / 'source_hashes.json', json.dumps(cache, ensure_ascii=False))
        except OSError:
            pass
    for row in block_rows:
        r = dict(row)
        details = json.loads(r['payload'])
        p = json.loads(r['paper_payload'])
        if hashes.get(r['path']) != r['sha256']:
            continue
        identity = 'block:' + r['id'] + ':' + digest(r['text'])[:16]
        result.append({'id': identity, 'source_id': identity, 'paper_id': r['paper_id'], 'document_id': r['document_id'], 'block_id': r['id'], 'kind': 'formula' if r['kind'] == 'formula' else 'figure', 'origin': 'paper_ocr' if details.get('origin') == 'ocr' else 'paper', 'title': p.get('title', ''), 'citekey': p.get('citekey'), 'text': r['text'], 'text_hash': digest(r['text']), 'page_start': r['page'], 'page_end': r['page'], 'page': r['page'], 'section': '公式' if r['kind'] == 'formula' else '表格', 'latex': r['text'] if r['kind'] == 'formula' and details.get('origin') == 'ocr' else '', 'latex_status': details.get('review_status', 'pending'), 'review_status': details.get('review_status', 'pending'), 'url': f"/api/documents/{quote(r['document_id'], safe='')}/file#page={r['page']}"})
    return result

def sync_documents(store, papers, client=None, budget=0, progress=lambda x: None, should_stop=lambda: False):
    results = []
    used = 0
    for p in papers:
        if should_stop():
            break
        attachments = [a for a in p.get('attachments', []) if a.get('exists') and (a.get('content_type') or '').endswith('pdf')]
        if not attachments and p.get('pdf'):
            attachments = [{'abs_path': str(store.base / p['pdf']), 'key': p.get('attachment_key', '')}]
        for a in attachments:
            if should_stop():
                break
            try:
                doc = register(store, p, a['abs_path'], a['key'])
                result = process(store, doc, client=client, budget=max(0, budget - used), progress=progress, should_stop=should_stop)
                used += result['ocr_calls']
                results.append(result)
            except Exception as exc:
                results.append({'paper_id': p['id'], 'attachment_key': a['key'], 'error': str(exc)})
    return results

REF_HEADING = re.compile(r'\s*(?:\d+\.?\s*)?(?:references|bibliography|参考文献|参考书目)\s*[:：]?\s*$', re.I)
REF_ENTRY = re.compile(r'^\s*(?:[\[［(（]\s*(\d{1,3})\s*[\]］)）]|(\d{1,3})\s*[.、)）])\s+(.+)')

def cites_label(text, label):
    """D03：支持 [4–8]、[4,5,8]、全角括号与多标签列表。"""
    if not label:
        return False
    for match in re.finditer(r'[\[［]([0-9,\s\-–—~、，]+)[\]］]', text):
        for part in re.split('[,，、]', match.group(1)):
            part = part.strip()
            if not part:
                continue
            span = re.match(r'^(\d+)\s*[-–—~]\s*(\d+)$', part)
            if span:
                if int(span.group(1)) <= int(label) <= int(span.group(2)):
                    return True
            elif part == label:
                return True
    return False

def cites_author_year(text, raw):
    """D03：作者年份引用；只在参考文献条目能解析出唯一作者与年份时匹配。"""
    year = re.search(r'\b(19|20)\d{2}\b', raw)
    author = re.search(r'^\s*(?:[\[［(（]\d{1,3}[\]］)）]|\d{1,3}[.、)）])?\s*([A-Z][A-Za-z\-]{2,})', raw)
    if not year or not author:
        return False
    surname, y = re.escape(author.group(1)), year.group(0)
    return bool(re.search(surname + r'[^.]{0,60}(?:et al\.?\s*)?[\(（]?' + y, text))

def citation_marker(text, label):
    """D04：返回命中的引用标记原文（含区间与列表形式）。"""
    for match in re.finditer(r'[\[［]([0-9,\s\-–—~、，]+)[\]］]', text or ''):
        for part in re.split('[,，、]', match.group(1)):
            part = part.strip()
            if not part:
                continue
            span = re.match(r'^(\d+)\s*[-–—~]\s*(\d+)$', part)
            if (span and int(span.group(1)) <= int(label) <= int(span.group(2))) or part == label:
                return match.group(0)
    return ''

def author_year_marker(text, raw):
    year = re.search(r'\b(19|20)\d{2}\b', raw)
    author = re.search(r'^\s*(?:[\[［(（]\d{1,3}[\]］)）]|\d{1,3}[.、)）])?\s*([A-Z][A-Za-z\-]{2,})', raw)
    if not year or not author:
        return ''
    match = re.search(re.escape(author.group(1)) + r'[^.]{0,60}(?:et al\.?\s*)?[\(（]?' + year.group(0), text)
    return match.group(0) if match else ''

def citation_surroundings(text, marker):
    """返回引用标记所在句的前一句与后一句。"""
    sentences = [s.strip() for s in re.split(r'(?<=[.!?。！？])\s+', text) if s.strip()]
    for index, sentence in enumerate(sentences):
        if marker in sentence:
            before = sentences[index - 1] if index > 0 else ''
            after = sentences[index + 1] if index + 1 < len(sentences) else ''
            return before, after
    return '', ''

def index_citations(store, document):
    """只把唯一 DOI 命中建立为引用事实，编号上下文不推断方法继承。"""
    from kbapp.core.metadata import normalize_doi
    doi_map = {}
    for p in store.papers().values():
        doi = normalize_doi(p.get('doi'))
        if doi:
            doi_map.setdefault(doi, []).append(p['id'])
    with store.db() as db:
        pages = [dict(r) for r in db.execute('SELECT * FROM document_pages WHERE document_id=? ORDER BY page', (document['id'],))]
    references = []
    body = []
    in_refs = False
    current = None
    for row in pages:
        text = row['ocr_text'] or row['native_text']
        section = json.loads(row['payload']).get('section', '')
        for line in text.splitlines():
            if REF_HEADING.match(line):
                in_refs = True
                continue
            if in_refs:
                match = REF_ENTRY.match(line)
                if match:
                    current = {'label': match.group(1) or match.group(2), 'raw': line, 'page': row['page']}
                    references.append(current)
                elif current:
                    current['raw'] += '\n' + line
            else:
                body.append((row['page'], section, line))
    unique = {}
    for ref in references:
        normalized = re.sub(r'\s+', ' ', ref['raw']).strip()
        unique.setdefault((ref['label'], normalized), ref)
    references = list(unique.values())
    counts = {}
    for ref in references:
        counts[ref['label']] = counts.get(ref['label'], 0) + 1
    with store.db() as db:
        db.execute('DELETE FROM citation_contexts WHERE document_id=?', (document['id'],))
        db.execute('DELETE FROM document_references WHERE document_id=?', (document['id'],))
        for ref in references:
            ambiguous = counts[ref['label']] > 1
            rid = digest(document['id'] + '|ref|' + ref['label'] + ('|' + digest(ref['raw']) if ambiguous else ''))
            m = re.search('10\\.\\d{4,9}/[^\\s<>]+', ref['raw'], re.I)
            doi = normalize_doi(m[0]) if m else ''
            candidates = doi_map.get(doi, [])
            target = candidates[0] if not ambiguous and len(candidates) == 1 and candidates[0] != document['paper_id'] else None
            db.execute('INSERT INTO document_references VALUES(?,?,?,?,?,?,?)', (rid, document['id'], ref['page'], ref['label'], ref['raw'], target, json.dumps({'doi': doi, 'ambiguous': ambiguous, 'match': 'ambiguous_label' if ambiguous else 'unique_doi' if target else 'unresolved'}, ensure_ascii=False)))
            for page, section, text in ([] if ambiguous else body):
                marker = citation_marker(text, ref['label']) or (author_year_marker(text, ref['raw']) if cites_author_year(text, ref['raw']) else '')
                if marker:
                    cid = digest(rid + '|' + str(page) + '|' + text)
                    db.execute('INSERT OR IGNORE INTO citation_contexts VALUES(?,?,?,?,?,?,?)', (cid, rid, document['id'], page, section, text, 'unknown'))
                    before, after = citation_surroundings(text, marker)
                    db.execute('INSERT OR REPLACE INTO citation_context_payload VALUES(?,?,?,?)',
                               (cid, marker, target, json.dumps({'before': before, 'after': after, 'label': ref['label'], 'target_paper_id': target}, ensure_ascii=False)))
