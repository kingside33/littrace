"""重构边界：导入、命令和迁移均使用隔离资料。"""

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kbapp.core.paths import Paths
from kbapp.workflows.data import plan, migrate, rollback, database_summary
from kbapp.workflows.diagnostics import architecture


class Architecture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.paths = Paths.from_base(self.temp.name, Path(self.temp.name) / '_index')

    def fixture(self):
        root = self.paths.data
        root.mkdir()
        pdf = root / 'external-pdfs/paper.pdf'
        pdf.parent.mkdir()
        pdf.write_bytes(b'%PDF-fixture')
        with closing(sqlite3.connect(root / 'workbench.sqlite3')) as db, db:
            db.executescript(
                'CREATE TABLE state(key TEXT PRIMARY KEY,value TEXT);\n                CREATE TABLE papers(id TEXT PRIMARY KEY,payload TEXT,library INTEGER);\n                CREATE TABLE relations(id TEXT PRIMARY KEY,manual INTEGER,payload TEXT);\n                CREATE TABLE approvals(paper_id TEXT PRIMARY KEY,filepath TEXT,status TEXT);\n                CREATE TABLE documents(id TEXT PRIMARY KEY,path TEXT);\n                CREATE TABLE jobs(id TEXT PRIMARY KEY,status TEXT);\n                CREATE TABLE agent_runs(id TEXT PRIMARY KEY,status TEXT);\n                CREATE TABLE reference_jobs(paper_id TEXT PRIMARY KEY,status TEXT);'
            )
            db.execute('INSERT INTO state VALUES(?,?)', ('active_version', '"version-a"'))
            db.execute(
                'INSERT INTO papers VALUES(?,?,1)',
                (
                    'paper-a',
                    json.dumps(
                        {'pdf': str(pdf), 'raw_zotero': {'path': str(pdf)}, 'notes': '人工内容'}
                    ),
                ),
            )
            db.execute(
                'INSERT INTO relations VALUES(?,1,?)', ('relation-a', '{"quote":"逐字证据"}')
            )
            db.execute('INSERT INTO approvals VALUES(?,?,?)', ('paper-a', str(pdf), 'downloaded'))
            db.execute('INSERT INTO documents VALUES(?,?)', ('document-a', str(pdf)))
        for name, content in [
            ('versions/version-a/corpus.npy', b'vector-bytes'),
            ('workbench-vector-cache/model/shard.npz', b'cached-vector'),
            ('ocr_pages/paper/p0001.txt', b'OCR text'),
            ('citation/openalex/works/W1.json', b'{}'),
            ('citation/evolution.html', b'old-html'),
            ('reference-plugin-source/README.md', b'upstream'),
        ]:
            p = root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)
        return root

    def test_dependencies_are_acyclic_and_follow_layers(self):
        self.assertTrue(architecture()['ok'], architecture())

    def test_importing_all_modules_does_not_open_data_network_or_threads(self):
        root = Path(__file__).resolve().parents[1]
        script = "import importlib,pathlib,threading,socket,sqlite3\nfrom unittest.mock import patch\nroot=pathlib.Path(__import__('sys').argv[1])\nwith patch.object(threading.Thread,'start',side_effect=AssertionError('thread')),patch.object(socket.socket,'connect',side_effect=AssertionError('network')),patch.object(sqlite3,'connect',side_effect=AssertionError('database')):\n for p in (root/'kbapp').rglob('*.py'):\n  if p.name!='__init__.py':importlib.import_module('.'.join(p.relative_to(root).with_suffix('').parts))\n"
        env = dict(
            os.environ,
            KB_BASE=self.temp.name,
            KB_DATA_DIR=str(self.paths.data),
            PYTHONPATH=str(root),
        )
        result = subprocess.run(
            [sys.executable, '-c', script, str(root)], env=env, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.paths.data.exists())

    def test_help_and_dry_run_are_read_only(self):
        entry = Path(__file__).resolve().parents[1] / 'kb.py'
        for options in [[], ['update', '--dry-run'], ['data', 'plan']]:
            cmd = (
                ['--help']
                if not options
                else ['--base', self.temp.name, '--data-dir', str(self.paths.data), *options]
            )
            result = subprocess.run([sys.executable, str(entry), *cmd], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(self.paths.data.exists())

    def test_plan_is_read_only_and_migration_preserves_records_and_cache(self):
        root = self.fixture()
        before = database_summary(root / 'workbench.sqlite3')
        vector = root / 'versions/version-a/corpus.npy'
        stamp = vector.stat().st_mtime_ns
        report = plan(self.paths)
        self.assertGreater(report['files'], 5)
        self.assertFalse(self.paths.state.exists())
        with patch('urllib.request.urlopen', side_effect=AssertionError('network')):
            result = migrate(self.paths)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(database_summary(self.paths.database), before)
        self.assertEqual(
            (self.paths.indexes / 'versions/version-a/corpus.npy').read_bytes(), b'vector-bytes'
        )
        self.assertEqual(
            (self.paths.indexes / 'versions/version-a/corpus.npy').stat().st_mtime_ns, stamp
        )
        self.assertEqual(
            (self.paths.cache / 'vectors/model/shard.npz').read_bytes(), b'cached-vector'
        )
        with closing(sqlite3.connect(self.paths.database)) as db, db:
            path = db.execute('SELECT path FROM documents').fetchone()[0]
            paper = json.loads(db.execute('SELECT payload FROM papers').fetchone()[0])
        self.assertTrue(Path(path).is_file())
        self.assertEqual(paper['raw_zotero']['path'], str(root / 'external-pdfs/paper.pdf'))
        self.assertEqual(migrate(self.paths)['completed'], result['completed'])

    def test_interrupted_migration_resumes_and_rollback_preserves_business(self):
        root = self.fixture()
        before = database_summary(root / 'workbench.sqlite3')

        def stop(_):
            raise RuntimeError('simulated interruption')

        with self.assertRaises(RuntimeError):
            migrate(self.paths, after_move=stop)
        self.assertEqual(migrate(self.paths)['status'], 'complete')
        self.assertEqual(rollback(self.paths)['status'], 'rolled_back')
        self.assertEqual(database_summary(root / 'workbench.sqlite3'), before)
        self.assertEqual((root / 'versions/version-a/corpus.npy').read_bytes(), b'vector-bytes')
        self.assertTrue((root / 'external-pdfs/paper.pdf').exists())
        self.assertEqual(migrate(self.paths)['status'], 'complete')

    def test_conflicting_target_is_never_overwritten(self):
        root = self.fixture()
        target = self.paths.indexes / 'versions'
        target.mkdir(parents=True)
        (target / 'keep').write_text('keep')
        with self.assertRaisesRegex(ValueError, '目标已存在'):
            plan(self.paths)
        self.assertEqual((target / 'keep').read_text(), 'keep')
        self.assertTrue((root / 'versions').exists())

    def test_active_jobs_prevent_migration(self):
        root = self.fixture()
        with closing(sqlite3.connect(root / 'workbench.sqlite3')) as db, db:
            db.execute("INSERT INTO jobs VALUES('job','running')")
        with self.assertRaisesRegex(ValueError, '活动任务'):
            migrate(self.paths)
        self.assertTrue((root / 'workbench.sqlite3').exists())
        self.assertFalse(self.paths.database.exists())

    def test_factory_separates_instances_without_import_global_store(self):
        from kbapp.workflows.context import AppContext
        from kbapp.web.service import create_app
        from fastapi.testclient import TestClient

        a = AppContext.create(self.temp.name, self.paths.data, background_jobs=False)
        b = AppContext.create(self.temp.name, Path(self.temp.name) / 'other', background_jobs=False)
        a.store.put_papers([{'id': 'one', 'kind': 'library', 'title': 'A'}])
        with TestClient(create_app(a)) as ca, TestClient(create_app(b)) as cb:
            self.assertEqual(ca.get('/api/status').json()['papers'], 1)
            self.assertEqual(cb.get('/api/status').json()['papers'], 0)
            self.assertNotEqual(ca.get('/api/session').json(), cb.get('/api/session').json())
            self.assertEqual(ca.post('/api/jobs', json={}).status_code, 403)

    def test_extraction_stages_use_library_interfaces_and_reuse_cache(self):
        import pymupdf
        from kbapp.core.io import atomic, jsonl
        from kbapp.storage.store import Store
        from kbapp.workflows.extraction import extract_stage

        store = Store(paths=self.paths)
        pdf = self.paths.base / 'fixture.pdf'
        with pymupdf.open() as document:
            page = document.new_page()
            page.insert_text(
                (60, 60),
                '1 Introduction\nWe plan an underwater inspection path.\nx = a + b\nReferences\n[1] Earlier Author. Planning method. 2020.',
            )
            document.save(pdf)
        index = store.active_dir()
        atomic(
            index / 'papers_all.jsonl',
            json.dumps(
                {
                    'id': 'fixture',
                    'citekey': 'Fixture2020',
                    'title': 'Fixture',
                    'pdf': str(pdf),
                    'tagfields': {},
                }
            )
            + '\n',
        )
        stages = ['chunk.py', 'extract_figures.py', 'extract_formulas.py', 'extract_refs.py']
        with (
            patch(
                'kbapp.integrations.models.request', side_effect=AssertionError('no model calls')
            ),
            patch('urllib.request.urlopen', side_effect=AssertionError('no network')),
        ):
            for stage in stages:
                extract_stage(store, index, stage, lambda message: None)
            chunks = (index / 'chunks.jsonl').read_bytes()
            for stage in stages:
                extract_stage(store, index, stage, lambda message: None)
        self.assertGreater(len(jsonl(index / 'chunks.jsonl')), 0)
        self.assertEqual((index / 'chunks.jsonl').read_bytes(), chunks)
        for name in ['figures.jsonl', 'formulas.jsonl', 'references.jsonl']:
            self.assertTrue((index / name).is_file())


if __name__ == '__main__':
    unittest.main()
