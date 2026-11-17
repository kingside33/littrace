"""D05/D06：表格上下文（标题/列头/单位/脚注/续表）与公式变量解释。"""
import os
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['KB_NO_STARTUP_JOBS'] = '1'
from kbapp.library.context import table_context, formula_context


class Context(unittest.TestCase):

    def test_table_context_extracts_header_units_footnotes(self):
        result = table_context(
            caption='Table 2: Energy consumption (J) under different currents.',
            table_text='Method | Energy (J) | Time (s)\nRRT* | 120 | 3.2\nInformed RRT* | 95 | 2.8',
            context='* Values averaged over 30 runs.\nTable 2: Energy consumption (J) under different currents.')
        self.assertEqual(result['header'], 'Method | Energy (J) | Time (s)')
        self.assertIn('J', result['units'])
        self.assertIn('s', result['units'])
        self.assertTrue(any('averaged over 30 runs' in note for note in result['footnotes']))
        self.assertEqual(result['rows'], 2)
        self.assertFalse(result['continued'])

    def test_continued_table_is_marked(self):
        result = table_context(caption='Table 3 (continued): Coverage results.', table_text='a | b\n1 | 2')
        self.assertTrue(result['continued'])

    def test_formula_context_extracts_variable_explanations(self):
        context = 'where v is the vehicle speed and d denotes the safety distance. The cost follows from the kinematic model.'
        result = formula_context('J = m v^2 / 2', context)
        symbols = {v['symbol']: v['meaning'] for v in result['variables']}
        self.assertTrue(symbols['v'].startswith('the vehicle speed'))
        self.assertIn('safety distance', symbols['d'])
        self.assertTrue(result['has_explanation'])
        self.assertTrue(result['latex_present'])

    def test_formula_without_explanation_is_marked(self):
        result = formula_context('x = y + z', 'The next section discusses experiments.')
        self.assertEqual(result['variables'], [])
        self.assertFalse(result['has_explanation'])


if __name__ == '__main__':
    unittest.main()
