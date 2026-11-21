#!/usr/bin/env python3
"""Aggregate the CI and CodeQL job results into one stable gate.

The gate exists so branch protection can require one name per workflow instead
of every matrix-expanded check name. The closed set is indexed by gate name and
contains job ids, not the names GitHub shows for matrix legs; GitHub already
aggregates a matrix into one job conclusion.

The CI gate has two lanes:

* the fast lane runs on every event;
* the heavy lane (``pytest-platforms`` and ``package-smoke``) runs only on
  ``merge_group`` or on a pull request carrying the ``full-ci`` label.

On an ordinary pull request every heavy job is skipped, and the gate reports
``deferred`` with exit code 0. On ``full-ci`` and ``merge_group`` a skipped
heavy job is a failure: the queue must never qualify a tree that skipped the
heavy lane. On ``push`` the DCO and heavy jobs are skipped by design, so they
are optional there.

The script uses only the standard library and is compatible with Python 3.10.
It is executed with ``python -I`` so a planted module in the checkout cannot
shadow an import.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Sequence

# Job ids, not matrix-expanded check names.
GATES: dict[str, tuple[str, ...]] = {
    "ci": (
        "python-lint",
        "pytest",
        "frontend-tests",
        "plugin-build",
        "dco",
        "actionlint",
        "pytest-platforms",
        "package-smoke",
    ),
    "codeql": ("analyze",),
}

# The fast lane is required on every event except that DCO is skipped on push.
CI_FAST = (
    "python-lint",
    "pytest",
    "frontend-tests",
    "plugin-build",
    "actionlint",
)
CI_DCO = ("dco",)
CI_HEAVY = ("pytest-platforms", "package-smoke")

EVENTS = ("pull_request", "merge_group", "push", "workflow_dispatch")
KNOWN_RESULTS = frozenset(
    {"success", "failure", "cancelled", "skipped", "neutral", "stale"}
)

PASS = "pass"
DEFERRED = "deferred"
FAIL = "fail"
ERROR = "error"

USAGE = (
    "usage: aggregate_gate.py --gate <ci|codeql> --event <event> "
    "--needs-json <json> [--full-ci true|false]"
)


@dataclass(frozen=True)
class Verdict:
    status: str
    gate: str
    event: str
    reason: str
    results: dict[str, str]

    @property
    def exit_code(self) -> int:
        if self.status == ERROR:
            return 2
        if self.status == FAIL:
            return 1
        return 0

    def as_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "gate": self.gate,
            "event": self.event,
            "reason": self.reason,
            "results": self.results,
        }


class HelpRequested(Exception):
    """Raised when the caller asks for usage instead of a verdict."""


def parse_args(argv: Sequence[str]) -> dict[str, str]:
    """Parse the small CLI without argparse's exit-on-error behaviour.

    The gate must print exactly one JSON line on every path, including a bad
    invocation, so argparse's default ``SystemExit`` would break the contract.
    """

    values = {"gate": "", "event": "", "needs_json": "", "full_ci": "false"}
    index = 0
    while index < len(argv):
        argument = argv[index]
        if argument in ("-h", "--help"):
            raise HelpRequested
        if argument not in ("--gate", "--event", "--needs-json", "--full-ci"):
            raise ValueError(f"unknown argument: {argument}")
        if index + 1 >= len(argv):
            raise ValueError(f"missing value for {argument}")
        key = argument[2:].replace("-", "_")
        values[key] = argv[index + 1]
        index += 2

    for key, flag in (("gate", "--gate"), ("event", "--event"), ("needs_json", "--needs-json")):
        if not values[key]:
            raise ValueError(f"missing {flag}")
    return values


def _error(gate: str, event: str, reason: str) -> Verdict:
    return Verdict(status=ERROR, gate=gate, event=event, reason=reason, results={})


def _validate_needs(gate: str, event: str, needs: Any) -> tuple[dict[str, str] | None, Verdict | None]:
    if gate not in GATES:
        return None, _error(gate, event, "unknown_gate")
    if event not in EVENTS:
        return None, _error(gate, event, "unknown_event")
    if not isinstance(needs, dict):
        return None, _error(gate, event, "invalid_input")

    expected = GATES[gate]
    results: dict[str, str] = {}
    for job in expected:
        if job not in needs:
            return None, _error(gate, event, "missing_jobs")
        entry = needs[job]
        if not isinstance(entry, dict) or "result" not in entry:
            return None, _error(gate, event, "invalid_input")
        results[job] = entry["result"]  # type: ignore[assignment]

    for job, entry in needs.items():
        if job in expected:
            continue
        if not isinstance(entry, dict) or "result" not in entry:
            return None, _error(gate, event, "invalid_input")
        results[job] = entry["result"]  # type: ignore[assignment]

    return results, None


def _is_bad_result(result: Any) -> bool:
    return not isinstance(result, str) or result not in KNOWN_RESULTS


def evaluate(
    gate: str,
    event: str,
    needs: Any,
    *,
    full_ci: bool = False,
) -> Verdict:
    """Return the gate verdict for one event.

    ``needs`` is the object produced by ``toJSON(needs)`` in the workflow.
    """

    results, error = _validate_needs(gate, event, needs)
    if error is not None:
        return error
    assert results is not None

    extra = [job for job in results if job not in GATES[gate]]
    for job in results:
        if _is_bad_result(results[job]):
            return Verdict(FAIL, gate, event, "upstream_not_success", results)
    for job in results:
        if results[job] in {"failure", "cancelled", "neutral", "stale"}:
            return Verdict(FAIL, gate, event, "upstream_not_success", results)
    for job in extra:
        if results[job] != "success":
            return Verdict(FAIL, gate, event, "extra_job_not_success", results)

    if gate == "codeql":
        if results["analyze"] != "success":
            return Verdict(FAIL, gate, event, "required_skipped", results)
        return Verdict(PASS, gate, event, "all_success", results)

    if event in {"push", "workflow_dispatch"}:
        # DCO and the heavy lane are skipped on push by design.
        required = CI_FAST
    elif event == "pull_request" and not full_ci:
        required = CI_FAST + CI_DCO
    else:
        # full-ci pull requests and merge_group entries must run the heavy lane.
        required = CI_FAST + CI_DCO + CI_HEAVY

    for job in required:
        if results[job] == "success":
            continue
        if job in CI_HEAVY and results[job] == "skipped":
            return Verdict(FAIL, gate, event, "heavy_required_skipped", results)
        return Verdict(FAIL, gate, event, "required_skipped", results)

    if event == "pull_request" and not full_ci:
        heavy_skipped = [job for job in CI_HEAVY if results[job] == "skipped"]
        if len(heavy_skipped) == len(CI_HEAVY):
            return Verdict(DEFERRED, gate, event, "heavy_deferred", results)
        # A half-run heavy lane is worse than a fully skipped one: it looks
        # qualified while one of the two platform/package legs never ran.
        if heavy_skipped:
            return Verdict(FAIL, gate, event, "partial_heavy", results)

    return Verdict(PASS, gate, event, "all_success", results)


def _write_summary(verdict: Verdict) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if not path:
        return
    lines = [
        "## Aggregate gate",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| gate | {verdict.gate} |",
        f"| event | {verdict.event} |",
        f"| status | {verdict.status} |",
        f"| reason | {verdict.reason} |",
        "",
        "| Job | Result |",
        "| --- | --- |",
    ]
    for job, result in verdict.results.items():
        lines.append(f"| {job} | {result} |")
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines).rstrip() + "\n")
    except OSError as exc:
        print(f"::warning::Could not write the job summary: {exc}", file=sys.stderr)


def _emit(verdict: Verdict) -> int:
    _write_summary(verdict)
    print(json.dumps(verdict.as_json(), ensure_ascii=False, sort_keys=False))
    return verdict.exit_code


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        options = parse_args(arguments)
    except HelpRequested:
        print(USAGE, file=sys.stderr)
        return 0
    except ValueError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return _emit(_error("", "", "invalid_input"))

    try:
        needs = json.loads(options["needs_json"])
    except json.JSONDecodeError as exc:
        print(f"::error::needs JSON is not valid: {exc}", file=sys.stderr)
        return _emit(_error(options["gate"], options["event"], "invalid_input"))

    full_ci = options["full_ci"].strip().lower() == "true"
    verdict = evaluate(options["gate"], options["event"], needs, full_ci=full_ci)
    return _emit(verdict)


if __name__ == "__main__":
    raise SystemExit(main())
