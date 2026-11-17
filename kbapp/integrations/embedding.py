"""kbapp.integrations.embedding：由职责边界组织的知识库能力。"""
import hashlib
import json
import re
import numpy as np
import kbapp.core.config as C
from kbapp.core.chunking import est_tokens
QUERY_INSTRUCTION = C.QUERY_INSTRUCTION
POLICY_VERSION = 'source-budget-v1'


def embedding_policy():
    return {'version': POLICY_VERSION, 'max_est_tokens': C.CHUNK_MAX_TOKENS,
            'estimator': 'cjk-1.5-other-third-v1', 'truncate': False,
            'normalization': 'split_long-whitespace'}


_BUDGET_CJK = re.compile('[一-鿿]')


def within_budget(text):
    # Exactly the same estimate as core.chunking.est_tokens, but length bounds
    # and the C regex scanner avoid a Python character loop on cold corpus load.
    limit = C.CHUNK_MAX_TOKENS
    size = len(text)
    if limit < 1:
        return False
    if int(size * 1.5) <= limit:
        return True
    if int(size / 3) > limit:
        return False
    cjk = 0 if text.isascii() else len(_BUDGET_CJK.findall(text))
    return max(1, int(cjk * 1.5 + (size - cjk) / 3)) <= limit


def validate_inputs(texts):
    for i, text in enumerate(texts):
        if not within_budget(text):
            raise ValueError(f'Embedding input {i} exceeds estimated token budget '
                             f'({est_tokens(text)} > {C.CHUNK_MAX_TOKENS}); split source or shorten query')

def http_post(path, payload, timeout=300):
    import urllib.request
    req = urllib.request.Request(C.OLLAMA + path, data=json.dumps(payload).encode('utf-8'), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))

def embed_texts(model, texts, timeout=300, store=None):
    validate_inputs(texts)
    from kbapp.storage.store import Store
    from kbapp.integrations.scheduling import model_slot
    from kbapp.integrations.models import prepare_local
    store = store or Store(C.BASE)
    with model_slot(store.root):
        prepare_local(store, 'embedding')
        return _embed_texts(model, texts, timeout)

def _embed_texts(model, texts, timeout=300):
    validate_inputs(texts)
    # The server tokenizer is authoritative. Indexing isolates rejected items;
    # no returned vector may silently describe only a prefix of its source.
    payload = {'model': model, 'input': texts, 'truncate': False, 'keep_alive': '5m'}
    if model == C.EMBED_MODEL:
        payload['dimensions'] = C.EMBED_DIMS
    from urllib.error import HTTPError
    try:
        d = http_post('/api/embed', payload, timeout=timeout)
    except HTTPError as exc:
        if exc.code not in (400, 413, 422):
            raise
        try:
            detail = exc.read(4096).decode('utf-8', errors='replace')
        finally:
            exc.close()
        if any(term in detail.lower() for term in ('context length', 'input length', 'too long', 'token limit')):
            raise ValueError('Embedding server context length exceeded; input was not truncated; '
                             'source remains keyword-only pending smaller chunks') from exc
        raise
    return d['embeddings']

def truncate_normalize(mat, dims):
    if mat.shape[1] > dims:
        mat = mat[:, :dims]
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (mat / norms).astype(np.float32)

def chunk_hash(c):
    h = c.get('text_hash')
    if h:
        return h
    return hashlib.sha1((c.get('text') or '').encode('utf-8')).hexdigest()[:12]

def check(model):
    try:
        e = embed_texts(model, ['测试'], timeout=120)
    except Exception as ex:
        print(f'[FAIL] 无法调用 {model}: {type(ex).__name__}: {ex}')
        print('       先让模型可用：ollama pull qwen3-embedding:8b')
        return None
    return len(e[0])
