"""kbapp.workflows.tools.validate_integrity：由职责边界组织的知识库能力。"""
from kbapp.core.paths import Paths
import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from contextlib import closing

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--models', action='store_true')
    args = ap.parse_args(argv)
    paths = Paths.from_base()
    root = paths.base
    stage = paths.runtime / 'validation/integrity'
    os.environ['KB_BASE'] = str(stage)
    os.environ['KB_DATA_DIR'] = str(stage / '_index')
    os.environ['KB_NO_STARTUP_JOBS'] = '1'
    os.environ['KB_NO_REFERENCE_WORKER'] = '1'
    from kbapp.storage.store import Store
    from kbapp.core.io import atomic
    from kbapp.library.sources import sync_library
    from kbapp.storage.schema import sync_zotero
    from kbapp.library import documents as documents
    import pymupdf
    store = Store(stage, data_dir=stage / '_index')
    index = store.active_dir()
    index.mkdir(parents=True, exist_ok=True)
    if (paths.state/'settings.json').exists():
        shutil.copy2(paths.state/'settings.json', store.paths.state/'settings.json')
    out = paths.exports / 'evaluation/integrity-upgrade'
    out.mkdir(parents=True, exist_ok=True)
    if not (index / 'papers_all.jsonl').exists():
        with closing(sqlite3.connect(paths.database.as_uri() + '?mode=ro', uri=True)) as db:
            version = json.loads(db.execute("SELECT value FROM state WHERE key='active_version'").fetchone()[0])
        shutil.copy2(paths.indexes / 'versions' / version / 'papers_all.jsonl', index / 'papers_all.jsonl')
    store.set_state('active_version', None)
    papers, notes, warnings = sync_library(store, index, source_base=root)
    for p in papers:
        if p.get('pdf') and (not Path(p['pdf']).is_absolute()):
            p['pdf'] = str(root / p['pdf'])
    store.put_papers(papers)
    sync_zotero(store, papers, notes)
    atomic(index / 'papers_all.jsonl', ''.join((json.dumps(p, ensure_ascii=False) + '\n' for p in papers)))
    report = {'sync': {'papers': len(papers), 'notes': len(notes), 'warnings': warnings}, 'samples': [], 'models': {'requested': args.models}}
    again, again_notes, again_warnings = sync_library(store, index, source_base=root)
    sync_zotero(store, again, again_notes)
    report['sync']['repeat_same_ids'] = sorted((p['id'] for p in papers)) == sorted((p['id'] for p in again))
    report['sync']['repeat_same_note_ids'] = sorted((p['id'] for p in notes)) == sorted((p['id'] for p in again_notes))
    report['sync']['raw_snapshots'] = sum((bool(p.get('raw_zotero')) for p in papers))
    report['sync']['multi_pdf_items'] = sum((sum((a.get('exists') and a.get('content_type') == 'application/pdf' for a in p.get('attachments', []))) > 1 for p in papers))
    multiple = next((p for p in papers if sum((a.get('exists') and a.get('content_type') == 'application/pdf' for a in p.get('attachments', []))) > 1), None)
    if multiple:
        attachments = [a for a in multiple['attachments'] if a.get('exists') and a.get('content_type') == 'application/pdf']
        registered = [documents.register(store, multiple, a['abs_path'], a['key']) for a in attachments]
        report['multi_attachment'] = {'paper_id': multiple['id'], 'citekey': multiple['citekey'], 'attachments': [{'id': d['id'], 'attachment_key': d['attachment_key'], 'sha256': d['sha256'], 'page_count': d['page_count']} for d in registered]}
    names = ['Gammell2014Informed', 'Karaman2011Sampling', 'Reeds1990Optimal', '丁子轩2024', 'Feng2024Smooth-2', 'Oladejo2024Hiking', 'Khatib1986Real-2']
    selected = [p for name in names for p in papers if p.get('citekey') == name and p.get('pdf') and Path(p['pdf']).is_file()]
    from kbapp.integrations.ocr import GLMOCR
    from kbapp.integrations.scheduling import model_slot

    class SharedOCR(GLMOCR):

        def _post(self, path, payload):
            with model_slot(paths.data, timeout=600):
                return self._post_unlocked(path, payload)
    client = SharedOCR() if args.models else None
    for p in selected:
        print('真实PDF:', p['citekey'], flush=True)
        d = documents.register(store, p, p['pdf'], p.get('attachment_key', ''))
        with pymupdf.open(p['pdf']) as pdf:
            classes = [dict(page=n + 1, **documents.classify(page)) for n, page in enumerate(pdf)]
            candidates = [c['page'] for c in classes if c['needs_ocr']]
            numbers = sorted({1, min(2, len(pdf)), len(pdf), *candidates[:1]})
            pdf[numbers[0] - 1].get_pixmap(matrix=pymupdf.Matrix(1, 1)).save(out / (p['citekey'] + '.png'))
        result = documents.process(store, d, numbers, client=client, budget=1, progress=lambda message: print(message, flush=True))
        report['samples'].append({'citekey': p['citekey'], 'document_id': d['id'], 'sha256': d['sha256'], 'classes': classes, 'result': result})
        atomic(out / 'real-validation.json', report)
    corpus = documents.sources(store)
    from kbapp.search.indexing import build_keyword_index, Search, build_vectors
    import numpy as np
    atomic(index / 'corpus.jsonl', ''.join((json.dumps(r, ensure_ascii=False) + '\n' for r in corpus)))
    atomic(index / 'corpus_meta.json', {'sources': len(corpus), 'vector_count': 0, 'vector_ids': [], 'vector_pending': len(corpus)})
    np.save(index / 'corpus.npy', np.zeros((len(corpus), 1024), np.float32))
    build_keyword_index(index, corpus)
    engine = Search(store)
    queries = ['Informed RRT*', '10.1109/IROS.2014.6942976', '路径规划', 'RRT']
    report['search'] = {q: {'count': len((result := engine.search(q, vectors=False))['results']), 'mode': result['mode'], 'locations': [r.get('url') for r in result['results'][:3]]} for q in queries}
    if args.models and corpus:
        vector_root = store.paths.indexes / 'versions/real-sample'
        vector_root.mkdir(parents=True, exist_ok=True)
        atomic(vector_root / 'corpus.jsonl', ''.join((json.dumps(r, ensure_ascii=False) + '\n' for r in corpus[:2])))
        with model_slot(paths.data, timeout=600):
            warnings = build_vectors(store, vector_root, corpus[:2], limit=2)
        report['models']['embedding_warnings'] = warnings
        from kbapp.integrations.model_identity import embedding_signature
        report['models']['embedding_signature'] = embedding_signature()
        report['models']['vector_count'] = json.loads((vector_root / 'corpus_meta.json').read_text(encoding='utf-8'))['vector_count']
        build_vectors(store, vector_root, corpus[:2], limit=0)
        report['models']['repeat_cached_vectors'] = store.state('vector_build')['reused']
        store.set_state('active_version', 'real-sample')
        with model_slot(paths.data, timeout=600):
            from kbapp.search.query import query_vector
            vector, status = query_vector(store, 'informed sampling', wait=True)
        report['models']['query_vector'] = {'status': status, 'dimensions': None if vector is None else len(vector)}
    with store.db() as db:
        report['database'] = {'integrity': db.execute('PRAGMA integrity_check').fetchone()[0], 'foreign_key_errors': [tuple(r) for r in db.execute('PRAGMA foreign_key_check')], 'documents': db.execute('SELECT count(*) FROM documents').fetchone()[0], 'pages': db.execute('SELECT count(*) FROM document_pages').fetchone()[0]}
        report['database']['page_states'] = [dict(r) for r in db.execute('SELECT classification,status,count(*) AS count FROM document_pages GROUP BY classification,status')]
        report['database']['block_kinds'] = [dict(r) for r in db.execute('SELECT kind,count(*) AS count FROM document_blocks GROUP BY kind')]
        report['database']['metadata_conflicts'] = db.execute('SELECT count(*) FROM metadata_conflicts').fetchone()[0]
        report['database']['table_counts'] = {r[0]: db.execute('SELECT count(*) FROM "' + r[0] + '"').fetchone()[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()}
        with tempfile.TemporaryDirectory(prefix='kb-restore-rehearsal-') as tmp:
            with closing(sqlite3.connect(Path(tmp) / 'restored.sqlite3')) as restored:
                db.backup(restored)
                restored_counts = {name: restored.execute('SELECT count(*) FROM "' + name + '"').fetchone()[0] for name in report['database']['table_counts']}
                report['database']['backup_restore'] = {'integrity': restored.execute('PRAGMA integrity_check').fetchone()[0], 'same_table_counts': restored_counts == report['database']['table_counts']}
    atomic(out / 'real-validation.json', report)
    print(json.dumps(report, ensure_ascii=False))
