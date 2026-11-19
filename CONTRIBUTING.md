<!-- contribution-terms: v1; effective: 2026-11-17 -->
# Contributing to littrace

Thanks for taking the time. A good bug report is worth as much as a patch.

> **The short version.** Your code stays yours. You let anyone use it under
> AGPL-3.0-only; in addition, you let the littrace maintainer use this
> contribution in future paid features, hosted services and commercially
> licensed editions. We do not ask you to assign copyright, and there is no
> separate CLA. If you are not comfortable with that, open an issue first
> instead of sending code.

> **Contribution terms v1, effective 2026-11-17.** This is a licence, not a
> copyright assignment. It is engineering text, not legal advice. Before the
> repository is made public, and before the first external contribution is
> accepted, have counsel review it — in particular the patent grant, the
> moral-rights waiver and the governing law. Later versions apply only to
> contributions submitted after their effective date; they do not reach back
> into a contribution that was already submitted.

## Reporting a bug

Open a [bug report](https://github.com/kingside33/littrace/issues/new?template=bug_report.yml).
The template asks for the two things that make a report actionable: a minimal
reproduction and the output of the state check.

Run this from the repository root and paste the output:

```powershell
python kb.py doctor status
```

If you suspect a damaged database or index, also run:

```powershell
python kb.py doctor integrity
```

`doctor` is the diagnostic entry point (`kbapp/workflows/cli.py:50-52`); it does
not change data. **The output can contain absolute paths, library names,
collection names and configured endpoint URLs.** Remove them before posting.
Replace a path with `<library>` and a key with `<key>`.

Do not attach:

- PDFs, notes or any other library content you cannot publish;
- `zotero.sqlite`, its `storage/` folder or a Zotero export;
- `_index/state/settings.json` or any file that may contain a DPAPI-encrypted
  or plaintext key;
- API keys, tokens or cookies, including them inside a log excerpt.

A cut-down reproduction with fake papers, fake notes and three or four lines of
code is more useful than a real library. Chinese or English is fine, in the
issue and in the pull request.

If the report is a security vulnerability, do not open a public issue. Use the
[security policy](SECURITY.md) and GitHub's private vulnerability reporting.

## Getting set up

Windows x64 is the primary and only verified platform. macOS and Linux are
unverified; the commands below should work, but nobody has recorded a full run.
The authoritative list is [`docs/support-matrix.json`](docs/support-matrix.json).

Python 3.10 or newer is required. Node is only a test runner for the `.cjs`
files; the frontend is native JavaScript with no build step and no npm
dependencies.

### Windows (PowerShell)

```powershell
git clone https://github.com/kingside33/littrace.git
cd littrace
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

If PowerShell refuses to run `Activate.ps1` because of the execution policy,
allow it for the current process only:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

You can also skip activation and call the interpreter directly, for example
`.\.venv\Scripts\python -m pip install -e ".[dev]"`.

### macOS / Linux

```sh
git clone https://github.com/kingside33/littrace.git
cd littrace
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

The `dev` extra installs pytest and ruff. `requirements-dev.txt` carries the
same set for environments that install requirements instead of the package.

The repository is private during pre-release. The `git clone` command works
only for accounts that have been granted access; there is no public download or
release package yet.

The first `update` or `index` also needs Ollama, which is not a Python
dependency and is not installed by `pip`. Start Ollama at
`http://127.0.0.1:11434` and pull the default model tags before running either
command:

```sh
ollama pull qwen3-embedding:8b
ollama pull glm-ocr:latest
ollama pull gemma4:e4b
```

These tags are code defaults (`qwen3-embedding:8b`, `glm-ocr:latest` and
`gemma4:e4b`), not a verified model combination. The support matrix records
Ollama as `unverified` and pins no version.

### Run it against a throwaway data directory

Never point a development run at a real library. A real library is the only
copy of the user's data; the maintainer's own library was 13,035 papers and
about 3.6 GB when this document was written. A test run must not touch it.

```powershell
New-Item -ItemType Directory -Force "$env:TEMP\littrace-dev-data" | Out-Null
.\.venv\Scripts\python kb.py --data-dir "$env:TEMP\littrace-dev-data" serve
```

Then open <http://127.0.0.1:8765/>. `serve` binds loopback only
(`kbapp/workflows/cli.py:109`).

`--base` and `--data-dir` may be given before the subcommand; they correspond
to `KB_BASE` and `KB_DATA_DIR`. `KB_BASE` defaults to the repository root and
`KB_DATA_DIR` to `<base>/_index`, so a checkout that already contains `_index`
will use it unless you pass `--data-dir`. `KB_ZOTERO_DIR` is optional and is
read through a read-only snapshot; littrace never writes to Zotero.

`kb.py` is the only entry point. There is no `app.py`, no `littrace` console
script and no `python -m` alias.

## Verifying a change

Run these from the repository root, with the virtual environment active:

| What | Command |
| --- | --- |
| Lint (what CI runs) | `ruff check .` |
| Offline suite (what CI runs) | `python -m pytest -m "not network and not model"` |
| Full suite | `python -m pytest` |
| Failures with context | `python -m pytest -rs --durations=10` |
| JavaScript tests | `node --test tests/test_*.cjs` |
| JavaScript inventory | `node --test $(git ls-files 'tests/test_*.cjs')` |
| Runtime state | `python kb.py doctor status` |

The table assumes the virtual environment is active. If you skipped
activation, call the interpreter explicitly: `.\.venv\Scripts\python -m pytest`
and `.\.venv\Scripts\python -m ruff check .` on Windows, or
`.venv/bin/python -m pytest` and `.venv/bin/python -m ruff check .` on macOS
and Linux.

`ruff check . --fix` is fine while you work; do not run it in a PR that is
about something else. CI runs the read-only form and never rewrites the tree.
Formatting (`ruff format --check`) is deliberately not part of the gate yet:
turning it on before the existing tree has been formatted would make every PR
red for reasons unrelated to its content. Do not reformat the whole repository
in an unrelated PR.

At the time of writing, `ruff check .` passes with the pinned ruff version and
the offline suite passes on Windows x64 with Python 3.13. A change must not add
new findings. If your baseline is red, include the failing command and its
output in the pull request; do not fold a repository-wide cleanup into an
unrelated change.

The suite has two markers:

- `network` — the test talks to a real network service.
- `model` — the test loads a real local model.

CI runs `-m "not network and not model"`. A test that needs either resource
must skip cleanly when it is unavailable; it must not fail because a model is
missing or a proxy is down. Do not add a `live` marker or a separate live
suite — the same tests run locally with the marker and are excluded in CI.

**Do not assume a red baseline.** Run the offline suite before changing
anything. If it fails, include the failing command and output in the pull
request; do not delete, deselect or `xfail` a failure to get a green run. The
baseline is recorded in [`docs/support-matrix.json`](docs/support-matrix.json).

**Use the explicit file list for Node.** `node --test tests/test_*.cjs` finds
the `.cjs` tests in this checkout and passes. `node --test tests` does not
work: Node treats the directory as a single test and fails. If you add or
remove a `.cjs` test, update the explicit inventory in
`.github/workflows/ci.yml` too; CI checks the list against `git ls-files` so a
missing file cannot be silently skipped.

The number of test files changes. Do not hard-code an inventory in a document;
count them with `git ls-files 'tests/test_*.py'` and
`git ls-files 'tests/test_*.cjs'`, and keep the explicit `.cjs` list in CI in
sync.

CI is the record, not this document. A green local run means the commands you
ran passed. Do not write "all green" in a pull request unless you actually ran
the commands, and say which platform and data shape you did not exercise.

### Tests and fixtures

- Put temporary state under pytest's `tmp_path`, or run the command with
  `--data-dir` pointing at a temporary directory. Never write to the real
  `_index`, `_知识库`, a Zotero directory or the repository root at runtime.
- Fixtures must not contain real papers, notes, abstracts, personal paths,
  Zotero item keys or keys. Build a small synthetic library instead.
- A test that needs a real model or network gets the matching marker and
  skips cleanly when the resource is absent.
- A bug that only happens on Windows becomes a test under `tests/` first,
  where it can fail for the right reason, before the fix.
- Tests must not depend on the maintainer's library or on the current date.

## Things that will get a PR sent back

These are boundaries, not style preferences. Each one protects data, a
licence, or the ability to run the code somewhere other than the maintainer's
machine.

- **Writing outside the data directory.** Everything writable goes through
  `Paths` (`kbapp/core/paths.py`) and lands under the data directory. Do not
  write to the repository root, the package directory or the current working
  directory. An installed wheel may live in a read-only site-packages.
- **Writing to Zotero.** `ro_connect` opens `zotero.sqlite` with `?mode=ro` and
  copies it into memory before reading (`kbapp/integrations/zotero.py:11-21`).
  Zotero is an external read-only input. Do not add a write path, a lock or a
  schema change on the Zotero side.
- **Silent network access or telemetry.** The default is no network. Outbound
  requests serve only a capability the user configured: a model endpoint,
  OpenAlex, an optional local HTTP proxy, or Ollama at
  `http://127.0.0.1:11434` (`kbapp/core/config.py:20`). Do not add an analytics
  SDK, a usage beacon, a background version check or a call that happens
  without an explicit setting.
- **Keys in logs, APIs or fixtures.** Keys are protected with DPAPI for the
  current Windows user (`kbapp/core/secrets.py:7-27`). The API returns whether
  a key exists, never the key. Do not put a key in a log line, an error message,
  a test fixture, a screenshot or a URL.
- **A second entry point.** `kb.py` is the only command entry point. Do not add
  a console script, a `python -m kbapp` path or a second CLI that bypasses the
  environment setup in `kb.py`.
- **An offline export that needs the network.** `python kb.py export offline`
  must produce a self-contained page from repository assets. An export that
  fetches a CDN, a font or a model is a privacy regression and will be rejected.
- **A migration that can lose data.** Schema and layout migrations must be
  previewable with `python kb.py data plan`, interruptible and resumable, and
  reversible with `python kb.py data rollback`. They must stop on a target
  conflict rather than overwrite. An upgrade that cannot complete without
  losing data is a high-priority bug, not a configuration question.
  `migrate` and `rollback` refuse to run while a job is active
  (`kbapp/workflows/data.py:52-59`).
- **Serving a file outside an allowed root.** Every file the HTTP surface
  returns must resolve under an allowed root. `/api/asset` already enforces
  this (`kbapp/web/library_api.py:153-156`); `/api/file` currently trusts the
  path stored in the database (`:115-130`) and is expected to get the same
  check. If you touch either endpoint, close the gap or say why the path is
  already constrained.
- **Language drift.** Code comments and the Web UI are Chinese. Governance
  documents, docstrings that describe public behaviour and the README are
  English. A `README.zh-CN.md`, when present, is a full translation, not a
  summary. Commit messages and pull request descriptions may be Chinese or
  English.
- **A personal absolute path as a default.** Do not commit a path such as
  `zotero-data` at a drive root, a home directory or a machine name as a
  default. Use an empty default plus environment variables or detection; a
  default that only works on one machine is a bug for everyone else.
- **A new dependency that is not compatible with AGPL-3.0-only.** Update
  [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) in the same PR, including
  the licence and the version range. A dependency that cannot be distributed
  under AGPL-3.0-only does not go into the runtime.

## Who decides

littrace is maintained by one person. The maintainer decides what is merged,
in what order it lands, and what belongs in the commercial layer. There is no
vote, no steering committee and no promise of a reply from a team.

A contribution may be rejected for scope, maintenance cost, security, licence
compatibility or because it does not fit the local-first design. A merged
contribution is not a promise that a related paid feature will exist, ship on a
date, or include your work. An issue is a discussion, not a roadmap commitment.
The open-source core stays under AGPL-3.0-only regardless of what the commercial
layer does.

## Issue and pull request etiquette

- Search the existing issues before opening a new one.
- One problem per issue; one purpose per pull request.
- Use the templates: [bug report](https://github.com/kingside33/littrace/issues/new?template=bug_report.yml)
  and [feature request](https://github.com/kingside33/littrace/issues/new?template=feature_request.yml).
- Do not paste private library content, absolute paths or keys. Redact first.
- Chinese or English is fine in an issue, a pull request or a commit message.
- Do not ping the maintainer for a status update; a comment on the issue is
  enough. There is no promise of a reply time beyond the best-effort target in
  [`SECURITY.md`](SECURITY.md).
- A feature request is a discussion, not a roadmap commitment.

## Lessons (错题本)

[`docs/lessons.md`](docs/lessons.md) records mistakes that cost time and
practices worth copying. It is a queue for rules, not a diary: an entry that
recurs is promoted into a binding rule, and an entry that no longer applies is
deleted.

Read the entries relevant to your change before you start and when you review.
Write only in a separate `docs(lessons)` pull request; feature pull requests
must not touch the file. The format:

```markdown
### L-001 <one actionable rule, at most 60 characters>
- 类: 别这样
- Seen: 2026-11-12, 2026-11-19
- 为什么: <what happened and what it cost>
- 证据: #58, #64
```

- `类` is `别这样` (do not do this) or `照这样` (do this).
- `Seen` lists distinct dates in ascending order; the same day counts once.
- `证据` lists issue numbers only. This repository files an issue before a fix,
  so every lesson has a traceable origin.
- Numbers are unique and only increase; never reuse one.

Promotion: after the same lesson appears on three distinct dates, write the rule
into [`AGENTS.md`](AGENTS.md), this file, or a new ADR, and compress the entry
to a single pointer line: `### L-002 <title> → AGENTS.md`. The target file must
name `L-002`.

Deletion: remove an entry when the code it names no longer exists, when an
existing gate already blocks the mistake, or when it has not recurred for 60
days and the binding rules already cover it. List deletions in the pull request
body; git history keeps the original text.

Privacy: this is a public repository. Do not write local paths, library
content, personal configuration or credentials; evidence stops at issue
numbers.

`python scripts/check_lessons.py` enforces the format, numbering, the 16 KiB
cap and the privacy patterns. CI runs it.

## Pull requests

Branch off `main`, keep one pull request to one purpose, and link the issue it
fixes. Fill in the [pull request template](.github/PULL_REQUEST_TEMPLATE.md):
what changed, why, how you verified it, and what you could not verify. Chinese
or English is fine.

Commit messages should say **why**, not just what. A fix should name the
symptom a user would have seen. Commit messages may be Chinese or English.

Before you push:

```powershell
ruff check .
python -m pytest -m "not network and not model"
node --test tests/test_*.cjs
```

### Merge method

The default is a merge commit or rebase-and-merge. Both keep the original
commits, so every `Signed-off-by` trailer stays in history.

If you squash, **the squash commit body must contain every author's
`Signed-off-by` trailer**. GitHub does not copy them from the squashed commits.
A squash without them leaves the contribution in history with no sign-off
record, even though the PR passed CI. The body should look like:

```text
Add the evidence filter

Signed-off-by: Alice <alice@example.com>
Signed-off-by: Bob <bob@example.com>
```

The DCO checkbox in the pull request template is a reminder, not the
signature. CI reads the `Signed-off-by` trailers in the commits, not the
checkbox. The same is true of the contribution-terms checkbox: it records that
you read the terms, but the enforceable acceptance is the combination described
in the terms below.

## Developer Certificate of Origin (DCO)

littrace uses the [Developer Certificate of Origin 1.1](https://developercertificate.org/).
The verbatim text is in the repository root at [`DCO`](DCO); do not edit it.

Every non-merge commit in a pull request must end with a trailer of the form:

```text
Signed-off-by: Your Name <your-email@example.com>
```

By adding that trailer you certify the four statements in the DCO: that you
created the contribution or have the right to submit it, that it is covered by
the project's licence, that you received any third-party contribution under the
same terms, and that you understand the contribution and your sign-off are
public and may be redistributed.

**The DCO certifies provenance. It does not grant a copyright licence and it
does not grant the right to sublicense.** That is why this repository also has
the separate contribution terms below. The sign-off is not a CLA and is not a
substitute for one.

### Signing

A new commit:

```sh
git commit -s
```

One commit that is already written:

```sh
git commit --amend -s
```

A branch with several commits:

```sh
git rebase --signoff origin/main
```

After rewriting a branch that is already pushed:

```sh
git push --force-with-lease
```

Use `--force-with-lease`, not `--force`: it refuses to overwrite a branch that
someone else pushed to while you were rewriting yours.

The sign-off e-mail must match the commit author e-mail, compared
case-insensitively. If `git config user.email` is not the address you want in
history, fix it before you commit. A `Co-authored-by` trailer is welcome but
does not replace a sign-off. Merge commits are not checked, because a merge
commit is a combination of commits that were already checked.

### Bots

A bot is exempt only when its login **and** immutable account id are listed in
the checker with a written reason. Matching a `[bot]` suffix is not enough: the
login is mutable and the suffix is a convention, not an identity. The list
currently contains Dependabot, with its account id and a reason, because
`.github/dependabot.yml` opens github-actions bumps that cannot carry a human
sign-off. Adding another entry is a code change reviewed like any other.

### What CI does

The check is `scripts/ci/check_dco.py`, run by one `dco` job in
[`.github/workflows/ci.yml`](.github/workflows/ci.yml). It uses no third-party
DCO action.

- It runs on `pull_request` only. The workflow is triggered by `push` to
  `main` and `pull_request`, and the `dco` job has
  `if: github.event_name == 'pull_request'`.
- `scripts/ci/check_dco.py` also implements a `merge_group` branch that reports
  `not_applicable` and exits `0`, but this repository has no merge queue and the
  workflow does not trigger on `merge_group`, so that branch is currently
  unused. Do not enable a merge queue expecting this check to run until the
  trigger and the job condition are changed together.
- It has `contents: read` and `pull-requests: read`; it does not need write
  access.
- It does not check out the pull request. The script, `DCO` and
  `CONTRIBUTING.md` come from the default branch, so the tree under review
  cannot supply the rule that judges it.
- It reads the commit list through `compare/{base}...{head}` and follows the
  pagination links. If GitHub reports more commits than it returns (the
  compare and pull-request endpoints have a 250-commit ceiling), the check
  fails closed instead of passing commits it never inspected.
- A missing trailer or a sign-off whose e-mail does not match the author exits
  `1`. A configuration error — missing token, missing file, unreadable event,
  API failure, no terms line — exits `2`.
- The job summary prints the `CONTRIBUTING.md` blob SHA and the contribution
  terms version that applied to the run.

On failure the job prints exactly three commands:

```sh
git commit --amend -s
git rebase --signoff origin/main
git push --force-with-lease
```

The `dco` job is part of the existing CI workflow, not a separate required
check. If branch protection uses an aggregate gate, include `dco` in that gate;
do not add a second, DCO-specific required status.

Bootstrapping: the script, `DCO` and this file must be on the default branch
before the check is made required. A pull request cannot add the rule that
judges itself, so land the tooling first and enable branch protection after it.

## Licence and contribution terms

littrace is licensed under **AGPL-3.0-only**; the full text is in
[`LICENSE`](LICENSE). The project does not use a CLA, and it does not ask you
to assign copyright. Instead, it uses the DCO for provenance and the terms
below for the rights the project needs. Inbound equals outbound: a contribution
is accepted under the same licence as the repository.

**This is a licence, not a copyright assignment.** The terms below are the
complete contribution licence for version v1. They are written as engineering
text, not legal advice.

### 1. Scope

These terms apply to any copyrightable material you submit to littrace in any
form — a pull request, a patch, a code fragment in an issue, documentation,
tests, configuration, a design or any other contribution. The material is
called "your contribution" below.

### 2. Copyright is not assigned

You retain copyright in your contribution. These terms grant licences; they do
not transfer ownership. This project has no CLA and does not require you to
sign a copyright assignment or any separate agreement.

### 3. Licence to everyone under the project licence

You agree that your contribution is licensed to everyone under the licence in
force for the repository at the time you submit it: **AGPL-3.0-only**
(inbound = outbound). It is treated the same way as the rest of the code.
Anyone may use, modify and redistribute your contribution under AGPL-3.0-only.

### 4. DCO sign-off

Each commit must be signed off with `git commit -s` under the Developer
Certificate of Origin 1.1, certifying that you created the contribution or
have the right to submit it and that you agree to license it under the project's
open-source licence. The sign-off record is kept in the commit history.

### 5. **Maintainer commercial-use licence**

**In addition to section 3, you grant the littrace maintainer, its successors
and assigns a perpetual, irrevocable, worldwide, non-exclusive, transferable,
royalty-free copyright licence, with the right to sublicense through multiple
tiers, to reproduce, modify, adapt, translate, distribute, publicly communicate,
publicly perform, publicly display and otherwise use your contribution,
including as part of paid features, hosted services and separately licensed
editions, and to sublicense your contribution under any licence, including
proprietary and commercial licences.**

This is the core of these terms. The DCO alone cannot support a separately
licensed edition, because it certifies provenance but grants no sublicensing
right. This section supplies that right.

### 6. **Permitted uses**

**The licence in section 5 may be used for the project's related commercial
features, paid services (including hosted or cloud subscriptions) and
commercially licensed editions (including institutional dual licensing). The
maintainer is not obliged to use the licence, to merge the contribution or to
pay for it. You may continue to use and license your contribution in any other
project.** A contribution is not a promise that a commercial feature will
exist or that your work will be part of it.

### 7. AGPL-3.0-only rights of others are unchanged

These terms do not reduce, replace or limit any third party's rights under
AGPL-3.0-only in the open-source edition. A commercial licence governs only the
commercial version the maintainer distributes; it is not an AGPL-3.0-only
waiver for downstream users. Your contribution also remains available under AGPL-3.0-only
in the community edition.

### 8. Patent licence

For any patent you own or have the right to license that is necessarily
infringed by your contribution, you grant the maintainer and downstream
recipients a perpetual, irrevocable, worldwide, royalty-free, non-exclusive
patent licence to make, use, sell, offer for sale, import and otherwise exploit
the contribution. If you bring a patent suit against the maintainer or a
downstream recipient alleging that the contribution infringes a patent, this
patent licence terminates as of the date the suit is filed. This follows the
approach of section 3 of the Apache License 2.0.

### 9. Your representations

You represent and warrant that:

- you have the right to grant all of the licences above;
- if the contribution was created in the course of employment or within the
  scope of your duties, you have your employer's or institution's written
  permission or a rights waiver;
- the contribution does not contain third-party code you are not entitled to
  submit; and
- if it contains third-party code, you have identified the source and licence
  in the pull request and that licence is compatible with AGPL-3.0-only.

**If your employer owns the work, do not sign off. Contact the maintainer
first.** A sign-off you are not entitled to give does not transfer rights your
employer holds.

### 10. Attribution and moral rights

The maintainer will keep your attribution in the commit history and in the
contributors list. Commercial versions and hosted services do not need to
credit each contributor in the interface, and that practice is not a waiver of
any right of attribution.

To the extent permitted by applicable law, you waive, and agree not to assert,
any moral rights (including rights of attribution and integrity) in your
contribution against the maintainer and downstream licensees. This waiver does
not change the maintainer's practice of keeping your attribution in the commit
history and the contributors list, and it does not require a commercial version
or a hosted service to credit you in its interface.

### 11. Acceptance and versioning

These terms are accepted by the combination of:

1. this statement in `CONTRIBUTING.md`;
2. the explicit contribution-terms checkbox in the pull request template; and
3. the `Signed-off-by` trailer on each commit.

The checkbox is a record of intent; CI reads the commit trailers. The terms
carry a version (`v1`) and an effective date at the top of this file. A later
version applies only to contributions submitted on or after its effective
date. It does not apply retroactively to a contribution that was already
submitted.

### 12. Survival

These terms take effect when you submit the contribution and continue after it
is merged. They are not terminated by you stopping participation, deleting a
branch, changing accounts or the project moving to another hosting platform.
The licences already granted are irrevocable. If your contribution is not
merged, the maintainer will not use it.

### 13. No warranty

Your contribution is provided "as is", without express or implied warranties,
consistent with the spirit of sections 15 and 16 of AGPL-3.0-only. You are
responsible for losses caused to the maintainer by a breach of the
representations in section 9.

### A note on these terms

These terms do not choose a governing law. That is deliberate at this stage:
the repository is private and pre-release, and a choice made without advice
would be worse than an explicit open question. Before the repository becomes
public and before the first external contribution is accepted, have counsel
review the patent grant, the moral-rights waiver, the employer representation
and the governing-law question. This document is an engineering implementation
of the intended policy, not a legal opinion.

## Trademark

The AGPL-3.0-only licence covers copyright, not the name. See [`TRADEMARKS.md`](TRADEMARKS.md)
for what you may do with the littrace name and with third-party names such as
Zotero.
