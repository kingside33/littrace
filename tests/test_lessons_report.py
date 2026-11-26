"""错题本周报:到期升级、删除候选与 CLI 形状。"""

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'check_lessons', ROOT / 'scripts' / 'check_lessons.py'
)
check_lessons = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_lessons)

TODAY = date(2026, 11, 20)

GOOD = """# 错题本

### L-001 一条规则
- 类: 别这样
- Seen: 2026-11-18
- 为什么: 原因
- 证据: #1
"""

DUE = GOOD.replace('2026-11-18', '2026-11-16, 2026-11-17, 2026-11-18')


class Report(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.docs = self.root / 'docs'
        self.docs.mkdir()

    def write(self, payload):
        path = self.docs / 'lessons.md'
        path.write_text(payload, encoding='utf-8')
        return path

    def entries(self, payload):
        path = self.write(payload)
        entries, errors = check_lessons.load(path, self.root)
        self.assertEqual([], errors)
        return entries

    def body(self, payload, today=TODAY):
        path = self.write(payload)
        entries, errors = check_lessons.load(path, self.root)
        self.assertEqual([], errors)
        return check_lessons.build_report(entries, today, path.stat().st_size)

    def cli(self, *arguments):
        return subprocess.run(
            [
                sys.executable,
                str(ROOT / 'scripts' / 'check_lessons.py'),
                '--dir',
                str(self.docs),
                '--root',
                str(self.root),
                *arguments,
            ],
            capture_output=True,
            text=True,
            encoding='utf-8',
        )

    def test_nothing_due_has_marker(self):
        body = self.body(GOOD)
        self.assertIn('<!-- no-lessons-due -->', body)
        self.assertIn('没有到期条目', body)
        self.assertIn('L-002', body)

    def test_due_entry_is_listed(self):
        body = self.body(DUE)
        self.assertNotIn('no-lessons-due', body)
        self.assertIn('## 到期升级', body)
        self.assertIn('| L-001 | 一条规则 | 3 | #1 |', body)

    def test_two_dates_are_not_due(self):
        body = self.body(GOOD.replace('2026-11-18', '2026-11-17, 2026-11-18'))
        self.assertIn('no-lessons-due', body)

    def test_promoted_entry_is_never_due(self):
        (self.root / 'AGENTS.md').write_text('规则见 L-001。\n', encoding='utf-8')
        text = """# 错题本

### L-001 已升级 → AGENTS.md
"""
        body = self.body(text)
        self.assertIn('no-lessons-due', body)

    def test_stale_boundary_is_sixty_days(self):
        exactly = self.body(GOOD.replace('2026-11-18', '2026-09-21'))
        self.assertNotIn('## 可删除候选:60 天未复发\n\n|', exactly)
        older = self.body(GOOD.replace('2026-11-18', '2026-09-20'))
        self.assertIn('| L-001 | 一条规则 | 2026-09-20 | #1 |', older)

    def test_table_cell_escapes_pipe_alone(self):
        body = self.body(DUE.replace('一条规则', '甲|乙'))
        self.assertIn(r'甲\|乙', body)
        self.assertNotIn('甲|乙', body)

    def test_table_cell_escapes_backslash_before_pipe(self):
        # GFM 里 \\ 是字面反斜杠、\| 是字面竖线:先转 \ 再转 |,
        # 否则含 \| 的标题会把单元格截断。
        body = self.body(DUE.replace('一条规则', r'甲\|乙'))
        self.assertIn(r'甲\\\|乙', body)
        self.assertNotIn(r'甲\|乙', body)

    def test_table_cell_joins_multiline_evidence(self):
        # 多行负载必须放进会渲染的「证据」列:放在「为什么」里删掉折叠逻辑
        # 测试照样全绿,不算锚定。
        text = DUE.replace('证据: #1', '证据: #1,\n  #2')
        body = self.body(text)
        self.assertIn('| #1, #2 |', body)

    def test_cli_report_is_markdown(self):
        self.write(DUE)
        result = self.cli('--report', '--today', TODAY.isoformat())
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('# 错题本周报', result.stdout)
        self.assertIn(f'- 日期:{TODAY}', result.stdout)
        self.assertIn('| L-001 |', result.stdout)

    def test_cli_report_rejects_bad_today(self):
        self.write(GOOD)
        result = self.cli('--report', '--today', '2026/11/20')
        self.assertEqual(2, result.returncode)
        self.assertIn('--today 不是合法日期', result.stderr)

    def test_real_file_report_runs(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / 'scripts' / 'check_lessons.py'), '--report'],
            capture_output=True,
            text=True,
            encoding='utf-8',
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('# 错题本周报', result.stdout)


if __name__ == '__main__':
    unittest.main()
