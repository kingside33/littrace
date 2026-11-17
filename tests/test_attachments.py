"""C04：附件角色、失效附件与主附件切换（证据版本偏好）。"""
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
from kbapp.library.attachments import classify_attachment, attachments_of, set_primary
from kbapp.library.documents import sources
from kbapp.core.io import now
from kbapp.core.files import file_hash


class Attachments(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.pdf_a = Path(self.temp.name) / 'paper.pdf'
        self.pdf_a.write_bytes(b'%PDF main')
        self.pdf_b = Path(self.temp.name) / 'supplement.pdf'
        self.pdf_b.write_bytes(b'%PDF supplement')
        self.store.put_papers([{'id': 'p', 'title': 'Paper', 'kind': 'library',
                                'attachments': [
                                    {'key': 'A', 'abs_path': str(self.pdf_a), 'exists': True, 'content_type': 'application/pdf'},
                                    {'key': 'B', 'abs_path': str(self.pdf_b), 'exists': True, 'content_type': 'application/pdf'},
                                    {'key': 'C', 'abs_path': 'missing.pdf', 'exists': False, 'content_type': 'application/pdf'},
                                ]}])
        for doc_id, key, path, text in (('doc-a', 'A', self.pdf_a, 'Main body evidence.'), ('doc-b', 'B', self.pdf_b, 'Supplemental table evidence.')):
            with self.store.db() as db:
                db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?)', (doc_id, 'p', key, file_hash(path), str(path), 1, '{}', now()))
                db.execute('INSERT INTO document_pages VALUES(?,?,?,?,?,?,?,?)', (doc_id, 1, 'NATIVE_TEXT', 'complete', text, '', '{}', now()))
                db.execute('INSERT INTO document_chunks VALUES(?,?,?,?,?,?)', (doc_id + '-c', doc_id, 1, None, text, json.dumps({'section': 'Method', 'parser_version': 2, 'origin': 'paper', 'quality': 'complete'})))

    def test_roles_and_missing_attachment(self):
        self.assertEqual(classify_attachment({'exists': True, 'abs_path': 'x/supplement.pdf', 'content_type': 'application/pdf'}), ('supplementary', 'available'))
        self.assertEqual(classify_attachment({'exists': True, 'abs_path': 'x/author-manuscript.pdf', 'content_type': 'application/pdf'}), ('author_manuscript', 'available'))
        self.assertEqual(classify_attachment({'exists': False, 'abs_path': 'x/paper.pdf', 'content_type': 'application/pdf'}), ('unknown', 'missing'))
        listing = attachments_of(self.store, 'p')
        roles = {i['attachment_key']: (i['role'], i['status']) for i in listing['items'] if i.get('attachment_key')}
        self.assertEqual(roles['A'], ('primary', 'available'))
        self.assertEqual(roles['B'], ('supplementary', 'available'))
        self.assertEqual(roles['C'], ('unknown', 'missing'))
        self.assertTrue(next(i for i in listing['items'] if i.get('document_id') == 'doc-a')['active'])

    def test_switch_primary_changes_active_document_and_source_preference(self):
        set_primary(self.store, 'p', 'doc-b')
        listing = attachments_of(self.store, 'p')
        self.assertEqual(listing['primary_document'], 'doc-b')
        self.assertTrue(next(i for i in listing['items'] if i.get('document_id') == 'doc-b')['active'])
        self.assertFalse(next(i for i in listing['items'] if i.get('document_id') == 'doc-a')['active'])
        flags = {s['document_id']: s.get('primary') for s in sources(self.store)}
        self.assertTrue(flags['doc-b'])
        self.assertFalse(flags['doc-a'])
        paper = self.store.papers()['p']
        self.assertEqual(paper['primary_set_by'], 'manual')
        self.assertEqual(paper['primary_attachment_key'], 'B')

    def test_primary_switch_rejects_foreign_or_missing_document(self):
        with self.assertRaisesRegex(ValueError, '不属于'):
            set_primary(self.store, 'p', 'doc-x')
        with self.assertRaisesRegex(ValueError, '需要指定'):
            set_primary(self.store, 'p')


if __name__ == '__main__':
    unittest.main()
