"""I02：在途单元租约、心跳、进程身份与安全接管。"""

import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['KB_NO_STARTUP_JOBS'] = '1'
os.environ['KB_NO_REFERENCE_WORKER'] = '1'
from kbapp.storage.store import Store
from kbapp.core.process import alive, identity, parse
from kbapp.core.io import TZ
from kbapp.workflows.lease import Lease, acquire, lease_of, takeover_allowed
from kbapp.workflows import agent_units, agents


class LeaseTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        with self.store.db() as db:
            db.execute(
                "INSERT INTO agent_runs VALUES('r','lineage','running','{}','fp',0,0,'{}','','2026-11-03','2026-11-03')"
            )
            db.execute(
                "INSERT INTO agent_tasks VALUES('t','r','verifier','p','fp','running','{}','{}',0,'','2026-11-03')"
            )
            db.execute(
                "INSERT INTO agent_units VALUES('u','t','r','gather-evidence','retriever','p','k','running','{}','{}',1,'','2026-11-03')"
            )

    def test_process_identity_is_parsable_and_alive(self):
        me = identity()
        self.assertIsNotNone(parse(me))
        self.assertTrue(alive(me))
        self.assertFalse(alive('999999:deadbeef:0'))
        self.assertFalse(alive(''))
        pid, _token, ticks = parse(me)
        self.assertFalse(
            alive(f'{pid}:different-token:{ticks}'), '同 pid 不同实例 token 不能视为同一执行者'
        )

    def test_claim_writes_lease_and_finish_releases(self):
        with self.store.db() as db:
            db.execute("UPDATE agent_units SET status='queued' WHERE id='u'")
        unit = agent_units.claim(self.store, 'r')
        self.assertIsNotNone(unit)
        lease = lease_of(self.store, unit['id'])
        self.assertEqual(lease['owner'], identity())
        self.assertFalse(takeover_allowed(self.store, unit['id']))
        agent_units.finish(self.store, unit, {'ok': True})
        self.assertIsNone(lease_of(self.store, unit['id']))

    def test_recover_takes_over_only_dead_or_expired_owners(self):
        acquire(self.store, 'u')
        result = agents.recover_units(self.store)
        self.assertEqual(result['taken_over'], 0, '本进程持有且租约有效时不得抢占')
        with self.store.db() as db:
            self.assertEqual(
                db.execute("SELECT status FROM agent_units WHERE id='u'").fetchone()[0], 'running'
            )
        with self.store.db() as db:
            db.execute(
                "UPDATE agent_unit_leases SET owner='999999:deadbeef:0', lease_until=? WHERE unit_id='u'",
                ((datetime.now(TZ) + timedelta(minutes=5)).isoformat(),),
            )
        result = agents.recover_units(self.store)
        self.assertEqual(result['taken_over'], 1)
        with self.store.db() as db:
            row = db.execute("SELECT status,error FROM agent_units WHERE id='u'").fetchone()
            self.assertEqual(row['status'], 'queued')
            self.assertIn('接管', row['error'])
            self.assertIsNone(
                db.execute("SELECT 1 FROM agent_unit_leases WHERE unit_id='u'").fetchone()
            )

    def test_expired_lease_allows_takeover_even_if_owner_alive(self):
        acquire(self.store, 'u')
        with self.store.db() as db:
            db.execute(
                "UPDATE agent_unit_leases SET lease_until=? WHERE unit_id='u'",
                ((datetime.now(TZ) - timedelta(minutes=1)).isoformat(),),
            )
        self.assertTrue(takeover_allowed(self.store, 'u'))
        self.assertEqual(agents.recover_units(self.store)['taken_over'], 1)

    def test_heartbeat_extends_lease_while_running(self):
        interval = 0.05
        # #88 的失败是心跳线程错过一个固定的 0.2 s 窗口。这里不再读一次就
        # 下结论,而是在 20 × interval(封顶 1 s)里按 interval / 5 采样、数
        # 续租次数,用绝对余量吸收 CI 的调度抖动。
        # 下限 = 窗口 / (4 × interval):旧用例的窗口就是 4 × interval 且只
        # 要求一次变化,等价于「4 个间隔内必须续租」;同强度换算到 20 个
        # 间隔的窗口就是 ≥ 5 次。所以放宽的是**窗口**,不是**续租速率**:
        # 注入 interval 实测 ~0.19 s 起判红,与旧用例同一量级(边界随负载
        # 抖动,不是精确值)。
        window = min(20 * interval, 1.0)
        expected = int(window / interval)
        required = expected // 4
        renewals = 0
        with Lease(self.store, 'u', interval=interval) as lease:
            first = lease_of(self.store, 'u')['lease_until']
            previous = first
            deadline = time.monotonic() + window
            while time.monotonic() < deadline:
                time.sleep(interval / 5)
                current = lease_of(self.store, 'u')['lease_until']
                if current != previous:
                    renewals += 1
                    previous = current
            beat_alive = lease._thread.is_alive()
            self.assertGreaterEqual(
                renewals,
                required,
                f'{window:.2f}s 窗口内(interval={interval}s)按间隔应续租约 {expected} 次,'
                f'要求至少 {required} 次,实际 {renewals} 次;心跳线程存活={beat_alive}。'
                f'线程未存活 → 「首次异常即静默退出」(#134);线程存活但 0 次 → 心跳一次都'
                f'没写成功;少于 {required} 次 → 续租间隔被放大。',
            )
            self.assertGreater(previous, first)
        self.assertIsNone(lease_of(self.store, 'u'))


if __name__ == '__main__':
    unittest.main()
