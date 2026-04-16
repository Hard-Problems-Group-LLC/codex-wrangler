<!-- THEKNOWLEDGE_MANAGED_HEADER_START -->
This project uses The Hard Problems Group's specifications and guidance
framework, TheKnowledge. Both AI agents and human developers should reference
`TheKnowledge/AGENTS.md` for detailed instructions.

AI agents must load `TheKnowledge/AGENTS.md` before running automated
tooling that edits, validates, stages, or tests repository files.

Keep only a very small amount of context locked in active memory: that
preflight rule, any live-state override record map, and repo-specific
non-destructive safety limits.

---

Any project-specific `AGENTS.md` content should go below this line and above
the managed TheKnowledge footer.
<!-- THEKNOWLEDGE_MANAGED_HEADER_END -->

## Project-Specific Instructions
- Maintain the top-level `CHANGELOG.md` using a Keep a Changelog style
  structure with an `Unreleased` section.
- Treat `project-management/completed-tasks.txt` as execution history for
  human and AI work, not as a substitute for the product changelog.
- Before commit workflows clear
  `project-management/state/pending-commit-changes.txt`, preserve its notable
  repository changes in `CHANGELOG.md`.
- Prefer `python scripts/git_commit_with_changelog.py -m "<subject>"` over a
  direct call to the TheKnowledge commit helper so the changelog sync happens
  before the pending-commit queue is truncated.
- Prefer targeted patch-style edits for small localized changes. For whole-file
  creation, large documentation records, mechanical multi-file updates,
  generated content, or environments where the patch helper is unavailable or
  failing, use an explicit non-destructive local edit command or short script
  against named files, then inspect `git diff` and run the appropriate
  validation. If a tool or approach proves generally unreliable in this
  repository, either open a bug and surface the issue to the operator, or
  lower that tool or approach in the strategy set for future work. Treat
  repo-local tool reliability as remembered context so agents do not keep
  rediscovering the same failure.
- Known local tool reliability record: `apply_patch` has failed in this
  checkout with `bwrap: loopback: Failed RTM_NEWADDR: Operation not
  permitted`. Try targeted patch-style edits when appropriate, but after
  one such failure in a session, demote that tool and use an explicit
  non-destructive named-file edit fallback instead of retrying it.

<!-- THEKNOWLEDGE_MANAGED_FOOTER_START -->
---

<!-- TheKnowledge-managed footer: place overrides here when the consuming
project needs behavior different from TheKnowledge's own repository setup. -->

## TheKnowledge Overrides
- Use `project-management/backlog.txt` as ordered pending work.
- Keep `project-management/tasks-in-progress.txt` minimal and current.
- Record finished work at the top of
  `project-management/completed-tasks.txt` with ISO 8601 timestamps.
- Track operator actions for AI in `project-management/ai-human-requests.txt`.
- Track proposal records under `project-management/proposals/` using
  `approved/`, `rejected/`, `deferred/`, and `under-review/`.
- Use `project-management/deferred.txt` for explicitly deferred work.
- Queue brief commit-ready summaries in
  `project-management/state/pending-commit-changes.txt`.
- Maintain bug lifecycle summary files under `project-management/bugs/` and
  detailed bug records under `open/`, `in-progress/`, and `closed/`.
- Use `TheKnowledge/standards-and-practices/docs/`
  `AI-backlog-iteration.txt` when told to iterate the backlog.
- Use the consuming project's own `project-management/git-flow.txt` for branch
  and merge operations.
- Treat source documentation as delivery work. Every maintained source file
  should carry top-of-file context, every type and callable should be
  documented, and non-trivial control flow should carry local rationale where
  structure alone would be ambiguous.
- Bootstrap, setup, prerequisite, and environment-selection code should
  follow Python 3.9 best practices unless a higher floor is documented.
- Normal runtime, automation, test, and developer-tooling code should follow
  Python 3.12 best practices unless a component is intentionally constrained.
- Use `./install.sh` when the project is relying on the managed starter
  Python toolchain. The default starter installs `install.sh`,
  `bootstrap.sh`, `scripts/install-stage-2.py`, `bootstrap-stage2.py`,
  `python-environments.json`, `.python-version`, `set-context.sh`,
  `set-context-bootstrap.sh`, `requirements-dev.txt`,
  `scripts/dev_setup.py`, `scripts/python_environment_bootstrap.py`,
  `scripts/tool_validation_profiles.py`, `tool_execution_constraints.json`,
  and `tool_validation_profiles.json`. That flow starts from Python 3.9+,
  defaults to a user-local standard install, supports explicit repo-local
  development mode and venv-only mode, permits system installs only with
  `--system` under root, and requires `direnv` for development mode.
  `bootstrap.sh` and
  `bootstrap-stage2.py` remain compatibility wrappers.
