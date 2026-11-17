"""kbapp.workflows.update：由职责边界组织的知识库能力。"""
from __future__ import annotations
from datetime import datetime
import json
import os
import shutil
import time
import traceback
from kbapp.storage.store import Store
from kbapp.core.io import TZ, atomic, digest, now, read_json
from kbapp.library.sources import sync_library, approved_papers
from kbapp.workflows.notes import publish_notes
from kbapp.search.indexing import build_corpus, build_vectors
from kbapp.research.graph import import_legacy, sync_references
from kbapp.library.network import expand, fetch_approved, hydrate_cached_metadata
from kbapp.research.relations import analyze_relations, opportunities
from kbapp.core.locks import update_lock
from kbapp.workflows.extraction import extract_stage, ocr_regions, quality_ocr
from kbapp.workflows.publication import publish_manifest

def needs_daily(store):
    local = datetime.now(TZ)
    return local.hour >= 9 and store.state('last_daily_attempt') != local.date().isoformat()

def run_update(store=None, bootstrap=False, embed_limit=None, local_only=False, full=False):
    store = store or Store()
    job_id = digest(str(time.time_ns()))[:20]
    with update_lock(store.root):
        from kbapp.integrations.models import set_cancelled, stop_text_runtime
        from kbapp.integrations.scheduling import model_slot
        with model_slot(store.root), update_lock(store.root, 'workbench-model.lock'):
            if store.state('local_text_runtime', {}):
                stop_text_runtime(store)
        set_cancelled(store, ['embedding', 'ocr', 'text'], False)
        with store.db() as db:
            db.execute("UPDATE jobs SET status='interrupted',finished=? WHERE kind='update' AND status='running'", (now(),))
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)', (job_id, 'update', 'running', os.getpid(), '{}', '准备版本', now(), None, None))
        from kbapp.storage.activity import emit, dashboard_phase
        from kbapp.core.execution import research_run
        activity_token = research_run.set('job:' + job_id)

        def progress(message):
            emit(store, 'update', 'running', message, role='ocr' if 'GLM-OCR' in message else 'cloud_reviewer' if '独立复核' in message else 'verifier' if '本地核验' in message else 'indexer')
            print(message, flush=True)
            with store.db() as db:
                db.execute('UPDATE jobs SET progress=? WHERE id=?', (message, job_id))
        try:
            index = store.paths.indexes / 'versions' / job_id
            index.mkdir(parents=True, exist_ok=True)
            source = store.active_dir()
            with store.db() as db:
                interrupted = [r[0] for r in db.execute("SELECT id FROM jobs WHERE kind='update' AND status IN ('failed','interrupted') AND started>? ORDER BY started DESC LIMIT 12", (store.state('last_success', ''),))]
            for previous in interrupted:
                candidate = store.paths.indexes / 'versions' / previous
                if (candidate / 'chunks.jsonl').exists() and (candidate / 'chunk_cache.json').exists():
                    source = candidate
                    progress('复用未完成更新中已完成的切块与提取缓存')
                    break
            for name in ['chunks.jsonl', 'figures.jsonl', 'formulas.jsonl', 'references.jsonl', 'chunk_cache.json', 'figures_cache.json', 'formulas_cache.json', 'refs_cache.json', 'chunk_failures.json', 'chunk_report.json']:
                p = source / name
                if p.exists():
                    for attempt in range(12):
                        try:
                            shutil.copy2(p, index / name)
                            break
                        except PermissionError:
                            if attempt == 11:
                                raise
                            time.sleep(0.1 * (attempt + 1))
            with dashboard_phase(store, 'sync', '同步文献'):
                progress('只读同步 Zotero 元数据、笔记与批注')
                papers, notes, warnings = sync_library(store, index)
                if not store.state('active_version'):
                    store.put_papers(papers)
                    from kbapp.storage.schema import sync_zotero
                    sync_zotero(store, papers, notes)
                if not store.state('legacy_imported'):
                    progress('迁入全部缓存节点、引用事实及待核关系')
                    import_legacy(store, papers)
                    store.set_state('legacy_imported', True)
                hydrate_cached_metadata(store)
            with dashboard_phase(store, 'extract', '原文识别与切块'):
                fetched = fetch_approved(store, progress) if not bootstrap else {'downloaded': [], 'failed': []}
                extraction_papers = papers + approved_papers(store)
                with store.db() as db:
                    for paper in extraction_papers:
                        db.execute('INSERT OR IGNORE INTO papers VALUES(?,?,?)', (paper['id'], json.dumps(paper, ensure_ascii=False), int(paper.get('kind') == 'library')))
                warnings += quality_ocr(store, index, extraction_papers, progress, bootstrap)
                if not bootstrap:
                    atomic(index / 'papers_all.jsonl', ''.join((json.dumps(p, ensure_ascii=False) + '\n' for p in extraction_papers)))
                    try:
                        extract_stage(store, index, 'chunk.py', progress, ['--full'] if full else None)
                        for script in ('extract_figures.py', 'extract_formulas.py', 'extract_refs.py'):
                            extract_stage(store, index, script, progress)
                    finally:
                        atomic(index / 'papers_all.jsonl', ''.join((json.dumps(p, ensure_ascii=False) + '\n' for p in papers)))
                    warnings += ocr_regions(store, index, progress)
                    sync_references(store, index, extraction_papers)
            network = {'state': 'not_requested'}
            if not local_only and (not bootstrap):
                network = expand(store, progress)
                refreshed = store.papers()
                for paper in papers:
                    for key in ('openalex_id', 'refs', 'oa_url', 'oa_landing_url', 'metadata_updated', 'cited_by', 'citation_count', 'citation_source', 'citation_updated'):
                        if refreshed.get(paper['id'], {}).get(key) is not None:
                            paper[key] = refreshed[paper['id']][key]
            with dashboard_phase(store, 'index', '构建来源与向量索引'):
                progress('构建论文、图表、公式、笔记、批注统一来源索引')
                corpus = build_corpus(store, index, papers, notes)
                warnings += build_vectors(store, index, corpus, progress, limit=0 if bootstrap else embed_limit)
            meta = read_json(index / 'corpus_meta.json', {})
            if meta.get('vector_pending'):
                emit(store, 'dashboard_phase', 'partial', '仍有来源待向量化，已保存缓存', dashboard_phase='index', completed=meta.get('vector_count', 0), total=len(corpus))
            warnings.extend((f"{r.get('citekey')}：PDF切块失败（{r.get('error')}）" for r in read_json(index / 'chunk_failures.json', [])))
            from kbapp.core.warnings import publish_warnings
            current_warnings, warning_archive = publish_warnings(store.state('coverage', {}), warnings)
            coverage = {'sources': len(corpus), 'by_kind': meta.get('by_kind', {}), 'vector_count': meta.get('vector_count'), 'vector_pending': meta.get('vector_pending'), 'fulltext_papers': len({r['paper_id'] for r in corpus if r['kind'] == 'paper_text'}), 'warnings': current_warnings, 'warnings_archive': warning_archive, 'network': network, 'fulltext': fetched}
            with store.db() as db:
                for paper in papers:
                    current = db.execute('SELECT payload FROM papers WHERE id=?', (paper['id'],)).fetchone()
                    current = json.loads(current[0]) if current else {}
                    if (current.get('citation_updated') or '') > (paper.get('citation_updated') or ''):
                        for key in ('cited_by', 'citation_count', 'citation_source', 'citation_updated', 'openalex_id', 'refs', 'oa_url', 'oa_landing_url', 'metadata_updated'):
                            if key in current:
                                paper[key] = current[key]
                    db.execute('INSERT INTO papers VALUES(?,?,1) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,library=1', (paper['id'], json.dumps(paper, ensure_ascii=False)))
                    for alias in (paper['id'], paper.get('citekey'), paper.get('openalex_id'), paper.get('doi')):
                        if alias:
                            db.execute('INSERT OR IGNORE INTO aliases VALUES(?,?)', (alias, paper['id']))
                for key, value in [('active_version', job_id), ('last_success', now()), ('coverage', coverage)]:
                    db.execute('INSERT OR REPLACE INTO state VALUES(?,?)', (key, json.dumps(value, ensure_ascii=False)))
            publish_manifest(store.paths)
            from kbapp.research.dependencies import record as record_invalidation
            record_invalidation(store, 'index', job_id, '新索引版本发布，候选与检索结果需按新来源重建')
            from kbapp.storage.schema import sync_zotero
            sync_zotero(store, papers, notes)
            # C01/C02：重建论文作品关联层（条目 ID 不变，仅新增作品归属与匹配证据）
            from kbapp.library.works import rebuild as rebuild_works
            rebuild_works(store)
            progress('发布 Obsidian 入口与证据来源链接')
            from kbapp.research.verified import mark_stale, seed_verified
            mark_stale(store, index)
            seed_verified(store, index)
            from kbapp.research.lineage import seed_methods
            seed_methods(store)
            publish_notes(store, papers)
            analysis = {'state': 'not_requested'}
            with dashboard_phase(store, 'analysis', '分析引用与研究关系'):
                if not bootstrap and (not local_only):
                    try:
                        with update_lock(store.root, 'workbench-analysis.lock'):
                            analysis = analyze_relations(store, progress)
                    except ValueError as exc:
                        analysis = {'state': 'deferred', 'reason': str(exc), 'processed': 0}
                    if analysis.get('processed'):
                        try:
                            opportunities(store)
                        except ValueError as exc:
                            warnings.append(str(exc))
                    publish_notes(store, papers)
                if not bootstrap and (not local_only):
                    store.set_state('analysis_state', analysis)
            if analysis.get('state') != 'complete':
                emit(store, 'dashboard_phase', 'not_applicable' if analysis.get('state')=='not_requested' else 'failed' if analysis.get('state')=='failed' else 'deferred', analysis.get('reason') or '关系分析未全部完成', dashboard_phase='analysis')
            with dashboard_phase(store, 'publish', '同步笔记与离线导出'):
                progress('导出离线演化图与更新报告')
                from kbapp.workflows.publication import sync_exports
                export_state = sync_exports(store)
            finished = now()
            report = {'job': job_id, 'version': job_id, 'completed': finished, 'coverage': coverage, 'analysis': analysis, 'export_sync':export_state}
            atomic(store.paths.exports / 'workbench-report.json', report)
            with store.db() as db:
                db.execute("UPDATE jobs SET status='complete',progress='完成',finished=?,payload=? WHERE id=?", (finished, json.dumps(report, ensure_ascii=False), job_id))
                db.execute("INSERT OR REPLACE INTO state VALUES('last_update_finished',?)",(json.dumps(finished),))
            publish_manifest(store.paths)
            if not bootstrap and (not local_only):
                from kbapp.workflows.references import enqueue
                for paper in sorted(papers, key=lambda p: not bool(p.get('project'))):
                    enqueue(store, paper['id'], priority=10)
            if not os.environ.get('KB_NO_STARTUP_JOBS') and (not bootstrap):
                from kbapp.workflows.agents import create_run
                create_run(store, {'kind': 'maintenance'})
            return report
        except Exception as exc:
            with store.db() as db:
                db.execute("UPDATE jobs SET status='failed',error=?,finished=? WHERE id=?", (str(exc), now(), job_id))
            atomic(store.paths.runtime / 'logs/workbench-last-error.log', traceback.format_exc())
            raise
        finally:
            with store.db() as db:
                row = db.execute('SELECT status,progress FROM jobs WHERE id=?', (job_id,)).fetchone()
            if row:
                emit(store, 'finished', row['status'], row['progress'] or '')
            research_run.reset(activity_token)
