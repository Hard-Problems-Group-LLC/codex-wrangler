# Dual-Wrangler Recovery Phase/Slice Stack

Use this tracked file as the recovery anchor for the interrupted local Codex
and Claude installer incident. Keep it aligned with
`.local/ubersight/status.json` at phase and slice transitions.

Do not record prompts, transcripts, credentials, operator-location details,
network names, or other sensitive runtime state here.

## Current Stack

Phases:

* P1: Evidence preservation, done.
* P2: Upstream reconciliation, done.
* P3: Codex transactional install, done.
* P4: Claude transactional install, done.
* P5: Live runtime recovery, done.
* P6: Validation and handoff, done.
* P7: Canonical local layout, done.
* P8: Canonical migration validation and handoff, done.

Slices:

* S1: Specify A/B invariants, done.
* S2: Implement slot activation, done.
* S3: Add interruption regressions, done.
* S4: Run full source validation, done.
* S5: Dog-food live activation in each owning project, done.
* S6: Verify context, rollback, installer isolation, and upstream state, done.
* S7: Specify canonical paths and migration invariants, done.
* S8: Implement discovery, atomic exchange, and launcher compatibility, done.
* S9: Add legacy, A/B, partial-state, conflict, and dry-run regressions, done.
* S10: Run source gates and dog-food migration, done.

## Current Work

P8/S10 completed after final adversarial audit. Canonical migration,
transactional maintenance, standalone repair, cache/workspace containment,
launcher authority, submodule containment, and cross-platform path disjointness
are validated. Live A/B/context identities remain unchanged; inspection and
networked self-test are clean; all 481 tests and required gates pass; independent
review reports no remaining Critical or High finding. The documented same-UID
pathname check-to-use class remains deferred for directory-FD hardening.
