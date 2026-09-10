# Changelog

All notable changes to this repository will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this repository aims to follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html) where practical.

## [Unreleased]

### Added

- Added canonical default roots at `.local/codex` and
  `.local/codex-home`, with managed legacy-layout discovery, a
  migration-capable bridge launcher, Linux atomic directory/link exchange,
  exact old-name compatibility links, protected-HOME ownership proof,
  power-loss resumption, and structured inspection reporting.
- Added transactional A/B npm prefixes prepared through unique candidates,
  reversible inactive-slot swaps, one atomic active pointer, stable maintenance
  serialization, durable concordant completion records, and legacy migration
  without in-place package replacement.
- Added elapsed-time subprocess heartbeats, bounded npm fetch retries,
  engine-strict installation, and whole-process-group termination on timeout
  or interruption.
- Added a standalone `--repair /absolute/project/root` operation that proves
  managed ownership, recovers one unambiguous exact Codex version from
  surviving target records, and rebuilds only bounded npm artifacts while
  preserving project-local context and history.
- Added a transplant-safe bootstrap specification, Rocky Linux 10.2 recovery
  notes, and TheKnowledge ECRs for pinned pyenv ownership plus venv runtime
  symlink preservation.
- Added regression coverage for direct bootstrap-wrapper execution,
  runtime-only stage-two provisioning, standard-venv base drift, nested
  validation-runtime selection, and preservation of project-local Codex
  memories during managed npm repair.
- Added a bounded managed npm operations specification and regression tests
  for npm timeout reporting plus install command wiring.
- Added a TheKnowledge ECR and executable companion proposed
  `install-stage-2.py` draft for preserving explicit user-home targeting and
  local managed-file overlays during submodule managed-starter refreshes.
- Added an open bug record for parent-repo entropy scans traversing ignored
  local Codex artifacts and submodule-hosted harness calls missing parent
  relative paths.
- Added `--repair-install` as a compatibility modifier that uses the normal
  transaction-unique A/B candidate for install or upgrade recovery without
  clearing either fixed slot in place or deleting the canonical or legacy
  isolated HOME.
- Added project-local npm cache routing for package install, audit, self-test,
  and candidate checks; registry discovery uses disposable out-of-project state
  so it cannot create target paths before ownership preflight.
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
- Added timestamped stable/beta/alpha catalog evidence resolved from npm
  `dist-tags` and retained in managed root and active-slot records.
- Added durable Ubersight phase/slice tracking for the interrupted-context,
  transactional-install, and canonical-migration recovery.

### Changed

- Changed default runtime and isolated-HOME paths from the deprecated flat
  `.codex-local` and `.codex-home` names to children of
  `.local/`. Existing non-A/B and A/B installs migrate without inspecting,
  copying, merging, or recreating context; ambiguous layouts and unsupported
  atomic-exchange hosts fail closed.
- Hardened fresh-install and update ownership boundaries so nonempty canonical
  runtime or isolated-HOME roots cannot be silently adopted, unverified
  inactive slots cannot be retired, and ordinary uninstall requires exact
  project/layout/HOME-bound metadata before removing runtime or history.
  Registry lookups now use disposable out-of-project state so they cannot
  create target roots before locked ownership preflight, and legacy ignore
  rules cover both pre-migration directories and post-migration symlinks.
- Bound historical root and slot authority to exact runtime and isolated-HOME
  compatibility links, reject incomplete records minted in the canonical
  namespace, refuse pending runtime migration over a nonempty unbound canonical
  HOME, and defer explicit HOME-mode transitions until the recorded mode
  finishes namespace migration. Migration-only proof still handles exact
  power-loss partial states, while uninstall retains complete ignore coverage
  until every managed tree and compatibility link is gone.
- Changed install, repair, upgrade, and channel-switch operations to build and
  validate only a unique candidate before an inactive-slot rename. Failed npm,
  exact/platform-version, native, timeout, or interruption checks now leave the
  active runtime, restored fixed slots, launcher, README, root metadata, and
  existing project context unchanged. Full ignore coverage and, outside repair,
  a proven absent empty isolated HOME may be safely prepublished; corrupt-pointer
  repair quarantines displaced slot evidence.
