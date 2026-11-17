"""kbapp.integrations.models：由职责边界组织的知识库能力。"""
from __future__ import annotations
import json
import re
from urllib.request import Request, urlopen
import kbapp.core.config as C
from kbapp.core.io import now
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from kbapp.core.win_runtime import process_info, listener_pid, terminate_owned

def release_local(store):
    from kbapp.integrations.scheduling import model_slot
    with model_slot(store.root):
        return _release_local(store)

def _release_local(store):
    previous = store.state('local_model_state', {})
    try:
        text_request(store, '/api/generate', {'model': store.settings()['local_text_model'], 'keep_alive': 0}, timeout=30, load=False)
    except Exception:
        pass
    finally:
        if previous.get('state') == 'failed':
            store.set_state('local_model_state', previous)
        elif previous.get('state') != 'stopped':
            store.set_state('local_model_state', {'state': 'idle', 'updated': now()})
TEXT_URL = 'http://127.0.0.1:11435'

def request(url, path, payload=None, timeout=30):
    req = Request(url + path, data=None if payload is None else json.dumps(payload).encode('utf-8'), headers={'Content-Type': 'application/json'})
    with urlopen(req, timeout=timeout) as response:
        return json.load(response)

def cancelled(store, role):
    return bool(store.state('model_cancelled', {}).get(role))

def set_cancelled(store, roles, value):
    with store.db() as db:
        row = db.execute("SELECT value FROM state WHERE key='model_cancelled'").fetchone()
        flags = json.loads(row[0]) if row else {}
        flags.update({role: value for role in roles})
        db.execute('INSERT OR REPLACE INTO state VALUES(?,?)', ('model_cancelled', json.dumps(flags)))

def _runtime_owned(record, require_listen=True):
    if not record or os.name != 'nt':
        return False
    pid = int(record.get('pid') or 0)
    value = process_info(pid)
    if not value:
        return False
    try:
        expected = 'llama-server.exe' if record.get('backend') == 'native' else 'ollama.exe'
        same_path = Path(value['executable']).name.lower() == expected
        if record.get('executable'):
            same_path = same_path and os.path.normcase(value['executable']) == os.path.normcase(record['executable'])
        if record.get('created_ticks'):
            same_time = value['created_ticks'] == int(record['created_ticks'])
        else:
            from datetime import datetime
            raw = record.get('created') or ''
            match = re.search('/Date\\((\\d+)', raw)
            milliseconds = int(match.group(1)) if match else datetime.fromisoformat(raw.replace('Z', '+00:00')).timestamp() * 1000
            same_time = abs(value['created_ticks'] / 10000 - 11644473600000 - milliseconds) < 1
        return same_path and same_time and (not require_listen or listener_pid(11435) == pid)
    except (ValueError, KeyError, TypeError):
        return False

