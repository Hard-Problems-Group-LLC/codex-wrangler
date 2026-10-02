# Local Quality-Gate Scope

The consuming repository's cached quality-gate wrapper must prune reserved
operator-local state before collecting fingerprint inputs. Exclude `.local`,
legacy `.codex-local` and `.codex-home`, `.codex`, `.agents`, and the local
restricted-name policy file. These are not publishable project sources.

Pruning must not depend on successful Git-ignore discovery: the shared
helper's broad ignore query can fail on submodule paths. Never read context,
credentials, sockets, or other runtime entries beneath those reserved roots
to fingerprint a commit. Keep ordinary source and untracked candidate files
in scope, retain every quality check, and leave the shared submodule unchanged.

Exercise real file discovery with local-state fixtures and a Unix socket.
Prove reserved roots are not walked, even when Git-ignore filtering is off.
This overlay does not resolve the separately tracked parent entropy-harness
scope defect; publication must still scan explicit parent candidates.