- Hardened launcher and maintenance containment, bounded candidate/runtime
  health checks, commit-aware pointer durability diagnostics, stable lock use
  across update/uninstall, read-only A/B inspection reporting, protected
  context-root disjointness including absent casefold aliases, pre-commit
  projection publishability checks, and whole-process-group cleanup on
  terminal-closing signals.
- Made valid selected-slot HOME and permission records authoritative over stale
  root projections; invalid pointers now require concordant surviving
  root/completed-slot evidence, and canonical migration rechecks HOME authority
  under lock before publishing its bridge. Repair requires an explicit HOME
  choice only when that evidence is lost, refuses to recreate a missing
  isolated HOME, and defaults lost permission state to disabled. Exact npm
  scratch roots are now safely purged before strict validation of the remaining
  persistent cache, while install, audit, and candidate checks receive fresh
  mode-0700 workspaces outside that cache with private pinned temp paths.
  Controlled npm variables are replaced case-insensitively, self-test refuses
  to audit after its runtime snapshot changes, and launcher/inspection evidence
  reads match Python's non-following, nonblocking regular-file checks.
- Updated the managed development runtime from Python 3.12.12 to 3.14.6 while
  retaining Python 3.9 as the bootstrap and package compatibility floor.
- Changed bootstrap to reuse existing pyenv and pyenv-virtualenv checkouts
  without unattended Git updates, provision only the steady-state runtime,
  and rebuild a standard user venv when its base interpreter has drifted.
- Changed the repo-root validation wrapper to preserve `.venv/bin/python`
  across symlink resolution and nested sandbox-safe Black subprocesses.
- Clarified that canonical `.local/codex-home` and legacy
  `.codex-home` contain protected transplanted authentication, memories,
  sessions, rules, history, goals, and context that install and
  repair operations must preserve.
- Changed managed Codex package installs to run with a 300-second default npm
  timeout, disable optional npm audit/funding/update-notifier/spinner checks,
  emit HTTP fetch and foreground lifecycle-script logs for troubleshooting,
  and expose `--npm-timeout-seconds` plus `--npm-install-loglevel` controls.
- Updated the TheKnowledge submodule from `dcf9e09` through its reviewed
  current `751a52a` trunk state and refreshed managed guidance while preserving
  project-local installer behavior when validation caught a managed refresh
  dropping `--user-home` support.
- Changed generated `bin/codex-local` launchers to execute the validated
  active-slot Codex binary directly, with narrowly proven pre-slot and migration
  fallbacks, instead of using `npx codex`; a missing local install can no
  longer fall through to the unrelated registry package named `codex`.
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
  repo-local `.local/codex-home` sessions require `--user-home PATH` or
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

- Bound stage-two and low-level user-install venv, pip, hook, and verification
  subprocesses to the selected user's `HOME`, persistent XDG homes, and pip
  cache, preventing an explicit `--user-home` install from writing cache data
  into the invoking project-local AI home. Submodule and pyenv children now
  retain only their needed overrides while the selected home wins over
  inherited Codex, Claude, and XDG context selectors.
- Changed bootstrap submodule preparation to recursively initialize only
  missing modules, preserving clean initialized checkouts that intentionally
  differ from the parent repository's recorded gitlink. Every existing
  submodule path component is revalidated as a real contained directory before
  Git inspection, initialization, and nested recursion, so replaced final or
  ancestor links cannot redirect writes into an external repository.
- Isolated npm lifecycle and candidate health-check subprocesses from an
  invoking project-local tool's `HOME`, `CODEX_HOME`, and `XDG_*` context by
  pinning and pre-creating a fresh maintenance workspace outside the persistent
  npm cache for every phase; registry lookups remain temporary and out of
  project.
- Detect truncated ELF64, Mach-O64, fat Mach-O, and PE32+ Codex payloads from
  their declared file-backed extents before post-install execution, inspection,
  or self-test launcher calls, and stop generated launchers at a failed health
  check with absolute-path repair guidance while accepting a valid version line
  surrounded by CLI diagnostics.
- Fixed `bootstrap.sh` so it is directly executable from a fresh checkout.
- Fixed false missing-Black failures caused by resolving a venv Python symlink
  to the sparse underlying pyenv interpreter.
- Fixed managed npm subprocesses so install, version lookup, update, and
  self-test audit calls fail with clear timeout diagnostics instead of
  waiting indefinitely.
- Extended repair install cleanup to remove managed npm cache temp files so
  interrupted tarball extraction can be retried cleanly.
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
