## What this changes

<!-- One or two sentences. What would a user or another contributor notice? -->

## Why

<!-- The reason for the change, or the symptom it fixes. For a fix, describe
     what the user saw before the change. -->

Fixes #

## How it was verified

<!-- Tick what you actually ran; delete what does not apply.
     Setup: python -m pip install -e ".[dev]" -->

- [ ] `python -m pytest`
- [ ] `node --test tests/test_*.cjs` (Git Bash; on PowerShell list the files)
- [ ] `ruff check .`
- [ ] Tried the affected command or workbench flow locally
- [ ] Not applicable, because:

### Scope not verified

<!-- Platforms, model providers, or data shapes you could not exercise.
     Write "none" if nothing is left out; do not leave this blank. -->

## Checklist

- [ ] Tests were added or updated for the changed behaviour
- [ ] Documentation was updated when behaviour, commands, or configuration changed
      (`README.md`, `READING_SPEC.md`, or the relevant docstring)
- [ ] `ruff check .` is clean
- [ ] No API keys, tokens, cookies, personal file paths, or private library data
      are included in the diff or logs

## Developer Certificate of Origin

- [ ] Every commit in this pull request is signed off under the
      [Developer Certificate of Origin](https://developercertificate.org/).
      I used `git commit -s` (or added the trailer with `git commit --amend -s`),
      the sign-off e-mail matches the commit author e-mail, and I understand
      that the `dco` check requires a `Signed-off-by:` trailer on every commit.

<!-- This checkbox is a reminder, not the signature. CI reads the
     `Signed-off-by:` trailers in the commits, not this box. If this PR is
     squashed, the squash body must keep every author's Signed-off-by; GitHub
     does not copy them from the squashed commits. -->

## Contribution terms

- [ ] I have read [CONTRIBUTING.md](https://github.com/kingside33/littrace/blob/main/CONTRIBUTING.md)
      and accept the contribution terms (v1), including the maintainer
      commercial-use licence. I understand that this is a licence, not a
      copyright assignment, and that there is no CLA.

<!-- The terms checkbox records that you read the terms. The enforceable
     acceptance is described in CONTRIBUTING.md: the terms statement, this
     checkbox and the commit sign-offs together. -->
