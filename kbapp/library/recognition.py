"""kbapp.library.recognition：由职责边界组织的知识库能力。"""
from pathlib import Path
from kbapp.core.chunking import chunk_paras
from kbapp.integrations.ocr import GLMOCR
import kbapp.core.config as C
from kbapp.core.io import atomic, digest, now, read_json
from kbapp.library.sources import paper_url
from kbapp.integrations.models import request, cancelled
from kbapp.library.page_ocr import recognize_page

def pdf_path(store, paper):
    if paper.get('kind') == 'library':
        return store.base / paper['pdf'] if paper.get('pdf') else None
    entry = next((a for a in store.approvals() if a['paper_id'] == paper['id'] and a['status'] == 'downloaded'), None)
    return Path(entry['filepath']) if entry and entry.get('filepath') else None

def pdf_fingerprint(path):
    stat = path.stat()
    return digest(str(stat.st_mtime_ns) + str(stat.st_size))

def active_sources(store):
    papers = store.papers()
    result = []
    for file in (store.paths.cache / 'recognized_sources').glob('*.json'):
        record = read_json(file, {})
        paper = papers.get(record.get('paper_id'))
        if not paper:
            continue
        path = pdf_path(store, paper)
        if record.get('document_id'):
            from kbapp.library.documents import get_document
            try:
                path = Path(get_document(store, record['document_id'])['path'])
            except ValueError:
                continue
        if path and path.is_file() and (pdf_fingerprint(path) == record.get('pdf_hash')):
            result.extend(record.get('sources', []))
    return result

