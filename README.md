# codex-wrangler

`codex-wrangler` is a Python utility for managing project-local
`@openai/codex` installs without making the repository root look like a Node
project.

It creates and manages a disciplined local layout:

- `.local/codex/slots/a` and `slots/b` for isolated A/B npm package prefixes
- `.local/codex/active` for the atomically selected POSIX runtime, with a
  strict `active-slot` file fallback on platforms where symlinks are unsuitable
- `.local/codex/.npm-cache` for the shared project-local npm cache
- `.codex-wrangler.lock` for a stable, ignored maintenance lock inode that is
  retained after uninstall to prevent lock-replacement races
- `.local/codex-home/` for project-local Codex state when isolation mode is enabled
- `bin/codex-local` as the generated launcher used inside the target project
- `README-LOCAL-Start-Codex.md` as an ignored local operator guide inside the
  target project
- a marked `.gitignore` block so uninstall can remove only what it owns

The utility also supports:

- installation of the current stable Codex release by default
- JSON inspection with `--inspect`
- pessimistic verification with `--selftest`
- context-preserving exact-version recovery with `--repair /absolute/root`
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
Python 3.9+ is available, initializes `TheKnowledge`, selects the configured
pyenv-managed Python 3.14.6 runtime, builds the repo-local `.venv`, installs the
package in editable mode, and makes `direnv` mandatory for automatic activation
when you enter the repository. Existing pyenv and pyenv-virtualenv checkouts
are treated as user-owned, deliberately versioned tools and are not updated by
project bootstrap.

After the script finishes, open a new shell in this repository so the managed
`direnv` hook can activate `.venv` automatically.

The installer bootstraps the `codex-wrangler` utility. On a clean checkout,
create the ignored isolated Codex payload as a separate explicit operation:

```bash
codex-wrangler .
```

If transplant inspection reports a missing or damaged managed `node_modules`
tree, use the documented standalone `--repair /absolute/project/root` path.

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
redirected `HOME` into the repository's `.local/codex-home`, user-scoped effects
are now intentionally explicit. `./install.sh`, `./install.sh --mode dev`,
`./install.sh --mode venv-only`, and `python3 scripts/install_user_tool.py`
refuse to target that isolated home silently. In that situation, rerun from a
normal terminal, pass `--user-home /real/home` to target an operator home
explicitly, or pass `--allow-isolated-home` when you deliberately want an
AI-local install rooted in the isolated home.

Once a user home is selected, the managed stage-two flow and the low-level
user installer run venv, pip, project-install, hook, and verification children
with `HOME`, the persistent `XDG_*` homes, and `PIP_CACHE_DIR` rooted there.
They discard inherited `XDG_*` values, including `XDG_RUNTIME_DIR`, plus
`CODEX_HOME` and `CLAUDE_CONFIG_DIR`, rather than letting an isolated caller
redirect caches or configuration back into another project. Git submodule and
pyenv children retain their required command-specific settings while these
selected-home boundaries remain authoritative.

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

### Canonical layout migration

With implicit default paths, mutating install, update, upgrade, repair, and
launcher-reconfiguration operations automatically recognize the deprecated
`.codex-local` runtime and `.codex-home` isolated HOME. Before moving
anything, the utility proves managed ownership, publishes a bridge launcher,
and on Linux atomically exchanges each real old directory with an exact
relative compatibility link to `.local/codex` or
`.local/codex-home`. Directory identity, A/B slots, active pointers,
rollback material, and all HOME contents move together; the migration does not
inspect, copy, merge, or recreate context or history.

Two real trees, foreign links, and an old HOME without full historical
managed-layout evidence fail closed without changing either path. A valid
selected slot controls HOME mode; without one, root and completed-slot evidence
must agree. The locked operation rechecks that authority and rebuilds its plan
before publishing ignore coverage, a bridge launcher, or either directory
exchange. Hosts without the required atomic exchange also refuse the move.
Explicit custom path flags are never reinterpreted as legacy defaults.

