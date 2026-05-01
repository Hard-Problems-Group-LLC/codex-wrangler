# Engineering Change Request: Managed Install Stage 2 Overlay and User-Home Contract

## Summary

TheKnowledge's managed `scripts/install-stage-2.py` template and consuming
project refresh workflow should support explicit user-home targeting and
local overlay preservation.

During a `codex-wrangler` TheKnowledge upgrade, the dedicated submodule
update helper correctly detected managed-file drift and refreshed the managed
starter files. That refresh replaced `codex-wrangler`'s locally extended
`scripts/install-stage-2.py` with the generic TheKnowledge version. The
resulting file was not usable for this consuming project because it removed
the isolated-home safety contract that prevents repo-local Codex sessions from
installing user-scoped tools into `.codex-home`.

## Incident

`codex-wrangler` intentionally carries local behavior in
`scripts/install-stage-2.py`:

- `--user-home PATH` explicitly selects the real target home for user-scoped
  pyenv, venv, launcher, direnv, and shell-init paths.
- `--allow-isolated-home` allows the current isolated `HOME` only when the
  operator explicitly accepts that scope.
- `resolve_user_home(...)` rejects unsafe implicit user-scope installs when
  `HOME` points at the repo-local `.codex-home`.
- The resolved user home is passed through pyenv-root, standard venv, launcher
  bin-dir, direnv, shell-init, and project install-hook path selection.

The managed refresh removed those arguments and changed target selection back
to `Path.home()` or the generic `default_pyenv_root()` path. It also stripped
the executable bit from `install.sh` and `scripts/install-stage-2.py`.

The immediate regression was caught by
`tests/test_entrypoints.py::test_install_stage_2_help_runs_from_checkout`,
which expects `--user-home USER_HOME` in the help output. The deeper
regression is that an isolated local Codex session could again direct
user-scoped installer state into `.codex-home` instead of the operator's real
home.

## Current Problem

TheKnowledge's upgrade path is supposed to reconcile upstream managed-file
improvements with consuming-project changes. In this case, the refresh behaved
like a generated-file overwrite. The only safe local response was to reject
the whole `install-stage-2.py` refresh, even though future upstream changes to
that file may contain useful fixes.

That creates a bad maintenance choice:

- accept the managed refresh and lose a local safety contract; or
- reject the managed refresh and risk missing upstream installer improvements.

## Requested Change

TheKnowledge should make `install-stage-2.py` usable for consuming projects
that run from isolated AI-agent homes while installing user-scoped tooling for
a real operator account.

At minimum, the managed starter installer should provide a generic contract
equivalent to:

- `--user-home PATH` for explicit user-scope targeting.
- `--allow-isolated-home` for deliberate installs into the current isolated
  home.
- A reusable `resolve_user_home(project_root, explicit_home,
  allow_isolated_home)` helper that fails closed when `HOME` appears to be a
  project-local AI-agent home such as `.codex-home`.
- User-home-aware path helpers for pyenv root, standard user venv, user
  launcher bin directory, direnv install location, and shell rc updates.
- A project install hook invocation that receives the already-resolved
  launcher bin directory instead of recomputing it from `Path.home()`.
- System-mode validation that rejects `--user-home` and
  `--allow-isolated-home`.
- Status output that prints the resolved user home and why it was selected.
- Preservation of executable modes for shell and script entry points.

## Overlay Preservation

The managed-file refresh tooling should also preserve deliberate local
overlays where a consuming project has not yet converged with the generic
template.

Acceptable implementation approaches include:

- Three-way refresh using the previous managed base, the new managed base, and
  the consuming project's current file.
- Named local overlay blocks that the managed refresh preserves while
  replacing generated sections.
- A managed-file metadata record that marks specific files or regions as
  intentionally locally extended, so drift reports can distinguish unresolved
  local overlays from accidental drift.

The goal is not to hide local differences. The goal is to make refresh output
reviewable and mergeable instead of forcing operators to choose between
wholesale overwrite and wholesale rejection.

## Requirements

- Running the TheKnowledge submodule update helper must not silently remove
  consuming-project installer safety contracts.
- Refreshing `scripts/install-stage-2.py` must preserve or reintroduce
  explicit user-home targeting.
- The refreshed installer must still run on the bootstrap Python floor.
- The generated installer must not depend on consuming-project package
  imports such as `codex_wrangler.install_scope`; generic helpers should live
  in managed starter code.
- File modes for executable starter entry points must remain executable.
- Drift reporting should continue to show unresolved differences clearly.
- The update helper should leave any conflict requiring operator judgment in a
  reviewable state.

## Recommended Validation

- Add a TheKnowledge consuming-project fixture whose current
  `scripts/install-stage-2.py` contains a local overlay.
- Run the dedicated submodule update helper against that fixture.
- Confirm upstream non-conflicting template changes are applied.
- Confirm the local overlay is preserved or represented as an explicit
  conflict, not silently deleted.
- Confirm `python scripts/install-stage-2.py --help` includes
  `--user-home USER_HOME` and `--allow-isolated-home`.
- Confirm a simulated isolated `HOME` under `.codex-home` fails unless
  `--user-home` or `--allow-isolated-home` is supplied.
- Confirm user-scoped pyenv, venv, shell-init, direnv, and launcher paths all
  derive from the resolved user home.
- Confirm `install.sh` and `scripts/install-stage-2.py` remain executable.

## Companion Draft Validation

The peer file
`managed-install-stage2-overlay-and-user-home-contract.proposed-install-stage-2.py`
was exercised as a candidate `scripts/install-stage-2.py` by copying it into
a temporary managed checkout layout with `scripts/python_environment_bootstrap.py`
beside it. That validation confirmed:

- `--help` imports successfully from the managed script location and includes
  `--user-home USER_HOME` plus `--allow-isolated-home`.
- An implicit user-scoped run with `HOME` set to a project-local
  `.codex-home` fails closed before creating pyenv, venv, launcher, direnv,
  or shell-init state.
- `--user-home PATH` redirects standard-install venv and launcher paths to
  the selected operator home.
- `--allow-isolated-home` deliberately permits the isolated `.codex-home`
  target and reports that source explicitly.
- `--system` rejects `--user-home` and `--allow-isolated-home` before any
  system install work.
- Project install hooks receive the already-resolved launcher bin directory
  through `--bin-dir`.
- The candidate still passes
  `tests/test_entrypoints.py::test_install_stage_2_help_runs_from_checkout`
  and
  `tests/test_entrypoints.py::test_install_stage_2_rejects_direct_execution`
  when temporarily installed at the real managed path.

The companion draft intentionally remains an upstream candidate, not a
self-contained script to run from the ECR directory. Its import path assumes
the managed `scripts/` location used by `install.sh` and `bootstrap-stage2.py`.

## Non-Goals

- Preserve arbitrary local edits without review.
- Make every managed starter file fully merge-aware in one step.
- Require consuming projects to carry project-specific Python package imports
  inside generic managed starter files.

## Why This Belongs To TheKnowledge

TheKnowledge owns both the managed starter installer and the submodule update
workflow that refreshes starter files in consuming projects. A consuming
project can follow the documented upgrade path and still end up with an
installer that no longer respects its local safety requirements.

The right upstream fix is to make the safety requirement generic where
possible, and to make the refresh workflow preserve deliberate local overlays
where generic support is not yet complete.
