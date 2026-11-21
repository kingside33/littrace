"""商业层授权解析、缓存、五态与零 I/O 默认值（issue #47）。"""
import json
import os
import sys
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['KB_NO_STARTUP_JOBS'] = '1'
os.environ['KB_NO_REFERENCE_WORKER'] = '1'
from kbapp.core.io import TZ
from kbapp.integrations import entitlements

NOW = datetime(2026, 11, 19, 10, 0, tzinfo=TZ)
FUTURE = (NOW + timedelta(hours=2)).isoformat()
FIXED_KEYS = {'tier', 'source', 'reason', 'checked_at', 'lease_expires_at', 'reachable'}


def response(**overrides):
    payload = {'schema': 1, 'product': 'littrace', 'tier': 'free',
               'entitlements': [], 'lease_expires_at': FUTURE}
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


class Transport:
    """可替换应答的假探测器；原子计数，便于断言探测次数。"""

    def __init__(self, reply):
        self.reply = reply
        self.calls = 0
        self._lock = threading.Lock()

    def __call__(self):
        with self._lock:
            self.calls += 1
            reply = self.reply
        if isinstance(reply, BaseException):
            raise reply
        return reply


class EntitlementsBase(unittest.TestCase):

    def setUp(self):
        entitlements.reset()
        self.addCleanup(entitlements.reset)

    def install(self, reply):
        transport = Transport(reply)
        entitlements.install(transport)
        return transport

    def verdict(self, reply, now=NOW):
        self.install(reply)
        return entitlements.refresh(now=now)


class EntitlementsMatrix(EntitlementsBase):

    def test_parse_result_has_exactly_the_contract_keys(self):
        result = entitlements.parse(200, response(tier='pro'), now=NOW)
        self.assertEqual(set(result), FIXED_KEYS)
        self.assertEqual(result['tier'], 'pro')

    def test_valid_pro_with_live_lease(self):
        result = self.verdict((200, response(tier='pro', entitlements=['analysis.full'])))
        self.assertEqual((result['tier'], result['source'], result['reason'], result['reachable']),
                         ('pro', 'commercial', None, True))
        self.assertEqual(result['lease_expires_at'], FUTURE)
        self.assertEqual(result['checked_at'], NOW.isoformat())

    def test_valid_pro_with_expired_lease_is_free(self):
        expired = (NOW - timedelta(seconds=1)).isoformat()
        result = self.verdict((200, response(tier='pro', lease_expires_at=expired)))
        self.assertEqual((result['tier'], result['source'], result['reason'], result['reachable']),
                         ('free', 'commercial', 'lease_expired', False))

    def test_valid_free_without_license_is_not_purchased(self):
        result = self.verdict((200, response(tier='free')))
        self.assertEqual((result['tier'], result['source'], result['reason'], result['reachable']),
                         ('free', 'commercial', 'not_purchased', True))

    def test_free_license_expired_or_revoked(self):
        for state in ('expired', 'revoked'):
            result = self.verdict((200, response(tier='free', license={'state': state})))
            self.assertEqual((result['tier'], result['source'], result['reason'], result['reachable']),
                             ('free', 'commercial', 'license_expired', True))
        past = (NOW - timedelta(days=1)).isoformat()
        result = self.verdict((200, response(tier='free', license={'state': 'active', 'expires_at': past})))
        self.assertEqual(result['reason'], 'license_expired')

    def test_free_active_license_with_future_expiry_is_not_purchased(self):
        future = (NOW + timedelta(days=30)).isoformat()
        result = self.verdict((200, response(tier='free', license={'state': 'active', 'expires_at': future})))
        self.assertEqual(result['reason'], 'not_purchased')
        result = self.verdict((200, response(tier='free', license={'state': 'active'})))
        self.assertEqual(result['reason'], 'not_purchased')

    def test_invalid_payloads_fail_closed(self):
        cases = [
            (200, 'not json'),
            (200, b'\xff\xfe'),
            (200, json.dumps({'schema': 2, 'product': 'littrace', 'tier': 'free',
                              'entitlements': [], 'lease_expires_at': FUTURE})),
            (200, json.dumps({'schema': True, 'product': 'littrace', 'tier': 'free',
                              'entitlements': [], 'lease_expires_at': FUTURE})),
            (200, response(product='other')),
            (200, response(tier='enterprise')),
            (200, response(entitlements='analysis.full')),
            (200, response(entitlements=[1])),
            (200, json.dumps({'schema': 1, 'product': 'littrace', 'tier': 'free', 'entitlements': []})),
            (200, response(lease_expires_at='not-a-time')),
            (200, response(lease_expires_at='2026-11-19T10:00:00')),
            (200, response(license='active')),
            (200, response(license={'state': 'unknown'})),
            (200, response(license={'state': 'active', 'expires_at': '2026-11-19T10:00:00'})),
            (200, b'x' * (entitlements.MAX_BODY_BYTES + 1)),
            (500, response()),
            (200, json.dumps(['not', 'an', 'object'])),
        ]
        for status, body in cases:
            result = self.verdict((status, body))
            self.assertEqual((result['tier'], result['source'], result['reason'], result['reachable']),
                             ('free', 'commercial-invalid', 'commercial_invalid_response', False),
                             (status, body))

    def test_unknown_entitlement_items_are_ignored(self):
        result = self.verdict((200, response(tier='pro', entitlements=['analysis.full', 'future.thing'])))
        self.assertEqual(result['tier'], 'pro')

    def test_transport_failures_are_unavailable_and_never_pro(self):
        for exc in (ConnectionRefusedError('refused'), TimeoutError('timeout'),
                    ValueError('bad transport'), RuntimeError('boom')):
            result = self.verdict(exc)
            self.assertEqual((result['tier'], result['source'], result['reason'], result['reachable']),
                             ('free', 'commercial-unavailable', 'commercial_not_running', False), repr(exc))

    def test_non_200_is_invalid_not_unavailable(self):
        result = self.verdict((503, response()))
        self.assertEqual((result['source'], result['reason']), ('commercial-invalid', 'commercial_invalid_response'))


