#!/usr/bin/env python3
"""DCO gate for littrace.

The workflow runs this file from the default branch. It never executes code
from the pull request; the only pull-request data it reads is the event payload
and the GitHub compare API. That keeps a pull request from rewriting the rule
that judges it.

Exit codes:
    0   every checked commit is signed off, or the event is a merge queue run
    1   at least one commit is missing a valid sign-off (policy failure)
    2   the check could not run: bad configuration, missing files, API error

The DCO proves provenance. It does not grant the maintainer the right to
sublicense a contribution. That right comes from the separate contribution
terms in CONTRIBUTING.md; see the "Licence and contribution terms" section.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

API_ROOT = "https://api.github.com"
PER_PAGE = 100
# A guard against a broken Link header, not a real limit.
MAX_PAGES = 1000

# Git trailers are case-insensitive. The e-mail is matched against the commit
# author, also case-insensitively.
SIGNOFF = re.compile(
    r"^Signed-off-by:\s*(?P<name>[^<>]+?)\s*<(?P<email>[^<>]+)>\s*$",
    re.IGNORECASE | re.MULTILINE,
)
# CONTRIBUTING.md carries a machine-readable line near the top. The check
# records it in the job summary so every run says which terms version applied.
TERMS = re.compile(
    r"contribution-terms:\s*(?P<version>[A-Za-z0-9._-]+)"
    r"(?:\s*;\s*effective:\s*(?P<date>\d{4}-\d{2}-\d{2}))?",
    re.IGNORECASE,
)

# This is the exact list printed on a policy failure, in the order a
# contributor should try them. Do not add a fourth command.
FIX_COMMANDS = (
    "git commit --amend -s",
    "git rebase --signoff origin/main",
    "git push --force-with-lease",
)

# Bots are skipped only when both the login and the immutable account id match
# an entry here. Matching a "[bot]" suffix is deliberately not enough: a login
# is mutable and the suffix is a convention, not an identity.
#
# Dependabot opens the github-actions bumps configured in .github/dependabot.yml.
# Its commits are created by GitHub and cannot carry a human sign-off; the PR is
# reviewed like any other. The id is from GET /users/dependabot%5Bbot%5D.
BOT_ALLOWLIST: dict[str, dict[str, Any]] = {
    "dependabot[bot]": {
        "account_id": 49699333,
        "reason": "Dependabot opens github-actions version bumps; the PR is reviewed like any other.",
    },
}


class ConfigError(RuntimeError):
    """The check cannot run. The caller exits with status 2."""


@dataclass(frozen=True)
class Commit:
    sha: str
    subject: str
    message: str
    author_email: str
    parents: tuple[str, ...]
    author_login: str
    author_id: int | None


@dataclass(frozen=True)
class Violation:
    sha: str
    subject: str
    problem: str
    author_email: str


@dataclass
class CheckResult:
    checked: int
    merge_commits: int
    bots: int
    violations: list[Violation]


@dataclass(frozen=True)
class Options:
    repo: str
    event_name: str
    event_path: str
    token: str
    default_branch: str = ""
    summary_path: str = ""
    bot_allowlist_path: str = ""


def parse_signoffs(message: str) -> list[tuple[str, str]]:
    """Return every Signed-off-by trailer as (name, e-mail)."""
    found: list[tuple[str, str]] = []
    for match in SIGNOFF.finditer(message):
        name = match.group("name").strip()
        email = match.group("email").strip()
        if name and email:
            found.append((name, email))
    return found


def parse_commit(raw: Mapping[str, Any]) -> Commit:
    commit = raw.get("commit")
    if not isinstance(commit, Mapping):
        raise ConfigError("compare response contained a commit without commit metadata")
    author = commit.get("author")
    if not isinstance(author, Mapping):
        author = {}
    message = commit.get("message")
    if not isinstance(message, str):
        message = ""
    subject = message.splitlines()[0].strip() if message.splitlines() else ""
    parents_raw = raw.get("parents")
    if not isinstance(parents_raw, list):
        parents_raw = []
    parents = tuple(
        parent.get("sha", "")
        for parent in parents_raw
        if isinstance(parent, Mapping) and isinstance(parent.get("sha"), str)
    )
    github_author = raw.get("author")
    if not isinstance(github_author, Mapping):
        github_author = {}
    login = github_author.get("login")
    account_id = github_author.get("id")
    email = author.get("email")
    return Commit(
        sha=str(raw.get("sha") or ""),
        subject=subject,
        message=message,
        author_email=email if isinstance(email, str) else "",
        parents=parents,
        author_login=login if isinstance(login, str) else "",
        author_id=account_id if isinstance(account_id, int) and not isinstance(account_id, bool) else None,
    )


def load_allowlist(path: str) -> dict[str, dict[str, Any]]:
    """Load the bot allowlist. An absent path means an empty allowlist."""
    if not path:
        return dict(BOT_ALLOWLIST)
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot read bot allowlist {path}: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise ConfigError("bot allowlist must be a JSON object keyed by login")
    allowlist: dict[str, dict[str, Any]] = {}
    for login, entry in raw.items():
        if not isinstance(login, str) or not login:
            raise ConfigError("bot allowlist contains an empty or non-string login")
        if not isinstance(entry, Mapping):
            raise ConfigError(f"bot allowlist entry for {login} must be an object")
        account_id = entry.get("account_id")
        reason = entry.get("reason")
        if not isinstance(account_id, int) or isinstance(account_id, bool):
            raise ConfigError(f"bot allowlist entry for {login} needs an integer account_id")
        if not isinstance(reason, str) or not reason.strip():
            raise ConfigError(f"bot allowlist entry for {login} needs a non-empty reason")
        allowlist[login] = {"account_id": account_id, "reason": reason.strip()}
    return allowlist


def bot_reason(commit: Commit, allowlist: Mapping[str, Mapping[str, Any]]) -> str | None:
    """Return the documented reason when this commit is an allowlisted bot."""
    if not commit.author_login:
        return None
    entry = allowlist.get(commit.author_login)
    if entry is None:
        return None
    if entry.get("account_id") != commit.author_id:
        # The login exists but the immutable id does not match. Treat it as a
        # normal human commit; do not silently exempt a renamed account.
        return None
    reason = entry.get("reason")
    return reason if isinstance(reason, str) else None


def evaluate(commits: Sequence[Commit], allowlist: Mapping[str, Mapping[str, Any]]) -> CheckResult:
    """Check every non-merge, non-bot commit for a matching sign-off."""
    violations: list[Violation] = []
    checked = 0
    merge_commits = 0
    bots = 0
    for commit in commits:
        if len(commit.parents) > 1:
            merge_commits += 1
            continue
        if bot_reason(commit, allowlist):
            bots += 1
            continue
        checked += 1
        signoffs = parse_signoffs(commit.message)
        if not signoffs:
            violations.append(Violation(commit.sha, commit.subject, "missing Signed-off-by trailer", commit.author_email))
            continue
        if not commit.author_email or not any(
            email.casefold() == commit.author_email.casefold() for _name, email in signoffs
        ):
            violations.append(
                Violation(
                    commit.sha,
                    commit.subject,
                    f"Signed-off-by e-mail does not match author <{commit.author_email}>",
                    commit.author_email,
                )
            )
    return CheckResult(checked=checked, merge_commits=merge_commits, bots=bots, violations=violations)


class GitHubClient:
    """Minimal read-only GitHub API client using only the standard library."""

    def __init__(self, token: str, api_root: str = API_ROOT):
        if not token:
            raise ConfigError("GITHUB_TOKEN is empty")
        self.token = token
        self.api_root = api_root.rstrip("/")

    def get_json(self, path: str, params: Mapping[str, Any] | None = None) -> tuple[Any, Mapping[str, str]]:
        url = self.api_root + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "User-Agent": "littrace-dco-check",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read()
                headers = dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise ConfigError(f"GitHub API returned {exc.code} for {path}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise ConfigError(f"GitHub API request failed for {path}: {exc.reason}") from exc
        try:
            return json.loads(body.decode("utf-8")), headers
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConfigError(f"GitHub API returned non-JSON content for {path}") from exc

    def get_file(self, repo: str, ref: str, path: str) -> tuple[str, str]:
        """Return (blob SHA, UTF-8 text) for a file on the default branch."""
        quoted_path = urllib.parse.quote(path, safe="/")
        data, _headers = self.get_json(f"/repos/{repo}/contents/{quoted_path}", {"ref": ref})
        if not isinstance(data, Mapping) or data.get("type") != "file":
            raise ConfigError(f"{path} at ref {ref} is not a file")
        blob_sha = data.get("sha")
        content = data.get("content")
        if not isinstance(blob_sha, str) or not isinstance(content, str):
            raise ConfigError(f"{path} at ref {ref} has no content or blob SHA")
        try:
            text = base64.b64decode(content).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ConfigError(f"{path} at ref {ref} is not valid UTF-8") from exc
        return blob_sha, text


def read_event(path: str) -> dict[str, Any]:
    if not path:
        raise ConfigError("GITHUB_EVENT_PATH is empty")
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read event payload {path}: {exc}") from exc
    try:
        event = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"event payload {path} is not valid JSON") from exc
    if not isinstance(event, dict):
        raise ConfigError("event payload is not a JSON object")
    return event


def event_default_branch(event: Mapping[str, Any]) -> str:
    repository = event.get("repository")
    if isinstance(repository, Mapping):
        branch = repository.get("default_branch")
        if isinstance(branch, str) and branch:
            return branch
    return ""


def pull_request_shas(event: Mapping[str, Any]) -> tuple[str, str, int | None]:
    pull_request = event.get("pull_request")
    if not isinstance(pull_request, Mapping):
        raise ConfigError("pull_request event has no pull_request object")
    base = pull_request.get("base")
    head = pull_request.get("head")
    if not isinstance(base, Mapping) or not isinstance(head, Mapping):
        raise ConfigError("pull_request event has no base or head")
    base_sha = base.get("sha")
    head_sha = head.get("sha")
    if not isinstance(base_sha, str) or not isinstance(head_sha, str) or not base_sha or not head_sha:
        raise ConfigError("pull_request event has no base or head SHA")
    number = pull_request.get("number")
    return base_sha, head_sha, number if isinstance(number, int) else None


def terms_metadata(contributing: str) -> tuple[str, str]:
    match = TERMS.search(contributing)
    if not match:
        raise ConfigError(
            "CONTRIBUTING.md on the default branch does not declare "
            "`<!-- contribution-terms: vN; effective: YYYY-MM-DD -->`"
        )
    version = match.group("version")
    effective = match.group("date") or "unspecified"
    return version, effective


def fetch_commits(client: Any, repo: str, base: str, head: str) -> tuple[list[Commit], int]:
    """Read every compare commit, following the Link header.

    GitHub caps some compare responses. If the API reports more commits than
    it returns, the check fails closed instead of passing commits it never
    inspected. Pages are requested until total_commits is reached or the Link
    header stops advertising a next page.
    """
    commits: dict[str, Commit] = {}
    order: list[str] = []
    total: int | None = None
    page = 1
    quoted_base = urllib.parse.quote(base, safe="")
    quoted_head = urllib.parse.quote(head, safe="")
    path = f"/repos/{repo}/compare/{quoted_base}...{quoted_head}"

    while page <= MAX_PAGES:
        data, headers = client.get_json(path, {"per_page": PER_PAGE, "page": page})
        if not isinstance(data, Mapping):
            raise ConfigError("compare response is not a JSON object")
        if total is None:
            reported = data.get("total_commits")
            if not isinstance(reported, int) or isinstance(reported, bool):
                raise ConfigError("compare response has no integer total_commits")
            total = reported
        batch = data.get("commits")
        if not isinstance(batch, list):
            raise ConfigError("compare response has no commits list")
        new_commits = 0
        for raw in batch:
            if not isinstance(raw, Mapping):
                raise ConfigError("compare response contains a non-object commit")
            sha = raw.get("sha")
            if not isinstance(sha, str) or not sha:
                raise ConfigError("compare response contains a commit without a SHA")
            if sha in commits:
                continue
            commits[sha] = parse_commit(raw)
            order.append(sha)
            new_commits += 1
        if not batch or new_commits == 0 or len(commits) >= total:
            break
        link = headers.get("Link", "") if hasattr(headers, "get") else ""
        if 'rel="next"' not in link:
            break
        page += 1

    if total is None:
        raise ConfigError("compare response has no total_commits")
    if len(commits) < total:
        raise ConfigError(
            f"GitHub reported {total} commits but returned {len(commits)}; "
            "the check refuses to pass commits it could not inspect. "
            "Rebase the pull request onto a newer base or split it."
        )
    return [commits[sha] for sha in order], total


def summary_header(
    result: str,
    terms_version: str,
    effective: str,
    contributing_sha: str,
    dco_sha: str,
    branch: str,
) -> list[str]:
    return [
        "## DCO check",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Result | `{result}` |",
        f"| Contribution terms | `{terms_version}` (effective {effective}) |",
        f"| CONTRIBUTING.md blob SHA on `{branch}` | `{contributing_sha}` |",
        f"| DCO blob SHA on `{branch}` | `{dco_sha}` |",
    ]


def append_summary(path: str, lines: Sequence[str]) -> None:
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines).rstrip() + "\n")
    except OSError as exc:
        print(f"::warning::Could not write the job summary: {exc}")


def _summary_row(subject: str) -> str:
    return subject.replace("|", "\\|")


def _run_check(options: Options, client: Any | None = None) -> int:
    if options.event_name not in {"pull_request", "merge_group"}:
        raise ConfigError(f"unsupported event: {options.event_name!r}")
    if not options.repo:
        raise ConfigError("repository is empty")
    if not options.event_path:
        raise ConfigError("event path is empty")
    event = read_event(options.event_path)
    branch = options.default_branch or event_default_branch(event)
    if not branch:
        raise ConfigError("cannot determine the default branch from the event payload")
    if client is None:
        client = GitHubClient(options.token)

    contributing_sha, contributing = client.get_file(options.repo, branch, "CONTRIBUTING.md")
    dco_sha, dco_text = client.get_file(options.repo, branch, "DCO")
    if "Developer Certificate of Origin" not in dco_text:
        raise ConfigError(f"DCO on {branch} is not the Developer Certificate of Origin text")
    terms_version, effective = terms_metadata(contributing)

    if options.event_name == "merge_group":
        lines = summary_header("not_applicable", terms_version, effective, contributing_sha, dco_sha, branch)
        lines.extend(
            [
                "",
                "The merge queue groups commits that already passed the pull-request check. "
                "There is no pull-request commit range to judge here.",
            ]
        )
        append_summary(options.summary_path, lines)
        print("DCO check: not_applicable (merge_group)")
        print(f"Contribution terms {terms_version}, effective {effective}; CONTRIBUTING.md blob {contributing_sha}")
        return 0

    base_sha, head_sha, number = pull_request_shas(event)
    commits, total = fetch_commits(client, options.repo, base_sha, head_sha)
    if total == 0:
        raise ConfigError(
            "compare returned no commits; a pull request must contain at least one commit. "
            "The base SHA may be stale."
        )
    allowlist = load_allowlist(options.bot_allowlist_path)
    result = evaluate(commits, allowlist)

    lines = summary_header(
        "fail" if result.violations else "pass",
        terms_version,
        effective,
        contributing_sha,
        dco_sha,
        branch,
    )
    lines.extend(
        [
            f"| Compare range | `{base_sha[:12]}...{head_sha[:12]}` |",
            f"| Pull request | #{number} |" if number is not None else "| Pull request | unknown |",
            f"| Commits checked | {result.checked} |",
            f"| Violations | {len(result.violations)} of {result.checked} checked commits |",
            f"| Merge commits skipped | {result.merge_commits} |",
            f"| Allowlisted bots skipped | {result.bots} |",
            f"| Commits reported by the API | {total} |",
        ]
    )
    if result.violations:
        lines.extend(
            [
                "",
                "### Commits that need a sign-off",
                "",
                "| Commit | Subject | Problem |",
                "| --- | --- | --- |",
            ]
        )
        for violation in result.violations:
            lines.append(
                f"| `{violation.sha[:12]}` | {_summary_row(violation.subject)} | {violation.problem} |"
            )
        lines.extend(
            [
                "",
                "Fix with:",
                "",
                "```sh",
                *FIX_COMMANDS,
                "```",
            ]
        )
    append_summary(options.summary_path, lines)

    if result.violations:
        for violation in result.violations:
            print(
                f"::error::Commit {violation.sha[:12]} ({violation.subject}): {violation.problem}"
            )
        print(
            f"DCO check failed: {len(result.violations)} of {result.checked} checked commit(s) "
            "do not have a Signed-off-by trailer that matches the author e-mail."
        )
        print("Fix with one of these three commands:")
        for command in FIX_COMMANDS:
            print(f"  {command}")
        print(
            "Use --force-with-lease, not --force: it refuses to overwrite a branch "
            "that someone else pushed to while you were rewriting yours."
        )
        return 1

    print(
        f"DCO check passed: {result.checked} commit(s) checked, "
        f"{result.merge_commits} merge commit(s) skipped, {result.bots} allowlisted bot(s) skipped."
    )
    print(f"Contribution terms {terms_version}, effective {effective}; CONTRIBUTING.md blob {contributing_sha}")
    return 0


def run_check(options: Options, client: Any | None = None) -> int:
    """Run the check and return the process exit code.

    Configuration failures are part of the exit-code contract (2), so they are
    converted here rather than escaping as an exception. A caller that wants a
    traceback can call the internal function directly.
    """
    try:
        return _run_check(options, client)
    except ConfigError as exc:
        print(f"::error::DCO check configuration error: {exc}")
        print(f"DCO check configuration error: {exc}")
        return 2


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check pull request commits for DCO sign-offs.")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--event-name", default=os.environ.get("GITHUB_EVENT_NAME", ""))
    parser.add_argument("--event-path", default=os.environ.get("GITHUB_EVENT_PATH", ""))
    parser.add_argument("--default-branch", default="")
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))
    parser.add_argument("--summary", default=os.environ.get("GITHUB_STEP_SUMMARY", ""))
    parser.add_argument("--bot-allowlist", default=os.environ.get("DCO_BOT_ALLOWLIST", ""))
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_args(argv)
        return run_check(
            Options(
                repo=args.repo,
                event_name=args.event_name,
                event_path=args.event_path,
                token=args.token,
                default_branch=args.default_branch,
                summary_path=args.summary,
                bot_allowlist_path=args.bot_allowlist,
            )
        )
    except ConfigError as exc:
        print(f"::error::DCO check configuration error: {exc}", file=sys.stderr)
        print(f"DCO check configuration error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - the check must fail closed, never crash green
        print(f"::error::DCO check crashed: {exc}", file=sys.stderr)
        print(f"DCO check crashed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
