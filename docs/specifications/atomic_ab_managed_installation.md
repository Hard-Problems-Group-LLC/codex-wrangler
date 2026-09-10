# Atomic A/B Managed Installation

## Purpose

Keep an existing project-local Codex runtime usable when package installation,
repair, upgrade, or channel switching fails or is interrupted. A package
operation must become visible only after its isolated candidate passes all
required validation.

## Scope

This specification covers managed npm installation under `.local/codex/`,
including legacy migration, active-slot discovery, candidate preparation,
promotion, generated support files, subprocess interruption, and repair.
Project-local Codex context under `.local/codex-home/` and `.codex/` is never part of
an install slot.

## Required Behavior

1. **Complete A/B prefixes**

   Each managed slot must be a complete npm prefix under
   `.local/codex/slots/a` or `.local/codex/slots/b`. A slot contains its own
   `package.json`, `package-lock.json`, `node_modules/`, and managed completion
   record. The shared npm cache may remain at `.local/codex/.npm-cache`.

2. **One authoritative active pointer**

   POSIX systems must use one relative `.local/codex/active` symbolic link
   whose target is exactly `slots/a` or `slots/b`. Promotion must create a
   temporary relative link in `.local/codex/` and atomically replace `active`.
   Systems where symbolic links are unsuitable may instead use one strict
   `.local/codex/active-slot` regular file containing exactly `a` or `b` plus
   one newline. An install must reject a state in which both pointer forms are
   present. Pointer targets, slot paths, and candidate paths must remain within
   the canonical managed local directory. A pointer is valid only when its
   selected slot is a real directory with a valid managed completion record;
   a syntactically valid pointer to a missing or incomplete slot must stop
   maintenance before either fixed slot is changed.

3. **Inactive-only preparation**

   Package writes, cleanup, and npm installation must first affect only a
   unique candidate directory outside both fixed slots. The active pointer,
   both fixed slots, launcher, local README, managed root metadata, legacy
   runtime, and every context directory entry must remain unchanged until
   candidate validation succeeds. Before candidate work, maintenance may
   atomically publish the complete managed ignore block and, for a non-repair
   operation that is already proven to use isolated HOME, create only the
   absent empty isolated-HOME directory needed by the eventual launcher. It
   must not inspect, merge, replace, or populate protected HOME contents. An
   occupied inactive fixed slot may be retired only
   when its completion record and package evidence validate as managed;
   otherwise maintenance must preserve both it and the candidate and fail
   closed. Only then may the inactive slot be renamed to a transaction-unique
   retired path and the candidate renamed into its place. The moved candidate
   must be revalidated and flushed before promotion. Any
   pre-pointer failure must restore the retired inactive slot; the former
   active slot must remain in place after successful promotion.

   The configured managed local root must be disjoint from the configured
   Codex home, launcher, local README, and repository `.gitignore`. Generated
   file paths must likewise be mutually distinct and must not contain, or be
   contained by, the Codex home. Configuration must reject any overlap before
   a maintenance lock, candidate, or generated file can be created, because a
   later inactive-slot replacement or uninstall could otherwise remove
   protected context or published support files. Because initially absent
   paths may later run on a case-insensitive filesystem, equality and containment
   must also be rejected component-wise under Unicode case folding. The fixed project roots
   `.local/codex-home`, `.codex-home`, `.codex`,
   `.agents`, and `.git` are reserved. The `.local` container is
   also protected except for the exact canonical managed children
   `.local/codex` and `.local/codex-home`. Neither the removable
   runtime nor a generated launcher/README may contain or descend into another
   protected tree.

4. **Candidate validation**

   A candidate is promotable only when its npm shim exists and is executable,
   the shim's canonical target remains within the candidate, its required
   native payloads pass structural extent validation, its regular non-link
   package manifests and lockfile remain within the candidate and agree with
   the selected exact version, and invoking the candidate with `--version`
   reports that exact version within a short hard timeout. The expected
   platform-package manifest must exist, parse, and report the platform-
   suffixed form of the selected exact version. A completion record must be
   written atomically only after those checks pass. It must also retain the
   launcher-affecting HOME and permission choices plus the available-version
   catalog needed to reconstruct root compatibility projections after a
   post-pointer crash. The record must be bound to the canonical project root,
   configured managed-local relative path, fixed slot name, and complete
   runtime/HOME/launcher/README path tuple; malformed selector, channel,
   Boolean preference, path, or catalog types invalidate it.

