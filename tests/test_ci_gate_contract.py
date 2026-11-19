"""Contract tests for the aggregate gate wiring.

The gate script is pure logic; these tests pin the workflow side of the
contract:

* the gate job names and their exact ``needs`` closed sets;
* the heavy jobs only run on ``merge_group`` or a ``full-ci`` pull request;
* ``dco`` runs on merge-queue entries;
* concurrency is grouped by head SHA and never cancels;
* the reusable pytest/package-smoke workflows are the only copies of those
  commands.

PyYAML is a runtime dependency (requirements.txt), so no extra test dependency
is introduced.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "ci" / "aggregate_gate.py"

_spec = importlib.util.spec_from_file_location("aggregate_gate", MODULE_PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover - broken checkout
    raise RuntimeError(f"cannot load {MODULE_PATH}")
aggregate_gate = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = aggregate_gate
_spec.loader.exec_module(aggregate_gate)


def load_workflow(name: str) -> dict[str, Any]:
    path = ROOT / ".github" / "workflows" / name
    with path.open(encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    if not isinstance(loaded, dict):  # pragma: no cover - broken workflow
        raise AssertionError(f"{name} did not parse as a mapping")
    return loaded


def triggers(workflow: dict[str, Any]) -> dict[str, Any]:
    # A quoted "on" stays a string; an unquoted one is YAML 1.1 boolean True.
    value = workflow.get("on")
    if value is None:
        value = workflow.get(True)
    if not isinstance(value, dict):  # pragma: no cover - broken workflow
        raise AssertionError("workflow has no event mapping")
    return value


class CiGateContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ci = load_workflow("ci.yml")
        self.jobs = self.ci["jobs"]

    def test_gate_name_and_needs_are_the_closed_set(self) -> None:
        gate = self.jobs["ci-gate"]
        self.assertEqual(gate["name"], "CI gate")
        self.assertEqual(set(gate["needs"]), set(aggregate_gate.GATES["ci"]))

    def test_gate_always_runs(self) -> None:
        self.assertIn("always()", self.jobs["ci-gate"]["if"])

    def test_heavy_jobs_need_full_ci_or_merge_group(self) -> None:
        for job in ("pytest-platforms", "package-smoke"):
            condition = self.jobs[job]["if"]
            self.assertIn("merge_group", condition)
            self.assertIn("full-ci", condition)

    def test_dco_runs_on_merge_group(self) -> None:
        self.assertIn("merge_group", self.jobs["dco"]["if"])

    def test_events_include_labels_and_merge_group(self) -> None:
        events = triggers(self.ci)
        self.assertIn("merge_group", events)
        pull_request = events["pull_request"]
        self.assertIn("labeled", pull_request["types"])
        self.assertIn("unlabeled", pull_request["types"])

    def test_no_path_filter(self) -> None:
        pull_request = triggers(self.ci)["pull_request"]
        self.assertNotIn("paths", pull_request)
        self.assertNotIn("paths-ignore", pull_request)

    def test_concurrency_is_grouped_by_sha_and_never_cancels(self) -> None:
        concurrency = self.ci["concurrency"]
        group = concurrency["group"]
        self.assertIn("github.event.pull_request.head.sha", group)
        self.assertIn("github.event.merge_group.head_sha", group)
        self.assertIn("github.sha", group)
        self.assertNotIn("github.ref", group)
        self.assertIs(concurrency["cancel-in-progress"], False)

    def test_fast_pytest_is_the_ubuntu_313_leg(self) -> None:
        matrix = self.jobs["pytest"]["strategy"]["matrix"]["include"]
        self.assertEqual(matrix, [{"os": "ubuntu-24.04", "python": "3.13"}])

    def test_heavy_pytest_covers_python_310_and_windows(self) -> None:
        matrix = self.jobs["pytest-platforms"]["strategy"]["matrix"]["include"]
        self.assertIn({"os": "ubuntu-24.04", "python": "3.10"}, matrix)
        self.assertIn({"os": "windows-latest", "python": "3.13"}, matrix)

    def test_pytest_jobs_use_the_reusable_workflow(self) -> None:
        reusable = "./.github/workflows/_pytest.yml"
        self.assertEqual(self.jobs["pytest"]["uses"], reusable)
        self.assertEqual(self.jobs["pytest-platforms"]["uses"], reusable)

    def test_package_smoke_is_shared_with_nightly(self) -> None:
        reusable = "./.github/workflows/_package-smoke.yml"
        self.assertEqual(self.jobs["package-smoke"]["uses"], reusable)
        nightly = load_workflow("nightly.yml")
        self.assertEqual(nightly["jobs"]["package-smoke"]["uses"], reusable)


class CodeQlGateContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.codeql = load_workflow("codeql.yml")
        self.jobs = self.codeql["jobs"]

    def test_gate_name_and_needs_are_the_closed_set(self) -> None:
        gate = self.jobs["codeql-gate"]
        self.assertEqual(gate["name"], "CodeQL gate")
        self.assertEqual(set(gate["needs"]), set(aggregate_gate.GATES["codeql"]))

    def test_gate_always_runs_but_not_on_schedule(self) -> None:
        condition = self.jobs["codeql-gate"]["if"]
        self.assertIn("always()", condition)
        self.assertIn("schedule", condition)

    def test_merge_group_is_an_event(self) -> None:
        self.assertIn("merge_group", triggers(self.codeql))

    def test_concurrency_is_grouped_by_sha_and_never_cancels(self) -> None:
        concurrency = self.codeql["concurrency"]
        group = concurrency["group"]
        self.assertIn("github.event.pull_request.head.sha", group)
        self.assertIn("github.event.merge_group.head_sha", group)
        self.assertIn("github.sha", group)
        self.assertNotIn("github.ref", group)
        self.assertIs(concurrency["cancel-in-progress"], False)

    def test_merge_group_does_not_upload_sarif(self) -> None:
        analyze = self.jobs["analyze"]
        steps = analyze["steps"]
        upload_steps = [
            step
            for step in steps
            if str(step.get("uses", "")).startswith("github/codeql-action/analyze@")
        ]
        self.assertEqual(len(upload_steps), 1)
        upload = upload_steps[0]["with"]["upload"]
        self.assertIn("merge_group", upload)
        self.assertIn("never", upload)


if __name__ == "__main__":
    unittest.main()
