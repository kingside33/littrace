"""HTTP 路由：search_api；通过 AppContext 使用业务能力。"""
from __future__ import annotations
from kbapp.research.relations import ask

def install(app, context, token):
    store = context.store
    search_engine = context.search

    @app.get('/api/search')
    def search(q: str, k: int=12, kind: str='', direction: str='', year: str='', project: bool=False, paper: str='', section: str='', vectors: bool=True, wait_vectors: bool=True, resume_model: bool=True, collection: str='', tag: str='', document: str='', level: str='passage'):
        if not q.strip() or len(q) > 2000:
            raise ValueError('检索问题长度应为1至2000字符')
        if vectors and resume_model:
            from kbapp.integrations.models import set_cancelled
            set_cancelled(store, ['embedding'], False)
        from kbapp.workflows.references import attach_citations
        return attach_citations(store, search_engine.search(q, min(max(k, 1), 50), kind, direction, year, project, paper, section, vectors, wait_vectors=wait_vectors, collection=collection, tag=tag, document=document, level='paper' if level == 'paper' else 'passage'))

    @app.post('/api/ask')
    def question(payload: dict):
        q = payload.get('query', '').strip()
        if not q or len(q) > 2000:
            raise ValueError('问题长度应为1至2000字符')
        allowed = {k: v for k, v in payload.get('filters', {}).items() if k in {'kind', 'direction', 'year', 'project', 'paper', 'section', 'collection', 'tag', 'document'}}
        from kbapp.integrations.models import set_cancelled
        set_cancelled(store, ['embedding', 'text'], False)
        return ask(store, q, allowed)
