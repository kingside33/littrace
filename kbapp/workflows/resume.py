"""从尚未发布的来源快照直接续算向量，跳过已经完成的 OCR 和切块。"""
import json
import os
import re
from kbapp.storage.store import Store
from kbapp.core.io import atomic, jsonl, read_json, now
from kbapp.core.locks import update_lock
from kbapp.workflows.export import export_static
from kbapp.search.indexing import build_vectors
from kbapp.workflows.notes import publish_notes
from kbapp.workflows.reports import publish_lineage, publish_audit
from kbapp.workflows.publication import publish_manifest
from kbapp.research.verified import mark_stale, seed_verified

def resume(version, store=None):
    store = store or Store()
    if not re.fullmatch('[a-f0-9]{20}', version):
        raise ValueError('无效的未发布版本标识')
    if store.state('active_version') == version:
        raise ValueError('当前已发布版本应通过常规增量更新续算')
    index = store.paths.indexes / 'versions' / version
    corpus = jsonl(index / 'corpus.jsonl')
    if not corpus:
        raise ValueError('未找到已完成的统一来源快照')
    with update_lock(store.root):
        with store.db() as db:
            job = db.execute('SELECT * FROM jobs WHERE id=?', (version,)).fetchone()
            if not job or job['kind'] != 'update':
                raise ValueError('未找到对应更新任务')
            db.execute("UPDATE jobs SET status='running',pid=?,finished=NULL,error=NULL,progress='复用来源快照，直接续算向量' WHERE id=?", (os.getpid(), version))

        def progress(message):
            print(message, flush=True)
            with store.db() as db:
                db.execute('UPDATE jobs SET progress=? WHERE id=?', (message, version))
        try:
            warnings = build_vectors(store, index, corpus, progress)
            meta = read_json(index / 'corpus_meta.json', {})
            previous = store.state('coverage', {})
            from kbapp.core.warnings import publish_warnings
            current_warnings, warning_archive = publish_warnings(previous, warnings)
            coverage = dict(previous, sources=len(corpus), by_kind=meta['by_kind'], vector_count=meta['vector_count'], vector_pending=meta['vector_pending'], warnings=current_warnings, warnings_archive=warning_archive, fulltext_papers=len({r['paper_id'] for r in corpus if r['kind'] == 'paper_text'}))
            with store.db() as db:
                for key, value in [('active_version', version), ('last_success', now()), ('coverage', coverage)]:
                    db.execute('INSERT OR REPLACE INTO state VALUES(?,?)', (key, json.dumps(value, ensure_ascii=False)))
            progress('发布完成的向量索引与状态')
            mark_stale(store, index)
            seed_verified(store, index)
            papers = [p for p in store.papers().values() if p.get('kind') == 'library']
            publish_notes(store, papers)
            publish_lineage(store)
            publish_audit(store)
            export_static(store)
            report = {'job': version, 'version': version, 'completed': now(), 'coverage': coverage, 'analysis': store.state('analysis_state', {})}
            atomic(store.paths.exports / 'workbench-report.json', report)
            publish_manifest(store.paths)
            with store.db() as db:
                db.execute("UPDATE jobs SET status='complete',progress='完成',finished=?,payload=? WHERE id=?", (now(), json.dumps(report, ensure_ascii=False), version))
            print(json.dumps(report, ensure_ascii=False), flush=True)
            return report
        except Exception as exc:
            with store.db() as db:
                db.execute("UPDATE jobs SET status='failed',finished=?,error=? WHERE id=?", (now(), str(exc), version))
            raise
