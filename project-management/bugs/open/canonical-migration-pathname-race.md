# Canonical Migration Path Validation Can Race Manual Namespace Swaps

Status: Open  
Date opened: 2026-09-10T02:58:22-07:00  
Owner: unassigned  
Scope: canonical migration, disposable maintenance cleanup, and submodule-path use on Linux

## Summary

Canonical migration proves ownership and path shape before invoking
`renameat2`, but its final exchange still addresses both entries by pathname.
A concurrent manual rename or symlink replacement outside the stable
codex-wrangler maintenance lock could change a validated parent or source
between validation and exchange. Disposable maintenance cleanup has the same
parent-path limitation if a same-UID external writer replaces the validated
`.local/codex/.maintenance` ancestor before cleanup. Stage-two submodule
traversal now revalidates real contained components around each Git operation,
but a same-UID writer could still swap a parent in the final check-to-use
interval.

## Impact

Normal concurrent wrangler processes are serialized and controlled migration
is safe, but the implementation does not yet defend completely against a
separate actor mutating the same namespace during the narrow validation-to-
exchange interval.

## Required hardening

- Open and validate both parent directories with no-follow directory handles.
- Bind source identity to device/inode evidence gathered during managed
  ownership proof.
- Invoke `renameat2` with those parent directory descriptors and relative
  basenames, including through the isolated system-Python fallback.
- Recheck opened-parent and source identities before exchange and add injected
  parent/source-swap regressions.
- Retain and validate a directory descriptor for the maintenance container,
  then clean transaction basenames relative to it or quarantine cleanup when
  its identity changes; add an injected maintenance-parent-swap regression.
- Bind recursive submodule traversal to opened repository/parent identities, or
  use a subprocess strategy that cannot resolve a swapped pathname outside
  those handles; add an injected check-to-Git-use swap regression.
