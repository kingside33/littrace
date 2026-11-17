"""kbapp.integrations.ocr：由职责边界组织的知识库能力。"""
import base64
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from kbapp.core.io import read_json, write_json
FORMULA_PROMPT = 'Formula Recognition:'
PAGE_PROMPT = 'Text Recognition:'
TABLE_PROMPT = 'Table Recognition:'

class GLMOCRError(RuntimeError):
    pass

class OCRLengthLimitError(GLMOCRError):
    """识别响应被模型长度上限截断，可以通过细分页面重试。"""

class OCRRepeatLimitError(OCRLengthLimitError):
    """识别生成重复中止，原响应不完整，需细分页面重试。"""

def _pick_inline(s):
    """从散文里挑一个像公式的内联 $...$，忽略 $u$ and $r$ 里误抓到的 'and'。"""
    best = ''
    for m in re.finditer('(?<!\\$)\\$([^$]{1,300})\\$(?!\\$)', s, re.S):
        cand = m.group(1).strip()
        if not cand:
            continue
        if not re.search('[\\\\^_{}=<>≤≥]|\\d', cand):
            continue
        if len(cand) > len(best):
            best = cand
    return best

def _clean(text, mode):
    s = (text or '').strip()
    s = re.sub('^```(?:latex|tex|markdown|md)?\\s*', '', s, flags=re.I)
    s = re.sub('\\s*```$', '', s)
    if mode == 'formula':
        s = re.sub('^(?:latex|LaTeX)\\s*[:：]\\s*', '', s)
        m = re.search('\\$\\$(.+?)\\$\\$', s, re.S)
        if not m:
            m = re.search('\\\\\\[(.+?)\\\\\\]', s, re.S)
        if m:
            s = m.group(1).strip()
        elif '$' in s:
            s = _pick_inline(s)
        if s.startswith('$$') and s.endswith('$$') and (len(s) > 4):
            s = s[2:-2].strip()
        elif s.startswith('$') and s.endswith('$') and (len(s) > 2):
            s = s[1:-1].strip()
        if len(re.findall('[A-Za-z]{4,}', s)) > 12 and s.count('\\') < 2:
            return ''
    return s.strip()

