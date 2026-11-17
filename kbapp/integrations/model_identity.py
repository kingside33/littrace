"""kbapp.integrations.model_identity：由职责边界组织的知识库能力。"""
import json
import time
from urllib.request import urlopen
_cache = {}

def signature(model, url, api='ollama', refresh=False):
    key = (url, model, api)
    cached = _cache.get(key)
    if cached and (not refresh) and (time.monotonic() - cached[0] < 30):
        return dict(cached[1])
    revision = None
    if api == 'ollama':
        try:
            with urlopen(url.rstrip('/') + '/api/tags', timeout=3) as response:
                data = json.load(response)
            revision = next((m.get('digest') for m in data.get('models', []) if m.get('name') == model), None)
        except (OSError, ValueError):
            pass
    value = {'model': model, 'endpoint': url.rstrip('/'), 'api': api, 'revision': revision or 'unknown'}
    _cache[key] = (time.monotonic(), value)
    return dict(value)

def embedding_signature():
    import kbapp.core.config as C
    return dict(signature(C.EMBED_MODEL, C.OLLAMA), dims=C.EMBED_DIMS, version=2)
