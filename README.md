# littrace

<p align="center">
  <b>English</b> · <a href="README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: AGPL-3.0-only" src="https://img.shields.io/badge/license-AGPL--3.0--only-blue?style=flat-square"></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-blue?style=flat-square">
  <a href="https://github.com/kingside33/littrace/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/kingside33/littrace/actions/workflows/ci.yml/badge.svg?branch=main"></a>
  <a href="https://github.com/sponsors/kingside33"><img alt="Sponsor on GitHub Sponsors" src="https://img.shields.io/badge/sponsor-GitHub%20Sponsors-ea4aaa?style=flat-square&logo=githubsponsors&logoColor=white"></a>
</p>

**A local-first workbench for tracing research lineages and answering questions with evidence.**

littrace turns a Zotero library (including the PDFs attached to its items) and an Obsidian vault into one research workbench. It indexes passages, figures and formulas, maps how papers cite, inherit from and improve on each other, and answers questions with quotes that point back to a page. The backend is FastAPI and SQLite; the front end is plain JavaScript with no build step. Your PDFs, notes and Zotero library stay where they are. Question answering is retrieval-augmented: answers are assembled from indexed passages, not from model memory.

> **Pre-release status.** The repository is private and nothing has been released. There is no published release, release tag, release date or download link yet. The only supported code is the tip of `main`. The Web interface is currently in Chinese.

The CI badge points at `.github/workflows/ci.yml`; it shows a status once the private repository is public and the workflow has run.

## What it does

- **Evidence retrieval.** One search box covers passages, figure captions and formulas. Filters cover research direction, project, year, collection, tag, section and individual paper. Results show the matching passage and its page rather than a generated summary.
- **Cited answers.** A question is split into up to four formulations, about 40 passages are reranked, and at most 16 evidence items are kept. The answer is assembled claim by claim: a claim enters the answer only after its quote, paper attribution, page, metric, unit, baseline and experimental conditions pass a local check and an independent cloud review. Claims that fail are reported as gaps, not smoothed into prose. Two to four papers can be compared side by side. This path needs an OpenAI-compatible cloud endpoint configured in the settings page; when cloud calls are disabled, Q&A reports that it is not configured instead of producing a local-only answer.
- **Research lineage and graph.** Citation facts and method relations are kept apart. A method relation (`inherits`, `improves`, `combines`, `transfers`, `compares`, `similar`) needs verbatim evidence from both papers and an independent review before it counts as verified; abstract-level support stays labeled as such and cannot be upgraded silently. Relation verification also needs the cloud model: with cloud calls disabled, relation analysis returns `awaiting_configuration` and does not start. The graph uses a built-in stable Chinese method taxonomy written for robotics and path-planning literature (sampling, graph search, metaheuristics, control, perception and related families); it is a display grouping, not a configurable taxonomy, and it offers a research-chain view and a time-evolution view. Citation counts are not treated as method inheritance.
- **Offline export.** `python kb.py export offline` writes a self-contained `_index/exports/evolution.html`, plus the machine-readable `current.json` and `llms.txt`. The offline page uses the same data and filters as the online graph and opens without a server. Exports are published as a version; a failed build leaves the previous version in place.
- **Research dashboard.** Long-running work is started, stopped and resumed in one place: full-text understanding and note maintenance, lineage completion, relation verification, full incremental updates and vector indexing. Tasks show real stage events, the papers or relation pairs in flight, verified versus insufficient results, and model calls, tokens and cost. Stopping preserves completed units; resuming reuses them.

littrace does not write to Zotero, does not modify your PDFs, and does not overwrite the free-form parts of your Obsidian notes; it only updates the blocks it manages. Cloud model calls and OpenAlex/Crossref lookups are optional for ingest, indexing, search and offline export. Cited answers and method-relation verification are not local-only: both need a configured cloud model, and the Web UI reports them as not configured until one is enabled. A local-only run that does not use those two features does not need a cloud account.

## Install and quick start

### Requirements

