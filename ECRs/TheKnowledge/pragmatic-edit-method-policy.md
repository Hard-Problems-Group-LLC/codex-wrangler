# Engineering Change Request: Pragmatic Edit Method Policy

## Summary

TheKnowledge and Codex-facing guidance should replace rigid requirements to
use one edit mechanism for every manual file change with a pragmatic,
risk-based edit-method policy.

Patch-style edits remain the preferred default for small localized changes,
because they produce targeted diffs and reduce accidental overwrites. However,
requiring patch helpers for every edit is counterproductive for whole-file
documentation records, generated content, mechanical multi-file changes, and
environments where the patch helper is unavailable or broken.

## Incident

During `codex-wrangler` maintenance on 2026-04-15, the active Codex session
had a higher-level instruction to always use `apply_patch` for manual edits.
That instruction conflicted with the local environment: `apply_patch` failed
before touching files with the sandbox-helper error:

```text
bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted
```

Repo-local writes were otherwise permitted and worked without approval prompts.
The rigid patch-only rule therefore caused repeated failed attempts and wasted
operator time, even though safe named-file local writes could complete the
requested work with normal `git diff` verification.

## Current Problem

A single mandated edit mechanism conflates two different concerns:

- edit safety, which comes from targeted scope, preserving unrelated changes,
  reviewing diffs, and validating behavior; and
- edit transport, which may be a patch helper, direct whole-file creation, a
  short script, a formatter, or another explicit tool.

When the transport is treated as the safety policy, agents keep retrying a
broken or inefficient tool instead of choosing the safest practical method for
the shape of the edit.

## Proposed Policy

Adopt this guidance in TheKnowledge-managed agent instructions and starter
material:

```text
Prefer patch-style edits for small, localized manual changes because they keep
diffs targeted and reviewable. For whole-file creation, large documentation
records, generated content, mechanical multi-file updates, or environments
where the patch helper is unavailable or failing, use an explicit
non-destructive local edit command or short script against named files. If a
tool or approach proves generally unreliable in a repository, either open a
bug and surface the issue to the operator, or lower that tool or approach in
the strategy set for future work. Treat repo-local tool reliability as
remembered context so agents do not keep rediscovering the same failure.
After editing, inspect git diff and run the
appropriate validation. Never overwrite unrelated user changes.
```

## Requirements

- Keep patch-style edits as the preferred method for small localized code and
  documentation changes.
- Permit direct named-file writes for new whole-file documentation records,
  project-management entries, and generated content.
- Permit short scripts or codemods for mechanical multi-file transformations
  when the file list and transformation are explicit.
- Require `git diff` or equivalent review after edits.
- Preserve the existing rule against reverting or overwriting unrelated user
  changes.
- Treat repeated patch-helper failure as a reason to switch methods, not as a
  reason to retry indefinitely.
- When a tool or approach is generally unreliable in one repository, either
  record the failure as a bug or complaint and tell the operator, or demote
  that tool or approach in the local strategy set. Treat repo-local tool
  reliability as remembered context so agents do not keep rediscovering the
  same failure.
- Local reliability records should include the known failure signature and
  the preferred fallback so future agents can avoid relearning the same
  environment-specific failure by trial and error.

## Non-Goals

- Encourage broad shell one-liners that obscure which files are being changed.
- Remove the preference for patch-style edits where they are the clearest and
  safest tool.
- Relax git review, validation, or user-change preservation rules.

## Recommended Validation

- Add a documentation test or fixture review that confirms TheKnowledge
  starter guidance no longer says patch helpers are mandatory for every
  manual edit.
- Add examples covering small localized patches, whole-file record creation,
  generated-content refreshes, and explicit mechanical multi-file updates.
- Confirm consuming projects can add a local policy override without fighting
  managed AGENTS footer content.

## Why This Belongs To TheKnowledge

TheKnowledge exists to standardize safe work across consuming projects. A
transport-specific mandate is less robust than a safety-oriented edit policy,
especially across sandboxes, terminals, generated documentation workflows, and
large maintenance tasks. The standard should optimize for targeted scope,
reviewable diffs, validation, and preservation of user work rather than one
particular edit transport.
