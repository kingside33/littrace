"""Guard tests for the contribution-authorization policy (#52).

The guard has three jobs:

* the real workflows must not wire in a CLA or a signing service;
* the DCO job must stay in place and keep reading the default branch;
* ``docs/legal/contribution-policy.md`` must keep its decision anchors.

The scanner takes a workflow directory, so the self-tests can point it at a
temporary copy and prove it is not a no-op. The real workflow files are test
inputs and are parsed with ``yaml.safe_load``; nothing here touches the network.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
POLICY = REPO_ROOT / "docs" / "legal" / "contribution-policy.md"
CONTRIBUTING = REPO_ROOT / "CONTRIBUTING.md"


class Violation(NamedTuple):
    """One CLA or signing-service signal found in a workflow."""

    source: str
    field: str
    rule: str
    text: str


# Each rule names a way to wire a CLA or a signing service into CI. The rules
# are intentionally narrow: this guard is not a general third-party action
# allowlist, and it must not fail on unrelated actions.
STRING_RULES = (
    ("contributor_assistant", re.compile(r"contributor-assistant", re.IGNORECASE)),
    ("cla_assistant", re.compile(r"cla-assistant", re.IGNORECASE)),
    ("cla_role", re.compile(r"\bCLA[-_ ]?(?:assistant|bot|service|signing)\b", re.IGNORECASE)),
    ("cla_env", re.compile(r"\bCLA_[A-Z0-9_]+\b", re.IGNORECASE)),
    ("signing_service", re.compile(r"(?:signature|signing)[-_ ]service", re.IGNORECASE)),
    (
        "contributor_license_agreement",
        re.compile(r"contributor[-_ ]license[-_ ]agreement", re.IGNORECASE),
    ),
)

# A `uses:` path with a standalone `cla` segment, for example
# `some-org/cla-action@v1`. The segment boundaries keep names such as
# `actions/checkout` from matching.
USES_CLA_SEGMENT = re.compile(r"(?:^|[/_.-])cla(?:[/_.-]|$)", re.IGNORECASE)


def _scan_text(
    source: str, field: str, text: object, violations: list[Violation]
) -> None:
    if not isinstance(text, str):
        return
    for rule, pattern in STRING_RULES:
        if pattern.search(text):
            violations.append(Violation(source, field, rule, text))


def _scan_uses(
    source: str, field: str, text: object, violations: list[Violation]
) -> None:
    _scan_text(source, field, text, violations)
    if isinstance(text, str) and USES_CLA_SEGMENT.search(text):
        violations.append(Violation(source, field, "uses_cla_segment", text))


def _scan_env(
    source: str, field: str, env: object, violations: list[Violation]
) -> None:
    if not isinstance(env, dict):
        return
    for key, value in env.items():
        _scan_text(source, f"{field}.{key}", key, violations)
        _scan_text(source, f"{field}.{key}", value, violations)


def _scan_run(
    source: str, field: str, script: object, violations: list[Violation]
) -> None:
    if not isinstance(script, str):
        return
    for line_number, line in enumerate(script.splitlines(), start=1):
        # YAML comments inside a run block never execute; skip them so the
        # "DCO replaces a CLA" comment in ci.yml does not trip the guard.
        if line.lstrip().startswith("#"):
            continue
        _scan_text(source, f"{field}:{line_number}", line, violations)


def scan_workflow_document(document: object, source: str) -> list[Violation]:
    """Return every CLA or signing-service signal in a parsed workflow."""
    violations: list[Violation] = []
    if not isinstance(document, dict):
        return [Violation(source, "document", "malformed_workflow", repr(document))]

    _scan_text(source, "name", document.get("name"), violations)

    jobs = document.get("jobs")
    if not isinstance(jobs, dict):
        return [Violation(source, "jobs", "malformed_workflow", repr(jobs))]

    for job_id, job in jobs.items():
        job_field = f"jobs.{job_id}"
        if not isinstance(job, dict):
            violations.append(Violation(source, job_field, "malformed_workflow", repr(job)))
            continue

        _scan_text(source, f"{job_field}.name", job.get("name"), violations)
        _scan_env(source, f"{job_field}.env", job.get("env"), violations)

        steps = job.get("steps")
        if not isinstance(steps, list):
            continue
        for index, step in enumerate(steps):
            step_field = f"{job_field}.steps[{index}]"
            if not isinstance(step, dict):
                violations.append(
                    Violation(source, step_field, "malformed_workflow", repr(step))
                )
                continue
            _scan_uses(source, f"{step_field}.uses", step.get("uses"), violations)
            _scan_text(source, f"{step_field}.name", step.get("name"), violations)
            _scan_env(source, f"{step_field}.env", step.get("env"), violations)
            _scan_run(source, f"{step_field}.run", step.get("run"), violations)

    return violations


def scan_workflows(directory: Path = WORKFLOW_DIR) -> list[Violation]:
    """Parse and scan every ``*.yml`` workflow in *directory*."""
    violations: list[Violation] = []
    for path in sorted(directory.glob("*.yml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        violations.extend(scan_workflow_document(document, path.name))
    return violations


def _format(violations: list[Violation]) -> str:
    return "\n".join(
        f"{item.source}:{item.field} [{item.rule}] {item.text}" for item in violations
    )


def _write_workflow(directory: Path, text: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "ci.yml"
    path.write_text(text, encoding="utf-8")
    return path


# --- real workflows -------------------------------------------------------


def test_all_real_workflows_parse_and_have_jobs() -> None:
    paths = sorted(WORKFLOW_DIR.glob("*.yml"))
    assert paths, "expected at least one workflow in .github/workflows"
    for path in paths:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert isinstance(document, dict), path.name
        assert isinstance(document.get("jobs"), dict), path.name


def test_real_workflows_have_no_cla_or_signing_wiring() -> None:
    violations = scan_workflows()
    assert violations == [], _format(violations)


def test_dco_job_still_runs_the_script_from_the_default_branch() -> None:
    document = yaml.safe_load((WORKFLOW_DIR / "ci.yml").read_text(encoding="utf-8"))
    job = document["jobs"]["dco"]
    steps = job["steps"]

    runs = [step.get("run", "") for step in steps if isinstance(step, dict)]
    assert any("python scripts/ci/check_dco.py" in run for run in runs), (
        "the dco job must run scripts/ci/check_dco.py"
    )

    checkouts = [
        step
        for step in steps
        if str(step.get("uses", "")).startswith("actions/checkout")
    ]
    assert checkouts, "the dco job must check out the default branch"
    refs = [step.get("with", {}).get("ref") for step in checkouts]
    assert "${{ github.event.repository.default_branch }}" in refs


def test_contributing_still_declares_no_cla_and_the_v1_terms() -> None:
    text = CONTRIBUTING.read_text(encoding="utf-8")
    assert "does not use a CLA" in text
    assert "<!-- contribution-terms: v1;" in text


# --- policy document anchors ---------------------------------------------

POLICY_ANCHORS = (
    "Status: accepted",
    "2026-11-19",
    "201cf72",
    "Contribution terms: v1 (effective 2026-11-17)",
    "## Decision",
    "## Why",
    "## Known gap",
    "## Changing this policy",
    "does not use a CLA",
    "DCO",
    "v1",
    "signing service",
    "CONTRIBUTING.md",
    "git log main",
)


@pytest.mark.parametrize("anchor", POLICY_ANCHORS)
def test_policy_document_keeps_decision_anchor(anchor: str) -> None:
    assert POLICY.is_file(), f"missing {POLICY}"
    assert anchor in POLICY.read_text(encoding="utf-8")


# --- guard self-tests -----------------------------------------------------

INJECTED_ACTION = """\
name: guard self-test
on: pull_request
jobs:
  sign:
    runs-on: ubuntu-latest
    steps:
      - uses: contributor-assistant/github-action@v2
