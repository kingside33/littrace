"""评测模式、续跑指纹与检索实例复用的真实链路回归。"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from kbapp.core.io import atomic
from kbapp.storage.store import Store
from kbapp.workflows.tools import kb_evaluate
from kbapp.research.retrieval import evidence_search


class ReliabilityEvaluation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.store.put_papers([{'id': 'a', 'title': 'Planning', 'kind': 'library'}])
        self.questions = Path(self.temp.name) / 'questions.json'
        self.output = Path(self.temp.name) / 'report.json'
        atomic(self.questions, {'questions': [{'id': 'one', 'query': 'planning', 'category': 'single', 'expected_sources': [{'paper_id': 'a', 'source_id': 's', 'quote': 'Planning evidence'}]}]})

    def run_evaluation(self, mode='baseline'):
        with patch.object(kb_evaluate, 'Store', return_value=self.store), patch.object(kb_evaluate, 'Search') as search:
            search.return_value.search.return_value = {'mode': 'bm25', 'results': [{'id': 's', 'paper_id': 'a', 'text': 'Planning evidence'}]}
            kb_evaluate.main(['--questions', str(self.questions), '--output', str(self.output), '--mode', mode])
            return search.return_value.search.call_args_list

    def test_baseline_explicitly_disables_vectors(self):
        calls = self.run_evaluation()
        self.assertIs(calls[0].kwargs.get('vectors'), False)

    def test_cloud_configuration_change_invalidates_saved_evaluation(self):
        self.run_evaluation()
        first = json.loads(self.output.read_text(encoding='utf-8'))['fingerprint']
        self.store.save_settings({'model': 'different-reviewer', 'api_base': 'https://example.invalid'})
        calls = self.run_evaluation()
        second = json.loads(self.output.read_text(encoding='utf-8'))['fingerprint']
        self.assertNotEqual(first, second)
        self.assertEqual(len(calls), 1)

    def test_fully_cached_subset_still_updates_report(self):
        dataset = json.loads(self.questions.read_text(encoding='utf-8'))
        dataset['questions'].append(dict(dataset['questions'][0], id='two'))
        atomic(self.questions, dataset)
        self.run_evaluation()
        with patch.object(kb_evaluate, 'Store', return_value=self.store):
            kb_evaluate.main(['--questions', str(self.questions), '--output', str(self.output), '--mode', 'baseline', '--limit', '1'])
        report = json.loads(self.output.read_text(encoding='utf-8'))
        self.assertEqual(len(report['questions']), 1)
        self.assertEqual(report['evaluated_positive'], 1)

    def test_same_store_reuses_search_engine_between_research_queries(self):
        with patch('kbapp.search.indexing.Search') as search:
            search.return_value.search.return_value = {'mode': 'bm25', 'results': [], 'warning': '', 'vector_status': 'no_index'}
            evidence_search(self.store, 'first')
            evidence_search(self.store, 'second')
        self.assertEqual(search.call_count, 1)

    def test_research_trace_retains_stage_timings(self):
        with patch('kbapp.search.indexing.Search') as search:
            search.return_value.search.return_value = {'mode': 'bm25', 'results': [], 'warning': '', 'timings_ms': {'load': 7.0, 'total': 9.0}}
            found = evidence_search(self.store, 'planning')
        self.assertEqual(found['process'][0].get('timings_ms'), {'load': 7.0, 'total': 9.0})
        self.assertIn('timings_ms', found)


if __name__ == '__main__':
    unittest.main()
