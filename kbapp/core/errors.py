"""kbapp.workflows.errors：错误分类与重试策略（I03/I04）。

分类：网络连接、服务限流、输出格式、证据不足、人工等待、费用额度、其他执行错误。
重试：只有“请求确定未生效”的错误自动退避重试；可能已计费的请求保持待处理，
必须由用户明确续跑，避免重复计费或掩盖问题。
"""
from __future__ import annotations
import http.client
import socket
import ssl
from urllib.error import HTTPError, URLError

KINDS = {
    'network': '网络连接问题',
    'rate_limit': '服务限流',
    'output_format': '输出格式不完整',
    'evidence_insufficient': '证据不足',
    'manual_wait': '等待人工处理',
    'budget': '费用额度不足',
    'execution': '执行错误',
}

POLICY = {
    'network': {'mode': 'backoff', 'reason': '连接未建立或服务端 5xx，可退避重试'},
    'rate_limit': {'mode': 'backoff', 'reason': '限流后按退避重试，不并发追加请求'},
    'output_format': {'mode': 'hold', 'reason': '已产生费用且结构不完整，保留原始响应等待人工续跑'},
    'evidence_insufficient': {'mode': 'none', 'reason': '证据不足不是执行错误，补充证据后重评'},
    'manual_wait': {'mode': 'none', 'reason': '等待人工审批或判断'},
    'budget': {'mode': 'hold', 'reason': '额度不足，保留任务到下一周期'},
    'execution': {'mode': 'hold', 'reason': '原因不明确，保留结果等待排查'},
}


def classify(exc=None, status=None, message=''):
    """把异常/状态/消息归入统一错误类别。"""
    text = ' '.join(str(x) for x in (message, status, exc) if x)
    if isinstance(exc, HTTPError):
        if exc.code == 429:
            return 'rate_limit'
        if 500 <= exc.code < 600:
            return 'network'
    reason = getattr(exc, 'reason', exc)
    if isinstance(exc, URLError) or isinstance(reason, (ConnectionError, TimeoutError, socket.gaierror, ssl.SSLError, http.client.HTTPException)):
        return 'network'
    if any(word in text for word in ('费用额度不足', '额度不足')):
        return 'budget'
    if any(word in text for word in ('结构不完整', '格式不兼容', '必须返回JSON', '未返回完整结构', '长度上限')):
        return 'output_format'
    if any(word in text for word in ('等待审批', '人工确认', '等待人工')):
        return 'manual_wait'
    if any(word in text for word in ('证据不足', 'insufficient')):
        return 'evidence_insufficient'
    return 'execution'


def retry_policy(kind, sent=None):
    """sent=True 表示请求可能已到达服务端；此时只有确定不生效的情况才自动重试。"""
    policy = dict(POLICY.get(kind, POLICY['execution']))
    if sent and kind in {'network', 'rate_limit'}:
        policy = {'mode': 'hold', 'reason': '请求可能已到达服务端，结果不确定，保留待人工续跑'}
    return policy


def explain(kind):
    return KINDS.get(kind, kind)
