# Recoverable First Installation

Before npm or candidate writes during a fresh install, record initialization
intent separately from completed runtime metadata. The receipt lives at
project-root `.codex-wrangler-initial-install.json`, outside the runtime tree,
so an interrupted atomic receipt write cannot poison runtime-root adoption.
Ignore the receipt and its atomic-write temporary files and reserve its path
against configurable managed outputs.

Create the receipt only under the stable maintenance lock after rechecking
adoption and provisioning empty runtime and isolated-HOME directories. Flush
these directories and their ancestors, then atomically publish and flush the
receipt before npm. Bind it to the exact project path, complete layout,
project/runtime directory identities, isolated-HOME identity when applicable,
exact resolved version, HOME mode, and permission defaults. Existing foreign
data without evidence must not acquire implicit authority, even if its names
resemble wrangler candidates or caches.

A valid receipt permits another install attempt or explicit repair using the
same version, HOME mode, and permission defaults. It is not evidence of a
usable runtime and must not turn a repeated permissions flag into a
reconfiguration-only operation. Retry uses a new unique candidate and leaves
prior candidate evidence and context untouched. Reject replaced directories,
linked/nonregular/malformed receipts, and inconsistent recovery options.
Never recreate an absent HOME during repair.

Completed root/slot authority takes precedence over unfinished intent. Remove
the receipt only after successful publication of completed state; clean a
still-valid receipt before ordinary uninstall removes its bound directories.
Do not let a stale receipt authorize a later replacement directory. Inspection
must report pending initialization without claiming a completed runtime.

Before requesting missing HOME authority for repair, determine whether usable
ownership and exact-version evidence exist. Give complete shell-quoted
absolute-path repair examples. For unknown data, explain that explicit HOME
selection does not establish ownership, and preserve all data. Old interrupted
installs without receipts still require a separate, deliberate operator
decision; do not infer their provenance from candidate names.

## Legacy Candidate Recovery

Explicit absolute-path repair may recover a receipt-less first install from
the exact generated package manifest inside an unfinished unique candidate.
This is a fallback only when root/slot/receipt authority is unavailable; it
must not broaden ordinary install adoption, migration, or uninstall ownership.
Receipt-less custom layouts without discoverable bindings remain unsupported.

Require a real contained runtime with at most 64 immediate entries, limited
to real `.npm-cache`, `.maintenance`, and `.candidate-<32 lowercase hex>`
directories. Every candidate must contain a bounded, single-link, regular,
non-following `package.json` with exactly the generated name, boolean private
flag, and sole exact-version Codex devDependency, without scripts or extra
fields. All candidate requested versions must agree. Do not infer HOME mode
or permissions from those manifests: require an explicit shared/isolated HOME
choice, preserve existing HOME, and default permissions to disabled.
Validate the selected existing HOME before rewriting generated ignore rules;
an unsupported custom layout must not lose context ignore coverage when its
default HOME is absent.

Revalidate the same evidence under the maintenance lock. Preserve old
candidates and never execute or promote their unvalidated payloads. Before
creating new candidate/cache data, publish a normal initialization receipt
binding the recovered exact version, explicitly chosen HOME, and conservative
permissions. Permit this nonempty-root transition only after revalidating the
strict legacy manifests, while holding the maintenance lock. Then install
and validate a fresh unique candidate at the recovered exact version.
If repair itself fails, the new receipt permits safe retry even when the
newest candidate has not acquired its own manifest yet.
Reject names alone, foreign entries, links, malformed or incomplete manifests,
and conflicting requests. A missing receipt alone is no longer sufficient to
reject this narrow, verifiable historical state.

Validation must cover first-install npm failure, timeout, interruption,
validation failure, repeated permissions flags, exact-version retry/repair,
receipt-write failure, directory replacement, unsafe receipt inputs, foreign
data introduced during lookup, and unchanged existing context. All
reproductions use disposable targets, not operator client checkouts.
