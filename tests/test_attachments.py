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

    def set_document_updated(self, document_id, updated):
        with self.store.db() as db:
            db.execute('UPDATE documents SET updated=? WHERE id=?', (updated, document_id))

    def add_document(self, document_id, paper_id, key, path, updated):
        with self.store.db() as db:
            db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?)',
                       (document_id, paper_id, key, file_hash(path), str(path), 1, '{}', updated))

    def test_default_active_prefers_primary_attachment_over_newest_document(self):
        # 回归 #79：document.updated 只精确到秒，跨秒时旧实现会把后登记的补充
        # 材料当成默认证据版本。这里把 doc-b 显式标成更晚更新，默认仍须是主附件。
        self.set_document_updated('doc-b', '2099-01-01T00:00:00+08:00')
        listing = attachments_of(self.store, 'p')
        self.assertEqual(listing['primary_document'], 'doc-a')
        active = {i['document_id']: i['active'] for i in listing['items'] if i.get('document_id')}
        self.assertEqual(active, {'doc-a': True, 'doc-b': False})

    def test_default_active_follows_primary_attachment_key(self):
        self.store.patch_paper('p', {'primary_attachment_key': 'B'})
        self.set_document_updated('doc-a', '2099-01-01T00:00:00+08:00')
        listing = attachments_of(self.store, 'p')
        self.assertEqual(listing['primary_document'], 'doc-b')
        self.assertTrue(next(i for i in listing['items'] if i.get('document_id') == 'doc-b')['active'])

    def test_default_active_follows_paper_pdf_path(self):
        self.store.patch_paper('p', {'pdf': str(self.pdf_b)})
        self.set_document_updated('doc-a', '2099-01-01T00:00:00+08:00')
        listing = attachments_of(self.store, 'p')
        self.assertEqual(listing['primary_document'], 'doc-b')
        self.assertTrue(next(i for i in listing['items'] if i.get('document_id') == 'doc-b')['active'])

    def test_default_active_without_primary_attachment_is_order_stable(self):
        extra = Path(self.temp.name) / 'appendix.pdf'
        extra.write_bytes(b'%PDF appendix')
        self.store.put_papers([{'id': 'q', 'title': 'Extra', 'kind': 'library', 'attachments': [
            {'key': 'X', 'abs_path': str(extra), 'exists': True, 'content_type': 'application/pdf'},
            {'key': 'Y', 'abs_path': str(self.pdf_b), 'exists': True, 'content_type': 'application/pdf'},
        ]}])
        self.add_document('doc-x', 'q', 'X', extra, '2026-01-01T00:00:00+08:00')
        self.add_document('doc-y', 'q', 'Y', self.pdf_b, '2026-01-01T00:00:00+08:00')
        with self.store.db() as db:
            # 建 (paper_id,updated) 索引，让 SQLite 反向扫描索引就满足 ORDER BY，
            # 此时同秒并列按索引内的 rowid 倒序返回（doc-y 在前）。若查询只写
            # updated DESC，结果会依赖这次扫描顺序；显式 rowid ASC 才与索引形状
            # 无关。这样能把“表扫描的偶然顺序”与确定性契约区分开。
            db.execute('CREATE INDEX documents_test_paper_updated ON documents(paper_id,updated)')
        self.assertEqual(attachments_of(self.store, 'q')['primary_document'], 'doc-x')
        self.set_document_updated('doc-y', '2026-01-01T00:00:01+08:00')
        self.assertEqual(attachments_of(self.store, 'q')['primary_document'], 'doc-y')

    def test_default_active_ignores_missing_files(self):
        os.remove(self.pdf_b)
        listing = attachments_of(self.store, 'p')
        self.assertEqual(listing['primary_document'], 'doc-a')
        self.assertEqual({i['document_id']: i['status'] for i in listing['items'] if i.get('document_id')},
                         {'doc-a': 'available', 'doc-b': 'missing'})
        os.remove(self.pdf_a)
        self.assertIsNone(attachments_of(self.store, 'p')['primary_document'])

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
