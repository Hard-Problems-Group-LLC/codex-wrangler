# Canonical Project-Local Codex Layout

## Purpose

Place all current project-local Codex runtime and isolated-home state beneath
the project's `.local/` directory without abandoning installations created by
older codex-wrangler releases.

## Canonical and Legacy Paths

The default managed npm root is `.local/codex`. The default isolated Codex
home is `.local/codex-home`. The generated launcher remains
`bin/codex-local`, and the generated operator guide remains
`README-LOCAL-Start-Codex.md`.

The deprecated `.codex-local` and `.codex-home` paths remain recognized only
for automatic migration and compatibility. Explicit custom `--local-dir` or
`--codex-home-dir` values are not silently reinterpreted as legacy defaults.

## Migration Requirements

1. Before changing paths, codex-wrangler must prove that a legacy package
   tree is managed or that protected legacy home state is the default
   isolated home selected by the managed installation. An isolated legacy
   runtime must not cross its first exchange unless a real legacy HOME can be
   exchanged too or an exact completed HOME compatibility binding already
   exists; absent or merely empty unbound HOME paths fail before mutation.
   Unknown files, links,
   or conflicting real trees at both old and canonical paths must stop the
   operation without merging, deleting, or overwriting either tree.
2. Install, upgrade, repair, catalog update, and launcher reconfiguration may
   complete a pending default-layout migration. Inspection, self-test,
   uninstall, `--skip-install`, and every `--dry-run` remain non-migrating.
   Dry-run and inspection output must identify a supported pending migration;
   conflicting layouts must fail closed with both paths named.
3. The stable project-root maintenance lock must serialize locked
   rediscovery, launcher bridging, and both directory exchanges. No
   configurable runtime, HOME, launcher, README, or other managed output may
   equal, contain, or be contained by that fixed lock pathname. The
   requested maintenance operation reacquires the same stable lock after a
   completed migration and revalidates canonical authority before mutation.
   Before publishing ignore coverage, a bridge launcher, or any namespace
   change, migration must rediscover HOME-mode authority while still holding
   that lock, abort if it differs from the mode used to construct the original
   plan, and rebuild the plan from the locked filesystem state.
4. Only a launcher rendered from a validated pending-migration plan may
   enable legacy fallback, and it must be atomically published before either
   directory moves. It selects a real canonical tree when present and falls
   back only to the exact real legacy default while migration is pending. A
   normal canonical launcher must refuse a real legacy tree rather than
   silently adopting it. During the exact runtime-migrated/HOME-pending state,
   only that validated bridge may bind an old isolated slot record to the real
   legacy HOME while the canonical HOME is absent or exactly staged. Both
   forms must reject unrelated symbolic links and continue to pin `CODEX_HOME`
   to the selected HOME's `.codex` directory. An explicit transition
   between shared and isolated HOME must be rejected while legacy migration is
   pending; migration-only discovery must recover that recorded mode even
   after runtime exchange has made ordinary incomplete HOME bindings
   non-authoritative. The recorded mode migrates first, and a second
   canonical-layout transaction may then change modes without weakening the
   bridge invariant.
5. On Linux, migration must stage at each absent canonical path a relative
   compatibility link whose final location is the corresponding legacy path,
   then use one atomic exchange to place the real directory at the canonical
   path and the link at the legacy path. Thus every namespace transition has
   either the old real directory or an old compatibility link to the new real
   directory, never a missing-path interval.
6. On platforms without a supported atomic directory/link exchange,
   codex-wrangler must refuse automatic physical migration without changing
   either tree. It must not approximate the operation with copy-and-delete or
   a rename gap that could split concurrently written context.
7. A power loss before an exchange leaves the legacy real directory and an
   exact staged link. A later mutating invocation may safely finish that
   exchange. A loss after one of the two exchanges leaves a supported partial
   state; the bridge launcher must select the real legacy or canonical path
   independently for runtime and HOME.