5. **Atomic, durable promotion**

   The candidate and its completion record must be flushed before promotion.
   Pointer replacement must use a same-directory temporary entry and
   `os.replace`. Regular managed support files must likewise use
   same-directory temporary files, flush and `fsync` before replacement, and
   flush the containing directory where the platform supports it. The fixed
   slot's parent must be flushed after candidate activation and before pointer
   commit. A failed replacement must leave the prior entry intact. If pointer
   replacement succeeds but the following directory flush fails, diagnostics
   must identify the new verified slot as active with uncertain crash
   durability instead of claiming it was not promoted.

6. **Failure invariant**

   npm failure, timeout, `KeyboardInterrupt`, native validation failure,
   version mismatch, or any exception before pointer replacement must leave
   the previous active runtime, both restored fixed slots, launcher, local
   README, root metadata, and every pre-existing context entry unchanged. The
   full managed ignore block may already have been published, and a proven
   non-repair isolated-HOME operation may have created only an absent empty
   HOME directory; neither exception makes a candidate active or changes
   existing context contents. Transaction-owned candidate and cache scratch
   may change or remain for diagnosis and may be cleared by the next locked
   transaction.
   Before candidate creation or npm installation, maintenance must preflight
   the existing package/lock/metadata projections, launcher, local README, and
   `.gitignore` for regular-file shape, managed ownership where required, and
   an unambiguous ignore block. Fresh install may adopt only absent, empty, or
   exactly owned runtime and isolated-HOME roots unless the operator explicitly
   passes `--force`. A conflict that is already observable may not be deferred
   until after pointer commit.

7. **Post-commit recovery**

   Pointer replacement is the runtime commit point. The former active slot
   must not be deleted during the committing transaction. If a later support
   file write fails, the verified new slot remains active and a subsequent
   operation must reconstruct the recoverable metadata projection from the
   active completion record instead of attempting a destructive rollback.
   Projection-only reconfiguration and catalog updates must atomically update
   that authoritative record while holding the maintenance lock, and must
   report explicitly when the authority changed but generated projections did
   not all refresh.

8. **Legacy migration**

   Canonical namespace migration from `.codex-local` to
   `.local/codex` is governed by
   `canonical_local_codex_layout.md`. A validated A/B tree, including both
   slots and its active pointer, moves as one directory identity. Historical
   completion records lacking the newer complete path tuple remain usable
   after the move only while the exact managed compatibility link proves that
   namespace transition.

   Separately, when no active pointer exists but the root
   `.codex-local/node_modules` runtime is usable before namespace
   migration, that legacy runtime remains active while slot `a` is
   prepared. A failed A/B conversion must preserve package files, runtime,
   launcher, README, metadata, and context byte-for-byte. The first successful
   pointer promotion may publish a slot-aware launcher; it must not delete the
   pre-slot runtime in that transaction.

9. **Maintenance serialization**

   Install-like, update, projection-only reconfiguration, and uninstall
   operations must hold one stable project-local maintenance lock from
   active-state selection through promotion and support-file publication. A
   lock must live outside every tree uninstall can remove so its inode cannot
   be replaced while held. Uninstall must retain that empty lock inode and a
   minimal ignore rule because unlinking a held lock creates a replacement-
   inode race. A second maintainer must fail safely before
   changing either slot. Before
   creating or opening that lock, the wrangler must prove that the configured
   managed local root is canonically contained by the project and that neither
   the local root nor an ancestor beneath the project is a symbolic link. A
   rejected path must not create a lock or any other file through the link.

10. **Subprocess cleanup**

    Managed npm commands must run in an isolated subprocess group where the
    platform supports it. On timeout or interruption, the wrangler must stop
    the group, wait for termination, and leave the active installation
    untouched. `KeyboardInterrupt` should return the conventional interrupted
    status without a Python traceback.

11. **Engine compatibility**

    When the selected package declares a Node.js engine requirement, the
    wrangler should reject a demonstrably incompatible active Node.js runtime
    before npm installation. An unknown or unparsable constraint may be left
    to npm, but it must not weaken the inactive-only failure invariant.

12. **Repair and dry-run behavior**

    `--repair` and `--repair-install` must rebuild through a unique candidate
    and may never remove a valid active slot. Exact-version evidence may come
    from a valid active slot completion record, concordant slot manifests, or
    legacy managed records. Repair may tolerate one malformed pointer or one
    pointer selecting an incomplete slot when ownership and an exact version
    survive. Only a valid completion record selected by a syntactically valid
    pointer is authoritative. Otherwise repair must aggregate surviving root,
    incomplete-selected-slot, and completed-slot evidence and reject any
    version disagreement. HOME-mode and reasonable-permissions recovery
    follows the same authority order: a valid selected completion record wins;
    otherwise every surviving root and completed-slot Boolean must agree.
    Repair must require an explicit `--shared-home` or `--isolated-home`
    only when no HOME-mode authority survives, and such a choice may fill
    missing evidence but never override surviving evidence. Lost permissions
    evidence defaults to disabled. Weak manifest evidence must be a regular
    non-link file reached through non-link ancestors inside its claimed prefix.
    Repair must not recreate an absent isolated HOME. It must validate the
    candidate before replacing that pointer and retain any
    displaced fixed-slot tree under a retired recovery name. Both pointer
    forms together remain an unsafe ambiguity and must be refused.
    `--skip-install` and `--dry-run` must not change the active pointer.

