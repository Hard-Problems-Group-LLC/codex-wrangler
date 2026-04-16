# Changelog

All notable changes to this repository will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this repository aims to follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html) where practical.

## [Unreleased]

### Added

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
  add `-a never -s workspace-write` only when callers did not already choose
  approval or sandbox behavior.
- Added automatic managed `.gitignore` enforcement for `.codex` and
  `bin/codex-local` so generated local Codex artifacts stay untracked.
- Added stale known Codex version bug tracking records under
  `project-management/bugs/`.

### Changed

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

### Fixed

- Added fallback state inference for older managed installs so refresh and
  launcher update notices can still work when package files exist before the
  newer metadata fields do.
- Added repo-local cached quality-gate helper wrappers so the standard commit
  helper can run from this repository root.
