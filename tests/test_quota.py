"""授权额度底座：常量、纯决策、配额事件表与幂等计数（issue #47）。"""
import os
import sqlite3
import string
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['KB_NO_STARTUP_JOBS'] = '1'
os.environ['KB_NO_REFERENCE_WORKER'] = '1'
from kbapp.core import quota
from kbapp.core.io import TZ
from kbapp.storage.store import Store


class QuotaDecisions(unittest.TestCase):

    def test_constants_are_frozen(self):
        self.assertEqual(quota.PAPERS_PER_RUN, 20)
        self.assertEqual(quota.REFERENCES_PER_PAPER, 10)
        self.assertEqual(quota.VERIFIER_UNITS_PER_RUN, 20)
        self.assertEqual(quota.ANALYSIS_TASKS_PER_DAY, 3)
        self.assertEqual(quota.LIMIT_KEYS, ('papers_per_run', 'references_per_paper',
                                            'verifier_units_per_run', 'analysis_tasks_per_day'))
        self.assertEqual(quota.REJECTING_QUOTAS, ('papers_per_run', 'verifier_units_per_run',
                                                  'analysis_tasks_per_day'))
        self.assertEqual(quota.EVENT_KINDS, ('analysis_task',))

    def test_limits_free_pro_and_fail_closed(self):
        self.assertEqual(quota.limits('free'),
                         {'papers_per_run': 20, 'references_per_paper': 10,
                          'verifier_units_per_run': 20, 'analysis_tasks_per_day': 3})
        self.assertEqual(quota.limits('pro'),
                         {'papers_per_run': None, 'references_per_paper': None,
                          'verifier_units_per_run': None, 'analysis_tasks_per_day': None})
        self.assertEqual(quota.limits('unknown'), quota.limits('free'))
        self.assertEqual(quota.limits(None), quota.limits('free'))

    def test_limits_returns_a_fresh_dict(self):
        first = quota.limits('free')
        first['papers_per_run'] = 999
        self.assertEqual(quota.limits('free')['papers_per_run'], 20)

    def test_exceeds_run_boundaries(self):
        self.assertFalse(quota.exceeds('papers_per_run', 'free', requested=20))
        self.assertTrue(quota.exceeds('papers_per_run', 'free', requested=21))
        self.assertFalse(quota.exceeds('verifier_units_per_run', 'free', requested=20))
        self.assertTrue(quota.exceeds('verifier_units_per_run', 'free', requested=21))

    def test_exceeds_daily_tasks_boundary(self):
        self.assertFalse(quota.exceeds('analysis_tasks_per_day', 'free', used_today=2))
        self.assertTrue(quota.exceeds('analysis_tasks_per_day', 'free', used_today=3))

    def test_exceeds_pro_is_never_over_limit(self):
        self.assertFalse(quota.exceeds('papers_per_run', 'pro', requested=21))
        self.assertFalse(quota.exceeds('verifier_units_per_run', 'pro', requested=21))
        self.assertFalse(quota.exceeds('analysis_tasks_per_day', 'pro', used_today=3))
        self.assertFalse(quota.exceeds('analysis_tasks_per_day', 'pro', used_today=99))

    def test_exceeds_rejects_non_rejecting_and_unknown_keys(self):
        with self.assertRaises(ValueError):
            quota.exceeds('references_per_paper', 'free')
        with self.assertRaises(ValueError):
            quota.exceeds('no_such_quota', 'free')

    def test_exceeds_run_quota_requires_requested(self):
        with self.assertRaises(ValueError):
            quota.exceeds('papers_per_run', 'free')
        with self.assertRaises(ValueError):
            quota.exceeds('verifier_units_per_run', 'free')

    def test_day_of_uses_beijing_natural_day(self):
        self.assertEqual(quota.day_of(datetime.fromisoformat('2026-11-19T23:59:59+08:00')), '2026-11-19')
        self.assertEqual(quota.day_of(datetime.fromisoformat('2026-11-20T00:00:00+08:00')), '2026-11-20')
        self.assertEqual(quota.day_of(datetime.fromisoformat('2026-11-19T15:59:59Z')), '2026-11-19')
        self.assertEqual(quota.day_of(datetime.fromisoformat('2026-11-19T16:00:00Z')), '2026-11-20')
        self.assertEqual(quota.day_of(datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc)), '2026-11-19')

    def test_day_of_refuses_naive_datetime(self):
        with self.assertRaises(ValueError):
            quota.day_of(datetime(2026, 11, 19, 10, 0))

    def test_event_id_is_stable_and_depends_on_all_three_fields(self):
        first = quota.event_id('2026-11-19', 'analysis_task', 'run-1')
        self.assertEqual(first, quota.event_id('2026-11-19', 'analysis_task', 'run-1'))
        self.assertEqual(len(first), 64)
        self.assertTrue(all(ch in string.hexdigits for ch in first))
        self.assertNotEqual(first, quota.event_id('2026-11-20', 'analysis_task', 'run-1'))
        self.assertNotEqual(first, quota.event_id('2026-11-19', 'other', 'run-1'))
        self.assertNotEqual(first, quota.event_id('2026-11-19', 'analysis_task', 'run-2'))


