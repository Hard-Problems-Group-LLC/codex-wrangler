# Development

## Project Layout

- `src/codex_wrangler/cli.py`
  Thin entry point and top-level dispatch.
- `src/codex_wrangler/config.py`
  Argument parsing and runtime configuration selection.
- `src/codex_wrangler/layout.py`
  Path validation and existing-state discovery.
- `src/codex_wrangler/rendering.py`
  Deterministic managed file content and summary rendering.
- `src/codex_wrangler/filesystem.py`
  Conservative filesystem mutation helpers.
- `src/codex_wrangler/operations.py`
  Install, inspect, uninstall, and self-test behaviors.
- `src/codex_wrangler/runtime.py`
  Small process and stderr helpers.
- `src/codex_wrangler/installer.py`
  Dedicated user-venv installer helpers for cross-distro command setup.
- `src/codex_wrangler/project_install.py`
  Repo-specific install-hook logic for dev, user-local standard, and
  system-standard installs plus launcher management.
- `src/codex_wrangler/constants.py`
  Stable script names, markers, and pinned Codex defaults.
- `src/codex_wrangler/_version.py`
  Single-source package version.
- `src/codex_wrangler/models.py`
  Internal dataclasses and shared error types.
- `src/codex_wrangler/__main__.py`
  Module entry point so `python -m codex_wrangler` works.
- `codex-wrangler.py`
  Thin repository-root wrapper for direct execution from the checkout.
- `scripts/install_user_tool.py`
  Repository-root helper that installs the user command into a dedicated venv.
- `scripts/install_project.py`
  Repository-root install hook that keeps developer installs bound to this
  checkout through a managed launcher in `~/.local/bin`.
- `scripts/install-stage-2.py`
  Managed stage-2 installer/bootstrap that selects user, system, or repo
  scope.
- `scripts/python_environment_bootstrap.py`
  Shared Python 3.9-safe helper layer for managed stage 1 and stage 2.
- `install.sh`
  Minimal top-level bootstrap that finds Python 3.9+ and launches stage 2.
- `bootstrap.sh` and `bootstrap-stage2.py`
  Compatibility wrappers that delegate to the managed install entry points.
- `tests/`
  Focused tests split by config, rendering, filesystem, operations, metadata,
  bootstrap and installer behavior, and entry points.

## Developer Bootstrap

Use this when you are setting up the repository itself for active development:

```bash
./install.sh --mode dev
```

That is the supported Rocky-and-Ubuntu bootstrap path. It ensures Python 3.9+
is available, initializes `TheKnowledge`, installs a pyenv-managed Python
3.12 line, rebuilds `.venv`, installs the package in editable mode, and makes
`direnv` mandatory for automatic activation inside the repo.

After the script finishes, open a new shell in this repository so the managed
`direnv` hook can activate `.venv`.

Run `./install.sh --help` when you need the current pass-through option list
from `scripts/install-stage-2.py`. Direct stage-2 execution is guarded and is
only intended for debugging with `--force-direct-run`.

## Install Modes

The managed installer now supports four distinct outcomes:

- `./install.sh`
  Standard user-local mode. Installs a stable non-development command into a
  user-local virtual environment and publishes the normal
  `~/.local/bin/codex-wrangler` launcher.
- `./install.sh --mode dev`
  Development mode with editable install, mandatory `direnv`, shell-hook
  integration, and a project-bound `~/.local/bin/codex-wrangler` launcher that
  keeps using this checkout's `.venv` even outside the repository directory.
- `./install.sh --mode venv-only`
  Toolchain-only mode that prepares `.venv` without installing the package.
- `sudo ./install.sh --system`
  Standard system mode. Installs a stable non-development command into a
  system-scoped virtual environment and publishes `/usr/local/bin/`
  launchers. `sudo ./install.sh` without `--system` is rejected.

Those modes are the shared TheKnowledge-managed contract. `bootstrap.sh` and
`bootstrap-stage2.py` remain compatibility wrappers, not the preferred
operator path.

## Low-Level User Installer Helper

`./install.sh` is the preferred normal-install entry point. Use this helper
only when you explicitly want the user-level install without the managed
stage-1 wrapper:

```bash
python3 scripts/install_user_tool.py
```

When the managed pyenv runtime declared in `python-environments.json` already
exists, the user installer prefers that shared interpreter for the dedicated
user venv. That keeps the packages isolated while reusing a sensible
user-scoped runtime family instead of creating an extra interpreter tree.

If you specifically want to test raw `pip --user` behavior on a distribution
that allows it, the older direct commands still work:

```bash
python3 -m pip install --user ~/codebase/HPG/actual/codex-wrangler
python3 -m pip install --user --editable ~/codebase/HPG/actual/codex-wrangler[dev]
```

## Managed Validation Toolchain

After `./install.sh --mode dev` or `./install.sh --mode venv-only`, use the
hardened local quality-gate script when you want the default repository
validation sequence:

```bash
.venv/bin/python scripts/run_local_quality_gate.py
```

That script uses `scripts/run_black_safe.py` for Black so sandbox hangs and
version-specific flag differences are handled in one place.


## TheKnowledge Submodule

This repository keeps `TheKnowledge/` as a normal git submodule at the project
root. After cloning `codex-wrangler`, run:

```bash
git submodule update --init --recursive
```

That brings the standards/tooling companion checkout into place before you use
repo-specific helper material from it. The developer bootstrap script does this
for you.

## Running The Utility From Source

After `./install.sh --mode dev`, entering the repository should
auto-activate `.venv` through `direnv`, so the user-facing command works
directly:

```bash
codex-wrangler --help
```

That same development install now also makes `~/.local/bin/codex-wrangler`
point at this checkout's `.venv`, so the command stays bound to the project
override even when launched outside the repository. Running `./install.sh`,
`sudo ./install.sh --system`, or `python3 scripts/install_user_tool.py` later
restores a stable non-development launcher.

If you want to execute code from this checkout without using the installed
command, use the repository wrapper:

```bash
python3 ./codex-wrangler.py --help
```

If you need module-style execution from the repo-local venv, use:

```bash
.venv/bin/python -m codex_wrangler --help
```

The installed console script remains the preferred operator path because it is
what normal users and fresh shells will rely on.

## Tests

For a checkout bootstrapped with `./install.sh --mode dev`, use the
standardized project path:

```bash
.venv/bin/python scripts/run_local_quality_gate.py pytest
```

The repo-local developer virtual environment also works directly:

```bash
.venv/bin/python -m pytest
```

The tests intentionally stay light on external side effects while covering the
version/channel logic, managed content generation, conservative uninstall
behavior, and inspection/self-test failure modes.

## Packaging Notes

This project uses a conventional `src/` layout and a `pyproject.toml`
configuration with a console script entry point:

- command name: `codex-wrangler`
- entry point: `codex_wrangler.cli:main`
- dynamic version source: `codex_wrangler.__version__`

That means both normal and editable installs place the same command into
`~/.local/bin`, which is the crucial detail for fresh-shell usability.
