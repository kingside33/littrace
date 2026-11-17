"""kbapp.workflows.verification：由职责边界组织的知识库能力。"""
from __future__ import annotations
from kbapp.workflows.status import status as workbench_status
import json
import os
import threading
import time
from kbapp.core.io import digest, now
from kbapp.core.locks import update_lock
from kbapp.workflows.export import export_static
from kbapp.research.relations import analyze_relations
from kbapp.integrations.models import set_cancelled, cancelled, stop_text_runtime
from kbapp.workflows.reports import publish_lineage, publish_audit
from kbapp.storage.activity import track_job, emit, dashboard_phase
_guard = threading.Lock()
_thread = None

def scope_filters(values):
    if not isinstance(values, dict):
        raise ValueError('核验范围无效')
    direction = values.get('direction') or ''
    if direction not in {'', '00', '01', '02', '03', '04', '05', '06', '07', '08', '09', '90'}:
        raise ValueError('研究方向无效')
    if not isinstance(values.get('project', False), bool):
        raise ValueError('课题筛选必须是布尔值')
    query = values.get('q') or ''
    if not isinstance(query, str) or len(query) > 200:
        raise ValueError('核验关键词最多200字')
    return {'project': values.get('project', False), 'direction': direction, 'q': query.strip()}

@track_job
def run_verification(store, job, filters):
    processed = 0
    seen_phases = set()
    reused = 0
    completed_ids = set()
    summary = {}
    initial = workbench_status(store)['method_relations']
    current = {'job': job, 'state': 'running', 'filters': filters, 'processed': 0, 'reused': 0, 'completed': 0, 'total': 0, 'message': '正在准备已发布证据', 'updated': now()}

    def stopped():
        return bool(store.state('verification_stop', {}).get(job)) or cancelled(store, 'text') or cancelled(store, 'ocr')

    def save(**changes):
        current.update(changes, updated=now())
        if current['state'] == 'running' and stopped():
            current.update(state='stopping', message='正在停止；完成的证据与缓存会保留')
        store.set_state('analysis_state', current)

    def progress(message):
        phase = {'ocr':'gather-evidence', 'local_verification':'review-local', 'cloud_review':'review-cloud'}.get(current.get('stage'), 'gather-evidence')
        seen_phases.add(phase)
        emit(store, current.get('stage', 'verification'), 'running', message, role='ocr' if current.get('stage') == 'ocr' else 'verifier', dashboard_phase=phase)
        save(message=message)
        with store.db() as db:
            db.execute('UPDATE jobs SET progress=? WHERE id=?', (f"核验 {current['completed']}/{current['total']} 篇；" + message, job))

    def counters(value):
        save(processed=processed + value['processed'], reused=reused + value['reused'], completed=value['completed'], total=value['total'], pending=value['pending'], **{k: value[k] for k in ('stage', 'ocr_papers_done', 'ocr_papers_total') if k in value})
    try:
        with update_lock(store.root, 'workbench-analysis.lock'), update_lock(store.root):
            save()
            emit(store, 'dashboard_phase', 'complete', '范围准备完成', dashboard_phase='planning')
            while True:
                if stopped():
                    summary = {'state': 'interrupted'}
                    break
                summary = analyze_relations(store, progress, filters=filters, on_status=counters, should_stop=stopped, locked=True, completed_ids=completed_ids)
                processed += summary.get('processed', 0)
                reused += summary.get('reused', 0)
                newly_completed = set(summary.get('batch_completed_ids', [])) - completed_ids
                completed_ids.update(newly_completed)
                if summary.get('state') != 'complete' or not summary.get('pending') or (not newly_completed):
                    break
            state = summary.get('state', 'complete')
            if stopped():
                state = 'interrupted'
            if state == 'complete' and summary.get('pending'):
                state = 'paused'
            final = workbench_status(store)['method_relations']
            save(state=state, processed=processed, reused=reused, reason=summary.get('reason', ''), confirmed_added=max(0, final.get('confirmed', 0) - initial.get('confirmed', 0)), candidates_added=max(0, final.get('candidate', 0) - initial.get('candidate', 0)), message='核验完成' if state == 'complete' else '核验已停止，完成的证据与缓存已保留' if state == 'interrupted' else '本轮核验暂缓，完成结果已保留')
            with store.db() as db:
                db.execute('UPDATE jobs SET status=?,finished=?,progress=?,payload=? WHERE id=?', (state, now(), f'已处理 {processed} 篇；' + current['message'], json.dumps(current, ensure_ascii=False), job))
            if state == 'complete':
                for phase in seen_phases:
                    emit(store, 'dashboard_phase', 'complete', '本轮阶段处理完成', dashboard_phase=phase)
            with dashboard_phase(store, 'publish', '发布核验结果', role='coordinator'):
                publish_lineage(store)
                publish_audit(store)
                export_static(store)
    except Exception as exc:
        import traceback
        from kbapp.core.io import atomic
        atomic(store.paths.runtime / 'logs/verification-error.log', traceback.format_exc())
        state = 'interrupted' if stopped() else 'failed'
        save(state=state, processed=processed, error=type(exc).__name__ + ': ' + str(exc), message='核验已停止，完成结果已保留' if state == 'interrupted' else '核验未完成，完成结果已保留')
        with store.db() as db:
            db.execute('UPDATE jobs SET status=?,finished=?,error=?,progress=? WHERE id=?', (state, now(), None if state == 'interrupted' else str(exc), current['message'], job))
    return current

