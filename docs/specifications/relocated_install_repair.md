# Repairing Moved Managed Installations

## Problem

Root metadata, completion records, and initialization receipts bind an
installation to an absolute project root. Moving or retaining a checkout at
a different path invalidates those bindings. Ordinary repair then loses
HOME and permission authority, and cannot retire occupied inactive slots.

## Recovery Interface

- `--repair CURRENT_ROOT --repair-from-root PREVIOUS_ROOT` explicitly
  authorizes recovery of records naming that absolute previous root.
- `--repair CURRENT_ROOT --repair-auto` discovers a previous root from
  validated records without requiring the operator to know the source path.
- These modifiers are mutually exclusive and valid only with `--repair`.
  They do not authorize selecting a new version, changing HOME mode,
  replacing context, or adopting arbitrary data with `--force`.
- Extend `--inspect CURRENT_ROOT` with a `repair` report describing recorded
  roots, validated relocation evidence, blockers, and shell-quoted suggested
  commands. Inspection must work when a receipt names a moved root.

## Evidence and Boundaries

Read only known root metadata, fixed-slot records, and the initialization
receipt. Bound metadata reads, reject duplicate fields and unsafe files,
and never execute old payloads or consult the old directory to establish
ownership. A previous root is a record binding, not a filesystem source.
All execution, writes, and path checks remain rooted at CURRENT_ROOT.

Require an otherwise valid record whose previous-root binding matches the
explicit selection. Directory names, launcher comments, package manifests,
and an operator assertion alone do not prove relocation. Automatic recovery
requires at most one distinct previous root among validated records. Refuse
conflicting origins or ambiguous layout bindings. Permit concordant old and
current bindings during recovery of interrupted publication.

Discover complete recorded layouts from standard canonical or historical
runtime roots, or from the root-level initialization receipt. Custom HOME,
launcher, and README paths may be recovered only from validated records;
all containment and reserved-context checks remain mandatory. Do not search
arbitrarily for custom runtime trees. Receipts still require unchanged
project, runtime, and isolated-HOME directory identities: a rename is
recoverable, but a copied or replaced receipt cannot authorize new identities.
Ordinary repair and inspection must also recognize validated current-root
supporting paths from completed slots or identity-checked receipts, not root
metadata alone, so a successfully recovered custom HOME remains usable
after the relocation modifier is no longer needed.

Preserve active-first exact-version, HOME, and permission authority. Recheck
the selected origin, layout, and ordinary authority under the maintenance
lock before writes. Validate existing HOME before ignore updates or candidate
work. Missing HOME remains an error, including in automatic mode.

## Publication and Retry

Use the ordinary unique-candidate repair transaction and pinned version.
Keep prior fixed slots and records unchanged until the new candidate validates
and is promoted. After promotion, rebind surviving validated rollback-slot
records to the current root and publish ordinary current-root projections.
Leave displaced slots and old candidate debris retained as diagnostic data.

For an unfinished moved first install, durably rebind its still-valid receipt
under the maintenance lock before new candidate writes. Further failures can
then be retried without remembering the old root. A completed-install repair
that fails before promotion leaves its old bindings intact and is retryable
with the same explicit or automatic modifier.

## Verification

Cover renamed and copied completed installations, unknown origins, custom
recorded HOME paths, active/rollback version differences, malformed and linked
evidence, conflicting source roots, stale authority at lock acquisition,
missing HOME, moved receipt identity checks, and interruption before and after
promotion. Real CLI fixtures must prove launcher usability, rollback record
validity, retry behavior, and preservation of context bytes and identities.
Run inspection and dry-run against retained installations before real repair.
