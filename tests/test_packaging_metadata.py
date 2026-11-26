"""issue #87：打包元数据必须带 readme，否则 long_description 为空，twine check --strict 失败。"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def project_section(text):
    match = re.search(r'^\[project\]\s*$(.*?)(?=^\[|\Z)', text, re.MULTILINE | re.DOTALL)
    return match.group(1) if match else ''


class PackagingMetadata(unittest.TestCase):

    def test_project_readme_yields_markdown_long_description(self):
        # twine check --strict 把「long_description 缺失」与「content type 缺失」
        # 当失败；[project].readme 指向的 .md 文件让 setuptools 同时补齐两者。
        # 断言文件真实存在且非空，挡住「填了字段但路径写错或内容为空」的回归。
        project = project_section((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
        match = re.search(r'^readme\s*=\s*"([^"]+)"\s*$', project, re.MULTILINE)
        self.assertIsNotNone(match, 'pyproject 的 [project] 缺少 readme，构建产物没有 long_description')
        readme = ROOT / match.group(1)
        self.assertTrue(readme.is_file(), f'readme 指向的文件不存在：{match.group(1)}')
        self.assertTrue(readme.read_text(encoding='utf-8').strip(), 'readme 为空时 long_description 仍为空')
        self.assertEqual(readme.suffix, '.md', 'setuptools 按 .md 后缀推导 text/markdown content type')


if __name__ == '__main__':
    unittest.main()