class EntitlementsCache(EntitlementsBase):

    def pro_reply(self, lease=FUTURE):
        return (200, response(tier='pro', lease_expires_at=lease))

    def test_pro_cache_ttl_is_300_seconds(self):
        transport = self.install(self.pro_reply((NOW + timedelta(hours=2)).isoformat()))
        entitlements.refresh(now=NOW)
        self.assertEqual(transport.calls, 1)
        entitlements.current(now=NOW + timedelta(seconds=299))
        self.assertEqual(transport.calls, 1)
        entitlements.current(now=NOW + timedelta(seconds=301))
        self.assertEqual(transport.calls, 2)

    def test_free_cache_ttl_is_60_seconds(self):
        transport = self.install((200, response(tier='free')))
        entitlements.refresh(now=NOW)
        self.assertEqual(transport.calls, 1)
        entitlements.current(now=NOW + timedelta(seconds=59))
        self.assertEqual(transport.calls, 1)
        entitlements.current(now=NOW + timedelta(seconds=61))
        self.assertEqual(transport.calls, 2)

    def test_lease_takes_priority_over_ttl(self):
        transport = self.install(self.pro_reply((NOW + timedelta(seconds=120)).isoformat()))
        entitlements.refresh(now=NOW)
        transport.reply = ConnectionRefusedError('down')
        result = entitlements.current(now=NOW + timedelta(seconds=200))
        self.assertEqual((result['tier'], result['source'], result['reason'], result['reachable']),
                         ('free', 'commercial-cached-expired', 'lease_expired', False))

    def test_expired_lease_reprobes_and_accepts_new_pro(self):
        transport = self.install(self.pro_reply((NOW + timedelta(seconds=120)).isoformat()))
        entitlements.refresh(now=NOW)
        transport.reply = self.pro_reply((NOW + timedelta(hours=1)).isoformat())
        result = entitlements.current(now=NOW + timedelta(seconds=200))
        self.assertEqual((result['tier'], result['source'], result['reason']),
                         ('pro', 'commercial', None))

    def test_failed_probe_with_valid_cached_lease_stays_pro_from_cache(self):
        transport = self.install(self.pro_reply((NOW + timedelta(hours=1)).isoformat()))
        entitlements.refresh(now=NOW)
        transport.reply = TimeoutError('timeout')
        result = entitlements.refresh(now=NOW + timedelta(seconds=301))
        self.assertEqual((result['tier'], result['source'], result['reason'], result['reachable']),
                         ('pro', 'commercial-cached', 'commercial_unreachable', False))

    def test_explicit_free_clears_cached_pro(self):
        transport = self.install(self.pro_reply((NOW + timedelta(hours=1)).isoformat()))
        entitlements.refresh(now=NOW)
        transport.reply = (200, response(tier='free'))
        self.assertEqual(entitlements.refresh(now=NOW + timedelta(seconds=301))['tier'], 'free')
        transport.reply = ConnectionRefusedError('down')
        result = entitlements.refresh(now=NOW + timedelta(seconds=302))
        self.assertEqual(result['tier'], 'free')
        self.assertEqual(result['source'], 'commercial-unavailable')

    def test_checked_at_only_moves_on_a_probe(self):
        transport = self.install(self.pro_reply((NOW + timedelta(hours=2)).isoformat()))
        first = entitlements.refresh(now=NOW)
        second = entitlements.current(now=NOW + timedelta(seconds=10))
        self.assertEqual(first['checked_at'], NOW.isoformat())
        self.assertEqual(second['checked_at'], NOW.isoformat())
        self.assertEqual(transport.calls, 1)

    def test_concurrent_refresh_is_single_flight(self):
        transport = self.install(self.pro_reply((NOW + timedelta(hours=2)).isoformat()))
        barrier = threading.Barrier(2)
        results = []
        errors = []

        def worker():
            try:
                barrier.wait(timeout=5)
                results.append(entitlements.refresh(now=NOW))
            except BaseException as exc:  # pragma: no cover - 失败时给出原因
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertEqual(errors, [])
        self.assertEqual(transport.calls, 1)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], results[1])


