"""kbapp.storage.activity：由职责边界组织的知识库能力。"""
from kbapp.core.execution import current_store
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import json
from kbapp.core.io import now
from kbapp.core.execution import research_run
step_id = ContextVar('research_step', default='')
current_role = ContextVar('research_role', default='coordinator')
work_item = ContextVar('research_work_item', default=None)
stop_check = ContextVar('research_stop_check', default=lambda: False)
dashboard_phase_id = ContextVar('dashboard_phase_id', default='')
ROLE_NAMES = {'coordinator': '研究协调', 'reader': '全文精读', 'retriever': '证据检索', 'lineage_researcher': '谱系研究', 'writer': '结论写作', 'verifier': '本地核验', 'cloud_reviewer': '云端复核', 'ocr': '原文识别', 'indexer': '索引构建'}
ALIASES = {'协调者': 'coordinator', '精读者': 'reader', '证据检索者': 'retriever', '谱系研究者': 'lineage_researcher', '写作者': 'writer', '核验者': 'verifier', 'GLM-OCR': 'ocr'}

def task_key():
    value = research_run.get()
    return '' if value == 'manual' else value

def emit(store, stage, status='running', message='', role=None, **payload):
    current_store.set(store)
    key = task_key()
    if not key:
        return
    role = ALIASES.get(role, role) if role else current_role.get()
    if work_item.get():
        payload.setdefault('work_item', work_item.get())
    if dashboard_phase_id.get():
        payload.setdefault('dashboard_phase', dashboard_phase_id.get())
    record = dict(task_id=key, step_id=step_id.get(), role=role, stage=stage, status=status, message=message, **payload)
    at = now()
    with store.db() as db:
        prior = db.execute('SELECT payload FROM task_activity WHERE task_id=?', (key,)).fetchone()
        if prior and json.loads(prior[0]) == record:
            return
        db.execute('INSERT INTO task_events(task_id,step_id,role,stage,status,message,payload,created) VALUES(?,?,?,?,?,?,?,?)', (key, step_id.get(), role, stage, status, message, json.dumps(payload, ensure_ascii=False), at))
        db.execute('INSERT OR REPLACE INTO task_activity VALUES(?,?,?)', (key, json.dumps(record, ensure_ascii=False), at))

@contextmanager
def dashboard_phase(store, phase, message, role='indexer'):
    """记录真实工作边界，并让内部进度、模型事件带上所属流程。"""
    token = dashboard_phase_id.set(phase)
    emit(store, 'dashboard_phase', 'running', message, role=role)
    try:
        yield
    except Exception:
        emit(store, 'dashboard_phase', 'failed', message + '未完成', role=role)
        raise
    else:
        emit(store, 'dashboard_phase', 'complete', message + '完成', role=role)
    finally:
        dashboard_phase_id.reset(token)

@contextmanager
def scope(store, key, step='', role='coordinator', *, subject='', label=''):
    tokens = (research_run.set(key), step_id.set(step), current_role.set(role), work_item.set({'subject': subject, 'label': label} if subject else None))
    try:
        yield
    finally:
        work_item.reset(tokens[3])
        current_role.reset(tokens[2])
        step_id.reset(tokens[1])
        research_run.reset(tokens[0])

def track_job(function):

    @wraps(function)
    def wrapped(store, job, *args, **kwargs):
        with scope(store, 'job:' + job):
            emit(store, 'prepare', message='准备任务')
            try:
                return function(store, job, *args, **kwargs)
            finally:
                with store.db() as db:
                    row = db.execute('SELECT status,progress FROM jobs WHERE id=?', (job,)).fetchone()
                if row:
                    emit(store, 'finished', row['status'], row['progress'] or '')
    return wrapped

def model_role(purpose, cloud=False):
    if purpose=='relation_material':
        return 'retriever'
    if any((word in purpose for word in ('review', 'verify', 'verification'))):
        return 'cloud_reviewer' if cloud else 'verifier'
    if 'extract' in purpose:
        return 'reader'
    if 'lineage' in purpose or 'classif' in purpose:
        return 'lineage_researcher'
    if 'search' in purpose or 'rerank' in purpose:
        return 'retriever'
    return 'writer' if cloud else 'reader'

def track_model(cloud=False):

    def decorate(function):

        @wraps(function)
        def wrapped(store, prompt, purpose, *args, **kwargs):
            token = current_role.set(model_role(purpose, cloud))
            cfg = store.settings()
            model = cfg['model' if cloud else 'local_text_model']
            emit(store, purpose, 'running', '准备云端请求' if cloud else '准备本地请求', model=model)
            try:
                result = function(store, prompt, purpose, *args, **kwargs)
                emit(store, purpose, 'complete', '模型阶段完成', model=model)
                return result
            except Exception as exc:
                emit(store, purpose, 'failed', '模型阶段未完成，已保存完成结果', model=model, error_type=type(exc).__name__)
                raise
            finally:
                current_role.reset(token)
        return wrapped
    return decorate

def queue_event(root, resource, status):
    store = current_store.get()
    if not task_key() or store is None or store.root != root:
        return
    emit(store, 'model_queue', status, ('等待' if status == 'waiting_resource' else '已取得') + ('本地模型槽位' if resource == 'local' else '云端请求槽位'), resource=resource)
