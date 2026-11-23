<!-- free-limits: v1; effective: 2026-11-21 -->

# Free limits

This file is the published, versioned table of the free-tier limits of littrace. It is a terms
document; the commercial layer's code is not in this repository. README states the two headline
numbers in principle (a) and links here for the full table; this file is the single source of
truth, and it wins if they disagree.

| Limit | Key | Free | Pro |
|---|---|---|---|
| Papers in one analysis run (starting set) | `papers_per_run` | 20 | 13000 |
| First-level neighbours expanded per paper | `first_level_neighbours_per_paper` | 10 | 24 |
| Relation-verification units per run | `verifier_units_per_run` | 20 | no per-run unit limit |
| Analysis tasks per day | `analysis_tasks_per_day` | 3 | no daily task limit |

Pro is the current implementation's own scale: 13,000 papers in one analysis request and the
existing candidate budget of 24 first-level neighbours per paper, subject to the caps in "Where
the limits live". Relation verification and the daily task count have no per-run or per-day cap in
the current implementation. Public name `first_level_neighbours_per_paper`; the
entitlement API (`GET /api/v2/entitlements`) exposes the same limit as `references_per_paper`, with
the same number.

## What counts

- The day boundary is the Asia/Shanghai calendar day (UTC+08:00).
- Papers outside your library that an analysis pulls in count against the same per-run number; a
  refused submission reports how many of the requested papers are in the library and how many are
  outside it.
- A resume of the same run or verification job does not count again.
- A submission refused by a limit is rolled back and not counted.
- A failed run is not refunded.
- Re-submitting the same payload is deduplicated and not counted twice.
- There is no manual reset.

## Where the limits live

- The limits are ordinary constants in the AGPL-3.0-only core. They are not in the settings page,
  not in the plugin preferences, and there is no environment variable or command-line switch to
  disable them.
- A Pro entitlement lifts the four limits above back to the Pro values in the table. It does
  not raise the existing per-run external-request and cloud-request caps
  (`external_requests_per_run`, `cloud_requests_per_run`) or the daily model-cost budget
  (`daily_budget`).

## How a limit is visible

- An over-limit submission is refused before any work starts, with the exact number.
- Neighbours skipped by the per-paper limit are recorded in that run's own result; nothing is
  silently truncated into a result that looks complete.

## Changes

Changing a number means a new version marker on the first line and an update to README's
principle (a) in both languages, in the same pull request.
