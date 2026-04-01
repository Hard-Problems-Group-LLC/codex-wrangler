# codex-wrangler

`codex-wrangler` is a Python utility for managing project-local
`@openai/codex` installs without making the repository root look like a Node
project.

It creates and manages a disciplined local layout:

- `.codex-local/` for the local npm package install and npm cache
- `.codex-home/` for project-local Codex state when isolation mode is enabled
- `bin/codex-local` as the generated launcher used inside the target project
- `README-LOCAL-Start-Codex.md` as an ignored local operator guide inside the
  target project
- a marked `.gitignore` block so uninstall can remove only what it owns

The utility also supports:

- installation of the latest known alpha by default
- JSON inspection with `--inspect`
- pessimistic verification with `--selftest`
- conservative removal with `--uninstall`
- channel-aware upgrades with `--upgrade`
- explicit `--upgrade-to-alpha`
- explicit `--downgrade-to-stable`

## Quick Start

Use the top-level installer when you want a normal user-local install from
this checkout:

```bash
./install.sh
```

That is the default supported path on both Rocky and Ubuntu. It ensures
Python 3.9+ is available and installs a stable non-development command into a
user-local virtual environment.

Use `sudo ./install.sh --system` when you explicitly want a system-level
non-development install. `sudo ./install.sh` without `--system` is rejected on
purpose. If you are logged in as `root` and omit `--system`, the script asks
for confirmation before doing a root-only user-local install.

## Developer Quick Start

Use this explicit development mode when you are setting up this repository for
active editing:

```bash
./install.sh --mode dev
```

That flow is the supported developer path on both Rocky and Ubuntu. It ensures
Python 3.9+ is available, initializes `TheKnowledge`, installs a pyenv-managed
Python 3.12 toolchain, builds the repo-local `.venv`, installs the package in
editable mode, and makes `direnv` mandatory for automatic activation when you
enter the repository.

After the script finishes, open a new shell in this repository so the managed
`direnv` hook can activate `.venv` automatically.

Run `./install.sh --help` to see the current stage-1 help plus the live
stage-2 options. Non-help options passed to `install.sh` are forwarded to
`scripts/install-stage-2.py`. The older `./bootstrap.sh` and
`./bootstrap-stage2.py` entry points remain as compatibility wrappers while
older checkouts and habits migrate.

## Install Modes

The managed bootstrap now supports four distinct outcomes:

- `./install.sh`
  Standard user-local mode. Installs a stable non-development command into a
  user-local virtual environment and publishes the normal
  `~/.local/bin/codex-wrangler` launcher.
- `./install.sh --mode dev`
  Development mode. Installs the package editable into `.venv`, requires
  `direnv`, updates shell hooks, and installs a project-bound
  `~/.local/bin/codex-wrangler` launcher that points at this checkout's
  `.venv`.
- `./install.sh --mode venv-only`
  Toolchain-only mode. Prepares `.venv` and pinned validation tools without
  installing the project itself. If the managed developer launcher currently
  points at this checkout, venv-only mode removes that project-bound override.
- `sudo ./install.sh --system`
  Standard system mode. Installs a stable non-development command into a
  system-scoped virtual environment and publishes `/usr/local/bin/`
  launchers. It is only valid under root.

Those modes are the shared TheKnowledge-managed bootstrap contract. This
repository keeps `scripts/install_user_tool.py` in addition to that contract
because it is also a CLI tool that benefits from a stable user-level command.

## Low-Level User Installer Helper

`./install.sh` is the preferred normal-install entry point. Use the repository
installer helper only when you explicitly want to bypass the managed stage-1
and stage-2 flow:

Use the repository installer helper:

```bash
python3 scripts/install_user_tool.py
```

That creates the same style of dedicated virtual environment under
`~/.local/share/codex-wrangler/venv`, installs `codex-wrangler` there, and
links `~/.local/bin/codex-wrangler` to the venv-managed command. When the
managed pyenv runtime from `python-environments.json` already exists, the
installer reuses that shared user-scoped interpreter family for the user venv
instead of defaulting immediately to the current system interpreter.