def ensure_text_runtime(store):
    from kbapp.core.locks import update_lock
    with update_lock(store.root, 'workbench-runtime.lock'):
        record = store.state('local_text_runtime', {})
        model = store.settings()['local_text_model']
        native = model.lower().startswith(('kb-qwen3.6-35b-text', 'hf.co/mudler/qwen3.6-35b-a3b-apex-gguf:'))
        if not native:
            if record and record.get('backend') != 'ollama_api':
                stop_text_runtime(store)
            elif record.get('model') and record['model'] != model:
                stop_text_runtime(store)
            installed = request(C.OLLAMA, '/api/tags', timeout=5).get('models', [])
            if not any((p.get('name') == model or p.get('model') == model for p in installed)):
                raise ValueError(f'{model} 尚未下载完成；请等待 Ollama 下载完成后继续核验，应用不会重复下载')
            store.set_state('local_text_runtime', {'backend': 'ollama_api', 'url': C.OLLAMA, 'model': model, 'context': store.settings()['local_text_context']})
            return C.OLLAMA
        if _runtime_owned(record):
            if record.get('backend') == 'native' and record.get('model', model) == model and (record.get('context') == store.settings()['local_text_context']):
                return TEXT_URL
            stop_text_runtime(store)
        elif record.get('backend') == 'ollama_api':
            stop_text_runtime(store)
        with socket.socket() as probe:
            try:
                probe.bind(('127.0.0.1', 11435))
            except OSError:
                raise ValueError('关系核验模型端口11435被其他程序占用，未接管该程序') from None
        info = request(C.OLLAMA, '/api/show', {'model': model})
        paths = re.findall('^FROM\\s+(.+)$', info.get('modelfile', ''), re.M)
        weights = next((Path(p.strip().strip('"')) for p in paths if Path(p.strip().strip('"')).is_file()), None)
        if not weights or weights.parent.name != 'blobs':
            raise ValueError('无法定位已下载的本地语言权重')
        ollama = shutil.which('ollama')
        if not ollama:
            raise ValueError('未找到本地Ollama程序')
        executable = Path(ollama).parent / 'lib/ollama/llama-server.exe'
        cuda = executable.parent / 'cuda_v13'
        if not executable.is_file() or not (cuda / 'ggml-cuda.dll').is_file():
            raise ValueError('未找到Ollama附带的CUDA推理运行时')
        env = dict(os.environ, GGML_BACKEND_PATH=str(cuda / 'ggml-cuda.dll'), PATH=str(cuda) + os.pathsep + os.environ.get('PATH', ''))
        context = store.settings()['local_text_context']
        args = [str(executable), '--model', str(weights), '--alias', model, '--no-host', '--parallel', '1', '--ctx-size', str(context), '--batch-size', '128', '--ubatch-size', '128', '--gpu-layers', 'auto', '--flash-attn', 'on', '--sleep-idle-seconds', '300', '--no-context-shift', '--host', '127.0.0.1', '--port', '11435', '--cors-origins', 'http://127.0.0.1:8765', '--no-webui', '--offline', '--no-reasoning-preserve']
        with (store.paths.runtime / 'logs/local-text-runtime.log').open('a', encoding='utf-8') as log:
            process = subprocess.Popen(args, env=env, stdout=log, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        identity = process_info(process.pid)
        if not identity:
            process.terminate()
            raise ValueError('无法登记刚启动的核验进程')
        store.set_state('local_text_runtime', dict(identity, url=TEXT_URL, backend='native', no_host=True, context=context, model=model))
        for _ in range(720):
            if cancelled(store, 'text'):
                stop_text_runtime(store)
                raise ValueError('关系核验模型已停止，已保留完成的缓存')
            if process.poll() is not None:
                detail = (store.paths.runtime / 'logs/local-text-runtime.log').read_text(encoding='utf-8', errors='replace')[-1200:]
                raise ValueError('本地核验运行环境启动失败：\n' + detail)
            try:
                request(TEXT_URL, '/health', timeout=1)
                return TEXT_URL
            except Exception:
                time.sleep(0.25)
        raise ValueError('本地核验运行环境启动超时')

def stop_text_runtime(store):
    record = store.state('local_text_runtime', {})
    if record.get('backend') == 'ollama_api':
        request(C.OLLAMA, '/api/generate', {'model': record['model'], 'keep_alive': 0}, timeout=60)
    elif _runtime_owned(record, require_listen=False):
        pid = int(record['pid'])
        info = process_info(pid)
        if info:
            if record.get('created_ticks') and int(record['created_ticks']) != info['created_ticks']:
                raise ValueError('进程身份已变化，已保留登记，未停止该进程')
            if record.get('backend') == 'native':
                terminate_owned(pid, int(record.get('created_ticks') or info['created_ticks']))
            else:
                subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), check=True)
    elif record and os.name == 'nt' and process_info(int(record.get('pid') or 0)):
        raise ValueError('模型进程身份无法核对，已保留登记，未结束该进程')
    store.set_state('local_text_runtime', {})
    store.set_state('local_model_state', {'state': 'stopped', 'updated': now()})