Python 3.10 or newer. Windows x64 is the primary and only verified platform; macOS and Linux are unverified. The authoritative support matrix — Python versions, platforms, Zotero releases and local model combinations — is [`docs/support-matrix.json`](docs/support-matrix.json). It records status and any tested versions; Zotero and Ollama are currently `unverified` with empty `tested_versions`, and the model tags in [First run](#first-run) are defaults, not a verified combination.

### From source

```bash
git clone https://github.com/kingside33/littrace.git
cd littrace
python -m venv .venv

# Windows (PowerShell)
.venv\Scripts\Activate.ps1
# Windows (cmd)
.venv\Scripts\activate.bat
# macOS / Linux
source .venv/bin/activate

pip install -e ".[dev]"
python kb.py serve
```

The repository is private during pre-release, so the `git clone` step works only for accounts that have been granted access. There is no public download or release package yet; without access the clone fails with a 404 or an authentication prompt.

Open <http://127.0.0.1:8765>. The interface is Chinese. `kb.py` is the only command entry point; there is no separate console script.

### Point it at your library

By default `KB_BASE` is the repository root and `KB_DATA_DIR` is `<KB_BASE>/_index`. Set them when the literature root and the code live apart, or pass `--base` / `--data-dir` before the subcommand:

```bash
python kb.py --base ~/research/library --data-dir ~/research/library/_index serve
```

| Variable | Purpose | Default |
|---|---|---|
| `KB_BASE` | Literature root: `_知识库`, `_index` and the PDF tree scanned by `map_pdfs` | Repository root |
| `KB_DATA_DIR` | Runtime data: SQLite, indexes, caches, exports, backups | `<KB_BASE>/_index` |
| `KB_ZOTERO_DIR` | Zotero data directory containing `zotero.sqlite` and `storage/` | Empty |
| `KB_BLAS_THREADS` | BLAS/OMP thread count for the local service | `1` |

`KB_ZOTERO_DIR` is read through a read-only SQLite snapshot. littrace never writes back to Zotero.

PDFs enter the pipeline as attachments of Zotero items. To add a paper, add it to Zotero with a PDF attachment, point `KB_ZOTERO_DIR` at the Zotero data directory, and run `update`; the command reads item metadata and attachment paths and does not import a standalone folder of PDFs. The `map_pdfs` data tool (`python kb.py data tool map_pdfs`) scans `KB_BASE` and matches PDF files to paper records that already exist in the index; it reports unmatched files in `orphan_pdfs.json` and does not turn them into papers. Generated literature cards live under `_知识库/10_文献/<direction>/`, and application-managed figure and formula images live under `_知识库/_attachments/`.

The direction taxonomy, project name, Zotero collection rules and embedding query instructions default to neutral placeholders so no personal library structure ships with the code. The default table has 11 entries (`00`–`09` and `90`), all neutral placeholders. Override them per machine with `KB_DIRECTIONS` (JSON array of `[id, folder, title, description]`), `KB_COLLECTION_RULES` / `KB_DISK_RULES` (JSON arrays of `[prefix, [direction ids]]`), `KB_DIR_COLLECTION_PREFIX` (JSON object), `KB_PROJECT`, `KB_PROJECT_STAGES` (JSON object), `KB_EVOLUTION_DIRECTIONS` (JSON array), and `KB_QUERY_INSTRUCTION` / `KB_FIG_QUERY_INSTRUCTION` / `KB_FORMULA_QUERY_INSTRUCTION`. `KB_LIBRARY_TITLE` and `KB_LIBRARY_TOPIC` override the heading and topic line of the generated index. See `kbapp/core/config.py` and `kbapp/core/markers.py` for the accepted shapes. The built-in method-family taxonomy in `kbapp/research/method_taxonomy.py` and the title-keyword fallback in `kbapp/library/cards.py` (`KEYWORD_RULES`) are fixed and written for robotics/path-planning vocabulary; they have no environment override, so a library from another field can set the direction variables above but may still need to correct keyword-only assignments by hand.

### First run

```bash
python kb.py update --local-only   # sync metadata, parse and OCR approved PDFs, chunk, index
python kb.py index                 # fill in missing vectors only
python kb.py search "..." --bm25-only --json
python kb.py export offline
```

Local models run through Ollama at `http://127.0.0.1:11434`. Ollama is an external prerequisite, not a Python dependency: install it, start the service, and pull the default tags before the first `update` or `index`:

```bash
ollama pull qwen3-embedding:8b
ollama pull glm-ocr:latest
ollama pull gemma4:e4b
```

These are the code defaults (`qwen3-embedding:8b` with 1,024 dimensions, `glm-ocr:latest` and `gemma4:e4b` for local relation verification), not a tested combination. The OCR endpoint can be redirected with `GLM_OCR_URL`, and the local verifier model can be changed in settings. Cloud verification and Q&A are optional for ingest, indexing, search and export, but cited answers and relation verification require a configured OpenAI-compatible endpoint and key in the settings page. On Windows, keys are protected with DPAPI. New full texts are fetched only after you approve them in the Web approval page.

## Architecture

`kb.py` is the only command entry point. `kbapp` is a single application built from eight modules with one-way dependencies:

| Module | Responsibility |
|---|---|
| `core` | Paths, I/O, fingerprints, locks, text and asset assembly |
| `storage` | SQLite schema, structural migrations and persistent events |
| `integrations` | Zotero, Ollama, cloud models, OpenAlex and OCR adapters |
| `library` | Metadata, attachments, parsing, recognition and note sources |
| `search` | Source index, vector cache, keyword and hybrid retrieval |
| `research` | Evidence checks, reading, Q&A and research relations |
| `workflows` | Task coordination, updates, publication, migration and operations |
| `web` | App factory and HTTP routes grouped by feature |

Dependencies flow from the HTTP/CLI entry points through `workflows` → `research` → `search` → `library` → `core`. `AppContext` supplies paths, storage, search and the job coordinator; `create_app(context)` assembles the FastAPI app. Importing a module does not open the database, start a thread or call a model. The front end is served by Python and needs no Node build at runtime.

| `_index` path | Contents |
|---|---|
| `state` | SQLite database, settings, migration journal and inventories |
| `indexes` | Published source, keyword and vector indexes, by version |
| `cache` | OCR, embedding, metadata, extraction and verification caches |
| `files` | Full texts managed by the application |
| `runtime` | Locks, queues, logs and isolated verification data |
| `exports` | Offline page, machine entries, audit and evaluation reports |
| `backups` | SQLite database backups only (`workbench.sqlite3` plus `manifest.json`); PDFs, Zotero originals and Obsidian notes are not included |
| `archive` | Old indexes, experiments and reference sources |

SQLite's `active_version` is the single authority for the live index. Normal search reads the published version; a failed rebuild never replaces the last good one. The database schema version and the data-layout version are tracked separately.

## Development

```bash
python -m pytest -m "not network and not model"
ruff check .
python kb.py doctor architecture
```

The Python checks use `pytest`; the offline suite is the one selected by `not network and not model`, the same filter CI uses. `pyproject.toml` defines those two markers; there is no `live` marker, so `-m "not live"` does not exclude anything. CI is configured for Python 3.10 and 3.13 on Ubuntu and 3.13 on Windows, with a nightly job on 3.14. Browser-logic checks are `.cjs` files under `tests/` and run with Node's built-in test runner; Node is not a runtime dependency. `doctor architecture` checks module dependency direction and import side effects.

## Support

### Data safety comes first

An upgrade runs schema and data-layout migrations against the only copy of your library. Migrations are part of the support surface, not an implementation detail.

The safe upgrade path has four commands; the last one is only for going back:

```bash
python kb.py backup --label before-upgrade
python kb.py data plan
python kb.py data migrate
python kb.py data rollback   # only when you need to go back
```

- `backup` uses SQLite's backup API, runs `PRAGMA integrity_check` and records key table counts (`kbapp/workflows/backup.py:32-52`).
- `plan` is strictly read-only. It returns the paths and a per-file SHA-256 inventory without moving anything (`kbapp/workflows/data.py:61-78`).
- `migrate` writes a journal before the first move, verifies every file against the inventory, resumes after an interruption and stops on a target conflict instead of overwriting (`kbapp/workflows/data.py:131-205`).
- `rollback` reverses the file moves and path fields while keeping business records (`kbapp/workflows/data.py:207-236`).
- Schema migrations are separate from the layout commands. They run transactionally and take a `pre-schema-<version>.sqlite3` backup before applying DDL (`kbapp/storage/schema.py:88-101`); restore that file if a new version cannot open the database.

These are commitments, not tips:

- An upgrade that cannot complete without losing data is a high-priority bug, not a configuration question.
- A migration never silently overwrites an existing target. It stops and reports the conflict.
- A layout change does not trigger a full OCR pass or a full vector rebuild.
- A backup is required before an upgrade. PDFs, Zotero originals and Obsidian notes live outside the SQLite database; the database backup does not include them, so back them up with the tools that own them.

Run migrations with the service stopped and no active tasks. `migrate` checks the job, agent-run and reference queues and refuses to proceed while one is running (`kbapp/workflows/data.py:52-59`).

### Version support

littrace is pre-release: the repository is private and no build has been published. The only supported version is the current tip of `main`; earlier commits are unsupported. There are no maintained release branches, no LTS and no backports — a fix lands on `main`.

| Channel | Status |
|---|---|
| `main` at the tip | Supported |
| Any earlier commit | Not supported |
| Release builds | None published |

After the first public release, support narrows to the latest release only; anything older will be unsupported. The release table, with a real version number and date, will be published then; nothing is announced in advance.

### Compatibility

The status of Python versions, platforms, Zotero releases and local model combinations is defined once in [`docs/support-matrix.json`](docs/support-matrix.json). README and `SECURITY.md` point here for the authoritative matrix and do not maintain a separate one. If a summary elsewhere disagrees, the JSON file wins. Status is not a promise of support: the file currently marks Zotero and Ollama `unverified` and leaves their `tested_versions` empty.

### Response expectations

Support is best effort from a single maintainer. The target is a first reply within 7 days. There is no SLA, no guaranteed fix time and no bug bounty. Report vulnerabilities through the private advisory form in [`SECURITY.md`](SECURITY.md), not a public issue. For bug reports, include the output of `python kb.py doctor status` with keys and personal paths redacted. A reproducible report against the current tip of `main` is the fastest path.

### Out of scope

- Builds you modified yourself, including patched dependencies or a forked `kbapp`.
- Older commits and pre-release snapshots.
- A report produced only by a scanner, with no reproduction on a clean checkout.
- Local model or proxy configuration errors: a wrong Ollama URL, a missing model tag, a proxy that blocks the request.
- Problems in Zotero itself, or in a Zotero library that littrace only reads.
- Feature requests. They are welcome, but they are not support commitments.

### Hosted service and institutional licensing

If a hosted service launches, it will run the latest version only, which is compatible with this policy. Institutional dual-licensing customers may need a pinned version; that will be covered by a separate institutional support agreement, not by changing the community policy.

When a Zotero plugin ships, it will get its own plugin × Zotero version matrix. The current matrix covers the desktop workbench only.

## Roadmap

**Roadmap is directional, not a commitment. Items may be reordered, changed, or dropped.**

| Stage | Direction | Layer |
|---|---|---|
| **Now** | Stabilize the open core and its documentation. Existing open-source capabilities stay complete and are not moved into a paid tier. | Open |
| **Next** | Zotero plugin. Form and scope are undecided; the first step is a feasibility prototype in the public repository. No feature list is promised. | Open |
| **Later** | Commercial layer. The paid shape and pay points are undecided; the principles are in [Business model](#business-model). | Commercial |

The table has no dates on purpose. A date would turn a direction into a promise.

## Business model

littrace is a local-first literature toolkit. The core is open source under AGPL-3.0-only; an optional commercial layer funds its maintenance.

### Paid forms

The paid feature set is not decided yet. This section describes principles, not a price list.

1. Advanced features in the future Zotero plugin — scope to be announced.
2. Hosted service subscription — scope to be announced.
3. Institutional dual licensing — scope to be announced.

### Principles

- **(a) No paywall on existing open source.** Existing open-source features are never moved behind a paywall. A paid capability must be additive.
- **(b) Local first stays free where possible.** Cloud-side, collaboration and scale capabilities are the natural paid surface.
- **(c) Clear boundaries.** Commercial code and the AGPL-3.0-only core keep a clear repository and process boundary. A commercial license never reduces anyone's rights under AGPL-3.0-only.
- **(d) Published terms.** Prices, terms, and what is and is not included are published and versioned.
- **(e) Contributors.** Contributors are credited as described in the contribution terms. Contributions are accepted with a DCO sign-off; the separate terms covering attribution and use in commercial versions are in [`CONTRIBUTING.md`](CONTRIBUTING.md).

### Open and commercial boundary

| Component | Repository | License |
|---|---|---|
| Open-source core | Public | AGPL-3.0-only |
| Advanced features of the future Zotero plugin | Private | Commercial |
| Hosted service | Private | SaaS terms |

This table is about repositories and licenses, not features. A component becomes public only by publishing its source under the stated license.

### Releases

Every public release corresponds to source code and a changelog at the same tag in the public repository. The tag is the source of truth for what shipped. There is no release whose source exists only in a private branch.

### Privacy and telemetry

**No telemetry by default.** littrace has no analytics SDK, usage beacon or background version check. It does make network requests for features you use: Zotero is read locally, while OpenAlex/Crossref metadata, cloud model verification and PDF downloads run only when those features are enabled or triggered. No request carries usage analytics. If telemetry is ever added, it will be opt-in, documented here, and switchable off.

## Sponsors

littrace is maintained by one person. Sponsorship pays for maintenance: dependency upgrades, CI minutes, documentation, and the time it takes to keep migrations safe.

| Channel | Link |
|---|---|
| GitHub Sponsors | <https://github.com/sponsors/kingside33> |
| 爱发电 (Afdian) | <https://afdian.com/a/littrace> |
| Open Collective | <https://opencollective.com/littrace> |
| Corporate sponsorship | Open an issue: <https://github.com/kingside33/littrace/issues> |

Chinese readers can also use the WeChat Pay and Alipay QR placeholders in [`README.zh-CN.md`](README.zh-CN.md) (`docs/assets/sponsor-wechat.png` and `docs/assets/sponsor-alipay.png`). Those images are placeholders; no real payment details are stored in this repository. Donations are voluntary, no invoice is issued for them, and they unlock no features.

Corporate sponsorship benefits are limited to a logo, an acknowledgment in the README and priority triage of issues. They do not include paid features, a private roadmap or a commercial license.

**Sponsorship funds maintenance. It does not unlock paid features; paid features are sold separately.**

The GitHub Sponsors, Afdian and Open Collective links are placeholders until those accounts are opened. Corporate sponsorship is handled through a GitHub issue; no e-mail address is published.

A short sponsorship update is posted quarterly when there is something to report: dependency upgrades, CI minutes, documentation and other maintenance work.

## License

littrace is licensed under the GNU Affero General Public License, version 3 only. SPDX identifier: `AGPL-3.0-only`. See [LICENSE](LICENSE).

Using littrace locally, modifying it for yourself and running it inside your lab are unrestricted. The obligations attach to distribution and to running a modified version as a network service: those users must be able to obtain the corresponding source.

The papers, PDFs, notes and exports you process with littrace remain yours. The license does not reach your research data.

A separate commercial license, if offered, covers only the code owned by this project. Third-party dependencies remain under their own licenses. For example, PyMuPDF is AGPL-3.0-only or commercial from Artifex, and its terms apply independently of littrace's license. See [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) for the dependency inventory and licenses, and check it before redistributing or hosting littrace.

Project names and marks are covered by [`TRADEMARKS.md`](TRADEMARKS.md).

### Contributing

Bug reports and patches are both welcome. Read [`CONTRIBUTING.md`](CONTRIBUTING.md) and [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) first: your contribution stays yours, is licensed to everyone under AGPL-3.0-only, and additionally grants the maintainer a commercial-use license; there is no CLA, and every commit needs a DCO `Signed-off-by`.

---

If littrace saves you time, consider starring the repository.
