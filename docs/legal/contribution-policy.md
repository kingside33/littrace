# Contribution authorization policy

Status: accepted
Decision date: 2026-11-19
Decision-time `main` tip: `201cf72` (`docs(adr): 决策记录批次 w3 (#39)`)
Contribution terms: v1 (effective 2026-11-17)

This document records why littrace accepts contributions under the Developer
Certificate of Origin (DCO) plus the v1 contribution terms, and why it does not
use a Contributor License Agreement (CLA). It is engineering policy, not legal
advice. It is the single place that states the conclusion; `CONTRIBUTING.md`
remains the normative text of the terms.

## Decision

littrace keeps **DCO 1.1 plus the v1 maintainer commercial-use licence** in
`CONTRIBUTING.md`. The project **does not use a CLA**, does not ask contributors
to assign copyright, and does not use a signing service or any third-party
compliance dependency.

Concretely:

- **Provenance.** Every commit carries a `Signed-off-by` trailer added with
  `git commit -s`, certifying the four statements of DCO 1.1
  (`CONTRIBUTING.md:532-537`). The `dco` CI job enforces this by running
  `python scripts/ci/check_dco.py` (`.github/workflows/ci.yml:296-316`).
- **Rights.** The v1 terms grant the maintainer the rights needed for paid
  features, hosted services and separately licensed editions
  (`CONTRIBUTING.md:539-552`).
- **Acceptance.** The terms are accepted by the statement in
  `CONTRIBUTING.md`, the contribution-terms checkbox in the pull request
  template (`.github/PULL_REQUEST_TEMPLATE.md:50-59`) and the sign-off on each
  commit (`CONTRIBUTING.md:614-626`).
- **No CLA infrastructure.** There is no CLA text, no signing service, no
  signature-status lookup, and no third-party compliance account, secret or
  environment variable in this repository. "DCO replaces a CLA" is stated in
  the workflow comments (`.github/workflows/ci.yml:286-289`).

## Why

### 1. The rights the project needs are already granted

The current terms already supply everything a CLA would be introduced to
obtain; a CLA would duplicate a licence that is already broader than what the
project needs.

- **§5, maintainer commercial-use licence** (`CONTRIBUTING.md:539-552`) grants
  the maintainer, its successors and assigns a **perpetual, irrevocable,
  worldwide, non-exclusive, transferable, royalty-free copyright licence, with
  the right to sublicense through multiple tiers**, to reproduce, modify,
  adapt, translate, distribute, publicly communicate, publicly perform,
  publicly display and otherwise use a contribution. It explicitly covers
  **paid features, hosted services and separately licensed editions**, and
  allows sublicensing under any licence, including proprietary and commercial
  licences.
- **DCO does not grant sublicensing** (`CONTRIBUTING.md:550-552`): the DCO
  certifies provenance but grants no copyright licence and no right to
  sublicense. §5 is the section that supplies that right; a CLA is therefore
  not needed to fill a gap in the DCO.
- **§6, permitted uses** (`CONTRIBUTING.md:554-562`) limits the §5 licence to
  the project's related commercial features, paid services (including hosted or
  cloud subscriptions) and commercially licensed editions, including
  institutional dual licensing. It also states that the maintainer is not
  obliged to use the licence, merge the contribution or pay for it, and that a
  contributor may keep licensing their work elsewhere.
- **§8, patent licence** (`CONTRIBUTING.md:572-581`) grants a perpetual,
  irrevocable, worldwide, royalty-free, non-exclusive patent licence, with a
  termination clause modelled on section 3 of the Apache License 2.0.
- **§11, acceptance and versioning** (`CONTRIBUTING.md:614-626`) records how
  the terms are accepted (statement, checkbox, per-commit sign-off) and that a
  later version applies only to contributions submitted on or after its
  effective date. It does not apply retroactively.
- **§12, survival** (`CONTRIBUTING.md:628-634`) keeps the terms in force after
  merge and after the contributor stops participating; the licences already
  granted are irrevocable.
- The summary statement of the no-CLA stance is at
  `CONTRIBUTING.md:503-506`: the project does not use a CLA and does not ask
  contributors to assign copyright.

### 2. There are no external contributions to protect yet

At decision time the contributor base was one person, so a CLA would impose
cost on a contributor population that does not exist.

Decision-time snapshot at `201cf72` (2026-11-19):

- `git log main --format='%an' | sort | uniq -c` → `17 Zixu Wang` (one author,
  17 commits). Pinned to the decision commit for reproducibility:
  `git log 201cf72 --format='%an' | sort | uniq -c` → `17 Zixu Wang`.
