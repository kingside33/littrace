"""HTTP 应用工厂：仅负责生命周期、会话校验及路由组装。"""
from __future__ import annotations
import contextlib
from datetime import datetime
import os
import secrets
import threading
from urllib.parse import urlparse
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from kbapp.core.io import TZ
from kbapp.workflows.update import needs_daily
from kbapp.web.document_api import install as install_document_api

def create_app(context):
    start_update = context.jobs.start_update
    start_vectors = context.jobs.start_vectors
    store = context.store
    TOKEN = secrets.token_urlsafe(32)

    @contextlib.asynccontextmanager
    async def lifespan(app):
        stop = threading.Event()
        if context.background_jobs:
            from kbapp.web.document_api import recover_documents
            recover_documents(store)
            from kbapp.workflows.vector_jobs import recover_vectors
            recover_vectors(store)
            from kbapp.workflows.verification import recover_verification
            recover_verification(store)
            from kbapp.research.lineage import seed_methods
            seed_methods(store)
            from kbapp.workflows.agents import start_dispatcher
            start_dispatcher(store)
            from kbapp.workflows.references import start_worker
            start_worker(store, stop)
            if not os.environ.get('KB_NO_STARTUP_JOBS'):

                def scheduler():
                    while not stop.is_set():
                        if needs_daily(store):
                            store.set_state('last_daily_attempt', datetime.now(TZ).date().isoformat())
                            start_update(bootstrap=not bool(store.state('active_version')))
                        stop.wait(30)
                threading.Thread(target=scheduler, daemon=True, name='kb-scheduler').start()
        yield
        stop.set()
        from kbapp.integrations.models import stop_text_runtime
        if context.background_jobs:
            from kbapp.workflows.agents import stop_dispatcher
            stop_dispatcher(store)
            stop_text_runtime(store)
    app = FastAPI(title='研究文献工作台', docs_url=None, redoc_url=None, lifespan=lifespan)
    install_document_api(app, store)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])
    from starlette.middleware.gzip import GZipMiddleware
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    @app.middleware('http')
    async def local_guard(request, call_next):
        if request.url.path.startswith('/api/'):
            origin = request.headers.get('origin')
            if origin and (urlparse(origin).netloc != request.headers.get('host') or urlparse(origin).scheme != 'http'):
                return JSONResponse({'detail': '仅允许本机工作台发起请求'}, status_code=403)
            if request.method not in {'GET', 'HEAD', 'OPTIONS'} and request.headers.get('x-kb-token') != TOKEN:
                return JSONResponse({'detail': '本机会话校验失败，请刷新页面'}, status_code=403)
            if int(request.headers.get('content-length', '0')) > 1024 * 1024:
                return JSONResponse({'detail': '请求内容过大'}, status_code=413)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    @app.exception_handler(ValueError)
    async def validation_error(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=400)
    app.state.context = context
    app.state.start_update = start_update
    app.state.start_vectors = start_vectors
    from kbapp.web.system_api import install as install_system_api
    install_system_api(app, context, TOKEN)
    from kbapp.web.search_api import install as install_search_api
    install_search_api(app, context, TOKEN)
    from kbapp.web.library_api import install as install_library_api
    install_library_api(app, context, TOKEN)
    from kbapp.web.research_api import install as install_research_api
    install_research_api(app, context, TOKEN)
    from kbapp.web.graph_api import install as install_graph_api
    install_graph_api(app, context, TOKEN)
    from kbapp.web.static_api import install as install_static_api
    install_static_api(app, context, TOKEN)
    from kbapp.web.v2_api import install as install_v2_api
    install_v2_api(app, context, TOKEN)
    return app
