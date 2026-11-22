"""kbapp.integrations.entitlements：商业层授权的解析、缓存与进程级解析器。

核心只消费商业层返回的 `tier` 与租约，不读 license 文件、不验签、不落盘。
模块级的默认解析器固定为 free/unconfigured，导入与读取都不发任何 socket；
真实探测器只在 `create_app` 的 lifespan 里通过 `install(fetch)` 装配。
"""
from __future__ import annotations
import http.client
import json
import logging
import threading
from datetime import datetime
from urllib.parse import urlparse
from kbapp.core.io import TZ

logger = logging.getLogger(__name__)

COMMERCIAL_BASE_URL = 'http://127.0.0.1:8766'
COMMERCIAL_PATH = '/api/v2/entitlements'
PRO_TTL_SECONDS = 300
OTHER_TTL_SECONDS = 60
CONNECT_TIMEOUT_SECONDS = 0.3
READ_TIMEOUT_SECONDS = 0.5
MAX_BODY_BYTES = 64 * 1024
SOURCES = ('commercial', 'commercial-cached', 'commercial-unavailable',
           'commercial-invalid', 'commercial-cached-expired', 'unconfigured')
REASONS = (None, 'not_purchased', 'license_expired', 'commercial_unreachable',
           'commercial_not_running', 'commercial_invalid_response',
           'lease_expired', 'not_configured')
STATES = ('available', 'needs-pro', 'commercial-offline',
          'commercial-unknown', 'service-offline')

_DEFAULT = {'tier': 'free', 'source': 'unconfigured', 'reason': 'not_configured',
            'checked_at': None, 'lease_expires_at': None, 'reachable': False}
_lock = threading.Lock()
_transport = None
_cache = None


def _default():
    return dict(_DEFAULT)


def _copy(verdict):
    return dict(verdict)


def _parse_time(value):
    """把 ISO 字符串解析成带时区的 datetime；坏格式或 naive 返回 None。

    `datetime.fromisoformat` 在 Python 3.10 不认识 `Z` 后缀（3.11 才加入），
    而本仓库支持 3.10+。先把末尾 `Z` 归一化成 `+00:00`，让同一份商业层
    应答在所有受支持解释器上得到同一结论。
    """
    if not isinstance(value, str):
        return None
    text = value[:-1] + '+00:00' if value.endswith(('Z', 'z')) else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _invalid(checked_at):
    return {'tier': 'free', 'source': 'commercial-invalid',
            'reason': 'commercial_invalid_response', 'checked_at': checked_at,
            'lease_expires_at': None, 'reachable': False}


def parse(status, body, *, now):
    """解析一次商业层应答；纯函数，不建连接。任何不合契约的输入都 fail-closed。"""
    checked_at = now.isoformat()
    if status != 200:
        return _invalid(checked_at)
    if isinstance(body, str):
        try:
            raw = body.encode('utf-8')
        except UnicodeEncodeError:
            return _invalid(checked_at)
    elif isinstance(body, (bytes, bytearray)):
        raw = bytes(body)
    else:
        return _invalid(checked_at)
    if len(raw) > MAX_BODY_BYTES:
        return _invalid(checked_at)
    try:
        payload = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        return _invalid(checked_at)
    if not isinstance(payload, dict):
        return _invalid(checked_at)
    # bool 是 int 的子类，必须用 type 严格判定，避免 schema=True 混过去。
    if type(payload.get('schema')) is not int or payload.get('schema') != 1:
        return _invalid(checked_at)
    if payload.get('product') != 'littrace':
        return _invalid(checked_at)
    tier = payload.get('tier')
    if tier not in ('free', 'pro'):
        return _invalid(checked_at)
    entitlements = payload.get('entitlements')
    if not isinstance(entitlements, list) or any(not isinstance(item, str) for item in entitlements):
        return _invalid(checked_at)
    for item in entitlements:
        if item != 'analysis.full':
            logger.info('忽略未知授权项：%s', item)
    lease_raw = payload.get('lease_expires_at')
    lease = _parse_time(lease_raw)
    if lease is None:
        return _invalid(checked_at)
    license_state = None
    license_expires = None
    license = payload.get('license')
    if license is not None:
        if not isinstance(license, dict):
            return _invalid(checked_at)
        license_state = license.get('state')
        if license_state not in ('active', 'expired', 'revoked'):
            return _invalid(checked_at)
        if 'expires_at' in license:
            license_expires = _parse_time(license.get('expires_at'))
            if license_expires is None:
                return _invalid(checked_at)

    if tier == 'pro':
        if now >= lease:
            return {'tier': 'free', 'source': 'commercial', 'reason': 'lease_expired',
                    'checked_at': checked_at, 'lease_expires_at': lease_raw, 'reachable': False}
        return {'tier': 'pro', 'source': 'commercial', 'reason': None,
                'checked_at': checked_at, 'lease_expires_at': lease_raw, 'reachable': True}
    reason = 'not_purchased'
    if license_state in ('expired', 'revoked'):
        reason = 'license_expired'
    elif license_expires is not None and now >= license_expires:
        reason = 'license_expired'
    return {'tier': 'free', 'source': 'commercial', 'reason': reason,
            'checked_at': checked_at, 'lease_expires_at': lease_raw, 'reachable': True}