`--inspect`, `--selftest`, `--uninstall`, `--skip-install`,
and every `--dry-run` invocation do not migrate paths.
`--inspect` exposes a structured `layout_migration` object when a
supported move is pending.

If a prior npm install was interrupted or a local Codex executable is damaged,
run `--repair` from a known-good spare `codex-wrangler` command. The project
root is the mandatory value of `--repair`, must be an absolute path, and must
name the directory above `bin/`, `.local/codex/`, and `.local/codex-home/`:

```bash
codex-wrangler --repair /absolute/path/to/project-root
```

Repair first proves that the target is an existing managed install and infers
one exact Codex version from surviving metadata, package manifests, or the
lockfile. It stops on missing or conflicting exact-version evidence rather
than resolving `latest` or silently upgrading. A valid selected slot also
controls HOME and launcher-permission state; otherwise every surviving root and
completed-slot value must agree. If HOME mode is absent from all surviving
authority, repair requires an explicit `--shared-home` or `--isolated-home`
choice, and that choice cannot override surviving evidence. Missing permission
evidence defaults to disabled. The running utility may come from another
checkout or installation; it does not invoke the damaged target launcher.

The operation first builds a transaction-unique candidate outside both fixed
slots. Only after exact-version, platform-package, native-payload, and bounded
`codex --version` validation does it reversibly rename the candidate into the
inactive `.local/codex/slots/a` or `slots/b` prefix and atomically change the
active pointer. A failed, timed-out, or interrupted npm command therefore
leaves the prior active runtime and generated launcher untouched. The first
successful A/B migration also retains a legacy root `node_modules` runtime as
rollback material.

If the sole active pointer or selected slot completion record is corrupt,
repair may replace that pointer only after the candidate validates. Any fixed
slot displaced during this recovery is retained under a transaction-unique
retired name for operator recovery; simultaneous pointer forms remain an
unsafe ambiguity that repair refuses.

Repair may clear only candidate/cache scratch state plus
`.local/codex/.npm-cache/_npx` and
`.local/codex/.npm-cache/_cacache/tmp`. Those exact scratch roots must be real
directories and are removed without following child links; a recursive
no-follow check then rejects links or special files everywhere else in the
persistent cache. It never removes `.local/codex-home`, the legacy
`.codex-home` name, `.codex`, `.agents`, unrelated `.local` contents, project
source, or ad hoc operator backups. Authentication, memories, sessions, rules,
history, goals, and other project-local Codex context therefore survive
package recovery. If a managed isolated HOME is missing, repair reports the
loss and stops instead of creating a replacement.

`--repair-install` remains as a compatibility modifier for an install or
upgrade whose version/channel the operator deliberately selected. It uses the
same normal transaction-unique A/B candidate and does not clear either fixed
slot in place, but it does not provide `--repair`'s managed-ownership proof or
exact-version recovery policy:

```bash
codex-wrangler --upgrade --channel stable --repair-install .
```

Managed npm operations use a 300-second timeout by default, emit elapsed-time
heartbeats every 15 seconds, and retain normal child-process output. Package
installation disables npm's optional audit, funding, update-notifier, and
spinner progress behavior, enables strict package-engine checks, and uses two
bounded fetch retries with explicit per-fetch and retry-delay limits.
Controlled npm configuration is replaced case-insensitively. Exact disposable
scratch roots are safely purged, and every remaining persistent-cache entry is
rejected if it is a link or special file. If a slow network or registry needs
a longer overall window, pass `--npm-timeout-seconds <seconds>`. To reduce npm
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
The developer bootstrap script runs an initialize-only equivalent
automatically: missing direct or nested modules are populated, while an
already-initialized checkout remains at its current commit even when it is
intentionally ahead of or otherwise different from the parent gitlink. Before
Git inspection, initialization, and nested recursion, every existing submodule
path component must be a real directory whose resolved child remains inside its
own repository; replaced final or ancestor links fail closed.

## Generated Target-Project Artifacts

