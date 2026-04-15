# Engineering Change Request: Standardized Commit Helper Wrapper Contract Drift

## Summary

TheKnowledge's standardized commit workflow currently presents conflicting
contracts to consuming projects.

Some managed guidance tells consuming projects to invoke helper scripts
through `TheKnowledge/scripts/...`. The standardized commit helper itself
instead assumes repo-root compatibility wrappers such as
`scripts/run_quality_gate_cached.py`, which then assume additional repo-root
wrappers like `scripts/run_tool_with_timeout.py`.

That contract drift turned a routine add/commit/push cycle in
`codex-wrangler` into a repair session. The workflow did not fail because the
repository lacked review or because quality checks found real defects. It
failed first because the documented helper path and the helper's own runtime
assumptions no longer matched.

## Incident

During a normal commit-and-push cycle in `codex-wrangler`:

- the repository-specific wrapper
  `scripts/git_commit_with_changelog.py` delegated to
  `TheKnowledge/scripts/git_standard_commit_push.py`;
- `git_standard_commit_push.py` tried to run
  `scripts/run_quality_gate_cached.py` from the consuming repository root;
- the consuming repository did not yet provide that wrapper or the adjacent
  repo-root helper entrypoints that the quality gate expected;
- the commit workflow therefore stalled before it could complete the normal
  validation and push path.

To recover, `codex-wrangler` had to add thin compatibility wrappers for:

- `scripts/run_quality_gate_cached.py`
- `scripts/run_tool_with_timeout.py`
- `scripts/validate_knacks.py`
- `standards-and-practices/dev-utils/security/run_entropy_harness.py`
- `standards-and-practices/dev-utils/security/verify_entropy_tripwire.py`

Only after that compatibility layer existed could the standardized helper
reach the repository's actual quality checks and expose separate local
defects.

## Why This Belongs To TheKnowledge

TheKnowledge currently sends mixed signals about the supported consuming-
project interface:

- `TheKnowledge/templates/project-management/git-flow.txt` tells consuming
  projects to run required checks through
  `python {{THEKNOWLEDGE_ROOT}}/scripts/run_tool_with_timeout.py ...`.
- That same template points the standardized push path at
  `python {{THEKNOWLEDGE_ROOT}}/scripts/git_standard_commit_push.py -m
  "<subject>"`.
- `TheKnowledge/templates/AGENTS-footer.md` still instructs consuming
  projects to invoke `run_tool_with_timeout.py` through
  `{$KNOWLEDGE_ROOT}/scripts/...`.
- `TheKnowledge/scripts/git_standard_commit_push.py` instead hardcodes
  `scripts/run_quality_gate_cached.py` relative to the consuming repository
  root.
- `TheKnowledge/scripts/run_quality_gate_cached.py` then hardcodes
  `scripts/run_tool_with_timeout.py`, again relative to the consuming
  repository root.
- The managed starter file list in the consuming-project `AGENTS.md` footer
  does not clearly promise that those repo-root compatibility wrappers will
  exist.

So a consuming project can follow the documented TheKnowledge workflow and
still fail at runtime because TheKnowledge's own standardized helper stack is
expecting a different entrypoint layout.

## What Was Not TheKnowledge's Fault

Not every part of the long cycle was an upstream bug.

- The repo-local Codex launcher uses an isolated `HOME`, so global git
  identity settings were not visible to the standardized helper. Requiring an
  explicit identity was consistent with policy, even though it added
  friction.
- The final push initially used the wrong GitHub SSH identity because this
  repository's `origin` push URL still targeted `git@github.com:...` instead
  of the operator's `github-hpg` host alias.
- Once the helper stack could actually run, the repository still had its own
  local quality-gate defects to fix.

Those issues mattered, but they were secondary. The first hard stop was the
TheKnowledge helper-contract mismatch.

## Requested Change

TheKnowledge should choose one consuming-project contract and enforce it
consistently.

Minimum acceptable outcomes:

- If repo-root wrappers are the supported public interface, the managed
  starter must install them all, the templates must document them, and the
  standardized helper tests must verify that a consuming project has the full
  required wrapper set.
- If `TheKnowledge/scripts/...` is the supported public interface, then the
  standardized helper stack must stop hardcoding repo-root `scripts/...`
  paths and instead resolve sibling helper scripts from the TheKnowledge
  checkout.

In either case, TheKnowledge should add an end-to-end regression test that
simulates a consuming project using the documented standardized commit path
and fails if any required helper entrypoint is missing.

## Recommended Validation

- Start from a fresh consuming-project fixture that has only the documented
  managed starter files.
- Run the documented commit helper path in dry-run mode.
- Confirm that the helper reaches quality-gate execution without any
  missing-path or missing-wrapper failures.
- Confirm that the documented required-check commands and the helper's own
  internal command resolution agree on the same entrypoint family.

## Why This Matters

TheKnowledge is supposed to reduce process variance for consuming projects.
When its standardized helper stack and its managed guidance disagree about the
entrypoints that are supposed to exist, the consuming project inherits hidden
workflow breakage right where operators expect the most reliable path:
routine validation, commit, and push.