class GLMOCR:

    def __init__(self, url=None, model=None, api=None, timeout=None, *, store=None):
        from kbapp.core.paths import Paths
        self.store = store
        self.paths = store.paths if store else Paths.from_base()
        self.url = (url or os.environ.get('GLM_OCR_URL') or 'http://127.0.0.1:11434').rstrip('/')
        self.model = model or os.environ.get('GLM_OCR_MODEL') or 'glm-ocr:latest'
        self.api = (api or os.environ.get('GLM_OCR_API') or 'ollama').lower()
        self.api_key = os.environ.get('GLM_OCR_API_KEY') or 'EMPTY'
        self.timeout = int(timeout or os.environ.get('GLM_OCR_TIMEOUT') or 180)
        self.cache_dir = str(self.paths.cache / 'ocr_cache')
        self._probe = None

    def probe(self):
        if self._probe is not None:
            return self._probe
        try:
            if self.api == 'ollama':
                data = self._get('/api/tags', timeout=3)
                models = [m.get('name', '') for m in data.get('models', [])]
            else:
                data = self._get('/v1/models', timeout=3)
                models = [m.get('id', '') for m in data.get('data', [])]
            ok = any((name.lower() == self.model.lower() for name in models))
            self._probe = {'ok': ok, 'url': self.url, 'api': self.api, 'model': self.model, 'models': models, 'detail': 'ok' if ok else '服务可达但未发现所配置的 OCR 模型'}
        except Exception as ex:
            self._probe = {'ok': False, 'url': self.url, 'api': self.api, 'model': self.model, 'models': [], 'detail': f'{type(ex).__name__}: {ex}'}
        return self._probe

    def available(self):
        return self.probe().get('ok', False)

    def _get(self, path, timeout=10):
        req = urllib.request.Request(self.url + path, headers={'Authorization': f'Bearer {self.api_key}'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8'))

    def _post(self, path, payload):
        from kbapp.storage.store import Store
        from kbapp.integrations.scheduling import model_slot
        from kbapp.integrations.models import prepare_local
        store = self.store or Store(paths=self.paths)
        with model_slot(store.root, should_stop=getattr(self, 'should_stop', lambda: False)):
            prepare_local(store, 'ocr')
            return self._post_unlocked(path, payload)

    def _post_unlocked(self, path, payload):
        req = urllib.request.Request(self.url + path, data=json.dumps(payload).encode('utf-8'), headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {self.api_key}'})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode('utf-8'))

    def image_to_text(self, png_bytes, mode='formula', use_cache=True):
        prompt = {'formula': FORMULA_PROMPT, 'page': PAGE_PROMPT, 'table': TABLE_PROMPT}[mode]
        from kbapp.integrations.model_identity import signature
        identity = signature(self.model, self.url, self.api)
        h = hashlib.sha256(json.dumps(identity, sort_keys=True).encode() + prompt.encode('utf-8') + png_bytes).hexdigest()
        use_cache = use_cache and identity['revision'] != 'unknown'
        cache_path = os.path.join(self.cache_dir, h + '.json')
        if use_cache:
            cached = read_json(cache_path)
            if cached and 'text' in cached:
                return cached['text']
        b64 = base64.b64encode(png_bytes).decode('ascii')
        last = None
        for attempt in range(3):
            if getattr(self, 'should_stop', lambda: False)():
                raise GLMOCRError('识别已停止，缓存已保留')
            try:
                if self.api == 'ollama':
                    data = self._post('/api/chat', {'model': self.model, 'messages': [{'role': 'user', 'content': prompt, 'images': [b64]}], 'stream': False, 'options': {'temperature': 0, 'num_ctx': 16384, 'num_predict': 8192}})
                    if data.get('done_reason') == 'length':
                        raise OCRLengthLimitError('OCR输出达到长度上限，不保存截断结果')
                    text = (data.get('message') or {}).get('content', '')
                else:
                    data = self._post('/v1/chat/completions', {'model': self.model, 'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': prompt}, {'type': 'image_url', 'image_url': {'url': f'data:image/png;base64,{b64}'}}]}], 'temperature': 0, 'max_tokens': 8192, 'stream': False})
                    if data['choices'][0].get('finish_reason') == 'length':
                        raise OCRLengthLimitError('OCR输出达到长度上限，不保存截断结果')
                    msg = data['choices'][0]['message']['content']
                    if isinstance(msg, list):
                        text = ''.join((x.get('text', '') for x in msg if isinstance(x, dict)))
                    else:
                        text = msg or ''
                text = _clean(text, mode)
                if not text:
                    if mode == 'page':
                        if use_cache:
                            write_json(cache_path, {'mode': mode, 'text': ''})
                        return ''
                    raise GLMOCRError('模型返回空文本')
                if use_cache:
                    write_json(cache_path, {'mode': mode, 'text': text})
                return text
            except Exception as ex:
                last = ex
                if isinstance(ex, OCRLengthLimitError):
                    raise
                if isinstance(ex, urllib.error.HTTPError):
                    detail = ex.read(4096).decode('utf-8', errors='replace')
                    if 'token repeat limit reached' in detail:
                        raise OCRRepeatLimitError('OCR生成重复中止，不保存不完整结果') from None
                    last = GLMOCRError(f'HTTP {ex.code}: {detail[:500]}')
                if attempt < 2:
                    time.sleep(2 * (attempt + 1))
        raise GLMOCRError(f'{type(last).__name__}: {last}')

    def formula_to_latex(self, png_bytes, use_cache=True):
        return self.image_to_text(png_bytes, 'formula', use_cache)

    def page_to_text(self, png_bytes, use_cache=True):
        return self.image_to_text(png_bytes, 'page', use_cache)

    def table_to_text(self, png_bytes, use_cache=True):
        return self.image_to_text(png_bytes, 'table', use_cache)
