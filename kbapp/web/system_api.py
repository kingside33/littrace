"""HTTP 路由：system_api；通过 AppContext 使用业务能力。"""
from __future__ import annotations
from importlib import metadata
from kbapp.workflows.status import status as workbench_status


def service_version():
    """service_version 的唯一出处：pyproject.toml 的 [project].version。

    已安装环境读取由它生成的包元数据（pip install -e . / wheel 均可）；
    源码直跑且未安装时，兜底解析同一个 pyproject.toml。
    不在代码里另设第二份版本常量，避免双出处漂移。
    """
    try:
        return metadata.version('littrace')
    except metadata.PackageNotFoundError:
        import re
        from pathlib import Path
        pyproject = Path(__file__).resolve().parents[2] / 'pyproject.toml'
        found = re.search(r'^version\s*=\s*"([^"]+)"', pyproject.read_text(encoding='utf-8'), re.MULTILINE)
        if not found:
            raise RuntimeError('pyproject.toml 缺少 [project].version，无法确定 service_version') from None
        return found.group(1)


def install(app, context, token):
    store = context.store
    TOKEN = token

    @app.get('/api/session')
    def session():
        return {'token': TOKEN}

    @app.get('/api/version')
    def version():
        # 版本协商采用方案②：公开 GET、无鉴权（与 /api/session 同级），
        # 供插件做存在性探测与兼容性判断；最终定案确认由维护者执行（S0）。
        return {'service_version': service_version(), 'api_version': 'v2'}

    @app.get('/api/status')
    def status(detail: bool=True):
        return workbench_status(store, detail=detail)

    @app.get('/api/models')
    def models():
        from kbapp.integrations.models import model_status
        return model_status(store)

    @app.post('/api/models')
    def control_model(payload: dict):
        from kbapp.workflows.model_control import start_control
        return start_control(store, payload.get('action'), payload.get('role'))

    @app.get('/api/settings')
    def settings():
        # `cache` is what the settings page shows for the (configurable) cache
        # directory: effective path, default path, whether it is overridden,
        # and how many files/bytes it holds. Read-only projection; the writable
        # setting stays `cache_dir` in the flat map.
        return dict(
            store.settings(public=True),
            connection=store.state('api_connection', {}),
            cache=store.cache_info(),
        )

    @app.post('/api/settings')
    def save_settings(values: dict):
        # Same shape as GET, `cache` included: the settings page shows the
        # save result from this response, and a save that silently omits the
        # projection would force a second GET to say what happened (and lose
        # the confirmation if that second call fails).
        return dict(store.save_settings(values), cache=store.cache_info())

    @app.post('/api/settings/test')
    def settings_test():
        # 同步 def，不是 async def：单进程 uvicorn 下同步端点由 starlette 丢线程池，
        # 探测的阻塞（最多 PROBE_TIMEOUT_S 秒）只占一个线程，不会卡住插件每 30 秒
        # 一次的 /api/version 状态点轮询。/api/ask 是同一先例。
        #
        # 不接收请求体：一律探测**已保存**的配置。save_settings 的 HTTPS/loopback
        # 限制是唯一的 URL 校验路径，从这里再收一个地址等于多开一条把 Bearer 密钥
        # 发往任意地址的入口。
        from kbapp.integrations.cloud import probe_models
        return probe_models(store)

    @app.post('/api/jobs')
    def jobs(data: dict):
        if data.get('action') == 'vectors':
            return app.state.start_vectors()
        if data.get('action', 'update') != 'update':
            raise ValueError('未知任务')
        return app.state.start_update()