When you run `codex-wrangler` against another repository, it manages these
artifacts inside that target repository:

- `.local/codex/`
- `.local/codex-home/` unless `--shared-home` was used
- exact deprecated-name compatibility links at `.codex-local` and
  `.codex-home` after an automatic migration
- `bin/codex-local`
- `README-LOCAL-Start-Codex.md`
- `.codex-wrangler.lock`, a stable ignored serialization inode intentionally
  retained after uninstall
- a marked `.gitignore` block (reduced to the stable-lock ignore on uninstall)

## Runtime Boundary

`codex-wrangler` localizes the `@openai/codex` package install under
`.local/codex/` and, by default, localizes Codex state under `.local/codex-home/`.
It does not currently install or pin the `node` runtime used by
`bin/codex-local`, or the `npm` executable used by maintenance commands such
as install, update, upgrade, audit, and self-test. Package install, audit,
self-test, and candidate checks set `NPM_CONFIG_CACHE` to the project-local
`.local/codex/.npm-cache`; normal and dry-run registry lookups instead use
disposable out-of-project caches so discovery cannot create target state.

The generated launcher snapshots one strict active pointer, rejects linked or
incomplete managed roots and escaping executable shims, verifies the selected
slot's recorded exact version with a 30-second health-check bound, and executes
the selected `.local/codex/slots/<a|b>/node_modules/.bin/codex` directly.
Before the first successful A/B promotion it can continue to use a surviving
pre-slot `.local/codex/node_modules/.bin/codex` runtime. During canonical
layout migration, the bridge launcher may instead select the exact real
`.codex-local` tree until its atomic exchange completes. It intentionally
does not use `npx codex`, because npm can otherwise fall back to a
different registry package named `codex` when the managed local binary is
missing or damaged.
The Node.js runtime family still comes from the shell `PATH` that launched the
command.

A/B promotion protects startup and maintenance transactions, but fixed slots
are not immutable generations. Do not perform two successive promotions while
a Codex process launched before the first promotion may still need auxiliary
files from its original slot; the second promotion may reuse that slot.

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
  repo-local `.local/codex-home`, they now require `--user-home /path/to/home`
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
`.local/codex-home/` unless `--shared-home` is used. That isolation keeps Codex
state project-local, but it can change how tools that consult `HOME` resolve
their config. `codex-wrangler` intentionally does not mutate the target
project's own Python or Node configuration files unless some separate
bootstrap feature is added explicitly.

Package installation, audit, and each candidate health check use a fresh
mode-0700 workspace under `.local/codex/.maintenance/.run-*`, outside the
persistent npm cache. Registry queries instead use separate temporary
out-of-project cache and HOME roots. None of these maintenance subprocesses
inherit the invoking shell's `HOME`, `CODEX_HOME`, `XDG_*`, `TMPDIR`,
`TMP`, or `TEMP` context paths, and no maintenance HOME resides inside a
persistent cache. Mutating operations safely create the pinned disposable home,
configuration, and private temp directories before a child starts, preventing
missing-home warnings from invalidating an otherwise healthy candidate. Every
disposable workspace is removed after its child process exits; dry runs
likewise keep their preview state temporary.

`codex-wrangler --inspect` and `--selftest` now also report the resolved
`node`, `npm`, and `npx` paths and versions, detected checked-in Node.js
selectors, any root `package.json` package-manager declaration, and the
effective `HOME` and `XDG_*` paths the launcher will expose. They also report
whether the managed local Codex binary exists, which A/B slots are active and
inactive, legacy-runtime usability, each slot's completion/version evidence,
transaction debris, which pointer kind selected the runtime, and whether the
requested, lockfile, and installed package versions agree. Inspection is
read-only, rejects non-regular diagnostic evidence without blocking on FIFOs,
and reports if its active-pointer snapshot changes while gathering. Self-test
also re-resolves the audited runtime under the maintenance lock and refuses a
mixed report if a concurrent promotion changed the pointer or runtime identity.

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
