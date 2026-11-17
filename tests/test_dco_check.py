"""Tests for the DCO gate in scripts/ci/check_dco.py.

The tests use a fake GitHub client. They never touch the network and never run
anything from a pull request. Each case maps to a boundary that the workflow
relies on: a missing trailer, a mismatched e-mail, merge commits, an explicitly
allowlisted bot, more than 250 commits, and a merge queue run that must report
not_applicable rather than skip.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "ci" / "check_dco.py"

_spec = importlib.util.spec_from_file_location("check_dco", MODULE_PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover - broken checkout
    raise RuntimeError(f"cannot load {MODULE_PATH}")
check_dco = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = check_dco
_spec.loader.exec_module(check_dco)

CONTRIBUTING = "<!-- contribution-terms: v1; effective: 2026-11-17 -->\n\n# Contributing\n"
DCO = "Developer Certificate of Origin\nVersion 1.1\n"


def signed(sha: str, email: str = "alice@example.com", subject: str = "Change something") -> dict[str, Any]:
    """A non-merge commit with a sign-off that matches its author."""
    return {
        "sha": sha,
        "commit": {
            "message": f"{subject}\n\nBody.\n\nSigned-off-by: Alice <{email}>\n",
            "author": {"name": "Alice", "email": email},
        },
        "author": {"login": "alice", "id": 100},
        "parents": [{"sha": "parent"}],
    }


def unsigned(sha: str, message: str = "Change something\n", email: str = "alice@example.com") -> dict[str, Any]:
    return {
        "sha": sha,
        "commit": {
            "message": message,
            "author": {"name": "Alice", "email": email},
        },
        "author": {"login": "alice", "id": 100},
        "parents": [{"sha": "parent"}],
    }


def merge(sha: str = "merge0000000") -> dict[str, Any]:
    return {
        "sha": sha,
        "commit": {
            "message": "Merge branch 'feature'\n",
            "author": {"name": "Alice", "email": "alice@example.com"},
        },
        "author": {"login": "alice", "id": 100},
        "parents": [{"sha": "one"}, {"sha": "two"}],
    }


def page(commits: list[dict[str, Any]], total: int | None = None) -> dict[str, Any]:
    return {"total_commits": len(commits) if total is None else total, "commits": commits}


class FakeClient:
    def __init__(self, pages: list[dict[str, Any]], files: dict[str, tuple[str, str]] | None = None):
        self.pages = pages
        self.files = files or {
            "CONTRIBUTING.md": ("contributing-blob-sha", CONTRIBUTING),
            "DCO": ("dco-blob-sha", DCO),
        }
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.file_requests: list[tuple[str, str, str]] = []

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> tuple[Any, dict[str, str]]:
        self.requests.append((path, dict(params or {})))
        page_number = int((params or {}).get("page", 1))
        if page_number < 1 or page_number > len(self.pages):
            raise check_dco.ConfigError(f"unexpected page {page_number}")
        headers: dict[str, str] = {}
        if page_number < len(self.pages):
            headers["Link"] = f'<https://api.github.com/page={page_number + 1}>; rel="next"'
        return self.pages[page_number - 1], headers

    def get_file(self, repo: str, ref: str, path: str) -> tuple[str, str]:
        self.file_requests.append((repo, ref, path))
        if path not in self.files:
            raise check_dco.ConfigError(f"unexpected file request: {path}")
        return self.files[path]


class DcoCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.event_path = self.root / "event.json"
        self.summary_path = self.root / "summary.md"
        self.event = {
            "repository": {"default_branch": "main"},
            "pull_request": {
                "number": 7,
                "base": {"sha": "base0000000"},
                "head": {"sha": "head0000000"},
            },
        }

    def write_event(self, event: dict[str, Any] | None = None) -> None:
        self.event_path.write_text(json.dumps(event if event is not None else self.event), encoding="utf-8")

    def run_check(
        self,
        commits: list[dict[str, Any]],
        *,
        event_name: str = "pull_request",
        event: dict[str, Any] | None = None,
        files: dict[str, tuple[str, str]] | None = None,
        bot_allowlist: str = "",
        pages: list[dict[str, Any]] | None = None,
    ) -> tuple[int, str, FakeClient, str]:
        self.write_event(event)
        client = FakeClient(pages if pages is not None else [page(commits)], files)
        options = check_dco.Options(
            repo="kingside33/littrace",
            event_name=event_name,
            event_path=str(self.event_path),
            token="test-token",
            summary_path=str(self.summary_path),
            bot_allowlist_path=bot_allowlist,
        )
        output = io.StringIO()
        with redirect_stdout(output):
            code = check_dco.run_check(options, client=client)
        summary = self.summary_path.read_text(encoding="utf-8") if self.summary_path.exists() else ""
        return code, output.getvalue(), client, summary

    def test_missing_trailer_exits_1_and_prints_three_commands(self) -> None:
        code, output, _client, summary = self.run_check([unsigned("a" * 40)])
        self.assertEqual(code, 1)
        for command in check_dco.FIX_COMMANDS:
            self.assertIn(command, output)
        self.assertIn("fail", summary)
        self.assertIn("missing Signed-off-by", summary)

    def test_email_mismatch_exits_1(self) -> None:
        raw = signed("b" * 40, email="alice@example.com")
        raw["commit"]["message"] = "Change\n\nSigned-off-by: Bob <bob@example.com>\n"
        code, output, _client, _summary = self.run_check([raw])
        self.assertEqual(code, 1)
        self.assertIn("does not match author", output)

    def test_multiple_commits_one_bad_exits_1(self) -> None:
        code, _output, _client, summary = self.run_check([signed("c" * 40), unsigned("d" * 40)])
        self.assertEqual(code, 1)
        self.assertIn("1 of 2", summary)

    def test_merge_commit_is_not_judged(self) -> None:
        code, _output, _client, summary = self.run_check([merge(), signed("e" * 40)])
        self.assertEqual(code, 0)
        self.assertIn("| Merge commits skipped | 1 |", summary)

    def test_allowlisted_bot_is_skipped_by_login_and_id(self) -> None:
        bot = {
            "sha": "f" * 40,
            "commit": {
                "message": "Bump dependency\n",
                "author": {"name": "dependabot[bot]", "email": "49699333+dependabot[bot]@users.noreply.github.com"},
            },
            "author": {"login": "dependabot[bot]", "id": 49699333},
            "parents": [{"sha": "parent"}],
        }
        allowlist = self.root / "bots.json"
        allowlist.write_text(
            json.dumps(
                {
                    "dependabot[bot]": {
                        "account_id": 49699333,
                        "reason": "dependency updates opened by the repository bot",
                    }
                }
            ),
            encoding="utf-8",
        )
        code, _output, _client, summary = self.run_check([bot], bot_allowlist=str(allowlist))
        self.assertEqual(code, 0)
        self.assertIn("| Allowlisted bots skipped | 1 |", summary)

    def test_bot_with_wrong_account_id_is_not_exempt(self) -> None:
        bot = {
            "sha": "1" * 40,
            "commit": {
                "message": "Bump dependency\n",
                "author": {"name": "dependabot[bot]", "email": "bot@example.com"},
            },
            "author": {"login": "dependabot[bot]", "id": 999},
            "parents": [{"sha": "parent"}],
        }
        allowlist = self.root / "bots.json"
        allowlist.write_text(
            json.dumps({"dependabot[bot]": {"account_id": 49699333, "reason": "dependency updates"}}),
            encoding="utf-8",
        )
        code, _output, _client, _summary = self.run_check([bot], bot_allowlist=str(allowlist))
        self.assertEqual(code, 1)

    def test_default_allowlist_covers_dependabot(self) -> None:
        bot = {
            "sha": "8" * 40,
            "commit": {
                "message": "Bump actions/checkout from 4 to 5\n",
                "author": {"name": "dependabot[bot]", "email": "49699333+dependabot[bot]@users.noreply.github.com"},
            },
            "author": {"login": "dependabot[bot]", "id": 49699333},
            "parents": [{"sha": "parent"}],
        }
        code, _output, _client, summary = self.run_check([bot])
        self.assertEqual(code, 0)
        self.assertIn("| Allowlisted bots skipped | 1 |", summary)

    def test_pagination_above_250_commits_checks_every_page(self) -> None:
        commits = [signed(f"{index:040x}") for index in range(300)]
        pages = [
            page(commits[0:100], total=300),
            page(commits[100:200], total=300),
            page(commits[200:300], total=300),
        ]
        code, _output, client, summary = self.run_check([], pages=pages)
        self.assertEqual(code, 0)
        self.assertEqual(len([request for request in client.requests if "/compare/" in request[0]]), 3)
        self.assertIn("| Commits checked | 300 |", summary)

    def test_incomplete_pagination_fails_closed(self) -> None:
        commits = [signed(f"{index:040x}") for index in range(100)]
        code, output, _client, _summary = self.run_check([], pages=[page(commits, total=300)])
        self.assertEqual(code, 2)
        self.assertIn("refuses to pass commits it could not inspect", output)

    def test_merge_group_is_not_applicable_and_succeeds(self) -> None:
        event = {"repository": {"default_branch": "main"}}
        code, output, client, summary = self.run_check([], event_name="merge_group", event=event)
        self.assertEqual(code, 0)
        self.assertIn("not_applicable", output)
        self.assertIn("`not_applicable`", summary)
        self.assertEqual([request for request in client.requests if "/compare/" in request[0]], [])

    def test_coauthored_by_does_not_replace_a_signoff(self) -> None:
        raw = unsigned("2" * 40, message="Change\n\nCo-authored-by: Bob <bob@example.com>\n")
        code, _output, _client, _summary = self.run_check([raw])
        self.assertEqual(code, 1)

    def test_signoff_email_is_case_insensitive(self) -> None:
        raw = signed("3" * 40, email="Alice@Example.com")
        code, _output, _client, _summary = self.run_check([raw])
        self.assertEqual(code, 0)

    def test_default_branch_files_and_terms_are_recorded(self) -> None:
        code, _output, client, summary = self.run_check([signed("4" * 40)])
        self.assertEqual(code, 0)
        self.assertEqual(
            client.file_requests,
            [("kingside33/littrace", "main", "CONTRIBUTING.md"), ("kingside33/littrace", "main", "DCO")],
        )
        self.assertIn("`v1` (effective 2026-11-17)", summary)
        self.assertIn("contributing-blob-sha", summary)
        self.assertIn("dco-blob-sha", summary)

    def test_missing_terms_declaration_is_a_configuration_error(self) -> None:
        files = {
            "CONTRIBUTING.md": ("contributing-blob-sha", "# Contributing\n\nNo version line here.\n"),
            "DCO": ("dco-blob-sha", DCO),
        }
        code, output, _client, _summary = self.run_check([signed("5" * 40)], files=files)
        self.assertEqual(code, 2)
        self.assertIn("contribution-terms", output)

    def test_empty_commit_range_fails_closed(self) -> None:
        code, output, _client, _summary = self.run_check([], pages=[page([], total=0)])
        self.assertEqual(code, 2)
        self.assertIn("no commits", output)

    def test_api_error_is_a_configuration_error(self) -> None:
        class BrokenClient(FakeClient):
            def get_json(self, path: str, params: dict[str, Any] | None = None) -> tuple[Any, dict[str, str]]:
                raise check_dco.ConfigError("GitHub API returned 502")

        self.write_event()
        options = check_dco.Options(
            repo="kingside33/littrace",
            event_name="pull_request",
            event_path=str(self.event_path),
            token="test-token",
            summary_path=str(self.summary_path),
        )
        output = io.StringIO()
        with redirect_stdout(output):
            code = check_dco.run_check(options, client=BrokenClient([page([signed("6" * 40)])]))
        self.assertEqual(code, 2)

    def test_missing_signoff_lists_exactly_three_commands(self) -> None:
        code, output, _client, _summary = self.run_check([unsigned("7" * 40)])
        self.assertEqual(code, 1)
        commands = [line.strip() for line in output.splitlines() if line.strip().startswith("git ")]
        self.assertEqual(commands, list(check_dco.FIX_COMMANDS))


if __name__ == "__main__":
    unittest.main()
