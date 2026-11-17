"""kbapp.workflows.vector_jobs：由职责边界组织的知识库能力。"""
import json
import os
import shutil
import time
from kbapp.search.indexing import build_vectors
from kbapp.integrations.models import cancelled, set_cancelled
from kbapp.library.recognition import active_sources
from kbapp.core.io import atomic, digest, jsonl, now, read_json
from kbapp.workflows.export import export_static
from kbapp.core.locks import update_lock
from kbapp.storage.activity import track_job, emit, dashboard_phase
from kbapp.workflows.publication import publish_manifest

def queue_vectors(store):
    if not store.state('active_version') or not (store.active_dir() / 'corpus.jsonl').is_file():
        raise ValueError('尚无已发布来源，请先使用右上角“完整更新知识库”建立来源索引')
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        running = db.execute("SELECT id,kind FROM jobs WHERE kind IN ('update','vectors','documents') AND status IN ('queued','running','stopping') LIMIT 1").fetchone()
        if running:
            return {'state': 'running', 'id': running['id'], 'kind': running['kind'], 'message': '完整更新正在运行，完成后可单独补齐向量' if running['kind'] == 'update' else '向量索引任务已在运行'}
        job = digest('vectors:' + str(time.time_ns()))[:20]
        db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)', (job, 'vectors', 'queued', os.getpid(), '{}', '等待补齐已有来源向量', now(), None, None))
    return {'state': 'started', 'id': job, 'kind': 'vectors', 'message': '已启动本地向量索引，复用已有来源与缓存'}

def recover_vectors(store):
    """只恢复已退出进程留下的向量任务，缓存留给下一次明确启动续算。"""
    with store.db() as db:
        rows = db.execute("SELECT id,pid FROM jobs WHERE kind='vectors' AND status IN ('queued','running')").fetchall()
    for row in rows:
        if os.name == 'nt':
            from kbapp.core.win_runtime import process_info
            alive = process_info(row['pid']) is not None
        else:
            try:
                os.kill(row['pid'], 0)
                alive = True
            except ProcessLookupError:
                alive = False
        if not alive:
            with store.db() as db:
                db.execute("UPDATE jobs SET status='interrupted',finished=?,progress='服务中断，向量缓存保留；可继续索引' WHERE id=? AND status IN ('queued','running')", (now(), row['id']))

@track_job
def run_vectors(store, job):

    def progress(message):
        emit(store, 'index', 'running', message, role='indexer')
        with store.db() as db:
            db.execute('UPDATE jobs SET progress=? WHERE id=?', (message, job))
    try:
        with update_lock(store.root):
            with dashboard_phase(store, 'prepare', '检查已有来源'):
                source = store.active_dir()
                if source == store.root:
                    raise ValueError('请先完整更新知识库并发布来源版本')
                with store.db() as db:
                    row = db.execute('SELECT kind,status FROM jobs WHERE id=?', (job,)).fetchone()
                    if not row or row['kind'] != 'vectors' or row['status'] != 'queued':
                        raise ValueError('向量任务状态已改变')
                    db.execute("UPDATE jobs SET status='running',progress='准备已有来源快照' WHERE id=?", (job,))
                set_cancelled(store, ['embedding'], False)
                corpus = {r['id']: r for r in jsonl(source / 'corpus.jsonl')}
                if not corpus:
                    raise ValueError('已发布来源为空，请先完整更新知识库')
                recognized = active_sources(store)
                for src in recognized:
                    corpus.setdefault(src['id'], dict(src, source_label='GLM-OCR论文原文'))
                from kbapp.library.documents import sources as document_sources
                documents = document_sources(store)
                valid_ids = {s['id'] for s in documents} | {s['id'] for s in recognized}
                corpus = {key: value for key, value in corpus.items() if not value.get('document_id') or key in valid_ids}
                for src in documents:
                    corpus[src['id']] = dict(src, source_label='PDF页面原文')
                corpus = list(corpus.values())
                for src in corpus:
                    src.setdefault('text_hash', digest(src['text']))
                index = store.paths.indexes / 'versions' / job
                excluded = {'corpus.jsonl', 'corpus.npy', 'corpus_meta.json', 'corpus_bm25.pkl', 'corpus-search.sqlite3'}
                shutil.copytree(source, index, ignore=lambda path, names: [n for n in names if n in excluded or n.endswith(('.tmp', '-wal', '-shm'))])
                atomic(index / 'corpus.jsonl', ''.join((json.dumps(r, ensure_ascii=False) + '\n' for r in corpus)))
            with dashboard_phase(store, 'index', '计算来源向量'):
                warnings = build_vectors(store, index, corpus, progress)
            meta = read_json(index / 'corpus_meta.json', {})
            if meta.get('vector_pending', len(corpus)):
                emit(store, 'dashboard_phase', 'partial', '向量未全部完成，缓存已保留', dashboard_phase='index', completed=meta.get('vector_count', 0), total=len(corpus))
                state = 'interrupted' if cancelled(store, 'embedding') else 'partial'
                report = {'operation': 'vectors', 'source_version': source.name, 'pending': meta.get('vector_pending'), 'cached': meta.get('vector_count', 0), 'warnings': warnings, 'published': False}
                with store.db() as db:
                    db.execute('UPDATE jobs SET status=?,finished=?,progress=?,payload=?,error=? WHERE id=?', (state, now(), '向量未全部完成，已保存缓存；可继续索引', json.dumps(report, ensure_ascii=False), None if state == 'interrupted' else '；'.join(warnings), job))
                return report
            if cancelled(store, 'embedding'):
                raise ValueError('向量索引已停止，完成缓存保留')
            with dashboard_phase(store, 'publish', '发布向量索引'):
                progress('发布本地向量索引')
                previous = store.state('coverage', {})
                from kbapp.core.warnings import publish_warnings
                current_warnings, warning_archive = publish_warnings(previous, warnings)
                coverage = dict(previous, sources=len(corpus), by_kind=meta['by_kind'], vector_count=meta['vector_count'], vector_pending=0, fulltext_papers=len({r['paper_id'] for r in corpus if r['kind'] == 'paper_text'}), warnings=current_warnings, warnings_archive=warning_archive)
                updated = now()
                with store.db() as db:
                    for key, value in [('active_version', job), ('last_success', updated), ('coverage', coverage)]:
                        db.execute('INSERT OR REPLACE INTO state VALUES(?,?)', (key, json.dumps(value, ensure_ascii=False)))
                publish_manifest(store.paths)
                report = {'operation': 'vectors', 'version': job, 'source_version': source.name, 'published': True, 'coverage': coverage}
                try:
                    export_static(store)
                except Exception as exc:
                    report['offline_export_error'] = str(exc)
                with store.db() as db:
                    db.execute("UPDATE jobs SET status='complete',progress='向量索引已发布',finished=?,payload=? WHERE id=?", (now(), json.dumps(report, ensure_ascii=False), job))
            return report
    except Exception as exc:
        with store.db() as db:
            db.execute("UPDATE jobs SET status='failed',finished=?,error=?,progress='向量索引未完成，已有版本与缓存保留' WHERE id=?", (now(), str(exc), job))
        raise
