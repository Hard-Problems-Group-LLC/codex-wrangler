# Changelog

All notable changes to this repository will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this repository aims to follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html) where practical.

## [Unreleased]

### Added

- Added a TheKnowledge ECR and executable companion proposed
  `install-stage-2.py` draft for preserving explicit user-home targeting and
  local managed-file overlays during submodule managed-starter refreshes.
- Added an open bug record for parent-repo entropy scans traversing ignored
  local Codex artifacts and submodule-hosted harness calls missing parent
  relative paths.
- Added `--repair-install` for install and upgrade operations. It removes
  only managed npm install artifacts under `.codex-local` before reinstalling,
  so interrupted npm installs can be repaired without deleting `.codex-home`.
- Added project-local npm cache routing for managed npm operations so install,
  update, upgrade, audit, and self-test avoid the operator's default npm cache.
- Added a low-priority meta bug record for the intentionally empty
  condition of having no new bugs to add.
- Added a local pragmatic edit-method policy and a TheKnowledge ECR to
  replace rigid patch-helper requirements with safety-oriented edit
  guidance.
- Added this top-level changelog and a repo-local commit helper that preserves
  pending commit summaries in it before the queue is cleared by the standard
  commit workflow.
- Added an approved proposal for opt-in managed reasonable-permissions
  defaults in generated `bin/codex-local` launchers, including the rule that
  set and clear operations must preserve the current package selection rather
  than silently reinstalling latest stable.
- Added `--set-reasonable-permissions` and
  `--clear-reasonable-permissions` to persist opt-in launcher defaults that
  add `-a on-request -s workspace-write` only when callers did not
  already choose approval or sandbox behavior.
- Added automatic managed `.gitignore` enforcement for `.codex` and
  `bin/codex-local` so generated local Codex artifacts stay untracked.
- Added stale known Codex version bug tracking records under
  `project-management/bugs/`.

### Changed

- Updated the TheKnowledge submodule to `dcf9e09` and refreshed the managed
  AGENTS footer guidance for Markdown proposal records and ACP scope while
  preserving the project-local `install-stage-2.py` override after validation
  caught the managed refresh dropping `--user-home` support.
- Changed generated `bin/codex-local` launchers to execute the managed
  `.codex-local/node_modules/.bin/codex` binary directly instead of using
  `npx codex`, so a missing local install can no longer fall through to the
  unrelated legacy registry package named `codex`.
- Changed the managed reasonable-permissions launcher default from
  `-a never -s workspace-write` to ACP-capable
  `-a on-request -s workspace-write` so Codex can request escalation for
  commit and push workflows.
- Updated AGENTS, README, development docs, git-flow, and state-file guidance
  so this repository treats `completed-tasks.txt` as operational history and
  `CHANGELOG.md` as durable change history.
- Added approved runtime-fidelity design records plus a compact
  startup-preflight specification and an explicit user-home targeting
  specification.
- Introduced runtime-signal detection and resolved-command diagnostics for
  inspect and self-test, including selector, package-manager, and HOME/XDG
  reporting, and added selector-aware launcher preflight with warn, strict,
  and off modes.
- Hardened `install-stage-2.py` and `install_user_tool.py` so isolated
  repo-local `.codex-home` sessions require `--user-home PATH` or
  `--allow-isolated-home` before any user-scoped pyenv, launcher, direnv, or
  shell-init changes can proceed, and updated docs and tests to match.
- Split Codex catalog refresh from Codex package upgrades so `--update`
  refreshes locally known stable, beta, and alpha versions, `--upgrade`
  requires an explicit `--channel`, and installs/upgrades now carry channel
  metadata plus locally known version tables.
- Extended generated metadata, inspect output, local README content, and the
  tracked `bin/codex-local` launcher to surface and preserve the managed
  reasonable-permissions state.

- Ignore `.local/` in the managed Codex gitignore block and extend the rendering/filesystem tests to enforce it.
- Repair managed bootstrap/runtime selection so explicit fresh 3.12 interpreters can create `.venv`, route hook-install fallback through the parent repo, and bound automatic `direnv` download hangs with an explicit timeout plus regression coverage.


### Fixed

- Added local Codex binary smoke checks and inspect/self-test reporting for
  missing executables, missing platform-package manifests, and mismatched
  requested, lockfile, and installed package versions, making interrupted npm
  installs fail closed with an actionable managed-install error.
- Changed inspect reporting so a corrupt managed `package-lock.json` is
  surfaced as a classified issue instead of escaping as a Python traceback.
- Hardened inspect and configuration loading around corrupt managed metadata
  and package manifests, and documented the intentionally narrow repair scope
  plus remaining operator-owned backup cleanup boundary.
- Hardened managed tree removal so files and symlinks at expected directory
  paths fail closed with explicit errors instead of raw `shutil.rmtree`
  exceptions.
- Clarified install summaries so completed npm installs are not reported as
  still merely required.
- Clarified dry-run filesystem output so simulated writes and removals are
  labeled as `Would write`, `Would update`, or `Would remove`.
- Promoted missing managed `node_modules` from a warning to an inspect issue
  when the managed package manifest exists, because the local launcher cannot
  work in that state.
- Report nonexistent project-root arguments that look like known long-form
  flags without `--` as friendly CLI errors with missing-dash suggestions
  instead of Python tracebacks.
- Added fallback state inference for older managed installs so refresh and
  launcher update notices can still work when package files exist before the
  newer metadata fields do.
- Added repo-local cached quality-gate helper wrappers so the standard commit
  helper can run from this repository root.
