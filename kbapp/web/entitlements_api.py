"""HTTP 路由：entitlements_api；只做路由与字段组装。

契约见 issue #47：字段集合固定，端点永远返回 200——商业层未运行、被占用、
应答不合法都是正常状态。pro 的 `limits` 为四个 null，表示不设上限。
"""
from __future__ import annotations
from datetime import datetime
from kbapp.core.io import TZ
from kbapp.core.quota import ANALYSIS_TASKS_PER_DAY, day_of, limits
from kbapp.integrations import entitlements


def install(app, context, token):
    store = context.store

    @app.get('/api/v2/entitlements')
    def entitlements_v2():
        verdict = entitlements.current()
        tier = verdict['tier']
        day = day_of(datetime.now(TZ))
        used = store.quota_count(day)
        return {
            'api_version': 'v2',
            'tier': tier,
            'source': verdict['source'],
            'reason': verdict['reason'],
            'checked_at': verdict['checked_at'],
            'lease_expires_at': verdict['lease_expires_at'],
            'limits': limits(tier),
            'usage': {'day': day,
                      'analysis_tasks': used,
                      'remaining': None if tier == 'pro' else max(0, ANALYSIS_TASKS_PER_DAY - used)},
            'commercial': {'base_url': entitlements.COMMERCIAL_BASE_URL,
                           'reachable': verdict['reachable']},
        }
