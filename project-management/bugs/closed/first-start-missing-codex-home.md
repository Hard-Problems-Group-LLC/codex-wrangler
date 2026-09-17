# First startup rejected an absent Codex state directory

Status: Closed
Date: 2026-09-11T11:38:47-07:00
Owner: Codex
Scope: generated launcher HOME selection

## Root Cause

A fresh managed install creates the outer isolated HOME without initializing
protected context contents. The launcher unconditionally exported
`CODEX_HOME="$HOME/.codex"`, but actual Codex 0.154.0 startup requires an
explicit CODEX_HOME to exist. Repeating a successful upgrade could not fix
this mismatch. A/B installation and promotion were working correctly.

The package and launcher `--version` checks did not catch the defect because
that command bypasses startup HOME resolution. Candidate checks also use a
separately initialized maintenance workspace, not the project context.

## Original Local Resolution

When the selected HOME exists and its `.codex` child is truly absent, clear
CODEX_HOME and let Codex initialize its own default there on first startup.
Never inherit another project's CODEX_HOME. Keep explicit selection for
existing entries, including files and dangling links, and for a missing outer
HOME. Do not add context initialization to package maintenance or weaken
repair's refusal to recreate lost HOME. The affected external project was not
modified; this resolution changes the tool's generated launcher.

## Original Validation

All 493 tests pass. Ten deterministic launcher cases cover shared/isolated
HOME with absent, existing, file, dangling-link, and lost-outer-HOME states.
Two additional tests use the locally installed Codex 0.154.0 binary with
`debug prompt-input` in disposable empty homes, without model requests or
existing session data. Those real-binary tests skip when the optional local
Codex runtime or Node is unavailable; the contract cases remain unconditional.

## Integration Decision, 2026-09-17

Origin commit `cd1009b` fixed the same defect with stricter runtime checks:
create only the missing `.codex` child with mode 0700, retain explicit
CODEX_HOME, and reject missing outer HOME and linked isolated context paths.
Adopt that implementation rather than combine it with the local unset logic.
The local A/B launcher tests now enforce that contract; the native startup
probe is retained alongside upstream's install-to-launch and recovery tests.
See `fresh-launch-missing-codex-home.md` for the current resolution. This
record preserves the earlier local diagnosis and validation, not a second
active implementation. Maintenance still does not initialize context.
