"""功能 3（issue #101）：按选定的历史备份只读校验并恢复 SQLite 到新的隔离数据目录。

当前库与历史备份做成不同数据：先备份一篇论文（含一条 manual=1 的关系），
再让当前库增加第二篇；恢复结果必须只有备份时的那一篇。所有写操作仅使用临时夹具。
"""
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['KB_NO_STARTUP_JOBS'] = '1'
os.environ['KB_NO_REFERENCE_WORKER'] = '1'
from kbapp.storage.store import Store
from kbapp.workflows.backup import create_backup
from kbapp.workflows.restore import RestoreError, restore_backup


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as fh:
        digest.update(fh.read())
    return digest.hexdigest()


def ro_scalar(db_path, sql):
    """只读打开一个 SQLite 文件取标量（与恢复实现同款 ?mode=ro 语义）。"""
    with closing(sqlite3.connect(Path(db_path).as_uri() + '?mode=ro', uri=True)) as db:
        return db.execute(sql).fetchone()[0]


class Restore(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'live')
        self.store.put_papers([{'id': 'p1', 'title': '备份中的唯一论文', 'kind': 'library'}])
        # manual=1 的关系（src=dst 会被 Store.relation 拒绝自引用，夹具直接落表）。
        with self.store.db() as db:
            db.execute('INSERT INTO relations VALUES(?,?,?,?,?,?,?,?,?)',
                       ('r1', 'p1', 'p1', 'similar', 'confirmed', 'human', 1, '{}', '2026-11-21T00:00:00+08:00'))
        backup = create_backup(self.store.paths, keep=3, label='before-upgrade')
        self.backup_dir = Path(backup['database']).parent
        self.backup_db = Path(backup['database'])
        self.manifest_path = self.backup_dir / 'manifest.json'
        # 当前库随后变化：第二篇论文只存在于活动库，不在备份里。
        self.store.put_papers([{'id': 'p2', 'title': '备份之后新增的论文', 'kind': 'library'}])
        self.paths = self.store.paths

    def test_restore_picks_selected_backup_not_live_db(self):
        target = self.root / 'isolated'
        report = restore_backup(self.paths, str(self.backup_dir), str(target))
        self.assertTrue(report['ok'])
        restored = Path(report['target']['restored_database'])
        self.assertEqual(restored, target / 'state' / 'workbench.sqlite3')
        self.assertTrue(restored.is_file())
        self.assertEqual(ro_scalar(restored, 'SELECT COUNT(*) FROM papers'), 1)
        self.assertEqual(ro_scalar(restored, 'SELECT COUNT(*) FROM relations WHERE manual=1'), 1)
        self.assertEqual(report['counts']['papers'], 1)
        self.assertEqual(report['counts']['relations_manual'], 1)
        # 当前库不受恢复影响，仍是两篇。
        self.assertEqual(ro_scalar(self.paths.database, 'SELECT COUNT(*) FROM papers'), 2)

    def test_backup_dir_moved_elsewhere(self):
        moved = self.root / 'moved-backup'
        self.backup_dir.rename(moved)
        # manifest 的历史绝对路径指向移动前的旧位置；它只是元数据，不作读取源或写入目标。
        manifest = json.loads((moved / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['database'], str(self.backup_db))
        self.assertFalse(Path(manifest['database']).exists())
        report = restore_backup(self.paths, str(moved), str(self.root / 'isolated-moved'))
        restored = Path(report['target']['restored_database'])
        self.assertTrue(restored.is_file())
        self.assertIn('isolated-moved', str(restored))
        self.assertEqual(ro_scalar(restored, 'SELECT COUNT(*) FROM papers'), 1)

    def test_old_manifest_unrecorded_counts(self):
        original = json.loads(self.manifest_path.read_text(encoding='utf-8'))
        # 变体一：manifest 缺 counts 与 integrity 字段。
        stripped = dict(original)
        del stripped['counts']
        del stripped['integrity']
        self.manifest_path.write_text(json.dumps(stripped, ensure_ascii=False), encoding='utf-8')
        report = restore_backup(self.paths, str(self.backup_dir), str(self.root / 't-old-1'))
        self.assertTrue(report['ok'])
        self.assertTrue(report['manifest']['unrecorded'])
        self.assertIn('integrity', report['manifest']['unrecorded'])
        # 变体二：计数值全部为 None（backup.py 对读取失败字段的既有形态）。
        nulled = dict(original, counts={key: None for key in original['counts']}, integrity=None)
        self.manifest_path.write_text(json.dumps(nulled, ensure_ascii=False), encoding='utf-8')
        report = restore_backup(self.paths, str(self.backup_dir), str(self.root / 't-old-2'))
        self.assertTrue(report['ok'])
        self.assertIn('counts.papers', report['manifest']['unrecorded'])

    def test_corrupted_backup_fails_clean(self):
        # 变体一：覆写垃圾字节。
        self.backup_db.write_bytes(b'this is not a sqlite database file' * 100)
        target = self.root / 't-corrupt-1'
        with self.assertRaises(RestoreError) as ctx:
            restore_backup(self.paths, str(self.backup_dir), str(target))
        self.assertEqual(ctx.exception.stage, 'validate')
        self.assertFalse((target / 'state' / 'workbench.sqlite3').exists())
        self.assertFalse(target.exists())  # 本运行新建的目录已清理
        # 变体二：截断文件。
        fresh = Path(create_backup(self.store.paths, keep=3, label='corrupt2')['database'])
        raw = fresh.read_bytes()
        fresh.write_bytes(raw[:len(raw) // 2])
        target2 = self.root / 't-corrupt-2'
        with self.assertRaises(RestoreError) as ctx:
            restore_backup(self.paths, str(fresh.parent), str(target2))
        self.assertEqual(ctx.exception.stage, 'validate')
        self.assertFalse(target2.exists())

    def test_count_mismatch_fails_clean(self):
        manifest = json.loads(self.manifest_path.read_text(encoding='utf-8'))
        manifest['counts']['papers'] = 99
        self.manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
        target = self.root / 't-mismatch'
        with self.assertRaises(RestoreError) as ctx:
            restore_backup(self.paths, str(self.backup_dir), str(target))
        self.assertEqual(ctx.exception.stage, 'validate')
        self.assertFalse((target / 'state' / 'workbench.sqlite3').exists())
        self.assertFalse(target.exists())

    def test_target_conflicts_rejected(self):
        inside_active = self.paths.data / 'nested'
        has_db = self.root / 't-has-db'
        (has_db / 'state').mkdir(parents=True)
        (has_db / 'state' / 'workbench.sqlite3').write_bytes(b'')
        has_zotero = self.root / 't-has-zotero'
        has_zotero.mkdir()
        (has_zotero / 'zotero.sqlite').write_bytes(b'')
        cases = {
            '等于活动数据目录': self.paths.data,
            '在活动数据目录内部': inside_active,
            '等于备份源目录': self.backup_dir,
            '含既有 workbench.sqlite3': has_db,
            '含既有 zotero.sqlite': has_zotero,
            '与活动数据目录父子重叠': self.root,
        }
        for label, target in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(RestoreError) as ctx:
                    restore_backup(self.paths, str(self.backup_dir), str(target))
                self.assertEqual(ctx.exception.stage, 'target')
        self.assertFalse(inside_active.exists())  # 被拒绝的目标未被创建

    def test_copy_interrupt_cleans_up(self):
        target = self.root / 't-interrupt'
        with patch('shutil.copyfile', side_effect=RuntimeError('模拟复制中断')):
            with self.assertRaises(RestoreError) as ctx:
                restore_backup(self.paths, str(self.backup_dir), str(target))
        self.assertEqual(ctx.exception.stage, 'copy')
        state = target / 'state'
        if state.exists():
            self.assertFalse(list(state.glob('workbench.sqlite3.staging-*')))
        self.assertFalse(target.exists())  # state 与 target 都是本运行新建且已空，回滚删除

    def test_source_and_active_db_unchanged(self):
        sha_before = sha256_of(self.backup_db)
        papers_before = ro_scalar(self.paths.database, 'SELECT COUNT(*) FROM papers')
        report = restore_backup(self.paths, str(self.backup_dir), str(self.root / 't-unchanged'))
        self.assertTrue(report['ok'])
        self.assertEqual(report['source']['sha256'], sha_before)
        self.assertTrue(report['source']['unchanged'])
        self.assertEqual(sha256_of(self.backup_db), sha_before)
        self.assertTrue(report['active_db']['unchanged'])
        self.assertEqual(report['active_db']['counts_before']['papers'], papers_before)
        self.assertEqual(report['active_db']['counts_after']['papers'], papers_before)

    def test_cli_parser_accepts_restore(self):
        # 纯 argparse 冒烟；不跑 cli.main——它会覆写 os.environ 的 KB_BASE/KB_DATA_DIR。
        from kbapp.workflows.cli import parser
        expected_target = str(self.root / 't-cli')
        args = parser().parse_args(['restore', '--backup', str(self.backup_dir), '--target', expected_target])
        self.assertEqual(args.command, 'restore')
        self.assertEqual(args.backup, str(self.backup_dir))
        self.assertEqual(args.target, expected_target)


if __name__ == '__main__':
    unittest.main()
