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

- installation of the current stable Codex release by default
- JSON inspection with `--inspect`
- pessimistic verification with `--selftest`
- conservative removal with `--uninstall`
- version-catalog refresh with `--update`
- channel-aware upgrades with `--upgrade`
- exact-version targeting with `--version latest|x.y.z`
- managed reasonable-permissions launcher defaults with
  `--set-reasonable-permissions` and
  `--clear-reasonable-permissions`

## Changelog

This repository keeps a top-level [CHANGELOG.md](/home/mheck/codebase/HPG/actual/codex-wrangler/CHANGELOG.md)
in Keep a Changelog style. The repo's short-lived
`project-management/state/pending-commit-changes.txt` queue is still used for
commit-body text, but this repo now mirrors those notable changes into the
`Unreleased` changelog section before commit.

For normal local commit-and-push flow, use:

```bash
python scripts/git_commit_with_changelog.py -m "Your subject"
```

That helper updates `CHANGELOG.md` first, then delegates to
`python TheKnowledge/scripts/git_standard_commit_push.py`.

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

When you run the managed installers from a `codex-local` shell that has
redirected `HOME` into the repository's `.codex-home`, user-scoped effects
are now intentionally explicit. `./install.sh`, `./install.sh --mode dev`,
`./install.sh --mode venv-only`, and `python3 scripts/install_user_tool.py`
refuse to target that isolated home silently. In that situation, rerun from a
normal terminal, pass `--user-home /real/home` to target an operator home
explicitly, or pass `--allow-isolated-home` when you deliberately want an
AI-local install rooted in the isolated home.

## Low-Level User Installer Helper

`./install.sh` is the preferred normal-install entry point. Use the repository
installer helper only when you explicitly want to bypass the managed stage-1
and stage-2 flow:

Use the repository installer helper:

```bash
python3 scripts/install_user_tool.py
```

That creates the same style of dedicated virtual environment under
`<user-home>/.local/share/codex-wrangler/venv`, installs `codex-wrangler`
there, and links `<user-home>/.local/bin/codex-wrangler` to the venv-managed
command. When the managed pyenv runtime from `python-environments.json`
already exists, the installer reuses that shared interpreter family for the
selected user home instead of defaulting immediately to the current system
interpreter.

Use `--user-home /path/to/home` when the current process `HOME` is not the
user scope you intend to manage. Use `--allow-isolated-home` only when an
isolated repo-local Codex home should receive the install on purpose.

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

Initialize the current repository with the current stable release:

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

Refresh the locally known stable, beta, and alpha versions:

```bash
codex-wrangler --update .
```

Upgrade to the latest known stable, beta, or alpha version:

```bash
codex-wrangler --upgrade --channel stable .
codex-wrangler --upgrade --channel beta .
codex-wrangler --upgrade --channel alpha .
```

If a prior npm install was interrupted and left `.codex-local/node_modules`,
`.codex-local/package-lock.json`, `.codex-local/.npm-cache/_npx`, or
`.codex-local/.npm-cache/_cacache/tmp` inconsistent, use the explicit repair
mode. It removes only managed npm install artifacts under `.codex-local`
before reinstalling with `NPM_CONFIG_CACHE` pointed at
`.codex-local/.npm-cache`; it does not remove `.codex-home`:

```bash
codex-wrangler --upgrade --channel stable --repair-install .
```

Repair mode deliberately targets only canonical managed artifact names:
`.codex-local/node_modules`, `.codex-local/package-lock.json`,
`.codex-local/.npm-cache/_npx`, and `.codex-local/.npm-cache/_cacache/tmp`.
It will not remove ad hoc operator backups such as
`.codex-local/node_modules.break-test`. If `--inspect` reports corrupt
managed metadata, package manifests, or lockfiles, treat that as damaged state
that should be repaired or reviewed before relying on the local launcher.
Plain `npm install` can sometimes self-heal interrupted installs; repair mode
exists for cases where the safer path is to discard the managed npm install
artifacts and recreate them from the managed manifest.

Managed npm operations use a 300-second timeout by default. Package
installation disables npm's optional audit, funding, update-notifier, and
spinner progress behavior, but emits npm HTTP fetch logs plus foreground
lifecycle-script output for troubleshooting. If a slow network or registry
needs a longer window, pass `--npm-timeout-seconds <seconds>`. To reduce npm
install chatter, pass `--npm-install-loglevel notice`.

