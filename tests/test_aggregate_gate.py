"""Tests for the aggregate gate in scripts/ci/aggregate_gate.py.

The gate is pure logic: these tests feed it the ``toJSON(needs)`` shape the
workflow produces and assert the exit code plus the one-line JSON verdict. They
never touch the network and never run anything from a pull request.

The lane cases are the contract that matters:

* an ordinary pull request with every heavy job skipped is ``deferred`` (exit 0);
* a ``full-ci`` pull request or a merge-queue entry with a skipped heavy job is
  a failure, not a deferred pass;
* a half-run heavy lane is a failure even on an ordinary pull request.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "ci" / "aggregate_gate.py"

_spec = importlib.util.spec_from_file_location("aggregate_gate", MODULE_PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover - broken checkout
    raise RuntimeError(f"cannot load {MODULE_PATH}")
aggregate_gate = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = aggregate_gate
_spec.loader.exec_module(aggregate_gate)


def ci_needs(**overrides: Any) -> dict[str, Any]:
    """A full CI needs object; overrides replace a job's result."""

    needs = {job: {"result": "success"} for job in aggregate_gate.GATES["ci"]}
    for job, result in overrides.items():
        needs[job] = {"result": result}
    return needs


def run_gate(
    *args: str,
    needs: Any | None = None,
    needs_json: str | None = None,
    summary: str = "",
) -> tuple[int, dict[str, Any] | None, str]:
    if needs_json is None:
        needs_json = json.dumps(ci_needs() if needs is None else needs)
    stdout = io.StringIO()
    stderr = io.StringIO()
    environment = {"GITHUB_STEP_SUMMARY": summary}
    with (
        mock.patch.dict(os.environ, environment, clear=False),
        redirect_stdout(stdout),
        redirect_stderr(stderr),
    ):
        code = aggregate_gate.main([*args, "--needs-json", needs_json])
    lines = [line for line in stdout.getvalue().splitlines() if line.strip()]
    payload = json.loads(lines[-1]) if lines else None
    return code, payload, stderr.getvalue()


class CiGateTests(unittest.TestCase):
    def test_all_success_on_pull_request(self) -> None:
        code, payload, _ = run_gate("--gate", "ci", "--event", "pull_request")
        self.assertEqual(code, 0)
        assert payload is not None
        self.assertEqual(payload["status"], "pass")
        self.assertEqual(payload["reason"], "all_success")

    def test_single_failure(self) -> None:
        code, payload, _ = run_gate(
            "--gate", "ci", "--event", "pull_request", needs=ci_needs(pytest="failure")
        )
        self.assertEqual(code, 1)
        assert payload is not None
        self.assertEqual(payload["status"], "fail")
        self.assertEqual(payload["reason"], "upstream_not_success")

    def test_cancelled_is_failure(self) -> None:
        code, payload, _ = run_gate(
            "--gate", "ci", "--event", "pull_request", needs=ci_needs(pytest="cancelled")
        )
        self.assertEqual(code, 1)
        assert payload is not None
        self.assertEqual(payload["status"], "fail")

    def test_required_job_skipped_is_failure(self) -> None:
        code, payload, _ = run_gate(
            "--gate", "ci", "--event", "pull_request", needs=ci_needs(pytest="skipped")
        )
        self.assertEqual(code, 1)
        assert payload is not None
        self.assertEqual(payload["reason"], "required_skipped")

    def test_ordinary_pr_with_heavy_skipped_is_deferred(self) -> None:
        code, payload, _ = run_gate(
            "--gate",
            "ci",
            "--event",
            "pull_request",
            needs=ci_needs(**{"pytest-platforms": "skipped", "package-smoke": "skipped"}),
        )
        self.assertEqual(code, 0)
        assert payload is not None
        self.assertEqual(payload["status"], "deferred")
        self.assertEqual(payload["reason"], "heavy_deferred")

    def test_ordinary_pr_with_half_heavy_lane_is_failure(self) -> None:
        code, payload, _ = run_gate(
            "--gate",
            "ci",
            "--event",
            "pull_request",
            needs=ci_needs(**{"pytest-platforms": "skipped", "package-smoke": "success"}),
        )
        self.assertEqual(code, 1)
        assert payload is not None
        self.assertEqual(payload["reason"], "partial_heavy")

    def test_full_ci_with_heavy_skipped_is_failure(self) -> None:
        code, payload, _ = run_gate(
            "--gate",
            "ci",
            "--event",
            "pull_request",
            "--full-ci",
            "true",
            needs=ci_needs(**{"pytest-platforms": "skipped", "package-smoke": "skipped"}),
        )
        self.assertEqual(code, 1)
        assert payload is not None
        self.assertEqual(payload["reason"], "heavy_required_skipped")

    def test_merge_group_with_heavy_skipped_is_failure(self) -> None:
        code, payload, _ = run_gate(
            "--gate",
            "ci",
            "--event",
            "merge_group",
            needs=ci_needs(**{"pytest-platforms": "skipped", "package-smoke": "skipped"}),
        )
        self.assertEqual(code, 1)
        assert payload is not None
        self.assertEqual(payload["reason"], "heavy_required_skipped")

    def test_push_allows_dco_and_heavy_to_be_skipped(self) -> None:
        code, payload, _ = run_gate(
            "--gate",
            "ci",
            "--event",
            "push",
            needs=ci_needs(**{"dco": "skipped", "pytest-platforms": "skipped", "package-smoke": "skipped"}),
        )
        self.assertEqual(code, 0)
        assert payload is not None
        self.assertEqual(payload["status"], "pass")

    def test_missing_job_is_an_error(self) -> None:
        needs = ci_needs()
        del needs["dco"]
        code, payload, _ = run_gate("--gate", "ci", "--event", "pull_request", needs=needs)
        self.assertEqual(code, 2)
        assert payload is not None
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["reason"], "missing_jobs")

    def test_extra_successful_job_is_allowed(self) -> None:
        needs = ci_needs(**{"future-job": "success"})
        code, payload, _ = run_gate("--gate", "ci", "--event", "pull_request", needs=needs)
        self.assertEqual(code, 0)
        assert payload is not None
        self.assertEqual(payload["status"], "pass")

    def test_extra_failing_job_is_failure(self) -> None:
        needs = ci_needs(**{"future-job": "failure"})
        code, payload, _ = run_gate("--gate", "ci", "--event", "pull_request", needs=needs)
        self.assertEqual(code, 1)
        assert payload is not None
        self.assertEqual(payload["reason"], "upstream_not_success")

    def test_unknown_result_string_is_failure(self) -> None:
        code, payload, _ = run_gate(
            "--gate", "ci", "--event", "pull_request", needs=ci_needs(pytest="mystery")
        )
        self.assertEqual(code, 1)
        assert payload is not None
        self.assertEqual(payload["reason"], "upstream_not_success")

    def test_null_result_is_failure(self) -> None:
        code, payload, _ = run_gate(
            "--gate", "ci", "--event", "pull_request", needs=ci_needs(pytest=None)
        )
        self.assertEqual(code, 1)
        assert payload is not None
        self.assertEqual(payload["status"], "fail")

    def test_missing_result_key_is_error(self) -> None:
        needs = ci_needs()
        needs["pytest"] = {}
        code, payload, _ = run_gate("--gate", "ci", "--event", "pull_request", needs=needs)
        self.assertEqual(code, 2)
        assert payload is not None
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["reason"], "invalid_input")

    def test_invalid_json_is_error(self) -> None:
        code, payload, _ = run_gate(
            "--gate", "ci", "--event", "pull_request", needs_json="{"
        )
        self.assertEqual(code, 2)
        assert payload is not None
        self.assertEqual(payload["status"], "error")
        self.assertEqual(payload["reason"], "invalid_input")

    def test_unknown_gate_is_error(self) -> None:
        code, payload, _ = run_gate("--gate", "nope", "--event", "pull_request")
        self.assertEqual(code, 2)
        assert payload is not None
        self.assertEqual(payload["reason"], "unknown_gate")

    def test_unknown_event_is_error(self) -> None:
        code, payload, _ = run_gate("--gate", "ci", "--event", "nope")
        self.assertEqual(code, 2)
        assert payload is not None
        self.assertEqual(payload["reason"], "unknown_event")