13. **Bounded progress feedback and network retries**

    A managed subprocess that remains running must emit periodic elapsed-time
    heartbeats while preserving the child's normal output stream. Heartbeats
    must show the overall timeout when one applies and must not extend that
    deadline. Managed npm environments must use a small, bounded retry count
    and explicit per-fetch and retry-delay limits so transient slow-network
    failures receive another chance while stalled operations still terminate.
    Controlled `NPM_CONFIG_*` names must be removed case-insensitively before
    safe canonical values are applied. Every managed child must receive a fresh
    mode-0700 maintenance HOME in a transaction-unique
    `.local/codex/.maintenance/.run-*` workspace outside `.npm-cache`, with
    `TMPDIR`, `TMP`, and `TEMP` pinned to a private materialized directory;
    registry lookup uses wholly out-of-project temporary state. Persistent
    cache ancestors and exact scratch roots must be real directories. Before
    npm starts, legacy maintenance scratch, `_npx`, and `_cacache/tmp` must
    be unlinked recursively without following descendants, after which every
    remaining cache entry must be a regular file or real directory. Timeout and
    interruption diagnostics during candidate preparation must
    explicitly state that the previously active runtime remains untouched.
    Repair ownership and exact-version evidence, and projection state used by
    update or reconfiguration, must be reread after acquiring the lock.
    Self-test must likewise re-resolve its audit target under that lock and
    refuse to audit when its pre-inspection pointer/runtime identity changed.

## Inspection and Self-Test

Inspection must remain read-only and report pointer kind, active slot, inactive
slot, legacy state, slot completion/version evidence, candidate or retired
debris, and ambiguity, invalid-pointer, or mid-inspection pointer-change issues.
Diagnostic reads must reject symbolic links, FIFOs, devices, sockets, and other
non-regular inputs without blocking. Self-test and npm audit must target the
resolved active slot rather than a fixed root npm prefix.

The generated launcher must reject a symbolic-link managed local root, an
invalid or incomplete selected slot, and any Codex shim whose canonical target
escapes the selected prefix. Normal npm-created in-prefix shim links remain
valid.

## Two-Slot Process-Lifetime Limit

The pointer and directory transaction protects launches and maintenance, not
arbitrarily long file lookups by a process after it starts. An operator must
not perform two successive promotions while an older Codex process might
still need path-based auxiliary files from the slot that would be reused. A
future immutable-generation design may remove this operational restriction;
the fixed A/B implementation does not claim process reference counting.

## Required Tests

- Preserve the active pointer, active slot, generated files, and context when
  npm fails, times out, or raises `KeyboardInterrupt`.
- Refuse nonempty unowned runtime/HOME roots and an occupied inactive slot
  without a valid managed completion record, preserving sentinels in each.
- Refuse promotion for a missing shim, truncated native payload, corrupt
  lockfile, escaping symlink, platform-manifest mismatch, hung smoke check, or
  exact-version mismatch.
- Exercise successful `a` to `b` and `b` to `a` promotions while retaining the
  former slot.
- Exercise failed and successful migration from a legacy root runtime.
- Inject failures immediately before and after the pointer commit point and
  prove that either the prior or verified candidate runtime remains usable.
- Reject simultaneous pointer forms, traversal, absolute link targets,
  symlinked managed-root or slot directories, and paths outside the managed
  local directory. Prove rejection occurs before an escaped lock file is
  created.
- Reject custom layouts in which the managed local root overlaps the Codex
  home, launcher, local README, or `.gitignore`, including both containment
  directions and paths nested beneath one fixed slot.
- Serialize concurrent maintenance and release the lock after failure or
  interruption, including update and uninstall against installation.
- Exercise POSIX symbolic-link promotion and the strict regular-file fallback
  without requiring symbolic-link privileges on fallback platforms.
- Verify that generated file replacement is atomic and preserves the old file
  when replacement fails.
- Verify periodic subprocess heartbeats, bounded npm retry settings, and an
  unchanged overall timeout deadline.