def recognize_sources(store, paper, sources, progress, should_stop=lambda: False, client=None):
    """按已索引证据的页范围识别；缺页/失败时暂缓，不把 native 文本伪装成 OCR。"""
    import pymupdf
    identities = {s.get('document_id') for s in sources if s.get('document_id')}
    if identities and (not paper.get('document_id')):
        from kbapp.library.documents import get_document
        from kbapp.core.files import file_hash
        result = []
        for identity in sorted(identities):
            document = get_document(store, identity)
            if document['paper_id'] != paper['id'] or file_hash(document['path']) != document['sha256']:
                raise ValueError('证据附件身份或版本不一致')
            selected = [s for s in sources if s.get('document_id') == identity]
            result += recognize_sources(store, dict(paper, pdf=document['path'], attachment_key=document['attachment_key'], document_id=identity, citekey=(paper.get('citekey') or digest(paper['id'])[:16]) + '-' + identity[-8:]), selected, progress, should_stop, client)
        return result
    path = pdf_path(store, paper)
    if not path:
        return [s for s in sources if s.get('paper_id') == paper['id'] and s['kind'] != 'paper_text']
    if not path.is_file():
        raise ValueError('原文附件不可用，无法进行 GLM-OCR 识别：' + paper.get('title', ''))
    client = client or GLMOCR(store=store)
    client.should_stop = should_stop
    from kbapp.integrations.model_identity import signature
    model_identity = signature(client.model, getattr(client, 'url', C.GLM_OCR_URL), getattr(client, 'api', 'ollama'))
    fingerprint = pdf_fingerprint(path)
    key = paper.get('citekey') or digest(paper['id'])[:16]
    saved = store.paths.cache / 'recognized_sources' / (digest(paper['id'])[:16] + '-' + fingerprint[:16] + ('-' + digest(paper['document_id'])[:8] if paper.get('document_id') else '') + '.json')
    record = read_json(saved, {})
    retained = record.get('sources', [])
    page_numbers = set()
    for source in sources:
        if source.get('paper_id') != paper['id'] or source.get('kind') != 'paper_text':
            continue
        start = source.get('page_start') or source.get('page')
        if start:
            page_numbers.update(range(int(start), int(source.get('page_end') or start) + 1))
    with pymupdf.open(path) as doc:
        if not doc.page_count:
            raise ValueError('PDF没有可识别的页面：' + paper.get('title', ''))
        pages = sorted(page_numbers or range(1, doc.page_count + 1))
        for index, number in enumerate(pages, 1):
            if should_stop() or cancelled(store, 'ocr'):
                raise ValueError('GLM-OCR 已停止，完成的识别缓存已保留')
            if number < 1 or number > doc.page_count:
                raise ValueError('索引页码超出 PDF 范围，需先更新该附件的索引')
            target = store.paths.cache / 'ocr_pages' / key / f'p{number:04d}.txt'
            stamp = target.with_suffix('.json')
            previous = read_json(stamp, {})
            cached = previous.get('pdf_hash') == fingerprint and previous.get('model') == client.model and (previous.get('model_identity') == model_identity) and target.is_file()
            progress(f"GLM-OCR 原文识别 {index}/{len(pages)} 页：{paper.get('citekey') or paper['title'][:40]} p.{number}" + ('（复用缓存）' if cached else ''))
            if cached:
                text = target.read_text(encoding='utf-8')
            else:
                if not client.available():
                    raise ValueError('GLM-OCR 未就绪，已暂停原文识别；没有改用关系核验模型代替识别')
                page = doc.load_page(number - 1)
                from kbapp.library.page_ocr import blank_page
                regions = []
                if blank_page(page):
                    text = ''
                else:
                    try:
                        text, regions = recognize_page(client, page, progress)
                    except Exception as exc:
                        raise ValueError(f'{key} p.{number} 识别未完成：{exc}') from None
                if not text.strip() and (not blank_page(page)):
                    raise ValueError('GLM-OCR未识别到非空页面文字，保留任务待重试')
                from kbapp.core.files import file_hash
                atomic(target, text)
                atomic(stamp, {'pdf_hash': fingerprint, 'content_sha256': file_hash(path), 'model': client.model, 'model_identity': model_identity, 'page': number, 'regions': regions, 'updated': now()})
            retained = [r for r in retained if r.get('page_start') != number]
            layout = read_json(stamp, {}).get('regions')
            for n, (text, tokens, _, _) in enumerate(chunk_paras([(number, text)], 512, 64), 1):
                identity = f"ocr:{paper['id']}:" + (paper['document_id'] + ':' if paper.get('document_id') else '') + f'{fingerprint[:16]}:p{number}:c{n}:' + digest(text)[:16]
                retained.append({'id': identity, 'source_id': identity, 'paper_id': paper['id'], 'citekey': paper.get('citekey'), 'document_id': paper.get('document_id'), 'kind': 'paper_text', 'origin': 'paper_ocr', 'title': paper['title'], 'text': text, 'tokens': tokens, 'text_hash': digest(text), 'page_start': number, 'page_end': number, 'section': 'OCR', 'url': paper_url(paper, number), 'source_label': 'GLM-OCR论文原文', 'ocr_provenance': {'model': client.model, 'pdf_hash': fingerprint, 'page': number, 'pdf': str(path), 'cache': str(target), 'regions': layout}})
            atomic(saved, {'paper_id': paper['id'], 'document_id': paper.get('document_id'), 'pdf_hash': fingerprint, 'sources': retained, 'updated': now()})
    return [r for r in retained if r.get('page_start') in pages]

def prepare_relation_sources(store, papers, grouped, select_sources, progress, should_stop=lambda: False, on_stage=lambda **x: None):
    from kbapp.integrations.models import release_local
    release_local(store)
    client = GLMOCR(store=store)
    client.should_stop = should_stop
    store.set_state('recognition_state', {'state': 'running', 'model': client.model, 'updated': now()})
    try:
        for number, paper in enumerate(papers, 1):
            if should_stop():
                raise ValueError('原文识别已停止，完成的缓存已保留')
            on_stage(stage='recognition', ocr_papers_done=number - 1, ocr_papers_total=len(papers))
            own = select_sources(grouped[paper['id']])
            recognized = recognize_sources(store, paper, own, progress, should_stop, client)
            metadata = [r for r in grouped[paper['id']] if r['kind'] != 'paper_text']
            grouped[paper['id']] = recognized + metadata
        on_stage(stage='local_verification', ocr_papers_done=len(papers), ocr_papers_total=len(papers))
        store.set_state('recognition_state', {'state': 'complete', 'papers': len(papers), 'model': client.model, 'updated': now()})
    except Exception as exc:
        store.set_state('recognition_state', {'state': 'stopped' if should_stop() else 'failed', 'model': client.model, 'error': str(exc), 'updated': now()})
        raise ValueError('GLM-OCR 原文识别未完成：' + str(exc)) from None
    finally:
        try:
            request(C.OLLAMA, '/api/generate', {'model': client.model, 'keep_alive': 0}, timeout=30)
        except Exception:
            pass
