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

Validation must cover first-install npm failure, timeout, interruption,
validation failure, repeated permissions flags, exact-version retry/repair,
receipt-write failure, directory replacement, unsafe receipt inputs, foreign
data introduced during lookup, and unchanged existing context. All
reproductions use disposable targets, not operator client checkouts.
