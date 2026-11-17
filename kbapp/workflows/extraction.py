"""kbapp.workflows.extraction：由职责边界组织的知识库能力。"""
from __future__ import annotations
import json
from pathlib import Path
import shutil
from kbapp.core.io import atomic, digest, jsonl, now

def quality_ocr(store, index, papers, progress, bootstrap=False):
    if bootstrap:
        return []
    from kbapp.integrations.ocr import GLMOCR
    from kbapp.library.documents import sync_documents
    from kbapp.core.files import file_hash
    from kbapp.integrations.models import cancelled
    client = GLMOCR(store=store)
    results = sync_documents(store, papers, client=client if client.available() else None, budget=store.settings()['ocr_pages_per_run'], progress=progress, should_stop=lambda: cancelled(store, 'ocr'))
    warnings = []
    if cancelled(store, 'ocr'):
        warnings.append('图文识别已停止，已完成缓存保留')
    for result in results:
        if result.get('error'):
            warnings.append(str(result))
        elif result.get('failed') or result.get('pending'):
            warnings.append(f"{result['document_id']}：失败 {result['failed']} 页，待处理 {result['pending']} 页")
    by_id = {p['id']: p for p in papers}
    with store.db() as db:
        rows = db.execute("SELECT p.*,d.paper_id,d.path,d.sha256 FROM document_pages p JOIN documents d ON d.id=p.document_id WHERE p.status='complete' AND p.ocr_text!=''").fetchall()
    hashes = {}
    for r in rows:
        paper = by_id.get(r['paper_id'])
        path = Path(r['path'])
        if not paper or str((store.base / paper.get('pdf', '')).resolve()) != str(path) or (not path.is_file()):
            continue
        if str(path) not in hashes:
            hashes[str(path)] = file_hash(path)
        if hashes[str(path)] != r['sha256']:
            continue
        target = store.paths.cache / 'ocr_pages' / paper['citekey'] / f"p{r['page']:04d}.txt"
        stat = path.stat()
        meta = json.loads(r['payload'])
        atomic(target, r['ocr_text'])
        atomic(target.with_suffix('.json'), {'pdf_hash': digest(str(stat.st_mtime_ns) + str(stat.st_size)), 'content_sha256': r['sha256'], 'model': client.model, 'model_identity': meta.get('model'), 'updated': now()})
    cache = store.paths.cache / 'ocr_pages'
    if cache.exists():
        shutil.copytree(cache, Path(index) / 'ocr_pages', dirs_exist_ok=True)
    return warnings

def ocr_regions(store, index, progress, bootstrap=False):
    if bootstrap:
        return []
    from kbapp.integrations.ocr import GLMOCR
    client = GLMOCR(store=store)
    if not client.available():
        return []
    cfg = store.settings()
    count = 0
    warnings = []
    datasets = {name: jsonl(Path(index) / name) for name in ('figures.jsonl', 'formulas.jsonl')}
    from itertools import zip_longest
    by_key = {p.get('citekey'): p for p in store.papers().values() if p.get('citekey')}

    def region_rank(r):
        p = by_key.get(r.get('citekey'), {})
        tf = p.get('tagfields') or {}
        core = any((x in (tf.get('priority') or []) for x in ('A', 'B'))) or any((x in (tf.get('reading_tier') or []) for x in ('Core-A', 'Core-B')))
        return (r.get('ocr_attempts', 0), not bool(p.get('project')), not core)
    pending = {name: sorted([r for r in records if not r.get('ocr_no_text') and (not (r.get('inside_text') if name.startswith('figures') else r.get('latex')))], key=region_rank) for name, records in datasets.items()}
    work = []
    for pair in zip_longest(*pending.values()):
        work.extend(((name, r) for name, r in zip(pending, pair) if r is not None))
    for name, r in work:
        from kbapp.integrations.models import cancelled
        if cancelled(store, 'ocr'):
            warnings.append('图文识别已停止，保留已完成的识别缓存')
            break
        if count >= cfg['ocr_regions_per_run']:
            break
        formula = name.startswith('formulas')
        if formula and r.get('latex') or (not formula and r.get('inside_text')):
            continue
        path = store.base / (r.get('file') or 'missing')
        if not path.is_file():
            continue
        progress(f"GLM-OCR 图表/公式：{r.get('citekey')} {r['id']}")
        try:
            mode = 'formula' if formula else 'table' if r.get('kind') == 'table' else 'page'
            text = client.image_to_text(path.read_bytes(), mode)
            r['latex' if formula else 'inside_text'] = text
            r['ocr_provenance'] = {'model': client.model, 'mode': mode, 'image_hash': digest(path.read_bytes()), 'at': now()}
            if not text and (not formula):
                r['ocr_no_text'] = True
            if mode == 'table':
                r['table_text'] = text
        except Exception as exc:
            warnings.append(f"{r.get('citekey')} 图表/公式OCR失败：{type(exc).__name__}")
            r['ocr_attempts'] = r.get('ocr_attempts', 0) + 1
        count += 1
    for name, records in datasets.items():
        atomic(Path(index) / name, ''.join((json.dumps(r, ensure_ascii=False) + '\n' for r in records)))
    return warnings

def extract_stage(store, index, script, progress, extra=None):
    from kbapp.library.chunk import process_collection as chunks
    from kbapp.library.figures import process_collection as figures
    from kbapp.library.formulas import process_collection as formulas
    from kbapp.library.reference_extraction import process_collection as references
    stages = {'chunk.py': chunks, 'extract_figures.py': figures, 'extract_formulas.py': formulas, 'extract_refs.py': references}
    if script not in stages:
        raise ValueError('未知提取阶段：' + script)
    if extra and extra != ['--full']:
        raise ValueError('提取选项无效')
    progress(script)
    return stages[script]({'base': str(store.base), 'index': str(index), 'full': bool(extra)})
