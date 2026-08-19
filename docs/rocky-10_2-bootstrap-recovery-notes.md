# Rocky Linux 10.2 Bootstrap Recovery Notes

## Purpose

Capture portable findings from rebuilding this checkout after source was
transplanted from Rocky Linux 9.8 to Rocky Linux 10.2. These notes are intended
to inform the related assistant-isolation utility without copying
host-generated state between machines.

## Ownership Layers

- Rocky owns `/usr/bin/python3` and the DNF-installed Node.js runtime. Project
  bootstrap must not install packages into or replace the system Python.
- The operator owns the pyenv controller and plugin repositories. They are
  pinned to reviewed detached tags and must not be updated as a side effect of
  entering or installing a project.
- A project owns its exact `.python-version`, declarative dependencies, and
  generated `.venv`. The runtime selection is source; the virtual environment
  is disposable host state.
- `codex-wrangler` owns the `.codex-local` package tree, generated launcher,
  generated local README, and marked `.gitignore` block. The ignored
  `.codex-home` path is managed in location but contains protected operator and
  assistant state that should survive package repair and host migration.
- npm `node_modules` trees are host-generated state. Preserve manifests and
  locks when coherent, but rebuild the dependency tree after an operating
  system or architecture transplant.

## Failure Pattern

The source scrub correctly removed `.venv` and both root and managed
`node_modules` trees. A later emergency `npm install @openai/codex` at the
repository root restored a runnable Codex, but it also created an untracked
root `package.json`, `package-lock.json`, and `node_modules`. That is the exact
layout this utility exists to avoid.

The intended managed launcher remained present but failed closed because its
`.codex-local/node_modules/.bin/codex` target was gone. Inspection also found a
surviving managed manifest/lock mismatch, so the correct recovery path is an
explicit repair install, not adoption of the emergency root npm tree.

The rsynced `.codex-home` survived and contains project-local memories,
sessions, rules, goals, history, and authentication state. This is valuable
accumulated context, not an npm or Python cache. Recovery must preserve the
entire home while rebuilding only the package payload.

The Python bootstrap had separate problems: `bootstrap.sh` was not executable,
and stage 2 tried to pull an existing detached pyenv checkout before restoring
old managed interpreter pins. Both assumptions conflict with the newer host
administration policy.

## Portable Recovery Sequence

1. Preserve source, lockfiles, specifications, and project-local runtime
   selectors. Remove host-built virtual environments and dependency trees.
2. Verify the organization-specific SSH host alias before any remote Git
   operation. This repository uses `github-hpg`; do not replace it with a
   generic GitHub host or infer commit identity from the alias.
3. Verify the user-owned pyenv remote and reviewed tag separately from project
   bootstrap. A project installer should consume that tool, not update it.
4. Run standard and development bootstrap paths independently. Standard mode
   proves the stable user command; development mode proves the repo-local
   editable toolchain and activation policy.
5. Use the installed isolation utility to inspect and repair its managed CLI
   payload. Do not move an emergency root dependency tree into the managed
   directory.
6. Back up an existing assistant home before any real repair or schema
   migration, then verify its memory and session paths remain present.
7. Verify the managed launcher, version command, inspection, self-test, HOME
   isolation, and authentication status before deleting the emergency install.
8. Delete only the emergency root npm artifacts. Keep `.codex-local` and
   `.codex-home` as ignored, project-owned runtime state.

## Related Utility Checklist

- Test direct executable modes for every documented shell entry point.
- Keep controller/version-manager updates explicit and separate from project
  bootstrap.
- Distinguish a supported language floor from the selected daily runtime.
- Detect missing generated dependency trees and stale lock/manifests with an
  actionable repair mode.
- Treat assistant homes, memories, sessions, rules, and history as protected
  state. Cache scrubbing must not classify the whole assistant home as cache.
- Never fall through from a missing managed executable to a registry lookup or
  global binary of the same name.
- Make sandbox failures identify the out-of-repository path they attempted to
  modify.
- Treat isolated authentication state as a separate recovery concern; package
  installation success does not prove that the isolated CLI can resume work.

## Validation Outcome

- Standard bootstrap rebuilt the user tool venv from the selected pyenv
  CPython 3.14.6 base and installed the stable user command successfully.
- Development bootstrap reused pyenv at detached reviewed tag `v2.7.3`,
  refreshed the Python 3.14.6 `.venv`, installed the editable project and Git
  hooks, allowed direnv, and restored the project-bound user launcher.
- Managed Codex catalog refresh selected stable `0.144.1`; repair installed
  matching manifest, lockfile, package, and Linux platform-package versions.
- Inspect reported zero issues and zero warnings. Self-test passed all checks,
  including launcher version, resume help, runtime diagnostics, and npm audit
  with zero vulnerabilities.
- The isolated launcher reported `codex-cli 0.144.1`, and the supported login
  status command confirmed that the transplanted ChatGPT authentication works.
- Before repair, `.codex-home` was copied with permissions to a temporary
  backup. Original and backup both measured 154,483,126 bytes and 5,252 files;
  those metrics and the memory/session/rule directory metadata were unchanged
  after package repair.
- The final required quality gate passed with 164 tests under Python 3.14.6.
  The emergency root npm install was then removed, and the isolated launcher
  plus inspect checks remained clean.
- The already-running emergency Codex process survived package removal, but
  its sandbox launcher still referenced helper binaries in the deleted root
  `node_modules` tree. Remaining checks had to run on the authorized real host.
  Future cleanup should preferably end the emergency session and relaunch from
  `bin/codex-local` before removing its package tree.
