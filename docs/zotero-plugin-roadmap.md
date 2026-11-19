# Zotero plugin roadmap

Status: **directional, not a commitment**. Items may be reordered, changed or
dropped; no date is promised. This is the public, commercial-information-free
summary of the plugin work. It contains milestones, dependencies, acceptance
criteria and the boundaries the implementation must keep — nothing about
pricing, competitors or commercial terms.

Scope note: the plugin is a shell around the existing local service. Its free
capabilities depend on that service running; nothing in the plugin replaces it.

## What exists today

A feasibility prototype in this repository, fully open source (AGPL-3.0-only):

| Area | State |
|---|---|
| Plugin shell, build chain, CI job | Done (`plugin/`, builds an XPI; CI builds it on Ubuntu and Windows) |
| Four function tabs (search / lineage / approvals / scheduler) as service pages in tabs | Implemented, rendered on a real install (see [plugin-verification.md](plugin-verification.md)) |
| Item-tree columns (「谱系」 lineage, 「精读」 reading) | Implemented; default off, enabled from the column picker |
| Item-pane sections (evidence / lineage relations / close reading) | Implemented, lazy per item with an explicit cache key |
| Reader panel + text-selection entries | Implemented; container form decided on a real install |
| Menus and offline degradation | Implemented; entries disable with a guide when the service is down |
| Settings page (connection / integration / cache / Pro placeholder) | Implemented, including a configurable cache directory |
| Version negotiation (`GET /api/version`) | Implemented with offline tests |
| Plugin × Zotero support matrix | `plugin` section in [support-matrix.json](support-matrix.json), currently `unverified` pending the acceptance pass below |

## Milestones

### M0 — feasibility prototype

Validate every assumption the design rests on before feature work: tab host
mechanics, iframe rendering of the service pages, privileged XHR against the
local service, deep links back into Zotero, and the plugin × Zotero version
matrix. **Acceptance**: `docs/plugin-verification.md` records each item with
evidence; a go/no-go decision is recorded there.

Current state: tab host mechanics, iframe rendering, token round-trip and
version negotiation are verified on Zotero 10.0.8. The lineage column, the
reader panel and deep-link navigation are not yet verified, so the matrix stays
`unverified` and the go/no-go decision is open.

### M1 — free plugin

Ship the plugin shell and its native integration layer: the columns, the
item-pane sections, the reader panel, the four function tabs, the settings page
and the offline-degradation path. **Acceptance**: the manual UI checklist in
`plugin-verification.md` passes end to end on a real install, and the added
automation (pytest offline suite, `node --test` inventory) stays green in CI.

Boundaries that M1 does not change:
- The plugin never writes to the Zotero library; it is a read-only consumer.
- Everything that ships in the XPI is free and AGPL; there is no hidden tier.
- When the service is unavailable, entries disable with a guide. No silent
  failure and no half-working state.

### M2 — service installer (Windows x64)

Replace the current manual service setup with an installer that bundles a
Python runtime, the service dependencies and the full source. **Acceptance**:
a clean machine reaches a running service from the installer alone; upgrade and
uninstall paths are repeatable; the size and duration measured on a real run are
published; third-party licence obligations are met and documented.

### M3 — optional commercial layer

Separate, independently distributed code that drives the open service through
its public HTTP API only. Never imports, vendors or repackages the open core's
internals. **Acceptance** (gates, all required before work starts): a real user
base signal from M1/M2, and a distribution and licensing path that is documented
publicly — terms first, then payment, then automation.

Boundary that M3 does not change: the free capabilities of the open core stay
free. Nothing that exists today moves behind the commercial layer.

## Dependencies

```
M0 (feasibility, real install) ─┬─> M1 (free plugin) ─> M1 stable ─> M2 (installer)
                                └─> version negotiation endpoint (independent)
M2 ─> M3 (commercial layer, gated)
```

- The version-negotiation endpoint is independent of the plugin UI work.
- M2 can start once M1 is stable; it does not wait for M3.
- M3 does not start without its gates, and has no revenue target attached to it.

## How the work is verified

| Layer | Check |
|---|---|
| Python service | `python -m pytest -m "not network and not model"` (offline suite) |
| Frontend modules | `node --test` over the explicit inventory in `.github/workflows/ci.yml` |
| Plugin build | `plugin-build` job (Ubuntu + Windows) in CI |
| Architecture | `python kb.py doctor architecture` |
| Real install | Manual checklist in [plugin-verification.md](plugin-verification.md); privileged context and real-machine behaviour are not unit-tested |

## What this document deliberately does not contain

Pricing, competitor comparisons, tax or payment-channel strategy, and internal
assessment of demand. Those belong to the commercial layer's own terms
documents if and when that layer ships; they are not part of the open
repository's roadmap.
