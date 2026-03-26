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
- `tests/`
  Focused tests split by config, rendering, filesystem, operations, metadata,
  and entry points.

## Normal Install

Use this when you want the utility available as a command but are not actively
editing it:

```bash
python3 -m pip install --user ~/codebase/HPG/actual/codex-wrangler
```

## Editable Developer Install

Use this when you want the installed command to follow your edits immediately:

```bash
python3 -m pip install --user --editable ~/codebase/HPG/actual/codex-wrangler[dev]
```

The editable install keeps the console command in `~/.local/bin` working in
fresh shells while pulling code and pinned validation tools from this checkout.

## Managed Validation Toolchain

For repository-local formatting, linting, and tests, bootstrap the managed
virtual environment:

```bash
python3 scripts/dev_setup.py
```

That creates `.venv/` and installs the same pinned Black, Ruff, pytest, and
pytest-timeout versions declared in both `requirements-dev.txt` and the
package's `dev` extra.

Use the hardened local quality-gate script when you want the default
repository validation sequence:

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
repo-specific helper material from it.

## Running The Utility From Source

After an editable install:

```bash
codex-wrangler --help
python3 -m codex_wrangler --help
```

Without installing, you can also run:

```bash
python3 ./codex-wrangler.py --help
```

The installed console script remains the preferred operator path because it is
what normal users and fresh shells will rely on.

## Tests

For a raw checkout bootstrapped with `python3 scripts/dev_setup.py`, use the
standardized project path:

```bash
.venv/bin/python scripts/run_local_quality_gate.py pytest
```

After an editable install, the quick direct path also works:

```bash
python3 -m pytest
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
