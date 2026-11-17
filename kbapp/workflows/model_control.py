"""kbapp.workflows.model_control：由职责边界组织的知识库能力。"""
from __future__ import annotations
from kbapp.workflows.status import status as workbench_status
import os
import threading
import time
import kbapp.core.config as C
from kbapp.core.io import digest, now
from kbapp.integrations.models import cancelled, request, set_cancelled, stop_text_runtime, text_busy
_control_thread = None
_control_guard = threading.Lock()

def start_control(store, action, role):
    global _control_thread
    if action not in {'load', 'stop'} or role not in {'embedding', 'ocr', 'text', 'all'}:
        raise ValueError('无效的模型操作')
    if action == 'load' and role == 'all':
        raise ValueError('模型按任务逐个加载，避免显存争用')
    roles = ['embedding', 'ocr', 'text'] if role == 'all' else [role]
    with _control_guard:
        if action == 'load' and _control_thread and _control_thread.is_alive():
            return {'state': 'running', 'message': '模型操作正在进行'}
        if action == 'load' and role == 'text' and (workbench_status(store)['indexing']['state'] == 'running'):
            raise ValueError('请先完成向量索引，再加载关系核验模型')
        if action == 'load' and role == 'text' and (store.state('recognition_state', {}).get('state') == 'running'):
            raise ValueError('GLM-OCR正在识别原文，请完成或停止识别后再加载关系核验模型')
        if action == 'load' and role != 'text' and text_busy(store):
            raise ValueError('请先停止关系核验模型，再加载其他模型')
        set_cancelled(store, roles, action == 'stop')
        job = digest('model:' + str(time.time_ns()))[:20]
        label = {'embedding': '向量模型', 'ocr': '图文识别模型', 'text': '关系核验模型', 'all': '本应用模型'}[role]
        with store.db() as db:
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)', (job, 'model', 'running', os.getpid(), '{}', ('正在加载' if action == 'load' else '正在停止') + label, now(), None, None))
        store.set_state('model_control', {'id': job, 'state': 'running', 'action': action, 'role': role, 'updated': now()})

        def work():
            error = None
            try:
                if action == 'stop':
                    if 'text' in roles:
                        stop_text_runtime(store)
                    for item in roles:
                        if item != 'text':
                            request(C.OLLAMA, '/api/generate', {'model': C.EMBED_MODEL if item == 'embedding' else C.GLM_OCR_MODEL, 'keep_alive': 0}, timeout=60)
                elif role == 'text':
                    from kbapp.integrations.local_model import complete_local
                    complete_local(store, '只返回 {"ready":true}。', 'manual_model_load', max_tokens=64)
                else:
                    from kbapp.integrations.scheduling import model_slot
                    from kbapp.integrations.models import prepare_local
                    with model_slot(store.root):
                        prepare_local(store, role)
                        if role == 'embedding':
                            request(C.OLLAMA, '/api/embed', {'model': C.EMBED_MODEL, 'input': [], 'keep_alive': '5m'}, timeout=180)
                        else:
                            request(C.OLLAMA, '/api/generate', {'model': C.GLM_OCR_MODEL, 'keep_alive': '5m'}, timeout=180)
                outcome = 'complete'
            except Exception as exc:
                error = str(exc)
                outcome = 'interrupted' if action == 'load' and cancelled(store, role) else 'failed'
            with store.db() as db:
                db.execute('UPDATE jobs SET status=?,finished=?,error=?,progress=? WHERE id=?', (outcome, now(), error, '模型操作已完成' if outcome == 'complete' else '模型操作已停止' if outcome == 'interrupted' else '模型操作未完成', job))
            if store.state('model_control', {}).get('id') == job:
                store.set_state('model_control', {'id': job, 'state': outcome, 'action': action, 'role': role, 'error': error, 'updated': now()})
        _control_thread = threading.Thread(target=work, daemon=True, name='kb-model-control')
        _control_thread.start()
        return {'state': 'started', 'message': '正在加载模型' if action == 'load' else '正在停止模型'}
