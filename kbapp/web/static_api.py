"""HTTP 路由：static_api；通过 AppContext 使用业务能力。"""
from __future__ import annotations
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, Response
from kbapp.core.io import digest

def install(app, context, token):
    WEB = context.paths.assets
    cache = {}

    def cached(request: Request, version, media_type: str, build):
        """H09：按内容版本缓存正文并生成 ETag；未变化时返回 304。"""
        entry = cache.get(version)
        if entry is None:
            body = build()
            if isinstance(body, str):
                body = body.encode('utf-8')
            entry = (digest(body), body)
            cache[version] = entry
        etag, body = entry
        headers = {'ETag': etag, 'Cache-Control': 'no-cache'}
        if request.headers.get('if-none-match') == etag:
            return Response(status_code=304, headers=headers)
        return Response(body, media_type=media_type, headers=headers)

    def file_version(name):
        path = WEB / name
        stat = path.stat()
        return (name, stat.st_mtime_ns, stat.st_size)

    @app.get('/')
    def homepage(request: Request):
        from kbapp.core.web_assets import render_html
        return cached(request, file_version('index.html'), 'text/html', render_html)

    @app.get('/app.css')
    def css(request: Request):
        return cached(request, file_version('app.css'), 'text/css', lambda: (WEB / 'app.css').read_bytes())

    @app.get('/app.js')
    def javascript(request: Request):
        from kbapp.core.web_assets import javascript as build_js, javascript_version
        return cached(request, ('app.js',) + javascript_version(), 'application/javascript', build_js)

    @app.get('/dashboard.js')
    def dashboard_javascript():
        return Response('', media_type='application/javascript')

    @app.get('/dashboard.css')
    def dashboard_css(request: Request):
        return css(request)

    @app.get('/icons/{name}.svg')
    def dashboard_icon(name: str):
        if not name.replace('-', '').isalnum():
            raise HTTPException(404)
        path = WEB / 'icons' / (name + '.svg')
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type='image/svg+xml')

    @app.get('/feedback.js')
    def feedback_javascript():
        return Response('', media_type='application/javascript')
