"""错题本格式:真实文件、反例与 CLI 行为。"""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'check_lessons', ROOT / 'scripts' / 'check_lessons.py'
)
check_lessons = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_lessons)

GOOD = """# 错题本

### L-001 一条规则
- 类: 别这样
- Seen: 2026-11-18
- 为什么: 原因
- 证据: #1
"""

FENCED = """# 错题本

```markdown
### L-001 示例条目,代码块里的不算
- 类: 别这样
- Seen: 2026-11-12
- 为什么: 示例
- 证据: #1
```
"""


class Lessons(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.docs = self.root / 'docs'
        self.docs.mkdir()

    def write(self, text):
        path = self.docs / 'lessons.md'
        path.write_text(text, encoding='utf-8')
        return path

    def errors(self, text):
        _, errors = check_lessons.load(self.write(text), self.root)
        return errors

    def messages(self, text):
        return '\n'.join(message for _, message in self.errors(text))

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

    def test_real_file_passes(self):
        _, errors = check_lessons.load(ROOT / 'docs/lessons.md', ROOT)
        self.assertEqual([], errors)

    def test_code_fence_is_not_an_entry(self):
        self.assertEqual([], self.errors(FENCED))

    def test_missing_field(self):
        self.assertIn('缺少字段「证据」', self.messages(GOOD.replace('- 证据: #1\n', '')))

    def test_bad_category(self):
        self.assertIn('类只能是', self.messages(GOOD.replace('别这样', '随便')))

    def test_number_must_ascend(self):
        text = GOOD.replace('L-001 一条规则', 'L-003 一条规则') + """
### L-002 后一条
- 类: 照这样
- Seen: 2026-11-18
- 为什么: 原因
- 证据: #2
"""
        self.assertIn('编号必须严格升序', self.messages(text))

    def test_duplicate_number(self):
        text = GOOD + """
### L-001 重复编号
- 类: 照这样
- Seen: 2026-11-18
- 为什么: 原因
- 证据: #2
"""
        self.assertIn('编号必须严格升序', self.messages(text))

    def test_seen_must_be_sorted_and_unique(self):
        text = GOOD.replace('2026-11-18', '2026-11-18, 2026-11-17')
        self.assertIn('Seen 日期必须升序', self.messages(text))
        text = GOOD.replace('2026-11-18', '2026-11-18, 2026-11-18')
        self.assertIn('Seen 日期不能重复', self.messages(text))

    def test_seen_rejects_future_and_bad_format(self):
        future = check_lessons.beijing_today() + timedelta(days=1)
        self.assertIn('晚于今天', self.messages(GOOD.replace('2026-11-18', str(future))))
        self.assertIn('YYYY-MM-DD', self.messages(GOOD.replace('2026-11-18', '2026/11/18')))

    def test_evidence_must_be_issue_numbers(self):
        self.assertIn('证据应为 issue 编号', self.messages(GOOD.replace('#1', 'PR 1')))

    def test_promoted_target_missing(self):
        text = """# 错题本

### L-001 已升级 → AGENTS.md
"""
        self.assertIn('指针目标不存在', self.messages(text))

    def test_promoted_target_must_name_the_id(self):
        (self.root / 'AGENTS.md').write_text('# 规则\n', encoding='utf-8')
        text = """# 错题本

### L-001 已升级 → AGENTS.md
"""
        self.assertIn('没有引用该编号', self.messages(text))

    def test_promoted_entry_has_no_fields(self):
        (self.root / 'AGENTS.md').write_text('L-001\n', encoding='utf-8')
        text = """# 错题本

### L-001 已升级 → AGENTS.md
- 类: 照这样
- Seen: 2026-11-18
- 为什么: 原因
- 证据: #1
"""
        self.assertIn('不应再有字段', self.messages(text))

    def test_promoted_entry_passes(self):
        (self.root / 'AGENTS.md').write_text('规则见 L-001。\n', encoding='utf-8')
        text = """# 错题本

### L-001 已升级 → AGENTS.md
"""
        self.assertEqual([], self.errors(text))

    def test_size_cap(self):
        text = GOOD + ('填充' * 3000) + '\n'
        self.assertIn('字节上限', self.messages(text))

    def test_privacy_patterns(self):
        cases = [
            'C:\\Users\\name\\data',
            'someone@example.com',
            '192.168.1.10',
            'api_key: abc123',
            'docs/zotero-plugin-plan.md',
        ]
        for sample in cases:
            with self.subTest(sample=sample):
                text = GOOD.replace('原因', sample)
                self.assertIn('不得写入', self.messages(text))

    def test_continuation_lines_are_joined(self):
        text = GOOD.replace('为什么: 原因', '为什么: 第一行\n  第二行')
        self.assertEqual([], self.errors(text))

    def test_unindented_line_is_rejected(self):
        text = GOOD.replace('为什么: 原因', '为什么: 原因\n随手写的一行')
        self.assertIn('无法解析的行', self.messages(text))

    def test_cli_check_and_due(self):
        self.write(GOOD)
        result = self.cli()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('格式校验通过', result.stdout)

        self.write(GOOD.replace('2026-11-18', '2026-11-16, 2026-11-17, 2026-11-18'))
        result = self.cli('--due')
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('L-001', result.stdout)

    def test_cli_fails_with_line_number(self):
        self.write(GOOD.replace('证据: #1', '证据: 无'))
        result = self.cli()
        self.assertEqual(1, result.returncode)
        self.assertIn('lessons.md:', result.stderr)
        self.assertIn('证据应为 issue 编号', result.stderr)

    def test_cli_stats_and_json(self):
        self.write(GOOD)
        stats = self.cli('--stats')
        self.assertEqual(0, stats.returncode, stats.stderr)
        self.assertIn('下一个编号 L-002', stats.stdout)

        payload = self.cli('--json')
        self.assertEqual(0, payload.returncode, payload.stderr)
        records = json.loads(payload.stdout)
        self.assertEqual('L-001', records[0]['id'])
        self.assertEqual('别这样', records[0]['category'])


if __name__ == '__main__':
    unittest.main()