class CodeQlGateTests(unittest.TestCase):
    def test_codeql_success(self) -> None:
        code, payload, _ = run_gate(
            "--gate", "codeql", "--event", "pull_request", needs={"analyze": {"result": "success"}}
        )
        self.assertEqual(code, 0)
        assert payload is not None
        self.assertEqual(payload["status"], "pass")

    def test_codeql_workflow_dispatch_is_valid(self) -> None:
        code, payload, _ = run_gate(
            "--gate",
            "codeql",
            "--event",
            "workflow_dispatch",
            needs={"analyze": {"result": "success"}},
        )
        self.assertEqual(code, 0)
        assert payload is not None
        self.assertEqual(payload["status"], "pass")

    def test_codeql_missing_analyze_is_error(self) -> None:
        code, payload, _ = run_gate("--gate", "codeql", "--event", "pull_request", needs={})
        self.assertEqual(code, 2)
        assert payload is not None
        self.assertEqual(payload["reason"], "missing_jobs")

    def test_codeql_skipped_is_failure(self) -> None:
        code, payload, _ = run_gate(
            "--gate",
            "codeql",
            "--event",
            "merge_group",
            needs={"analyze": {"result": "skipped"}},
        )
        self.assertEqual(code, 1)
        assert payload is not None
        self.assertEqual(payload["reason"], "required_skipped")


class OutputTests(unittest.TestCase):
    def test_summary_is_written(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / "summary.md"
            code, _, stderr = run_gate(
                "--gate",
                "ci",
                "--event",
                "pull_request",
                needs=ci_needs(**{"pytest-platforms": "skipped", "package-smoke": "skipped"}),
                summary=str(summary),
            )
            self.assertEqual(code, 0)
            self.assertEqual(stderr, "")
            text = summary.read_text(encoding="utf-8")
            self.assertIn("Aggregate gate", text)
            self.assertIn("heavy_deferred", text)
            self.assertIn("pytest-platforms", text)

    def test_summary_write_failure_does_not_change_exit_code_or_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            code, payload, stderr = run_gate(
                "--gate",
                "ci",
                "--event",
                "pull_request",
                needs=ci_needs(**{"pytest-platforms": "skipped", "package-smoke": "skipped"}),
                summary=directory,
            )
            self.assertEqual(code, 0)
            assert payload is not None
            self.assertEqual(payload["status"], "deferred")
            self.assertIn("::warning::", stderr)

    def test_stdout_is_exactly_one_json_line_on_error(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": ""}, clear=False),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            code = aggregate_gate.main(["--gate", "nope", "--event", "push", "--needs-json", "{}"])
        self.assertEqual(code, 2)
        lines = [line for line in stdout.getvalue().splitlines() if line.strip()]
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["status"], "error")


if __name__ == "__main__":
    unittest.main()
