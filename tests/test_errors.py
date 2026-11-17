"""I03/I04：错误分类与重试策略；请求结果不确定时不自动重试。"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['KB_NO_STARTUP_JOBS'] = '1'
os.environ['KB_NO_REFERENCE_WORKER'] = '1'
from kbapp.storage.store import Store
from kbapp.core.errors import classify, explain, retry_policy
from kbapp.workflows import agent_units


class Errors(unittest.TestCase):

    def test_classification_covers_network_rate_limit_format_evidence_and_manual(self):
        self.assertEqual(classify(HTTPError('http://x', 429, 'Too Many Requests', {}, None)), 'rate_limit')
        self.assertEqual(classify(HTTPError('http://x', 503, 'Unavailable', {}, None)), 'network')
        self.assertEqual(classify(URLError(ConnectionResetError('reset'))), 'network')
        self.assertEqual(classify(TimeoutError()), 'network')
        self.assertEqual(classify(ValueError('模型返回格式不兼容')), 'output_format')
        self.assertEqual(classify(ValueError('核验模型未返回完整结构')), 'output_format')
        self.assertEqual(classify(ValueError('今日模型费用额度不足，任务保留至次日')), 'budget')
        self.assertEqual(classify(ValueError('等待审批后继续')), 'manual_wait')
        self.assertEqual(classify(ValueError('证据不足，待补证')), 'evidence_insufficient')
        self.assertEqual(classify(ValueError('boom')), 'execution')
        for kind in ('network', 'rate_limit', 'output_format', 'evidence_insufficient', 'manual_wait', 'budget', 'execution'):
            self.assertTrue(explain(kind))

    def test_retry_policy_backs_off_only_when_request_certainly_not_sent(self):
        self.assertEqual(retry_policy('network')['mode'], 'backoff')
        self.assertEqual(retry_policy('rate_limit')['mode'], 'backoff')
        self.assertEqual(retry_policy('network', sent=True)['mode'], 'hold')
        self.assertEqual(retry_policy('output_format')['mode'], 'hold')
        self.assertEqual(retry_policy('budget')['mode'], 'hold')
        self.assertEqual(retry_policy('evidence_insufficient')['mode'], 'none')
        self.assertEqual(retry_policy('manual_wait')['mode'], 'none')

    def test_failed_unit_records_error_kind_and_retry_policy(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        store = Store(temp.name)
        with store.db() as db:
            db.execute("INSERT INTO agent_runs VALUES('r','lineage','running','{}','fp',0,0,'{}','','2026-11-03','2026-11-03')")
            db.execute("INSERT INTO agent_tasks VALUES('t','r','verifier','p','fp','running','{}','{}',0,'','2026-11-03')")
            db.execute("INSERT INTO agent_units VALUES('u','t','r','gather-evidence','retriever','p','k','running','{}','{}',1,'','2026-11-03')")
        agent_units.fail(store, {'id': 'u', 'run_id': 'r', 'parent_id': 't'}, URLError(ConnectionResetError('reset')))
        with store.db() as db:
            row = db.execute("SELECT status,result FROM agent_units WHERE id='u'").fetchone()
        result = json.loads(row['result'])
        self.assertEqual(row['status'], 'failed')
        self.assertEqual(result['error_kind'], 'network')
        self.assertEqual(result['retry']['mode'], 'backoff')
        with store.db() as db:
            db.execute("UPDATE agent_units SET status='running' WHERE id='u'")
        agent_units.fail(store, {'id': 'u', 'run_id': 'r', 'parent_id': 't'}, ValueError('核验模型未返回完整结构'))
        with store.db() as db:
            result = json.loads(db.execute("SELECT result FROM agent_units WHERE id='u'").fetchone()[0])
        self.assertEqual(result['error_kind'], 'output_format')
        self.assertEqual(result['verification_status'], 'structure_error')


if __name__ == '__main__':
    unittest.main()
