"""Public-promise wording invariants for the bilingual README.

These tests read four files only: ``README.md``, ``README.zh-CN.md``,
``AGENTS.md`` and ``docs/commercial/limits.md``. They do not import ``kbapp``,
touch the network or load a model. The point is to make two regressions red:

* the old "existing features are never metered" wording comes back;
* the English and Chinese README drift apart on wording, structure or numbers.

When a number changes it changes in ``docs/commercial/limits.md`` first; the
READMEs repeat only the two headline numbers, which are parsed from that table
here instead of being hardcoded a second time.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
README_ZH = ROOT / "README.zh-CN.md"
AGENTS = ROOT / "AGENTS.md"
LIMITS = ROOT / "docs" / "commercial" / "limits.md"
LIMITS_LINK = "docs/commercial/limits.md"

OLD_EN = (
    "No paywall on existing open source",
    "not moved into a paid tier",
    "never moved behind a paywall",
    "A paid capability must be additive",
    "The paid feature set is not decided yet",
    "The paid shape and pay points are undecided",
    "Advanced features in the future Zotero plugin",
    "Local first stays free where possible",
)

OLD_ZH = (
    "现有开源功能不设付费墙",
    "不被移入付费层",
    "付费能力必须是增量",
    "付费功能集尚未确定",
    "付费形态与付费点未定",
    "未来 Zotero 插件中的高级功能",
    "本地优先能力尽量免费可用",
)

NEW_EN = (
    "under published limits",
    "published free limits",
    "The paid surface has one decided shape",
    "A Pro entitlement",
    "within the free limits",
)

NEW_ZH = (
    "规模按公开额度计量",
    "公开的免费额度",
    "付费面已有确定的形态",
    "Pro 授权",
    "在免费额度内可用",
)

BLACKLIST = ("试用", "限免", "阉割", "解锁码", "激活码")
BLACKLIST_README = ("编排层", "Pro 解锁")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def section(text: str, heading_prefix: str, stop_prefix: str) -> str:
    """Lines under ``heading_prefix`` up to the next ``stop_prefix`` line."""
    lines = text.splitlines()
    start = next(
        (i for i, line in enumerate(lines) if line.startswith(heading_prefix)), None
    )
    if start is None:
        raise AssertionError(f"missing section {heading_prefix!r}")
    body = []
    for line in lines[start + 1 :]:
        if line.startswith(stop_prefix):
            break
        body.append(line)
    return "\n".join(body)


def table_values(text: str) -> dict[str, str]:
    """Map the backticked key column of the limits table to its Free column."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 3 and cells[1].startswith("`") and cells[1].endswith("`"):
            values[cells[1].strip("`")] = cells[2]
    return values


def count_markdown_links(text: str, target: str) -> int:
    return len(re.findall(r"\[[^\]]*\]\(" + re.escape(target) + r"\)", text))


class OldPromisesStayDead(unittest.TestCase):
    def test_english_readme_has_no_old_promise(self):
        text = read(README)
        for phrase in OLD_EN:
            self.assertEqual(
                text.count(phrase),
                0,
                f"README.md still contains old promise: {phrase!r}",
            )

    def test_chinese_readme_has_no_old_promise(self):
        text = read(README_ZH)
        for phrase in OLD_ZH:
            self.assertEqual(
                text.count(phrase),
                0,
                f"README.zh-CN.md still contains old promise: {phrase!r}",
            )


class NewPromisesPresent(unittest.TestCase):
    def test_english_wording_present(self):
        text = read(README)
        for phrase in NEW_EN:
            self.assertIn(phrase, text, f"README.md is missing new wording: {phrase!r}")

    def test_chinese_wording_present(self):
        text = read(README_ZH)
        for phrase in NEW_ZH:
            self.assertIn(phrase, text, f"README.zh-CN.md is missing new wording: {phrase!r}")


class NumbersMatchLimitsTable(unittest.TestCase):
    def setUp(self):
        self.limits = read(LIMITS)
        self.values = table_values(self.limits)

    def test_limits_table_has_all_four_free_values(self):
        self.assertEqual(self.values.get("papers_per_run"), "20")
        self.assertEqual(self.values.get("first_level_neighbours_per_paper"), "10")
        self.assertEqual(self.values.get("verifier_units_per_run"), "20")
        self.assertEqual(self.values.get("analysis_tasks_per_day"), "3")

    def test_english_readme_uses_table_numbers(self):
        papers = self.values["papers_per_run"]
        neighbours = self.values["first_level_neighbours_per_paper"]
        text = read(README)
        self.assertIn(f"at most {papers} papers as its starting set", text)
        self.assertIn(f"at most {neighbours} first-level neighbours per paper", text)

    def test_chinese_readme_uses_table_numbers(self):
        papers = self.values["papers_per_run"]
        neighbours = self.values["first_level_neighbours_per_paper"]
        text = read(README_ZH)
        self.assertIn(f"最多以 {papers} 篇文献作为起始集合", text)
        self.assertIn(f"每篇最多展开 {neighbours} 篇一级邻居", text)


