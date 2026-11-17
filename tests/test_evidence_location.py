"""C06：证据字符区间、句子编号与 PDF 坐标。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['KB_NO_STARTUP_JOBS'] = '1'
from kbapp.core.text import locate_span, sentence_spans
from kbapp.library.chunk import pdf_rects
from kbapp.research.evidence import evidence_ref


class EvidenceLocation(unittest.TestCase):

    def test_span_offsets_and_sentence_ids(self):
        text = 'Intro sentence. We propose an adaptive sampling method. Results follow.'
        quote = 'adaptive sampling method'
        span = locate_span(text, quote)
        self.assertEqual(text[span['start']:span['end']], quote)
        self.assertEqual(span['sentence_ids'], [1])
        first = locate_span(text, 'Intro sentence')
        self.assertEqual(first['sentence_ids'], [0])
        self.assertEqual(len(sentence_spans(text)), 3)
        self.assertIsNone(locate_span(text, 'not present'))

    def test_span_covers_hyphenated_quote_in_original(self):
        text = 'This subset is sam-\npled directly.'
        span = locate_span(text, 'This subset is sampled directly.')
        self.assertIsNotNone(span)
        self.assertEqual(text[span['start']:span['end']], 'This subset is sam-\npled directly.')

    def test_pdf_rects_locate_quote_on_page(self):
        import pymupdf
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'p.pdf'
            doc = pymupdf.open()
            page = doc.new_page()
            page.insert_textbox((50, 50, 500, 400), 'We propose an adaptive sampling method for planning.')
            doc.save(path)
            doc.close()
            rects = pdf_rects(path, 1, 'adaptive sampling method')
            self.assertTrue(rects)
            self.assertEqual(len(rects[0]), 4)
            self.assertEqual(pdf_rects(path, 1, 'absent phrase'), [])
            self.assertEqual(pdf_rects(path, 9, 'adaptive'), [])

    def test_evidence_ref_records_span_sentence_and_bbox(self):
        import pymupdf
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'p.pdf'
            doc = pymupdf.open()
            page = doc.new_page()
            page.insert_textbox((50, 50, 500, 400), 'We propose an adaptive sampling method for planning.')
            doc.save(path)
            doc.close()
            source = {'id': 's1', 'paper_id': 'p', 'kind': 'paper_text', 'text': 'We propose an adaptive sampling method for planning.', 'page_start': 1, 'page_end': 1}
            ref = evidence_ref({'source_id': 's1', 'quote': 'adaptive sampling method'}, {'s1': source}, pdf_path=path)
            self.assertEqual(ref['span']['start'], 14)
            self.assertIsNotNone(ref['sentence_id'])
            self.assertTrue(ref['bbox'])
            self.assertEqual(ref['bbox_status'], 'located')


if __name__ == '__main__':
    unittest.main()
