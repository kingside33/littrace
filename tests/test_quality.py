"""D01：双栏、页眉页脚、跨页段落与旋转页面质量检查。"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['KB_NO_STARTUP_JOBS'] = '1'
from kbapp.library.quality import analyze


def make_pdf(path, build):
    import pymupdf
    doc = pymupdf.open()
    build(doc)
    doc.save(path)
    doc.close()


class Quality(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def pdf(self, name, build):
        path = Path(self.temp.name) / name
        make_pdf(path, build)
        return path

    def test_two_column_page_is_detected(self):
        def build(doc):
            page = doc.new_page()
            for n in range(3):
                page.insert_text((50, 80 + n * 60), f'Left column sentence {n} about planning.')
                page.insert_text((330, 80 + n * 60), f'Right column sentence {n} about coverage.')
        result = analyze(self.pdf('two.pdf', build))
        self.assertIn('two_column', result['pages'][1])
        self.assertEqual(result['summary']['two_column'], 1)

    def test_rotated_page_is_detected(self):
        def build(doc):
            page = doc.new_page()
            page.insert_text((50, 80), 'Rotated landscape figure caption.')
            page.set_rotation(90)
        result = analyze(self.pdf('rot.pdf', build))
        self.assertIn('rotated', result['pages'][1])

    def test_repeated_header_and_footer_are_detected(self):
        def build(doc):
            for n in range(3):
                page = doc.new_page()
                page.insert_text((50, 30), 'Journal of Field Robotics 2024')
                page.insert_text((50, 120), f'Body paragraph number {n} with enough text.')
                page.insert_text((50, 780), 'Page footer line')
        result = analyze(self.pdf('hf.pdf', build))
        self.assertEqual(result['summary']['header_footer'], 3)

    def test_cross_page_paragraph_is_detected(self):
        def build(doc):
            first = doc.new_page()
            first.insert_text((50, 80), 'The method is')
            second = doc.new_page()
            second.insert_text((50, 80), 'extended by a second stage.')
        result = analyze(self.pdf('cross.pdf', build))
        self.assertIn('cross_page', result['pages'][1])
        self.assertNotIn('cross_page', result['pages'].get(2, []))


if __name__ == '__main__':
    unittest.main()
