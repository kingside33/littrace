# Security policy

## Reporting a vulnerability

**Please report privately, not in a public issue.** Use GitHub's private
vulnerability reporting:

**[Report a vulnerability →](https://github.com/kingside33/littrace/security/advisories/new)**

If private reporting is not available — for example while the repository is
still private and the Security tab has not been enabled — contact
[@kingside33](https://github.com/kingside33) through a GitHub direct message.
There is no security e-mail address.

Include what you did, what happened, what you expected, the commit SHA and your
platform. A proof of concept is welcome but not required. If the report is
confirmed, you will be credited in the advisory and the release notes unless
you prefer not to be.

Support is best effort from a single maintainer. The target is a first reply
within **7 days**. There is no SLA, no guaranteed fix time and no bug bounty.

## Data migration safety

An upgrade runs schema and data-layout migrations against the only copy of the
user's library. Migration safety is part of the support surface, not an
implementation detail, so it is stated before the version policy.

A safe upgrade is four commands:

```powershell
python kb.py backup --label before-upgrade
python kb.py data plan
python kb.py data migrate
python kb.py data rollback   # only when you need to go back
```

These are commitments, not tips:

- **Preview before you move.** `data plan` is strictly read-only: it returns
  the paths and a per-file SHA-256 inventory without moving anything
  (`kbapp/workflows/data.py:61-78`).
- **A migration is interruptible and resumable.** `data migrate` writes a
  journal before the first move, verifies every file against the inventory and
  continues after an interruption (`kbapp/workflows/data.py:131-205`).
- **A migration stops on a conflict.** It does not silently overwrite an
  existing target; it stops and reports the conflict.
- **A rollback is possible.** `data rollback` reverses the file moves and path
  fields while keeping business records (`kbapp/workflows/data.py:207-236`).
- **A backup is required.** `backup` uses SQLite's backup API, runs
  `PRAGMA integrity_check` and records key table counts
  (`kbapp/workflows/backup.py:32-52`). The database backup does not contain your
  PDFs, Zotero originals or Obsidian notes; back those up with the tools that
  own them.
- **The service is stopped.** `migrate` and `rollback` refuse to run while a
  job, agent run or reference job is active
  (`kbapp/workflows/data.py:52-59`).
- **A layout change is not a rebuild.** A layout change does not trigger a full
  OCR pass or a full vector rebuild.
- **Losing data is a bug.** An upgrade that cannot complete without losing data
  is a high-priority bug, not a configuration question. Report it with the
  migration journal and the output of `data plan`.

The database schema version and the data-directory layout version are tracked
separately. A migration failure must not leave the previous version
unreadable.

## Supported versions

### Before the first public release

The repository is private and nothing has been released. There is no published
release, release tag, release date or download link yet. The only supported
code is the tip of `main`; earlier commits are unsupported. Security fixes land
on `main`. There are no maintained release branches, no LTS and no backports.

### After the first public release

Support narrows to the latest release only. There are no maintained release
branches: if you are affected, the fix will be in the next release. No version
number, date or download link is announced in advance.

This table applies after the first public release and deliberately contains no
version number or date yet:

| Version | Supported |
|---|---|
| Latest release | ✅ |
| Anything older | ❌ — upgrade |

The exact Python versions, platforms, Zotero releases and local model
combinations that have actually been exercised live in
[`docs/support-matrix.json`](docs/support-matrix.json). README and this page
link there instead of keeping a second matrix. If a summary here disagrees with
that file, the file wins.

A hosted service, if one launches, will run the latest version only; that is
compatible with this policy. A future Zotero plugin is a separate component and
gets its own plugin × Zotero version matrix. Institutional customers that need
a pinned version require a separate institutional support agreement, not a
change to the community policy.

## What the threat model actually is

This is a local-first application. Knowing what it does and does not defend
against will save you time deciding whether something is a bug.

**The HTTP surface is loopback only.** `serve` binds `127.0.0.1`
(`kbapp/workflows/cli.py:109`). The app uses `TrustedHostMiddleware` for
`127.0.0.1`, `localhost` and `testserver` (`kbapp/web/service.py:59`). Every
`/api/` request is checked by a local guard: a request with an `Origin` must be
same-origin (`kbapp/web/service.py:67`), every non-GET/HEAD/OPTIONS request
must carry the per-process `x-kb-token` (`:69`), and a request body over 1 MB is
rejected (`:71`). The token is generated fresh for each process
(`kbapp/web/service.py:22`).

**The token is not authentication.** It raises the cost of a request from
another web page or another origin. It is not a boundary against a process
running as the same Windows user: such a process can read the app's memory,
read the token from the page, or ask DPAPI to decrypt the stored key. Do not
describe the token as a login, a user account or an authorization system.

**The default is no network.** There is no telemetry, no analytics SDK, no
usage beacon, no account and no background version check. Outbound requests
serve only a capability the user configured or invoked:

- the model endpoint configured in settings;
- OpenAlex and, where configured, Crossref;
- an optional local HTTP proxy;
- Ollama on `http://127.0.0.1:11434` (`kbapp/core/config.py:20`);
- a PDF download you explicitly approved in the Web approval page.

A network capability must be explicit, configurable and switchable off. A
request that happens without a user action or a setting is a bug.

**Inputs are not trusted.**

- **Zotero is an external read-only input.** `ro_connect` opens
  `zotero.sqlite` with `?mode=ro` and copies it into memory before reading
  (`kbapp/integrations/zotero.py:11-21`). littrace never writes to Zotero.
- **PDFs are untrusted input.** PyMuPDF parses them in-process
  (`kbapp/web/library_api.py:148`), so a malicious PDF can trigger a parser
  memory error or exhaust memory. Report it; we will coordinate with the
  upstream parser where the defect is upstream.
- **Model output is untrusted text.** It may contain instructions, HTML or
  links. It may only be written into the managed blocks of a note; it must not
  be rendered as trusted HTML or executed.
- **Note and PDF text may contain HTML or JavaScript.** An XSS path from either
  into the Web interface is in scope.

**Secrets are protected for the current Windows user.** API keys are encrypted
with DPAPI for the logged-in Windows user (`kbapp/core/secrets.py:7-27`). A
different Windows user cannot decrypt them. On a non-Windows platform the
module raises `RuntimeError` instead of storing a key in plaintext
(`kbapp/core/secrets.py:10-11`). That is a known limitation, not a
vulnerability. `_index/state/settings.json` may contain DPAPI ciphertext; do
not paste it into an issue.

## In scope

Reports that show a working path to one of these are in scope:

- Bypassing the loopback bind, `TrustedHost`, the per-process token or the
  same-origin check, including driving the API from another local web page or
  through DNS rebinding.
- Reading or writing a file outside the allowed roots through `/api/file`,
  `/api/asset` or an attachment endpoint. **Invariant: every file the HTTP
  surface serves must resolve under an allowed root.** `/api/asset` enforces
  this (`kbapp/web/library_api.py:153-156`); `/api/file` currently uses the path
  stored in the database (`:115-130`). A stored path that escapes the root is a
  vulnerability.
- Writing outside the data directory, or changing repository or package files
  at runtime.
- Leaking a stored key through the API, a log, a diagnostic output, an error
  message or an offline export.
- Damaging or losing a note, a database row or a file through a write-back or
  migration path.
- An offline export that embeds a local absolute path, a key, a Zotero path or
  library content that was not part of the exported data.
- XSS from note text, PDF text, model output or a filename.
- A malicious PDF that triggers a parser memory error or a crash with a
  reproducible input. If the defect is in PyMuPDF, we will coordinate
  disclosure with the upstream project.
- A dependency vulnerability with a working path to one of the above in
  littrace.

## Out of scope

- A malicious process running as the same Windows user. It can read memory,
  read the token and use DPAPI; the local model is not a boundary against it.
- A build you modified yourself, including a patched dependency or a forked
  `kbapp`.
- An older commit or a pre-release snapshot.
- A report produced only by an automated scanner, with no reproduction on a
  clean checkout and no path to impact.
- A local model or proxy configuration error: a wrong Ollama URL, a missing
  model tag, a proxy that blocks the request, or a model that is still
  downloading.
- A local denial of service caused by a very large library, a very large PDF or
  a full disk. Report it as a bug if it corrupts data; it is not a security
  boundary.
- A problem in Zotero itself, or in a Zotero library that littrace only reads.
- A feature request, including a request for authentication, multi-user
  accounts or a hosted service. Those are product decisions, not
  vulnerabilities.

## Running from source

There is no installer and no signed binary yet. littrace is run from a source
checkout, so there is nothing to verify with a code-signing certificate and no
"unsigned installer" warning to explain.

If you want to know what you are running, use the commit SHA:

```powershell
git rev-parse HEAD
python kb.py doctor status
```

Every public release will correspond to a tag in this repository and a
changelog at that tag. A build whose source exists only in a private branch is
not a release.

## What happens after a report

A confirmed report is fixed on `main` and, after the first public release, in
the next release. The advisory describes the affected versions, the fix and any
workaround. Reporters are credited unless they ask not to be. There is no bug
bounty and no promise of a fix date; the 7-day target is a first reply, not a
remediation deadline.
