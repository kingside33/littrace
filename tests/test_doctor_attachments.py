"""功能 1：整库 PDF 可用性检查（kb.py doctor attachments）——离线、只读、零写入。

最小 zotero.sqlite 覆盖 load_items + fill_attachments 所需表
（itemTypesCombined/items/deletedItems/itemAttachments）；权限分支用注入
（替换 probe_file 或 os.stat/open），不依赖 CI 账户的真实权限行为。
"""
import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('KB_NO_STARTUP_JOBS', '1')
os.environ.setdefault('KB_NO_REFERENCE_WORKER', '1')

from kbapp.core.paths import Paths
from kbapp.workflows import cli
from kbapp.workflows.tools import doctor_attachments

PAPER = 1      # journalArticle
ATTACHMENT = 10

SUMMARY_KEYS = {'items', 'attachments', 'pdf_attachments', 'non_pdf_attachments',
                'attachments_by_state', 'items_by_category', 'libraries'}


def build_zotero_db(zdir, items, attachments, deleted_items=()):
    """items: (itemID, key, itemTypeID, libraryID)；attachments: (itemID, parentItemID, contentType, path)。"""
    os.makedirs(zdir, exist_ok=True)
    conn = sqlite3.connect(os.path.join(zdir, 'zotero.sqlite'))
    conn.executescript(
        'CREATE TABLE itemTypesCombined (itemTypeID INTEGER PRIMARY KEY, typeName TEXT);'
        'CREATE TABLE items (itemID INTEGER PRIMARY KEY, key TEXT, itemTypeID INTEGER,'
        ' dateAdded TEXT, dateModified TEXT, libraryID INTEGER, version INTEGER, clientDateModified TEXT);'
        'CREATE TABLE deletedItems (itemID INTEGER PRIMARY KEY);'
        'CREATE TABLE itemAttachments (itemID INTEGER PRIMARY KEY, parentItemID INTEGER,'
        ' linkMode INTEGER, contentType TEXT, path TEXT);')
    conn.executemany('INSERT INTO itemTypesCombined VALUES (?,?)', [(PAPER, 'journalArticle'), (ATTACHMENT, 'attachment')])
    stamp = '2026-11-21 00:00:00'
    conn.executemany('INSERT INTO items VALUES (?,?,?,?,?,?,?,?)',
                     [(item_id, key, type_id, stamp, stamp, library, 1, stamp) for item_id, key, type_id, library in items])
    conn.executemany('INSERT INTO deletedItems VALUES (?)', [(d,) for d in deleted_items])
    conn.executemany('INSERT INTO itemAttachments VALUES (?,?,?,?,?)',
                     [(att_id, parent, 0, content_type, path) for att_id, parent, content_type, path in attachments])
    conn.commit()
    conn.close()


def storage_file(zdir, att_key, filename, content=b'%PDF-1.4 test'):
    folder = Path(zdir) / 'storage' / att_key
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / filename
    target.write_bytes(content)
    return 'storage:' + filename


