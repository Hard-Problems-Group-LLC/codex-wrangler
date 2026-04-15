# Engineering Change Request: Changelog SOP and Pending-Queue Preservation

## Summary

TheKnowledge currently standardizes a short-lived
`pending-commit-changes.txt` queue and a commit helper that uses the queue as
commit-body text before truncating it after a successful local commit. That
works for commit hygiene, but it does not preserve notable change history in a
stable changelog.

This ECR proposes making changelog maintenance a standard operating procedure
for repositories that use TheKnowledge templates and workflows.

## Current Situation

- TheKnowledge templates create and document
  `project-management/state/pending-commit-changes.txt`.
- `scripts/git_standard_commit_push.py` uses that queue as commit-body text.
- After a successful local commit, the helper truncates the queue.
- There is no parallel SOP that preserves those notable changes in a
  human-facing changelog.

The result is that repositories can have clean commit messages while still
lacking a durable release-oriented history unless maintainers invent their own
separate process.

## Proposed SOP

Adopt a changelog standard based on Keep a Changelog for consuming projects
and TheKnowledge itself.

Recommended baseline:

- Keep a top-level `CHANGELOG.md`.
- Maintain an `## [Unreleased]` section.
- Use the standard category headings:
  - `### Added`
  - `### Changed`
  - `### Fixed`
- Continue using `pending-commit-changes.txt` as short-lived commit-body
  input, but preserve notable queued items in the changelog before the queue
  is truncated.

## Implementation Options

### Preferred

Add standard changelog guidance and a helper path that syncs pending queue
content into `CHANGELOG.md` before delegating to
`scripts/git_standard_commit_push.py`.

This could be implemented as either:

- native support in `git_standard_commit_push.py`; or
- a standard wrapper script installed by templates and documented as the
  preferred commit path.

### Minimum Acceptable

Document a mandatory pre-commit SOP that requires maintainers to copy notable
entries from `pending-commit-changes.txt` into `CHANGELOG.md` before running
the standardized commit helper.

## Recommended Template and Documentation Changes

- Add `CHANGELOG.md` to starter templates.
- Update template `AGENTS` instructions to distinguish operational history
  files such as `completed-tasks.txt` from the changelog.
- Update git-flow and installation docs to point operators at the changelog-
  preserving commit path.
- Update the pending-queue specification so truncation after commit does not
  imply loss of durable release notes.

## Why This Should Become SOP

- It preserves user-facing change history instead of discarding it after
  commit.
- It reduces the chance that changelog maintenance will be skipped.
- It cleanly separates:
  - operational traceability (`completed-tasks.txt`, bug/proposal records); and
  - delivery history (`CHANGELOG.md`).
- It builds on the existing queue model instead of replacing it.

## Open Questions

- Should changelog support be mandatory for all TheKnowledge repos, or only
  for repos with user-visible releases and operator-facing tooling?
- Should the standard helper learn changelog sync directly, or should the SOP
  standardize a wrapper layer?
- Should templates require the full Keep a Changelog category set, or a
  smaller default such as Added/Changed/Fixed?
