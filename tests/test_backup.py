"""O02/O03：一致性备份、完整性检查与自动备份保留策略。"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['KB_NO_STARTUP_JOBS'] = '1'
os.environ['KB_NO_REFERENCE_WORKER'] = '1'
from kbapp.storage.store import Store
from kbapp.workflows.backup import create_backup, prune


class Backup(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.store.put_papers([{'id': 'p', 'title': 'Paper', 'kind': 'library'}])
        self.paths = self.store.paths

    def test_backup_is_consistent_and_reports_counts(self):
        with self.store.db() as db:
            db.execute('INSERT INTO usage VALUES(?,?,?,?,?,?,?)', ('u1', '2026-11-03', 0.1, 0.1, 'test', 'complete', '2026-11-03'))
        result = create_backup(self.paths, keep=3, label='test')
        self.assertEqual(result['integrity'], 'ok')
        self.assertTrue(Path(result['database']).is_file())
        self.assertEqual(result['counts']['papers'], 1)
        self.assertEqual(result['counts']['usage_records'], 1)
        self.assertIn('retention', result)
        manifest = json.loads((Path(result['database']).parent / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['label'], 'test')
        from contextlib import closing
        with closing(sqlite3.connect(result['database'])) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM papers').fetchone()[0], 1)

    def test_prune_keeps_manual_backups_and_recent_auto_backups(self):
        created = []
        for n in range(3):
            created.append(Path(create_backup(self.paths, keep=10, label=f'auto{n}')['database']).parent)
            os.utime(created[-1], (1000 + n, 1000 + n))
        manual = self.paths.backups / 'before-upgrade-manual'
        manual.mkdir()
        (manual / 'keep.txt').write_text('x', encoding='utf-8')
        removed = prune(self.paths, keep=2)
        self.assertEqual(len(removed), 1)
        self.assertTrue(manual.exists())
        self.assertEqual(len([p for p in self.paths.backups.glob('auto-*') if p.is_dir()]), 2)


if __name__ == '__main__':
    unittest.main()