class BilingualStructureMatches(unittest.TestCase):
    def test_roadmap_stage_rows_match(self):
        en = section(read(README), "## Roadmap", "## ")
        zh = section(read(README_ZH), "## Roadmap", "## ")
        en_rows = [line for line in en.splitlines() if line.startswith("| **")]
        zh_rows = [line for line in zh.splitlines() if line.startswith("| **")]
        self.assertEqual(len(en_rows), 3, f"README.md roadmap rows: {en_rows}")
        self.assertEqual(len(zh_rows), 3, f"README.zh-CN.md roadmap rows: {zh_rows}")

    def test_paid_form_entries_match(self):
        en = section(read(README), "### Paid forms", "### ")
        zh = section(read(README_ZH), "### 付费形态", "### ")
        en_items = re.findall(r"^\d+\. ", en, flags=re.MULTILINE)
        zh_items = re.findall(r"^\d+\. ", zh, flags=re.MULTILINE)
        self.assertEqual(len(en_items), 3, "README.md paid-form entries")
        self.assertEqual(len(zh_items), 3, "README.zh-CN.md paid-form entries")

    def test_principle_entries_match(self):
        en = section(read(README), "### Principles", "### ")
        zh = section(read(README_ZH), "### 原则", "### ")
        en_labels = re.findall(r"^- \*\*\(([a-e])\)", en, flags=re.MULTILINE)
        zh_labels = re.findall(r"^- \*\*（([a-e])）", zh, flags=re.MULTILINE)
        self.assertEqual(en_labels, ["a", "b", "c", "d", "e"], "README.md principles")
        self.assertEqual(zh_labels, ["a", "b", "c", "d", "e"], "README.zh-CN.md principles")

    def test_both_readmes_link_the_same_limits_file(self):
        self.assertTrue(LIMITS.exists(), f"missing {LIMITS}")
        self.assertTrue((ROOT / LIMITS_LINK).exists())
        self.assertEqual(count_markdown_links(read(README), LIMITS_LINK), 1)
        self.assertEqual(count_markdown_links(read(README_ZH), LIMITS_LINK), 1)

    def test_changed_lines_stay_single_lines(self):
        en_lines = read(README).splitlines()
        zh_lines = read(README_ZH).splitlines()
        en_now = [line for line in en_lines if line.startswith("| **Now**")]
        zh_now = [line for line in zh_lines if line.startswith("| **现在（Now）**")]
        self.assertEqual(len(en_now), 1, "README.md Now row must be one line")
        self.assertEqual(len(zh_now), 1, "README.zh-CN.md Now row must be one line")
        self.assertIn("under the published free limits", en_now[0])
        self.assertIn("公开的免费额度内", zh_now[0])
        en_a = [line for line in en_lines if "under published limits" in line]
        zh_a = [line for line in zh_lines if "规模按公开额度计量" in line]
        self.assertEqual(len(en_a), 1, "README.md principle (a) must be one line")
        self.assertEqual(len(zh_a), 1, "README.zh-CN.md principle (a) must be one line")
        self.assertIn(LIMITS_LINK, en_a[0])
        self.assertIn(LIMITS_LINK, zh_a[0])


class LimitsFileIsSelfConsistent(unittest.TestCase):
    def setUp(self):
        self.text = read(LIMITS)
        self.flat = " ".join(self.text.split())
        self.values = table_values(self.text)

    def test_version_marker(self):
        first = self.text.splitlines()[0]
        self.assertRegex(first, r"^<!-- free-limits: v1; effective: \d{4}-\d{2}-\d{2} -->$")

    def test_pro_column_is_checkable(self):
        rows = {}
        for line in self.text.splitlines():
            if not line.startswith("|"):
                continue
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) >= 4 and cells[1].startswith("`"):
                rows[cells[1].strip("`")] = cells[3]
        self.assertEqual(rows.get("papers_per_run"), "13000")
        self.assertEqual(rows.get("first_level_neighbours_per_paper"), "24")
        self.assertIn("no per-run unit limit", rows.get("verifier_units_per_run", ""))
        self.assertIn("no daily task limit", rows.get("analysis_tasks_per_day", ""))

    def test_counting_rules(self):
        self.assertIn("Asia/Shanghai", self.text)
        self.assertIn("resume", self.text)
        self.assertIn("refund", self.text)
        self.assertIn("manual reset", self.text)
        self.assertIn("outside your library", self.text)

    def test_limits_are_not_configurable(self):
        self.assertIn("not in the settings page", self.text)
        for key in ("external_requests_per_run", "cloud_requests_per_run", "daily_budget"):
            self.assertIn(key, self.text)
        self.assertIn("environment variable", self.text)
        self.assertIn("command-line switch", self.text)

    def test_public_key_maps_to_entitlement_api_key(self):
        sentences = re.split(r"(?<=\.)\s+", self.flat)
        mapped = [
            sentence
            for sentence in sentences
            if "first_level_neighbours_per_paper" in sentence
            and "references_per_paper" in sentence
        ]
        self.assertTrue(mapped, "public key and references_per_paper must share a sentence")

    def test_visibility_rules_stated(self):
        self.assertIn("refused before any work starts", self.flat)
        self.assertIn("recorded in that run's own result", self.flat)
        self.assertIn("silently truncated", self.flat)


class AgentsClauseInPlace(unittest.TestCase):
    def test_new_clause_and_original_definitions(self):
        text = read(AGENTS)
        for keyword in (
            "免费额度是核心的公开承诺",
            "超限必须可见拒绝或可见暂停",
            "不得静默截断",
            LIMITS_LINK,
        ):
            self.assertIn(keyword, text, f"AGENTS.md is missing {keyword!r}")
        self.assertIn("插件壳(XPI 及其原生集成层)永久免费且 AGPL 开源", text)
        self.assertIn(
            "「编排层」一词专指 M3 商业编排层(收费物),不得出现在免费承诺里", text
        )


class TerminologyBlacklist(unittest.TestCase):
    def test_forbidden_terms_absent_from_docs(self):
        for path in (README, README_ZH, LIMITS):
            text = read(path)
            for term in BLACKLIST:
                self.assertEqual(text.count(term), 0, f"{path.name} contains {term!r}")

    def test_orchestration_layer_absent_from_readmes(self):
        for path in (README, README_ZH):
            text = read(path)
            for term in BLACKLIST_README:
                self.assertEqual(text.count(term), 0, f"{path.name} contains {term!r}")


if __name__ == "__main__":
    unittest.main()
