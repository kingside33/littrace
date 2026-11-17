"""G02：按任务独立的提示模板、输入结构与输出契约。"""
import json
import os
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['KB_NO_STARTUP_JOBS'] = '1'
from kbapp.research.prompts import TASKS, build, contract


class Prompts(unittest.TestCase):

    def test_each_task_has_instruction_input_and_output_contract(self):
        for task in ('classification', 'abstract_lineage', 'material', 'claim_review'):
            self.assertIn(task, TASKS)
            self.assertTrue(TASKS[task]['instruction'])
            self.assertTrue(TASKS[task]['output'])
            self.assertTrue(contract(task))

    def test_build_appends_structured_input(self):
        prompt = build('classification', source={'id': 's', 'text': 't'})
        self.assertTrue(prompt.startswith(TASKS['classification']['instruction']))
        payload = json.loads(prompt.split('\n', 1)[1])
        self.assertEqual(payload, {'source': {'id': 's', 'text': 't'}})
        proposal = build('abstract_lineage', source={'id': 'a'}, target={'id': 'b'})
        self.assertEqual(json.loads(proposal.split('\n', 1)[1]), {'source': {'id': 'a'}, 'target': {'id': 'b'}})

    def test_unknown_task_is_rejected(self):
        with self.assertRaises(ValueError):
            build('unknown-task', x=1)
        with self.assertRaises(ValueError):
            contract('unknown-task')


if __name__ == '__main__':
    unittest.main()