class EntitlementsState(EntitlementsBase):

    def test_state_vocabulary(self):
        self.assertEqual(len(entitlements.STATES), 5)
        self.assertEqual(len(set(entitlements.STATES)), 5)
        self.assertEqual(len(set(entitlements.SOURCES)), len(entitlements.SOURCES))
        self.assertEqual(len(set(entitlements.REASONS)), len(entitlements.REASONS))
        self.assertIn('service-offline', entitlements.STATES)

    def test_state_mapping(self):
        available = {'tier': 'pro', 'source': 'commercial-cached', 'reason': 'commercial_unreachable'}
        needs_pro = {'tier': 'free', 'source': 'commercial', 'reason': 'not_purchased'}
        offline = {'tier': 'free', 'source': 'commercial-unavailable', 'reason': 'commercial_not_running'}
        unknown = {'tier': 'free', 'source': 'commercial-invalid', 'reason': 'commercial_invalid_response'}
        self.assertEqual(entitlements.state(available), 'available')
        self.assertEqual(entitlements.state(needs_pro), 'needs-pro')
        self.assertEqual(entitlements.state(offline), 'commercial-offline')
        self.assertEqual(entitlements.state(unknown), 'commercial-unknown')
        self.assertNotEqual(entitlements.state(needs_pro), entitlements.state(offline))
        for verdict in (available, needs_pro, offline, unknown,
                        {'tier': 'free', 'source': 'unconfigured', 'reason': 'not_configured'},
                        {'tier': 'free', 'source': 'commercial-cached-expired', 'reason': 'lease_expired'}):
            self.assertNotEqual(entitlements.state(verdict), 'service-offline')


class EntitlementsDefaults(EntitlementsBase):

    def expected_default(self):
        return {'tier': 'free', 'source': 'unconfigured', 'reason': 'not_configured',
                'checked_at': None, 'lease_expires_at': None, 'reachable': False}

    def test_default_resolver_does_no_io(self):
        with patch('socket.socket.connect', side_effect=AssertionError('network')):
            self.assertEqual(entitlements.current(now=NOW), self.expected_default())
            self.assertEqual(entitlements.refresh(now=NOW), self.expected_default())
        entitlements.reset()
        with patch('socket.socket.connect', side_effect=AssertionError('network')):
            self.assertEqual(entitlements.current(now=NOW), self.expected_default())

    def test_default_dict_is_a_fresh_copy(self):
        first = entitlements.current(now=NOW)
        first['tier'] = 'pro'
        self.assertEqual(entitlements.current(now=NOW)['tier'], 'free')


class EntitlementsTime(unittest.TestCase):

    def test_utc_and_beijing_representations_are_the_same_instant(self):
        self.assertEqual(datetime.fromisoformat('2026-11-19T10:00:00+08:00'),
                         datetime.fromisoformat('2026-11-19T02:00:00Z'))
        self.assertIsNotNone(timezone.utc)


if __name__ == '__main__':
    unittest.main()