def control_verification(store, values):
    global _thread
    action = values.get('action', 'start')
    if action not in {'start', 'stop'}:
        raise ValueError('未知核验操作')
    with _guard:
        active = store.state('analysis_state', {})
        if action == 'stop':
            if not _thread or not _thread.is_alive() or (not active.get('job')):
                return {'state': 'idle', 'message': '当前没有网页核验任务'}
            store.set_state('verification_stop', {active['job']: True})
            store.set_state('analysis_state', dict(active, state='stopping', message='正在停止；等待当前请求结束，保留已完成结果', updated=now()))
            set_cancelled(store, ['text', 'ocr'], True)
            if store.settings().get('relation_extractor') == 'local':
                stop_text_runtime(store)
            return {'state': 'stopping'}
        if _thread and _thread.is_alive():
            return {'state': 'running', 'message': '核验任务已在运行'}
        filters = scope_filters(values.get('filters', {}))
        cfg = store.settings()
        if not store.state('active_version'):
            raise ValueError('请先发布可用的文献索引')
        if not cfg['cloud_enabled'] or not cfg.get('api_key'):
            raise ValueError('请先在模型与费用设置中启用并配置关系复核 API')
        if store.state('model_control', {}).get('state') == 'running':
            raise ValueError('本地模型操作正在进行，请完成后再核验')
        with update_lock(store.root, 'workbench-analysis.lock'), update_lock(store.root):
            pass
        set_cancelled(store, ['text', 'ocr'], False)
        store.set_state('verification_stop', {})
        from kbapp.research.review import retry_deferred
        if active.get('job'):
            retry_deferred(store, 'job:' + active['job'])
        job = digest('web-verification:' + str(time.time_ns()))[:20]
        with store.db() as db:
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)', (job, 'relations', 'running', os.getpid(), json.dumps(filters), '准备研究关系核验', now(), None, None))
        store.set_state('analysis_state', {'job': job, 'state': 'running', 'filters': filters, 'processed': 0, 'total': 0, 'completed': 0, 'message': '正在准备已发布证据', 'updated': now()})
        _thread = threading.Thread(target=run_verification, args=(store, job, filters), daemon=True, name='kb-verification')
        _thread.start()
        return {'state': 'started', 'job': job, 'filters': filters}

def recover_verification(store):
    active = store.state('analysis_state', {})
    if not active.get('job') or active.get('state') not in {'running', 'stopping'}:
        return
    try:
        with update_lock(store.root, 'workbench-analysis.lock'), update_lock(store.root):
            active.update(state='interrupted', message='服务重新启动，完成的证据与缓存已保留；可继续核验', updated=now())
            store.set_state('analysis_state', active)
            recognition = store.state('recognition_state', {})
            if recognition.get('state') == 'running':
                store.set_state('recognition_state', dict(recognition, state='stopped', updated=now()))
            model = store.state('local_model_state', {})
            if model.get('state') == 'running':
                stop_text_runtime(store)
            with store.db() as db:
                db.execute("UPDATE jobs SET status='interrupted',finished=?,progress=? WHERE id=? AND status='running'", (now(), active['message'], active['job']))
    except ValueError:
        pass
