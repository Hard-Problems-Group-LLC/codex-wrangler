# Fresh launch failed with a missing CODEX_HOME

Status: Closed
Date opened: 2026-09-13T12:31:56-07:00
Date closed: 2026-09-13T12:41:30-07:00
Owner: Codex
Scope: generated launcher initialization, not client-project recovery

## Root Cause

A successful fresh install creates an empty isolated HOME. The generated
launcher exported `CODEX_HOME=$HOME/.codex` without creating that child.
Codex 0.154.0 rejects the explicit nonexistent directory at normal startup.
The candidate verification did not expose the gap: it used a disposable
maintenance HOME with `.codex` already present, and `--version` alone did not
exercise normal context initialization. Existing launcher stubs also omitted
the external CLI's directory requirement.

## Resolution

Initialize only the missing `.codex` child at normal launch, with mode 0700
and no recursive parent creation. Preserve existing context and permissions;
refuse a missing HOME, non-directory destinations, and isolated `.codex`
links. Keep existing shared-mode directory links supported. Accept a failed
mkdir only when a concurrent first launch has created a real directory.
Do not change install, repair, migration, or inspection preservation rules.

## Verification

`tests/test_launcher_home.py` reproduces successful installation followed by
failed first launch before the fix, using the real owned installation and
launcher code with deterministic external npm/Codex fixtures. It covers
default isolated, custom isolated, and shared HOME modes, existing context,
missing HOME, unsafe destinations, and mkdir failure/concurrency.

The full suite passed 507 tests, including two opt-in smoke tests against
the actual Codex 0.154.0 native binary in disposable directories.
Those offline `login status` checks reached `Not logged in` instead of the
missing-directory error. No client checkout or existing context was changed.
See `DEVELOPMENT.md` for the repeatable native-binary smoke command.