8. Root metadata may authorize protected-HOME migration only when its project
   root, isolated-HOME choice, and complete path tuple match the exact
   historical defaults. A valid active pointer and its completed slot record
   are authoritative over stale root projections. Without that selection,
   every surviving root and completed-slot HOME-mode value must agree or
   migration must fail closed. New A/B records must carry a complete path tuple.
   Historical root metadata and older completion records may remain authority
   at the canonical runtime only while the exact legacy runtime compatibility
   link proves the old local-root binding. When such a record selects isolated
   HOME, the exact legacy HOME compatibility link and real canonical HOME must
   also bind that state. Migration proof must bind its evidence file to a
   real legacy runtime or to a real canonical runtime behind the exact legacy
   runtime link; copied old-format metadata in an unbound canonical tree is
   never authority. A narrowly migration-only validator may accept a real
   legacy HOME plus an absent or exactly staged canonical HOME to finish the
   runtime-first partial state; ordinary maintenance and launchers may not.
   New projections use canonical paths; arbitrary aliases remain invalid.
9. Migration must not inspect, copy, merge, delete, or recreate context,
   authentication, sessions, memories, history, logs, goals, or other Codex
   home contents. Directory identity and contents move together.
10. The managed ignore block must cover `.local/` and retain explicit legacy
    path rules that match both real directories and compatibility symlinks, so
    neither migrated links nor retained diagnostic evidence can become
    tracked. Uninstall must keep that full coverage until every managed tree
    and compatibility link is gone, then atomically narrow the block to the
    stable maintenance-lock rule. If shared-HOME uninstall preserves any
    project-local HOME or compatibility link, its complete ignore coverage
    must remain as long as that state survives.
11. A fresh install may adopt canonical runtime or isolated-HOME roots only
    when they are absent or empty, or when exact root/slot authority already
    binds them to this project. A nonempty isolated HOME previously used only
    with shared-HOME mode remains unowned. Ordinary install must preserve and
    refuse every unowned entry; only explicit `--force` may adopt it when no
    legacy migration is pending. `--force` does not waive ambiguous physical
    provenance between a pending legacy runtime and an unbound canonical HOME.
12. Ordinary uninstall must require exact, regular root metadata bound to this
    project, canonical-or-proven-legacy path tuple, and locked HOME mode before
    removing runtime or history. Parseable or linked JSON is not ownership;
    `--force` remains the explicit destructive override.
13. Every managed runtime, HOME, generated output, lock, Git, and reserved
    context path must remain disjoint under both ordinary component comparison
    and component-wise case folding. Initially absent case-only aliases must be
    rejected before a layout accepted on a case-sensitive host can collide after
    transplant to a case-insensitive filesystem.

## Required Tests

- Migrate both legacy non-A/B and validated A/B package layouts.
- Preserve package and context sentinels, directory identity, active pointer,
  rollback slot, and generated-file ownership.
- Exercise runtime-only, home-only, fully migrated, pre-exchange staged-link,
  and post-first-exchange partial states, and reject both explicit HOME-mode
  transitions until namespace migration has completed in the recorded mode.
- Refuse component-wise casefold aliases among runtime, HOME, generated output,
  Git, lock, and reserved context paths before any of them need to exist.
- Refuse two real trees, foreign links, files at either path, unproved package
  ownership, nonempty unowned canonical roots, isolated runtimes with no
  migratable/bound legacy HOME, runtime-only compatibility bindings that could
  claim an unrelated HOME, and unsupported atomic exchange
  without changing any input.
- Prove dry-run, inspect, self-test, uninstall, and skip-install do not move
  either tree.
- Verify the bridge launcher selects each valid partial state, including
  old A/B completion records between runtime and HOME exchange, rejects
  canonical pathless records and foreign links, and pins `CODEX_HOME`
  consistently.
- Verify standalone repair discovers exact-version evidence in both layouts
  and repairs into the canonical layout after migration.
- Verify active-slot HOME authority overrides stale root metadata, invalid or
  incomplete pointers force concordant root/slot aggregation, and a locked
  authority change aborts before either exchange.
