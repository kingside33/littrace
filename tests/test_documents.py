"""D02/D03：参考文献区域不进证据来源；区间、全角与作者年份引用可解析。"""
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
from kbapp.library.documents import cites_label, cites_author_year, index_citations, sources
from kbapp.core.io import now


class Documents(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.store.put_papers([{'id': 'p', 'title': 'Paper', 'kind': 'library'}, {'id': 'target', 'title': 'Target', 'kind': 'library', 'doi': '10.1234/target'}])

    def test_citation_ranges_lists_and_fullwidth_brackets(self):
        self.assertTrue(cites_label('见 [4–8] 的方法', '5'))
        self.assertTrue(cites_label('见 [4-8] 的方法', '8'))
        self.assertTrue(cites_label('见 [4,5,8] 的方法', '5'))
        self.assertTrue(cites_label('见［7］的方法', '7'))
        self.assertFalse(cites_label('见 [4–8] 的方法', '9'))
        self.assertFalse(cites_label('没有编号引用', '5'))

    def test_author_year_citation_requires_matching_author_and_year(self):
        raw = '[1] Smith, J. (2020). Sampling-based planning. Journal.'
        self.assertTrue(cites_author_year('as shown by Smith et al. (2020), the method works', raw))
        self.assertFalse(cites_author_year('as shown by Smith et al. (2019), the method works', raw))

    def test_references_section_is_separated_and_ranges_bind_contexts(self):
        with self.store.db() as db:
            db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?)', ('doc', 'p', '', 'hash', 'unused', 2, '{}', now()))
            body = 'We build on prior sampling work [4–8].\nReferences:\n[1] Smith, J. (2020). Sampling. doi:10.1234/target\n[4] A (2019) One\n[5] B (2018) Two\n[8] C (2017) Three\n'
            db.execute('INSERT INTO document_pages VALUES(?,?,?,?,?,?,?,?)', ('doc', 1, 'NATIVE_TEXT', 'complete', body, '', '{}', now()))
        index_citations(self.store, {'id': 'doc', 'paper_id': 'p'})
        with self.store.db() as db:
            refs = {r['label']: json.loads(r['payload']) for r in db.execute('SELECT label,payload FROM document_references')}
            contexts = db.execute('SELECT text FROM citation_contexts').fetchall()
        self.assertEqual(set(refs), {'1', '4', '5', '8'})
        self.assertEqual(refs['1']['match'], 'unique_doi')
        self.assertEqual(refs['1']['doi'], '10.1234/target')
        self.assertEqual(len(contexts), 3, '区间引用应绑定 4/5/8 三条上下文，而不是 1')

    def test_reference_section_chunks_are_not_evidence_sources(self):
        from kbapp.core.files import file_hash
        pdf = Path(self.temp.name) / 'paper.pdf'
        pdf.write_bytes(b'%PDF-1.4 test')
        sha = file_hash(pdf)
        with self.store.db() as db:
            db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?)', ('doc', 'p', '', sha, str(pdf), 1, '{}', now()))
            db.execute('INSERT INTO document_pages VALUES(?,?,?,?,?,?,?,?)', ('doc', 1, 'NATIVE_TEXT', 'complete', 'Body text.', '', '{}', now()))
            db.execute('INSERT INTO document_chunks VALUES(?,?,?,?,?,?)', ('c1', 'doc', 1, None, 'We propose a method.', json.dumps({'section': 'Method', 'parser_version': 2, 'origin': 'paper', 'quality': 'complete'})))
            db.execute('INSERT INTO document_chunks VALUES(?,?,?,?,?,?)', ('c2', 'doc', 1, None, '[1] Smith 2020. A cited paper.', json.dumps({'section': 'References', 'parser_version': 2, 'origin': 'paper', 'quality': 'complete'})))
        texts = [s['text'] for s in sources(self.store)]
        self.assertIn('We propose a method.', texts)
        self.assertNotIn('[1] Smith 2020. A cited paper.', texts)



    def test_same_content_new_path_reuses_parsed_document(self):
        """C08：同内容换路径/换附件键复用已解析文档；真实内容变化才新建版本。"""
        from kbapp.library.documents import register, process
        first = Path(self.temp.name) / 'a.pdf'
        import pymupdf
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_textbox((50, 50, 500, 400), 'We propose an adaptive sampling method for planning.')
        doc.save(first)
        doc.close()
        paper = self.store.papers()['p']
        registered = register(self.store, paper, first, 'KEY-A')
        process(self.store, registered)
        with self.store.db() as db:
            count_before = db.execute('SELECT COUNT(*) FROM document_pages WHERE document_id=?', (registered['id'],)).fetchone()[0]
        moved = Path(self.temp.name) / 'b.pdf'
        moved.write_bytes(first.read_bytes())
        again = register(self.store, paper, moved, 'KEY-A')
        self.assertEqual(again['id'], registered['id'])
        self.assertEqual(again['path'], str(moved))
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM documents WHERE paper_id=?', ('p',)).fetchone()[0], 1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM document_pages WHERE document_id=?', (registered['id'],)).fetchone()[0], count_before)
        changed_doc = pymupdf.open()
        changed_page = changed_doc.new_page()
        changed_page.insert_textbox((50, 50, 500, 400), 'A completely different revision of the manuscript text.')
        changed_doc.save(moved)
        changed_doc.close()
        changed = register(self.store, paper, moved, 'KEY-A')
        self.assertNotEqual(changed['id'], registered['id'])
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM documents WHERE paper_id=?', ('p',)).fetchone()[0], 2)



    def test_citation_context_saves_marker_target_and_surroundings(self):
        """D04：引用标记、目标论文与前后句保存为独立引用语境。"""
        with self.store.db() as db:
            db.execute('INSERT INTO documents VALUES(?,?,?,?,?,?,?,?)', ('doc', 'p', '', 'hash', 'unused', 2, '{}', now()))
            body = 'Previous work introduced fixed sampling. We build on prior work [1] for planning. The results follow in Section 5.\nReferences:\n[1] Smith, J. (2020). Sampling. doi:10.1234/target\n[4] A (2019) One\n[5] B (2018) Two\n[8] C (2017) Three\n'
            db.execute('INSERT INTO document_pages VALUES(?,?,?,?,?,?,?,?)', ('doc', 1, 'NATIVE_TEXT', 'complete', body, '', '{}', now()))
        index_citations(self.store, {'id': 'doc', 'paper_id': 'p'})
        with self.store.db() as db:
            rows = [dict(r) for r in db.execute('SELECT c.text,p.marker,p.target_paper_id,p.payload FROM citation_context_payload p JOIN citation_contexts c ON c.id=p.context_id')]
        target_rows = [r for r in rows if r['target_paper_id'] == 'target']
        self.assertEqual(len(target_rows), 1)
        self.assertEqual(target_rows[0]['marker'], '[1]')
        payload = json.loads(target_rows[0]['payload'])
        self.assertIn('fixed sampling', payload['before'])
        self.assertIn('Section 5', payload['after'])


if __name__ == '__main__':
    unittest.main()
