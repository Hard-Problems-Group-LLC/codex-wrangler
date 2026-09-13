# Repair ignores legacy unfinished candidate manifests

Status: Closed
Date opened: 2026-09-13T13:46:04-07:00
Date closed: 2026-09-13T14:00:49-07:00
Owner: Codex
Priority: High

## Evidence

After deploying initialization-receipt support, an operator still could not
repair an older interrupted first install. Read-only inspection on the
external machine found a canonical runtime containing npm cache, maintenance
state, and one unfinished candidate. That candidate's package manifest
matched the utility's generated shape and selected exact Codex `0.154.0`.
No root manifest, completion record, or initialization receipt survived; the
isolated HOME directory existed and was empty. No context contents were
read, no target utility was executed, and no external project was changed.

The prevention fix in `interrupted-first-install-ownership-gap.md` does not
retroactively create receipts. Repair checks generated root manifests but
does not inspect equivalent manifests in unfinished unique candidates. This
leaves a bounded historical recovery case unsupported, even when an operator
explicitly requests repair and can confirm HOME mode.

## Resolution Contract

Extend explicit repair with the narrow legacy-candidate contract in
`docs/specifications/first_install_recovery.md`. Require generated manifest
content and matching exact versions, not directory names alone. Refuse
foreign or unsafe entries. Preserve old candidates and context, require HOME
selection, and reinstall into a new validated candidate. Keep ordinary
install and uninstall ownership rules unchanged.

## Resolution

Explicit repair now validates a bounded canonical legacy namespace and exact
generated manifests, requires one agreed version and explicit HOME selection,
and records durable initialization intent before new candidate writes. It
preserves old candidate payloads without executing or promoting them, builds
a fresh validated runtime, and leaves recovery retryable after interruption.
The proof helper keeps legacy candidates disabled for migration, and ordinary
install/uninstall ownership rules remain unchanged. Invalid receipts cannot
be bypassed through the legacy fallback.

Moved repair HOME validation ahead of ignore-rule publication. A refused
custom-layout repair now preserves the original context ignore coverage
instead of replacing it with guessed default paths.

## Validation and Limits

All 595 tests passed in 60.57 seconds, including real CLI transactions with
deterministic external npm fixtures and actual native Codex offline startup
smoke checks. Added coverage for repeated repair failure, receipt transition,
pre-manifest crash, shared/isolated context preservation, missing custom HOME,
unsafe or ambiguous evidence, locked revalidation, and unchanged migration
and uninstall boundaries. Black, Ruff, compileall, explicit candidate entropy
scans, entropy sentinel/tripwire verification, and diff checks passed.

External-machine inspection was read-only; all reproductions and repairs ran
in disposable fixtures. The original npm interruption cause is not known.
No remote utility or client checkout was modified. This follow-up is local
and uncommitted pending publication. Receipt-less custom bindings and foreign
state without verifiable evidence remain unsupported; the separately tracked
same-UID pathname race limitation is unchanged.