Upgrade to one exact version:

```bash
codex-wrangler --upgrade --channel beta --version 0.31.0-beta.2 .
```

Enable or clear the managed reasonable-permissions launcher default:

```bash
codex-wrangler --set-reasonable-permissions .
codex-wrangler --clear-reasonable-permissions .
```

When enabled, the generated launcher defaults Codex to
`-a on-request -s workspace-write` unless the caller already supplied
approval or sandbox flags. That keeps ordinary workspace edits sandboxed
while preserving approval prompts for ACP operations such as commit and
push. Operators may approve local add and commit work as maintenance
activity while treating push as the explicit remote publication step.

Uninstall the managed setup:

```bash
codex-wrangler --uninstall .
```

`codex-wrangler` resolves the exact release for stable, beta, and alpha
channels at install time from npm dist-tags, then writes that exact version
plus the known channel catalog into managed metadata. `--update` refreshes
that local catalog without changing the install, and `--upgrade --channel ...`
uses the recorded catalog unless you supply an explicit exact `--version`.


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

## Runtime Boundary

`codex-wrangler` localizes the `@openai/codex` package install under
`.codex-local/` and, by default, localizes Codex state under `.codex-home/`.
It does not currently install or pin the `node` runtime used by
`bin/codex-local`, or the `npm` executable used by maintenance commands such
as install, update, upgrade, audit, and self-test. Managed npm operations set
`NPM_CONFIG_CACHE` to `.codex-local/.npm-cache` so package installs and npm
scratch/cache writes stay inside the target project.

The generated launcher executes
`.codex-local/node_modules/.bin/codex "$@"` directly. It intentionally does
not use `npx codex`, because npm can otherwise fall back to a different
registry package named `codex` when the managed local binary is missing or
damaged. The Node.js runtime family still comes from the shell `PATH` that
launched the command.

## Trust and Install Scope

`codex-wrangler` now treats user-scoped install targets as an explicit trust
boundary.

- Less-trusted AI or isolated `codex-local` sessions should normally stay in
  repo scope and should not mutate an operator's shell init, `~/.local/bin`,
  or `~/.pyenv` implicitly.
- More-trusted AI can still manage its own user scope, but that should be its
  own explicit home directory, not an accidental by-product of whatever
  `HOME` the current session inherited.
- Operator-user installs remain supported, but when the current `HOME` is the
  repo-local `.codex-home`, they now require `--user-home /path/to/home`
  instead of silently targeting the wrong scope.
- System installs remain separate and still require `sudo ./install.sh
  --system`.

This is the current middle ground: repo-local work remains easy, AI-local
user installs remain possible with explicit intent, and operator-user installs
no longer ride on an ambiguous `HOME`.
In a project that already uses `direnv`, `nvm`, `fnm`, `asdf`, `volta`, or a
similar selector, start `bin/codex-local` from the same project-activated
shell a human operator would use. That keeps Codex sessions and the agent
sandboxes they launch closer to the same interpreter view as the rest of the
project.

The generated launcher now performs a cheap startup preflight before it starts
Codex. It always refuses to launch when `node` or the managed local Codex
binary is unavailable. By default it also warns when a checked-in Node.js
selector such as `.nvmrc`, `.node-version`, or `.tool-versions` appears
inconsistent with the active shell. Use `CODEX_LOCAL_PREFLIGHT=warn`,
`strict`, or `off` to control the selector-warning behavior.

The launcher also redirects `HOME` and related `XDG_*` paths into
`.codex-home/` unless `--shared-home` is used. That isolation keeps Codex
state project-local, but it can change how tools that consult `HOME` resolve
their config. `codex-wrangler` intentionally does not mutate the target
project's own Python or Node configuration files unless some separate
bootstrap feature is added explicitly.

`codex-wrangler --inspect` and `--selftest` now also report the resolved
`node`, `npm`, and `npx` paths and versions, detected checked-in Node.js
selectors, any root `package.json` package-manager declaration, and the
effective `HOME` and `XDG_*` paths the launcher will expose. They also report
whether the managed local Codex binary exists and whether the requested,
lockfile, and installed package versions agree.

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
