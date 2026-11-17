"""C05：原生文本、OCR 与旧/新切块的内容等价组。"""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['KB_NO_STARTUP_JOBS'] = '1'
from kbapp.library.equivalence import annotate, normalize_text, text_key


class Equivalence(unittest.TestCase):
    def test_whitespace_hyphenation_and_fullwidth_are_equivalent(self):
        base = 'This subset is sampled directly from the configuration space.'
        variants = [
            base,
            'This subset is sam-\npled directly from the configuration space.',
            '  This   subset is sampled\ndirectly from the configuration space.  ',
            'Ｔｈｉｓ subset is sampled directly from the configuration space.',
        ]
        keys = {text_key(v) for v in variants}
        self.assertEqual(len(keys), 1, normalize_text(variants[1]))

    def test_same_content_different_origins_share_group(self):
        sources = [
            {
                'id': 'doc:1',
                'text': 'We propose a sampling method.',
                'origin': 'paper',
                'kind': 'paper_text',
            },
            {
                'id': 'ocr:1',
                'text': 'We  propose a sampling\nmethod.',
                'origin': 'paper_ocr',
                'kind': 'paper_text',
            },
            {
                'id': 'legacy:1',
                'text': 'We propose a sampling method.',
                'origin': 'legacy_chunk',
                'kind': 'paper_text',
            },
            {
                'id': 'doc:2',
                'text': 'A different passage about coverage planning.',
                'origin': 'paper',
                'kind': 'paper_text',
            },
        ]
        stats = annotate(sources)
        self.assertEqual(stats['duplicated_groups'], 1)
        self.assertEqual(sources[0]['content_group'], sources[1]['content_group'])
        self.assertEqual(sources[0]['content_group'], sources[2]['content_group'])
        self.assertNotEqual(sources[0]['content_group'], sources[3]['content_group'])
        self.assertEqual(sources[0]['equivalent_count'], 3)
        self.assertEqual(sources[0]['equivalent_origins'], ['legacy_chunk', 'paper', 'paper_ocr'])
        self.assertEqual(sources[3]['equivalent_count'], 1)


if __name__ == '__main__':
    unittest.main()
