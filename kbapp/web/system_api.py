"""HTTP 路由：system_api；通过 AppContext 使用业务能力。"""
from __future__ import annotations
from kbapp.workflows.status import status as workbench_status

def install(app, context, token):
    store = context.store
    TOKEN = token

    @app.get('/api/session')
    def session():
        return {'token': TOKEN}

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
        return dict(store.settings(public=True), connection=store.state('api_connection', {}))

    @app.post('/api/settings')
    def save_settings(values: dict):
        return store.save_settings(values)

    @app.post('/api/jobs')
    def jobs(data: dict):
        if data.get('action') == 'vectors':
            return app.state.start_vectors()
        if data.get('action', 'update') != 'update':
            raise ValueError('未知任务')
        return app.state.start_update()
