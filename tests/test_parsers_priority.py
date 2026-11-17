"""D07/D08/D09：可选解析器适配层与 OCR 页面优先级。"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['KB_NO_STARTUP_JOBS'] = '1'
os.environ['KB_NO_REFERENCE_WORKER'] = '1'
from kbapp.storage.store import Store
from kbapp.library import parsers
from kbapp.library.documents import ocr_priority
from kbapp.core.io import now


class ParsersAndPriority(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)

    def test_optional_parsers_report_availability_and_degrade_clearly(self):
        status = parsers.status()
        self.assertEqual(set(status), {'docling', 'grobid'})
        for name in ('docling', 'grobid'):
            adapter = parsers.get(name)
            self.assertEqual(adapter.name, name)
            if not adapter.available():
                with self.assertRaises(parsers.ParserUnavailable):
                    adapter.parse('missing.pdf')
        with self.assertRaises(ValueError):
            parsers.get('unknown')

    def test_adapter_comparison_reports_char_ratio(self):
        result = parsers.compare({'adapter': 'docling', 'markdown': 'a' * 120}, [{'text': 'b' * 100}])
        self.assertEqual(result['ratio'], 1.2)

    def test_ocr_priority_orders_failed_and_key_sections_first(self):
        self.store.put_papers([{'id': 'p', 'title': 'P', 'kind': 'library'}])
        with self.store.db() as db:
            db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?)', ('doc', 'p', '', 'hash', 'unused', 4, '{}', now()))
            for number, status, classification, section in [
                (1, 'complete', 'NATIVE_TEXT', 'Introduction'),
                (2, 'failed', 'SCANNED', 'Method'),
                (3, 'pending', 'MIXED', 'Results'),
                (4, 'complete', 'NATIVE_TEXT', 'References'),
            ]:
                db.execute('INSERT INTO document_pages VALUES(?,?,?,?,?,?,?,?)', ('doc', number, classification, status, 'text', '', json.dumps({'section': section}), now()))
        order = ocr_priority(self.store, {'id': 'doc'}, range(1, 5))
        self.assertEqual(order[:2], [2, 3])
        self.assertEqual(order[-1], 4, '参考文献页应后置')
        self.assertLess(order.index(1), order.index(4))


if __name__ == '__main__':
    unittest.main()
