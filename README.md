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

## Install For Normal Usage

This is the best choice when you want a stable user-level command and you are
not actively editing `codex-wrangler` itself.

```bash
python3 -m pip install --user ~/codebase/HPG/actual/codex-wrangler
```

That installs the `codex-wrangler` command into `~/.local/bin/`.

Your shell startup already needs `~/.local/bin` on `PATH` for the command to be
available in fresh shells. On this machine, `~/.local/bin` is already added in
`~/.bashrc`, so a new shell should pick the command up automatically.

Check it with:

```bash
command -v codex-wrangler
codex-wrangler --help
```

## Install For Active Development

This is the best choice when you expect to edit the utility from time to time
and want the installed command to track your working tree immediately.

```bash
python3 -m pip install --user --editable ~/codebase/HPG/actual/codex-wrangler[dev]
```

That still installs the `codex-wrangler` console command into `~/.local/bin/`,
but the command imports code directly from this checkout. The `dev` extra keeps
the editable install aligned with the repository's pinned formatting, linting,
and test tools.

Use the editable install when:

- you are modifying the `src/codex_wrangler/` package
- you want `codex-wrangler` to reflect those edits without reinstalling
- you want the pinned local validation tools available immediately

Check it with:

```bash
command -v codex-wrangler
codex-wrangler --help
python3 -m pytest
```

## Repository Validation Toolchain

For repeatable local validation, prefer the managed bootstrap helper:

```bash
python3 scripts/dev_setup.py
```

That creates `.venv/` and installs the same pinned Black, Ruff, pytest, and
pytest-timeout versions declared by the package's `dev` extra.

Use the hardened local quality gate when you want the repository's default
validation sequence:

```bash
.venv/bin/python scripts/run_local_quality_gate.py
```

That script runs Black through `scripts/run_black_safe.py`, which formats one
file at a time with timeout protection and probes whether the installed Black
supports flags such as `--no-cache`.

## Fresh-Shell Behavior

Both install modes above use a console script entry point. In both cases,
freshly launched shells should be able to run:

```bash
codex-wrangler --help
```

provided that `~/.local/bin` is on `PATH`.

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