- `git rev-list --count origin/main` → `17`. Pinned form:
  `git rev-list --count 201cf72` → `17`.
- `gh api repos/kingside33/littrace --jq '{visibility,forks_count}'` →
  `public`, `forks_count=0`.

This is a decision-time snapshot, not a standing fact. Re-check it before
relying on it. A re-check on 2026-11-21 at `c35d15c` still showed a single
author (`git log origin/main --format='%an' | sort | uniq -c` → `35 Zixu Wang`,
`git rev-list --count origin/main` → `35`) and `forks_count=0`.

Do not use `--all` for this evidence: `--all` also counts commits on unmerged
branches and overstates the contributor base.

### 3. A CLA would add cost without adding protection

An enforceable CLA is not a checkbox; it brings a stack of obligations the
repository does not have today:

- **Legal text.** Executable CLA wording needs a lawyer to draft and maintain.
  The v1 terms themselves still owe that review (see Known gap).
- **Signing service.** A CLA flow needs either a self-hosted service or a
  purchased one, plus the account and operational work that comes with it.
- **Per-PR enforcement.** Every pull request would need a signature-status
  query and a fail-closed check, or the CLA would be decorative.
- **New failure surface.** That means new secrets, a third-party dependency and
  a new way for CI to fail for reasons unrelated to the change under review.
- **Contributor friction.** Every contributor, including a one-line typo fix,
  would take one more step before a pull request can pass.

The repository has none of this infrastructure. Introducing it for zero
external contributors would add cost and failure modes with no corresponding
rights gain, because §5 already grants the required rights.

## Known gap

The rights position is sound; the acceptance *evidence* has gaps. All three are
recorded here and deferred to a later, separate terms-version upgrade
(`Contribution-Terms: vN` trailer and `docs/legal/contribution-terms.json`).
This policy does not implement that upgrade.

1. **The PR-template checkbox is not persisted in git.** The checkbox in
   `.github/PULL_REQUEST_TEMPLATE.md:50-59` records intent, but its state lives
   in the pull request body, not in the repository history. It is a reminder,
   not a durable signature; the enforceable record is the commit sign-off
   (`CONTRIBUTING.md:616-626`).
2. **The terms version and blob SHA go only to a volatile job summary.**
   `scripts/ci/check_dco.py:403-419` writes the contribution terms version and
   the `CONTRIBUTING.md` blob SHA into the GitHub Actions job summary. That
   summary is not retained as a durable audit record and is not checked by any
   later gate.
3. **The DCO check reads the terms from the default branch.** In
   `scripts/ci/check_dco.py:451-455` the check fetches `CONTRIBUTING.md` and
   `DCO` from the default branch. After a terms upgrade, an old pull request is
   therefore judged under the new terms, which conflicts with §11's
   non-retroactivity rule (`CONTRIBUTING.md:622-626`: a later version applies
   only to contributions submitted on or after its effective date). The
   intended fix is a `Contribution-Terms: vN` trailer that pins the version a
   pull request was submitted under; it is out of scope here.

**Counsel review is still pending.** `CONTRIBUTING.md:643-651` requires counsel
to review the patent grant, the moral-rights waiver, the employer
representation and the governing-law question before the repository becomes
public and before the first external contribution is accepted. That review has
not happened. This document records the intended policy; it does not claim
legal review, and it is not a legal opinion.

## Changing this policy

Changing the decision — in particular introducing a CLA, a signing service, a
third-party compliance dependency or a copyright assignment — requires all of
the following:

1. a new issue and a pull request that state the change and its rationale;
2. an update to this document in the same pull request;
3. a DecisionRecord (title / context / decision / alternatives / consequences)
   reported in the pull request, as required by `AGENTS.md:47-54`, and landed
   by the batch ADR pull request as `docs/adr/NNNN-<slug>.md`;
4. an update to the contribution terms themselves, including the version
   marker and effective date, with the non-retroactivity rule in §11 preserved
   for already-submitted contributions.

It must not be done by quietly adding an action, a service call or a secret to
`.github/workflows/**`.

The guard test `tests/test_contribution_authorization.py` supports this policy:
it scans the real workflows for CLA and signing-service wiring, asserts that
the `dco` job still runs `scripts/ci/check_dco.py` after checking out the
default branch, and checks that this document keeps its decision anchors. The
guard is scoped to CLA and signing-service wiring only. It is not a general
third-party action allowlist; action admission is a separate decision that
needs its own issue and DecisionRecord.