def text_request(store, path, payload, timeout=600, load=True):
    record = store.state('local_text_runtime', {})
    url = ensure_text_runtime(store) if load else record.get('url')
    if not url:
        return {}
    if path == '/api/generate' and payload.get('keep_alive') == 0:
        stop_text_runtime(store)
        return {}
    if path != '/api/chat':
        raise ValueError('不支持的本地关系核验模型请求')
    if url == C.OLLAMA:
        return ollama_chat(store, payload, timeout)
    options = payload.get('options', {})
    body = {'model': payload['model'], 'messages': payload['messages'], 'stream': False, 'max_tokens': options.get('num_predict', 1536), 'temperature': options.get('temperature', 0.1), 'response_format': {'type': 'json_object'}, 'chat_template_kwargs': {'enable_thinking': False}, 'cache_prompt': True, 'n_keep': 0, 'context_shift': False, 'truncate': False}
    raw = request(url, '/v1/chat/completions', body, timeout)
    choice = raw.get('choices', [{}])[0]
    usage = raw.get('usage', {})
    return {'message': choice.get('message', {}), 'done_reason': 'length' if choice.get('finish_reason') == 'length' else 'stop', 'prompt_eval_count': usage.get('prompt_tokens'), 'eval_count': usage.get('completion_tokens')}

def ollama_chat(store, payload, timeout):
    """消费流式响应以支持停止；只把完整的最终 JSON 返回核验缓存。"""
    body = dict(payload, stream=True)
    req = Request(C.OLLAMA + '/api/chat', data=json.dumps(body).encode('utf-8'), headers={'Content-Type': 'application/json'})
    content = []
    with urlopen(req, timeout=timeout) as response:
        for line in response:
            if cancelled(store, 'text'):
                raise ValueError('本地关系核验模型已停止；已保留完成的缓存')
            if not line.strip():
                continue
            part = json.loads(line)
            if part.get('error'):
                raise ValueError(part['error'])
            content.append((part.get('message') or {}).get('content') or '')
            if part.get('done'):
                return dict(part, message={'role': 'assistant', 'content': ''.join(content)})
    raise ValueError('本地模型响应中断，未缓存不完整的核验结果')

def text_busy(store):
    if store.state('recognition_state', {}).get('state') == 'running':
        return True
    state = store.state('local_model_state', {}).get('state')
    if state == 'running':
        return True
    try:
        record = store.state('local_text_runtime', {})
        url = record.get('url')
        if not url:
            return False
        if record.get('backend') == 'ollama_api':
            return any((p.get('name') == record['model'] or p.get('model') == record['model'] for p in request(C.OLLAMA, '/api/ps', timeout=2).get('models', [])))
        return request(url, '/health', timeout=2).get('status') == 'ok'
    except Exception:
        return False

def model_status(store):
    base = []
    text = []
    installed = None
    try:
        base = request(C.OLLAMA, '/api/ps', timeout=2).get('models', [])
    except Exception:
        pass
    try:
        installed = {p.get('name') or p.get('model') for p in request(C.OLLAMA, '/api/tags', timeout=2).get('models', [])}
    except Exception:
        pass
    record = store.state('local_text_runtime', {})
    if record.get('url') and record.get('backend') != 'ollama_api':
        try:
            if request(record['url'], '/health', timeout=2).get('status') == 'ok':
                text = [{'model': record.get('model') or store.settings()['local_text_model']}]
        except Exception:
            pass
    items = []
    for role, name in [('embedding', C.EMBED_MODEL), ('ocr', C.GLM_OCR_MODEL), ('text', store.settings()['local_text_model'])]:
        loaded = next((p for p in base + text if p.get('model') == name or p.get('name') == name), None)
        items.append({'role': role, 'model': name, 'loaded': bool(loaded), 'installed': None if installed is None else name in installed, 'size': (loaded or {}).get('size', 0), 'gpu_size': (loaded or {}).get('size_vram', 0)})
    return {'items': items, 'control': store.state('model_control', {}), 'local_state': store.state('local_model_state', {})}

def prepare_local(store, role):
    """已取得唯一槽位后才切换模型；不终止用户的 Ollama 服务。"""
    import kbapp.core.config as C
    if cancelled(store, role):
        raise ValueError('本地模型已被手动停止；已完成缓存保留')
    if role != 'text' and store.state('local_text_runtime', {}):
        stop_text_runtime(store)
    targets = {'embedding': C.EMBED_MODEL, 'ocr': C.GLM_OCR_MODEL, 'text': store.settings()['local_text_model']}
    loaded = request(C.OLLAMA, '/api/ps', timeout=3).get('models', [])
    owned = set(targets.values())
    for model in loaded:
        name = model.get('name') or model.get('model')
        if name in owned and name != targets[role]:
            request(C.OLLAMA, '/api/generate', {'model': name, 'keep_alive': 0}, timeout=60)