class DoctorAttachments(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name) / 'base'
        self.zdir = Path(self.temp.name) / 'zotero'
        self.saved = {key: os.environ.get(key) for key in ('KB_BASE', 'KB_DATA_DIR', 'KB_ZOTERO_DIR')}
        for key in self.saved:
            os.environ.pop(key, None)
        self.addCleanup(self.restore_env)

    def restore_env(self):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def run_json(self, argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = doctor_attachments.main(argv)
        return code, json.loads(out.getvalue())

    # 场景 1：空库 → 全 0 汇总、无问题项、退出 0。
    def test_empty_library_reports_zeroes_and_exits_zero(self):
        build_zotero_db(str(self.zdir), [], [])
        code, report = self.run_json(['--zotero-dir', str(self.zdir), '--base', str(self.base), '--json'])
        self.assertEqual(code, 0)
        summary = report['summary']
        self.assertEqual(summary['items'], 0)
        self.assertEqual(summary['attachments'], 0)
        self.assertEqual(summary['attachments_by_state'], {s: 0 for s in doctor_attachments.STATES})
        self.assertEqual(summary['items_by_category'], {c: 0 for c in doctor_attachments.CATEGORIES})
        self.assertEqual(summary['libraries'], {})
        self.assertEqual(report['problems'], [])

    # 场景 2：同篇多个 PDF（均可读）→ readable=2，文献 pdf_readable=1 且不在缺失类。
    def test_multiple_readable_pdfs_counted_per_attachment_and_item(self):
        build_zotero_db(str(self.zdir),
                        [(1, 'PAPERA01', PAPER, 1), (2, 'ATTAA001', ATTACHMENT, 1), (3, 'ATTAA002', ATTACHMENT, 1)],
                        [(2, 1, 'application/pdf', storage_file(str(self.zdir), 'ATTAA001', 'main.pdf')),
                         (3, 1, 'application/pdf', storage_file(str(self.zdir), 'ATTAA002', 'copy.pdf'))])
        code, report = self.run_json(['--zotero-dir', str(self.zdir), '--base', str(self.base), '--json'])
        self.assertEqual(code, 0)
        summary = report['summary']
        self.assertEqual(summary['items'], 1)
        self.assertEqual(summary['attachments'], 2)
        self.assertEqual(summary['pdf_attachments'], 2)
        self.assertEqual(summary['attachments_by_state']['readable'], 2)
        self.assertEqual(summary['attachments_by_state']['missing'], 0)
        cats = summary['items_by_category']
        self.assertEqual(cats['pdf_readable'], 1)
        self.assertEqual(cats['no_pdf'], 0)
        self.assertEqual(cats['pdf_missing'], 0)
        self.assertEqual(cats['pdf_unreadable'], 0)
        self.assertEqual(report['problems'], [])

    # 场景 3：主 PDF 缺失而副本可读 → 文献同时计入 pdf_missing 与 pdf_readable（两事实保留），
    # 问题项含 missing 附件、不含 readable 附件；发现问题时退出码仍为 0。
    def test_missing_main_with_readable_copy_keeps_both_facts(self):
        build_zotero_db(str(self.zdir),
                        [(1, 'PAPERB01', PAPER, 1), (2, 'ATTBB001', ATTACHMENT, 1), (3, 'ATTBB002', ATTACHMENT, 1)],
                        [(2, 1, 'application/pdf', 'storage:main.pdf'),
                         (3, 1, 'application/pdf', storage_file(str(self.zdir), 'ATTBB002', 'copy.pdf'))])
        code, report = self.run_json(['--zotero-dir', str(self.zdir), '--base', str(self.base), '--json'])
        self.assertEqual(code, 0)
        cats = report['summary']['items_by_category']
        self.assertEqual(cats['pdf_missing'], 1)
        self.assertEqual(cats['pdf_readable'], 1)
        self.assertEqual(cats['no_pdf'], 0)
        self.assertEqual(len(report['problems']), 1)
        problem = report['problems'][0]
        self.assertEqual((problem['library_id'], problem['item_key'], problem['attachment_key'], problem['state']),
                         (1, 'PAPERB01', 'ATTBB001', 'missing'))
        # 默认摘要：含中文状态词与来源说明，不打印绝对路径。
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(doctor_attachments.main(['--zotero-dir', str(self.zdir), '--base', str(self.base)]), 0)
        text = out.getvalue()
        self.assertIn('数据来源', text)
        self.assertIn('含缺失 PDF 1', text)
        self.assertIn('有可读 PDF 1', text)
        self.assertNotIn(str(self.zdir), text)
        self.assertNotIn(str(self.base), text)

    # 场景 4：两个库有同名键 → 汇总与问题项按 library_id 区分，不串项。
    def test_same_keys_in_two_libraries_stay_separate(self):
        build_zotero_db(str(self.zdir),
                        [(1, 'SAMEKEY1', PAPER, 1), (2, 'SAMEKEY2', ATTACHMENT, 1),
                         (3, 'SAMEKEY1', PAPER, 2), (4, 'SAMEKEY2', ATTACHMENT, 2)],
                        [(2, 1, 'application/pdf', 'storage:gone.pdf'),
                         (4, 3, 'application/pdf', storage_file(str(self.zdir), 'SAMEKEY2', 'ok.pdf'))])
        code, report = self.run_json(['--zotero-dir', str(self.zdir), '--base', str(self.base), '--json'])
        self.assertEqual(code, 0)
        libraries = report['summary']['libraries']
        self.assertEqual(libraries['1']['items_by_category']['pdf_missing'], 1)
        self.assertEqual(libraries['1']['items_by_category']['pdf_readable'], 0)
        self.assertEqual(libraries['2']['items_by_category']['pdf_readable'], 1)
        self.assertEqual(libraries['2']['items_by_category']['pdf_missing'], 0)
        self.assertEqual([(p['library_id'], p['item_key'], p['attachment_key'], p['state']) for p in report['problems']],
                         [(1, 'SAMEKEY1', 'SAMEKEY2', 'missing')])

    # 场景 5：非 PDF 附件（html 快照 / linked_url）→ 非 PDF/not_file 计数正确，不影响 PDF 类别；
    # 已删除附件不计入。
    def test_non_pdf_and_linked_url_and_deleted_excluded_from_pdf_categories(self):
        build_zotero_db(str(self.zdir),
                        [(1, 'PAPERC01', PAPER, 1), (2, 'ATTCC001', ATTACHMENT, 1),
                         (3, 'ATTCC002', ATTACHMENT, 1), (4, 'ATTCC003', ATTACHMENT, 1)],
                        [(2, 1, 'text/html', storage_file(str(self.zdir), 'ATTCC001', 'snapshot.html', b'<html></html>')),
                         (3, 1, 'text/html', None),
                         (4, 1, 'application/pdf', 'storage:deleted.pdf')],
                        deleted_items=[4])
        code, report = self.run_json(['--zotero-dir', str(self.zdir), '--base', str(self.base), '--json'])
        self.assertEqual(code, 0)
        summary = report['summary']
        self.assertEqual(summary['items'], 1)
        self.assertEqual(summary['attachments'], 2)
        self.assertEqual(summary['pdf_attachments'], 0)
        self.assertEqual(summary['non_pdf_attachments'], 2)
        self.assertEqual(summary['attachments_by_state']['readable'], 1)
        self.assertEqual(summary['attachments_by_state']['not_file'], 1)
        self.assertEqual(summary['attachments_by_state']['missing'], 0)
        self.assertEqual(summary['items_by_category']['no_pdf'], 1)
        self.assertEqual(summary['items_by_category']['pdf_readable'], 0)
        self.assertEqual(report['problems'], [])

    # 场景 6：文件在同步后被移走（先登记后删除）→ missing；attachments: 相对路径按 base 解析。
    def test_file_moved_after_sync_reports_missing_and_linked_file_resolves(self):
        linked_dir = self.base / 'linked'
        linked_dir.mkdir(parents=True)
        (linked_dir / 'linked.pdf').write_bytes(b'%PDF-1.4 linked')
        build_zotero_db(str(self.zdir),
                        [(1, 'PAPERD01', PAPER, 1), (2, 'ATTDD001', ATTACHMENT, 1), (3, 'ATTDD002', ATTACHMENT, 1)],
                        [(2, 1, 'application/pdf', storage_file(str(self.zdir), 'ATTDD001', 'moved.pdf')),
                         (3, 1, 'application/pdf', 'attachments:linked/linked.pdf')])
        Path(self.zdir, 'storage', 'ATTDD001', 'moved.pdf').unlink()
        code, report = self.run_json(['--zotero-dir', str(self.zdir), '--base', str(self.base), '--json'])
        self.assertEqual(code, 0)
        self.assertEqual(report['summary']['attachments_by_state']['readable'], 1)
        self.assertEqual(report['summary']['attachments_by_state']['missing'], 1)
        self.assertEqual(report['summary']['items_by_category']['pdf_missing'], 1)
        self.assertEqual(report['summary']['items_by_category']['pdf_readable'], 1)
        self.assertEqual([p['attachment_key'] for p in report['problems']], ['ATTDD001'])
        # 默认输出发现问题仍退出 0（观测命令，非门禁）。
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(doctor_attachments.main(['--zotero-dir', str(self.zdir), '--base', str(self.base)]), 0)

    # 场景 7：权限分支可控注入（替换模块探测函数），不依赖 CI 账户权限。
    def test_permission_and_oserror_injection_maps_states(self):
        build_zotero_db(str(self.zdir),
                        [(1, 'PAPERE01', PAPER, 1), (2, 'ATTEE001', ATTACHMENT, 1)],
                        [(2, 1, 'application/pdf', storage_file(str(self.zdir), 'ATTEE001', 'main.pdf'))])
        with mock.patch.object(doctor_attachments, 'probe_file', side_effect=PermissionError):
            code, report = self.run_json(['--zotero-dir', str(self.zdir), '--base', str(self.base), '--json'])
        self.assertEqual(code, 0)
        self.assertEqual(report['summary']['attachments_by_state']['no_permission'], 1)
        self.assertEqual(report['summary']['items_by_category']['pdf_unreadable'], 1)
        self.assertEqual(report['problems'][0]['state'], 'no_permission')
        with mock.patch.object(doctor_attachments, 'probe_file', side_effect=OSError('模拟读取失败')):
            code, report = self.run_json(['--zotero-dir', str(self.zdir), '--base', str(self.base), '--json'])
        self.assertEqual(code, 0)
        self.assertEqual(report['summary']['attachments_by_state']['unreadable'], 1)
        self.assertEqual(report['summary']['items_by_category']['pdf_unreadable'], 1)
        self.assertEqual(report['problems'][0]['state'], 'unreadable')

    # 探测函数本体：真实缺失/可读取/目录，与注入的 stat/open 错误映射。
    def test_probe_file_maps_errors_without_account_permissions(self):
        existing = self.base / 'real.pdf'
        existing.parent.mkdir(parents=True, exist_ok=True)
        existing.write_bytes(b'%PDF-1.4 real')
        self.assertEqual(doctor_attachments.probe_file(str(existing)), 'readable')
        self.assertEqual(doctor_attachments.probe_file(str(self.base / 'nope.pdf')), 'missing')
        self.assertEqual(doctor_attachments.probe_file(str(self.base)), 'unreadable')
        with mock.patch('os.stat', side_effect=PermissionError):
            self.assertEqual(doctor_attachments.probe_file('any'), 'no_permission')
        with mock.patch('os.stat', side_effect=FileNotFoundError):
            self.assertEqual(doctor_attachments.probe_file('any'), 'missing')
        with mock.patch('os.stat', side_effect=OSError('模拟 stat 失败')):
            self.assertEqual(doctor_attachments.probe_file('any'), 'unreadable')
        with mock.patch('builtins.open', side_effect=PermissionError):
            self.assertEqual(doctor_attachments.probe_file(str(existing)), 'no_permission')
        with mock.patch('builtins.open', side_effect=OSError('模拟打开失败')):
            self.assertEqual(doctor_attachments.probe_file(str(existing)), 'unreadable')

    # JSON 输出：可解析、键集合稳定、不含绝对路径与文件名。
    def test_json_shape_is_stable_and_carries_no_paths(self):
        build_zotero_db(str(self.zdir),
                        [(1, 'PAPERF01', PAPER, 1), (2, 'ATTFE001', ATTACHMENT, 1)],
                        [(2, 1, 'application/pdf', 'storage:secret-name.pdf')])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = doctor_attachments.main(['--zotero-dir', str(self.zdir), '--base', str(self.base), '--json'])
        self.assertEqual(code, 0)
        text = out.getvalue()
        report = json.loads(text)
        self.assertEqual(set(report), {'source', 'summary', 'problems'})
        self.assertEqual(set(report['summary']), SUMMARY_KEYS)
        self.assertEqual(set(report['summary']['attachments_by_state']), set(doctor_attachments.STATES))
        self.assertEqual(set(report['summary']['items_by_category']), set(doctor_attachments.CATEGORIES))
        self.assertEqual(set(report['problems'][0]), {'library_id', 'item_key', 'attachment_key', 'state'})
        self.assertNotIn(str(self.zdir), text)
        self.assertNotIn(str(self.base), text)
        self.assertNotIn('secret-name', text)

    # zotero_dir 解析顺序：--zotero-dir > KB_ZOTERO_DIR > settings.json；都没有则报错退出 2。
    def test_zotero_dir_resolution_order_and_missing_guidance(self):
        paths = Paths.from_base(str(self.base))
        self.assertEqual(doctor_attachments.resolve_zotero_dir('explicit', paths), 'explicit')
        with mock.patch.dict(os.environ, {'KB_ZOTERO_DIR': 'from-env'}):
            self.assertEqual(doctor_attachments.resolve_zotero_dir(None, paths), 'from-env')
        # 无任何配置 → 指引并退出 2（在写 settings.json 之前检查）。
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = doctor_attachments.main(['--base', str(self.base), '--json'])
        self.assertEqual(code, 2)
        self.assertIn('KB_ZOTERO_DIR', err.getvalue())
        settings = paths.state / 'settings.json'
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(json.dumps({'zotero_dir': 'from-settings'}), encoding='utf-8')
        with mock.patch.dict(os.environ, {'KB_ZOTERO_DIR': ''}):
            self.assertEqual(doctor_attachments.resolve_zotero_dir(None, paths), 'from-settings')
        # settings.json 兜底端到端：不加 --zotero-dir 也能跑到库。
        build_zotero_db(str(self.zdir), [], [])
        settings.write_text(json.dumps({'zotero_dir': str(self.zdir)}), encoding='utf-8')
        code, report = self.run_json(['--base', str(self.base), '--json'])
        self.assertEqual(code, 0)
        self.assertEqual(report['summary']['items'], 0)

    # zotero.sqlite 缺失 → 非零退出（2）+ 指引；未知参数 → argparse 退出码 2。
    def test_missing_sqlite_and_bad_args_exit_2(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = doctor_attachments.main(['--zotero-dir', str(self.zdir), '--base', str(self.base)])
        self.assertEqual(code, 2)
        self.assertIn('zotero.sqlite', err.getvalue())
        with self.assertRaises(SystemExit) as caught:
            doctor_attachments.main(['--bogus'])
        self.assertEqual(caught.exception.code, 2)

    # CLI 分派守卫：doctor attachments 走早退分支，绝不初始化可写 AppContext（需求 4 点名位置）。
    def test_cli_dispatch_reaches_tool_without_appcontext_and_writes_nothing(self):
        build_zotero_db(str(self.zdir),
                        [(1, 'PAPERG01', PAPER, 1), (2, 'ATTGE001', ATTACHMENT, 1)],
                        [(2, 1, 'application/pdf', storage_file(str(self.zdir), 'ATTGE001', 'main.pdf'))])
        out = io.StringIO()
        with mock.patch('kbapp.workflows.context.AppContext.create',
                        side_effect=AssertionError('只读 doctor 命令不得初始化可写 AppContext')):
            with contextlib.redirect_stdout(out):
                code = cli.main(['--base', str(self.base), 'doctor', 'attachments', '--json',
                                 '--zotero-dir', str(self.zdir)])
        self.assertEqual(code, 0)
        report = json.loads(out.getvalue())
        self.assertEqual(report['summary']['items'], 1)
        self.assertEqual(report['summary']['attachments_by_state']['readable'], 1)
        # 零写入：运行后数据目录不应被创建或改动。
        self.assertFalse((self.base / '_index' / 'state').exists())


if __name__ == '__main__':
    unittest.main()