This path works on Ubuntu and other distributions that block
`python3 -m pip install --user ...` via PEP 668.

Your shell startup still needs `~/.local/bin` on `PATH` for the command to be
available in fresh shells.

Check it with:

```bash
command -v codex-wrangler
codex-wrangler --help
```

## Direct Pip Install Notes

On distributions that still allow it, direct `pip --user` installation remains
valid:

```bash
python3 -m pip install --user ~/codebase/HPG/actual/codex-wrangler
python3 -m pip install --user --editable ~/codebase/HPG/actual/codex-wrangler[dev]
```

On modern Ubuntu and Debian releases, those commands can fail with
`externally-managed-environment`. In that case, use
`python3 scripts/install_user_tool.py` instead of forcing
`--break-system-packages`.

## Repository Validation Toolchain

For repeatable local validation after `./install.sh --mode dev` or
`./install.sh --mode venv-only`, prefer the managed project-local virtual
environment:

```bash
.venv/bin/python scripts/run_local_quality_gate.py
```

The bootstrap flow creates `.venv/` with the pinned developer tooling and
installs the project according to the selected mode. In development mode that
includes the editable package install. The quality-gate script runs Black
through `scripts/run_black_safe.py`, which formats one file at a time with
timeout protection and probes whether the installed Black supports flags such
as `--no-cache`.

## Fresh-Shell Behavior

After a developer install, a fresh shell inside this repository should activate
`.venv` automatically through `direnv`, which makes the project-bound command
available:

```bash
codex-wrangler --help
```

After a normal user install, `codex-wrangler` should work from fresh shells
provided that `~/.local/bin` is on `PATH`.

Because development mode also manages `~/.local/bin/codex-wrangler`, the last
install path you ran wins for that command name. `./install.sh --mode dev`
makes the command project-bound to this checkout; `./install.sh`,
`sudo ./install.sh --system`, or `python3 scripts/install_user_tool.py`
restore a stable non-development launcher.

If you ever need to confirm the shell setup explicitly:

```bash
printf '%s\n' "$PATH"
command -v codex-wrangler
```

## Typical Usage

Initialize the current repository with the latest known alpha:

```bash
codex-wrangler .
```

Inspect the managed state as JSON:

```bash
codex-wrangler --inspect .
```

Self-test the managed state:

```bash
codex-wrangler --selftest .
```

Upgrade while preserving the current channel:

```bash
codex-wrangler --upgrade .
```

Upgrade explicitly to the latest known alpha:

```bash
codex-wrangler --upgrade-to-alpha .
```

Downgrade explicitly to the latest known stable:

```bash
codex-wrangler --downgrade-to-stable .
```

Uninstall the managed setup:

```bash
codex-wrangler --uninstall .
```


## TheKnowledge Submodule

This repository now carries `TheKnowledge/` as a git submodule in the standard
project-root location. After a fresh clone, initialize it with:

```bash
git submodule update --init --recursive
```

That ensures the repository's local standards/tooling companion is present.
The developer bootstrap script runs this automatically.

## Generated Target-Project Artifacts

When you run `codex-wrangler` against another repository, it manages these
artifacts inside that target repository:

- `.codex-local/`
- `.codex-home/` unless `--shared-home` was used
- `bin/codex-local`
- `README-LOCAL-Start-Codex.md`
- a marked `.gitignore` block

## Safety Notes

The tool is intentionally suspicious.

- Managed paths must stay under the target project root.
- Uninstall removes files only when it can prove ownership, unless `--force`
  is used.
- The generated launcher and generated local README are content-checked.
- Inspection emits a JSON report so automation or operators can see what the
  tool believes it owns.

## Development

See [DEVELOPMENT.md](DEVELOPMENT.md) for project layout, test commands, and
packaging notes.
