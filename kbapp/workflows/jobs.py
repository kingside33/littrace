"""统一服务任务入口；持久互斥、恢复与进度沿用各工作流。"""
from __future__ import annotations
import threading
from kbapp.workflows.update import run_update
from kbapp.core.locks import update_lock

class JobCoordinator:

    def __init__(self, store):
        self.store = store
        self.guard = threading.Lock()
        self.task = None

    def start_vectors(self):
        from kbapp.workflows.vector_jobs import queue_vectors, run_vectors
        with self.guard:
            if self.task and self.task.is_alive():
                return {'state': 'running', 'message': '已有索引或完整更新任务运行中'}
            if self.store.state('analysis_state', {}).get('state') in {'running', 'stopping'}:
                raise ValueError('研究关系核验正在运行，请完成或停止后再补齐向量')
            try:
                with update_lock(self.store.root):
                    pass
            except ValueError:
                raise ValueError('完整更新或核验正在运行，请完成后再补齐向量') from None
            queued = queue_vectors(self.store)
            if queued['state'] != 'started':
                return queued

            def work():
                try:
                    run_vectors(self.store, queued['id'])
                except Exception:
                    pass
            self.task = threading.Thread(target=work, daemon=True, name='kb-vectors')
            self.task.start()
            return queued

    def start_update(self, bootstrap=False):
        with self.guard:
            if self.task and self.task.is_alive():
                return {'state': 'running', 'message': '更新任务已在运行'}
            with self.store.db() as db:
                active = db.execute("SELECT kind FROM jobs WHERE kind IN ('update','vectors','documents') AND status IN ('queued','running','stopping') LIMIT 1").fetchone()
            if active:
                return {'state': 'running', 'message': '已有完整更新或向量索引任务运行中'}
            if self.store.state('analysis_state', {}).get('state') in {'running', 'stopping'}:
                return {'state': 'running', 'message': '研究关系核验正在进行，请完成或停止后再更新知识库'}
            try:
                with update_lock(self.store.root):
                    pass
            except ValueError:
                return {'state': 'running', 'message': '已有命令行或后台更新任务运行中，可在索引与更新中查看进度'}
            from kbapp.integrations.models import set_cancelled
            if self.store.state('local_model_state', {}).get('state') == 'running':
                raise ValueError('原文模型正在处理任务，请完成或停止后再更新知识库')
            set_cancelled(self.store, ['embedding', 'ocr', 'text'], False)

            def work():
                try:
                    run_update(self.store, bootstrap=bootstrap)
                except Exception:
                    import traceback
                    from kbapp.core.io import atomic
                    atomic(self.store.paths.runtime / 'logs/workbench-service-error.log', traceback.format_exc())
            self.task = threading.Thread(target=work, daemon=True, name='kb-update')
            self.task.start()
            return {'state': 'started'}