def fetch():
    """真实探测：直连本机商业层，总预算不超过 800 ms，不走环境代理。"""
    url = urlparse(COMMERCIAL_BASE_URL)
    connection = http.client.HTTPConnection(url.hostname, url.port, timeout=CONNECT_TIMEOUT_SECONDS)
    try:
        connection.connect()
        if connection.sock is not None:
            connection.sock.settimeout(READ_TIMEOUT_SECONDS)
        connection.request('GET', COMMERCIAL_PATH, headers={'Accept': 'application/json'})
        response = connection.getresponse()
        return response.status, response.read(MAX_BODY_BYTES + 1)
    finally:
        connection.close()


def install(transport):
    """安装进程级探测器；安装本身不发请求，并清空旧缓存。"""
    global _transport, _cache
    with _lock:
        _transport = transport
        _cache = None


def reset():
    """清空进程级探测器与缓存，回到默认 free/unconfigured。"""
    global _transport, _cache
    with _lock:
        _transport = None
        _cache = None


def _fallback(now):
    """探测失败时的降级：只有此前验证过且租约仍有效的 pro 缓存能给出 pro。"""
    cached = _cache
    if cached is not None and cached.get('tier') == 'pro':
        lease_raw = cached.get('lease_expires_at')
        lease = _parse_time(lease_raw)
        if lease is not None:
            if now < lease:
                return {'tier': 'pro', 'source': 'commercial-cached',
                        'reason': 'commercial_unreachable', 'checked_at': now.isoformat(),
                        'lease_expires_at': lease_raw, 'reachable': False}
            return {'tier': 'free', 'source': 'commercial-cached-expired',
                    'reason': 'lease_expired', 'checked_at': now.isoformat(),
                    'lease_expires_at': lease_raw, 'reachable': False}
    return {'tier': 'free', 'source': 'commercial-unavailable',
            'reason': 'commercial_not_running', 'checked_at': now.isoformat(),
            'lease_expires_at': None, 'reachable': False}


def refresh(*, now=None):
    """在缓存缺失或过期时发起一次探测；并发刷新复用同一次探测结果。

    单飞以“缓存是否仍新鲜”判定，而不是比较调用方的时间戳：真实并发请求
    各自取 `datetime.now()`，微秒级差异必然不同；只比较时间戳相等会让等待者
    再发一次探测。持有 `_lock` 期间完成探测，后来者在锁内看到新鲜缓存就直接
    返回，不再进入 transport。
    """
    global _cache
    current_time = now or datetime.now(TZ)
    with _lock:
        if _transport is None:
            return _default()
        cached = _cache
        if cached is not None and not _stale(cached, current_time):
            return _copy(cached)
        try:
            status, body = _transport()
        except Exception:
            result = _fallback(current_time)
        else:
            result = parse(status, body, now=current_time)
        _cache = result
        return _copy(result)


def _stale(verdict, now):
    checked = _parse_time(verdict.get('checked_at'))
    if checked is None:
        return True
    ttl = PRO_TTL_SECONDS if verdict.get('tier') == 'pro' else OTHER_TTL_SECONDS
    if (now - checked).total_seconds() >= ttl:
        return True
    if verdict.get('tier') == 'pro':
        lease = _parse_time(verdict.get('lease_expires_at'))
        if lease is None or now >= lease:
            return True
    return False


def current(*, now=None):
    """请求路径只读缓存；没有安装 transport 时直接返回默认值，零 I/O。"""
    current_time = now or datetime.now(TZ)
    with _lock:
        if _transport is None:
            return _default()
        cached = _copy(_cache) if _cache is not None else None
    if cached is None or _stale(cached, current_time):
        return refresh(now=current_time)
    return cached


def state(verdict):
    """五态词表的唯一 Python 定义；本模块永远不返回 service-offline。"""
    if verdict.get('tier') == 'pro':
        return 'available'
    reason = verdict.get('reason')
    if reason in ('commercial_not_running', 'commercial_unreachable'):
        return 'commercial-offline'
    if reason in ('commercial_invalid_response', 'lease_expired', 'not_configured'):
        return 'commercial-unknown'
    return 'needs-pro'