"""

INJECTED_SIGNING_RUN = """\
name: guard self-test
on: pull_request
jobs:
  sign:
    runs-on: ubuntu-latest
    steps:
      - name: Record the sign-off
        run: |
          curl https://cla-assistant.io/sign
"""

CLEAN_WORKFLOW = """\
name: guard self-test
on: pull_request
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - name: Run tests
        run: |
          # DCO replaces a CLA; this comment must not trip the guard.
          echo ok
"""


def test_guard_flags_contributor_assistant_action(tmp_path: Path) -> None:
    _write_workflow(tmp_path, INJECTED_ACTION)
    violations = scan_workflows(tmp_path)
    assert violations, "contributor-assistant action was not flagged"
    assert any(item.rule == "contributor_assistant" for item in violations)
    assert any(item.field.endswith(".uses") for item in violations)


def test_guard_flags_cla_assistant_signing_curl(tmp_path: Path) -> None:
    _write_workflow(tmp_path, INJECTED_SIGNING_RUN)
    violations = scan_workflows(tmp_path)
    assert violations, "cla-assistant.io call was not flagged"
    assert any(item.rule == "cla_assistant" for item in violations)
    # Exact-line equality, not a URL substring match: CodeQL flags
    # `"host" in url` as incomplete URL sanitization, and the injected line is
    # known verbatim, so a substring check buys nothing here.
    assert any(item.text == "curl https://cla-assistant.io/sign" for item in violations)


def test_guard_flags_cla_env_key(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        """\
name: guard self-test
on: pull_request
jobs:
  sign:
    runs-on: ubuntu-latest
    env:
      CLA_SIGNING_TOKEN: example
    steps:
      - run: echo ok
""",
    )
    violations = scan_workflows(tmp_path)
    assert violations, "CLA_* environment key was not flagged"
    assert any(item.rule == "cla_env" for item in violations)


def test_guard_flags_uses_path_with_cla_segment(tmp_path: Path) -> None:
    _write_workflow(
        tmp_path,
        """\
name: guard self-test
on: pull_request
jobs:
  sign:
    runs-on: ubuntu-latest
    steps:
      - uses: some-org/cla-action@v1
""",
    )
    violations = scan_workflows(tmp_path)
    assert violations, "a uses path with a cla segment was not flagged"
    assert any(item.rule == "uses_cla_segment" for item in violations)


def test_guard_skips_comment_lines_and_clean_copy_returns_empty(
    tmp_path: Path,
) -> None:
    _write_workflow(tmp_path, CLEAN_WORKFLOW)
    assert scan_workflows(tmp_path) == []


def test_guard_reports_a_malformed_document_instead_of_passing() -> None:
    violations = scan_workflow_document(["not", "a", "mapping"], "broken.yml")
    assert violations
    assert violations[0].rule == "malformed_workflow"


def test_guard_fails_closed_on_unparseable_yaml(tmp_path: Path) -> None:
    _write_workflow(tmp_path, "jobs: [unclosed\n")
    with pytest.raises(yaml.YAMLError):
        scan_workflows(tmp_path)