class QuotaStorage(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)

    def test_fresh_store_has_quota_table_index_and_version(self):
        with self.store.db() as db:
            table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='quota_events'").fetchone()
            index = db.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='quota_event_day'").fetchone()
            version = db.execute('SELECT 1 FROM schema_migrations WHERE version=12').fetchone()
        self.assertIsNotNone(table)
        self.assertIsNotNone(index)
        self.assertIsNotNone(version)
        self.assertTrue((self.store.paths.backups / 'pre-schema-12.sqlite3').exists())

    def test_v11_database_is_backed_up_and_migrated_without_data_loss(self):
        self.store.put_papers([{'id': 'p1', 'title': 'One', 'kind': 'library'},
                               {'id': 'p2', 'title': 'Two', 'kind': 'library'}])
        self.store.relation({'src': 'p1', 'dst': 'p2', 'kind': 'cites', 'status': 'confirmed'})
        backup = self.store.paths.backups / 'pre-schema-12.sqlite3'
        backup.unlink()
        with self.store.db() as db:
            db.execute('DELETE FROM schema_migrations WHERE version=12')
            db.execute('DROP TABLE quota_events')
        reopened = Store(self.temp.name)
        with reopened.db() as db:
            table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='quota_events'").fetchone()
            index = db.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='quota_event_day'").fetchone()
            version = db.execute('SELECT 1 FROM schema_migrations WHERE version=12').fetchone()
            self.assertEqual(db.execute('SELECT COUNT(*) FROM papers').fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM relations').fetchone()[0], 1)
        self.assertIsNotNone(table)
        self.assertIsNotNone(index)
        self.assertIsNotNone(version)
        self.assertTrue(backup.exists())
        with closing(sqlite3.connect(backup)) as db:
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='quota_events'").fetchone())
            self.assertEqual(db.execute('SELECT COUNT(*) FROM papers').fetchone()[0], 2)

    def test_record_is_idempotent_per_key(self):
        self.assertEqual(self.store.quota_count('2026-11-19'), 0)
        self.assertTrue(self.store.quota_record('2026-11-19', 'analysis_task', 'run-1', 'free'))
        self.assertEqual(self.store.quota_count('2026-11-19'), 1)
        self.assertFalse(self.store.quota_record('2026-11-19', 'analysis_task', 'run-1', 'free'))
        self.assertEqual(self.store.quota_count('2026-11-19'), 1)
        # 换 tier 不改 id，仍然幂等。
        self.assertFalse(self.store.quota_record('2026-11-19', 'analysis_task', 'run-1', 'pro'))
        self.assertEqual(self.store.quota_count('2026-11-19'), 1)
        # 换 day 或 subject 才增加。
        self.assertTrue(self.store.quota_record('2026-11-20', 'analysis_task', 'run-1', 'free'))
        self.assertTrue(self.store.quota_record('2026-11-19', 'analysis_task', 'run-2', 'free'))
        self.assertEqual(self.store.quota_count('2026-11-19'), 2)
        self.assertEqual(self.store.quota_count('2026-11-20'), 1)

    def test_count_filters_by_day_and_kind(self):
        self.store.quota_record('2026-11-19', 'analysis_task', 'run-1', 'free')
        self.store.quota_record('2026-11-19', 'other_kind', 'run-1', 'free')
        self.assertEqual(self.store.quota_count('2026-11-19'), 1)
        self.assertEqual(self.store.quota_count('2026-11-19', 'other_kind'), 1)
        self.assertEqual(self.store.quota_count('2026-11-18'), 0)

    def test_record_rejects_unknown_tier(self):
        with self.assertRaises(ValueError):
            self.store.quota_record('2026-11-19', 'analysis_task', 'run-1', 'enterprise')
        self.assertEqual(self.store.quota_count('2026-11-19'), 0)

    def test_record_sets_created_timestamp(self):
        self.store.quota_record('2026-11-19', 'analysis_task', 'run-1', 'free')
        with self.store.db() as db:
            created = db.execute('SELECT created FROM quota_events').fetchone()[0]
        self.assertTrue(created)
        self.assertIsNotNone(datetime.fromisoformat(created).tzinfo)


class QuotaClock(unittest.TestCase):
    """day_of 的北京日界与 event_id 的稳定性不依赖 Store。"""

    def test_tz_constant_is_beijing(self):
        self.assertEqual(datetime(2026, 11, 19, 0, 0, tzinfo=TZ).utcoffset(), timedelta(hours=8))


if __name__ == '__main__':
    unittest.main()