- For substantive development work, prefix intermediary status
  updates with an inline bracketed ISO 8601 timestamp including the
  timezone offset, for example
  `[2026-03-25T01:05:12-07:00] Running full pytest.`
- Use timestamped updates when work begins, before and after
  commands or waits likely to take more than a few seconds, and at
  major phase boundaries.
- Include elapsed durations when they are easy to compute.
- Keep final answers readable; this rule applies to intermediary
  development updates for workflow profiling, not to every sentence
  of casual chat.
- Before any `git add`, list the files about to be staged and ask the
  operator whether to review them.
- Offer these staging-review choices: `1.` review at least one file in the
  changeset, `2.` proceed without review for this changeset, `3.` proceed and
  suppress review prompts for the rest of the current session until the
  operator asks to resume them.
- If review is requested, prefer changeset review in Meld when
  available as the default visual review path.
- Launch `meld .` from the repository or submodule root so Meld opens its
  version-control view for the full working tree.
- If Meld version-control view is unavailable or unsuitable, compare a
  temporary clean snapshot directory against the working tree in Meld's
  folder-comparison mode.
- Otherwise offer file-by-file review in the conversation or abort the
  staging step.
- Never guess a Git author or committer email address from commit history,
  hostnames, remote URLs, network overlays, or similar context.
- Require an explicit commit identity before creating a commit. Prefer
  configured `git` properties such as `user.name` and `user.email`; explicit
  `GIT_COMMITTER_*` and optional separate `GIT_AUTHOR_*` overrides are also
  acceptable when set deliberately.
- When a separate author identity is not explicitly configured, reuse the
  explicit committer identity for author instead of inventing a second
  address.
- Run `git diff` before any `git add` and `git diff --cached` before any
  commit. The standardized commit helper does both automatically.
- Use `python TheKnowledge/scripts/git_standard_commit_push.py -m
  "<subject>"` after the operator has either reviewed the changes or
  explicitly approved proceeding. Pass `--assume-reviewed` only after an
  explicit review decision made outside the helper. Use
  `--resume-review-prompts` to re-enable prompts for the current shell
  session. The helper rejects commits whose author/committer identity is not
  explicitly configured.
- When working primarily in the consuming project and discovering bugs,
  proposals, complaints, or general notes about TheKnowledge itself,
  record them on the TheKnowledge `Feedback` branch.
- When filing that feedback from a consuming project, use the active
  `TheKnowledge/` submodule checkout in that project. Prefer
  `python TheKnowledge/scripts/send_theknowledge_feedback.py prepare`
  and `finish` so the helper captures and restores the submodule state for
  you. Use `--push` only when the configured remote push URL is writable for
  the current operator, and use `abort` when you want to restore the prior
  state without publishing.
- When the active `TheKnowledge/` checkout is read-only for upstream
  maintenance, draft the request first under `ECRs/TheKnowledge/` in the
  consuming project so the handoff stays reviewable before it reaches a
  writable TheKnowledge checkout.
- The `Feedback` branch is only for cross-project feedback flowing back
  into TheKnowledge. Direct maintenance of TheKnowledge itself should keep
  using its normal internal trees on `trunk`.
- Projects may keep proprietary or third-party knacks in the consuming
  project's top-level `knacks/` directory, separate from the stock knacks
  under `TheKnowledge/knacks/`.
- Validate changed knack files with
  `python TheKnowledge/scripts/validate_knacks.py --project-root .`.
- Knack validation should stay lightweight: malformed Markdown and
  high-entropy findings are errors, word-count overruns are warnings, the
  validator uses `.git/knack-validation-cache.json`, and path collisions with
  stock knacks should warn while still evaluating both files.
- When updating the TheKnowledge submodule itself, prefer
  `python TheKnowledge/scripts/update_theknowledge_submodule.py`
  `--project-root . --knowledge-root TheKnowledge`. That helper fetches
  and briefly summarizes the incoming upstream `trunk` delta, adopts the
  reviewed version, runs the drift report, refreshes the managed starter
  files when needed, and leaves a reviewable parent-repo diff.
- For a manual managed-file check without adopting a new upstream commit, run
  `python TheKnowledge/scripts/report_managed_agents_drift.py`
  `--project-root . --knowledge-root TheKnowledge`.
- Follow `tool_execution_constraints.json` when it marks a tool-and-
  environment combination unsafe to parallelize. In a matched Codex sandbox,
  run Black only through
  `python TheKnowledge/scripts/run_tool_with_timeout.py black`; the
  harness will fall back to exactly one file at a time when Black would
  otherwise hit the known multi-file hang. Do not assume `black -W 1` is
  enough. If another file-safe formatter or linter still hangs inside a
  Codex sandbox on a multi-file run, retry the explicit file list one file at
  a time, and rerun the full required checks outside the affected sandbox or
  in CI before clearing the work.
<!-- THEKNOWLEDGE_MANAGED_FOOTER_END -->
