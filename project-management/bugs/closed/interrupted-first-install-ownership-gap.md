# Interrupted first install can block both install and repair

Status: Closed
Date opened: 2026-09-13T12:43:46-07:00
Date closed: 2026-09-13T13:21:56-07:00
Owner: Codex
Priority: High
Scope: first-install transaction provenance and repair diagnostics

## Audit Evidence

An operator supplied an external-machine report in which ordinary install
refused a populated canonical runtime root lacking exact ownership evidence.
Repair first demanded an explicit HOME choice and then refused ownership.
The report does not establish how that runtime root was populated. No client
checkout was inspected, mutated, or used for reproduction; this record is
based on source inspection in the utility repository.

The code at audit time could produce that state during a first installation:

1. `operations.install_like_operation` accepts an initially empty root and
   writes the package manifest inside a transaction-unique candidate, then
   creates persistent npm cache state before running npm.
2. If npm, validation, or the process fails before candidate promotion, no
   completed fixed-slot record or root projection need exist. The failure
   handlers preserve the candidate/cache; there is no durable initialization
   receipt binding that unfinished work to the project and requested layout.
3. The next ordinary install runs `require_safe_install_adoption`, which
   rejects that now-populated root because `managed_install_home_modes`
   cannot recover exact authority from it.
4. `repair.prove_managed_install` accepts completed fixed slots and managed
   root metadata/manifests, not an unfinished unique candidate. Standalone
   repair therefore cannot recover this state either.

This is a confirmed source-level retry gap and a possible explanation of the
external report, not a confirmed remote filesystem diagnosis. Existing
transaction-failure tests primarily preserve a previously managed runtime;
they do not verify retry after a failed first install.

## Related Diagnostic Defect

`config.config_from_args` requires a HOME-mode choice before calling
`build_repair_plan`. It can therefore instruct an operator to choose a HOME
mode even when no usable ownership evidence exists. The initial install
refusal also mentions `--repair` without its required absolute-path value.
The required argument and ownership refusal are intentional safeguards; the
problem is incomplete guidance and validation order, not those safeguards.

## Recommended Repair

- Design and atomically publish durable, project/layout-bound first-install
  intent before candidate/cache population, allowing bounded resume or
  rollback without claiming a completed runtime.
- Retain fail-closed handling of foreign roots and old debris without
  verifiable provenance; do not treat names such as `.candidate-*` or
  `.npm-cache` as ownership proof and do not apply `--force` automatically.
- Check whether repair has usable ownership/version evidence before asking
  for missing HOME authority; include complete quoted command examples and
  distinguish interrupted initialization from a damaged completed install.
- Add disposable first-install failure/retry tests for npm errors, timeout,
  interruption, and pre-promotion validation failures; retain foreign-root,
  context-preservation, concurrency, and power-loss boundary coverage.

## Resolution

Published a bounded, atomic initialization receipt outside the runtime tree
before candidate/cache writes. It binds the complete layout, exact version,
HOME and permission selections, and project/runtime/isolated-HOME directory
identities. Retries and absolute-path repair preserve those choices and
context, including custom layouts. Completed runtime authority takes
precedence; successful publication or owned uninstall retires valid intent.
Invalid, linked, transplanted, or stale receipts remain fail-closed.

Moved repair ownership/version checks before missing-HOME guidance and added
complete quoted command examples. Repeated permission flags cannot skip a
pending install, adoption is rechecked under lock after registry lookup, and
dry runs cannot retire recovery evidence. Receipt atomic-write debris stays
outside the runtime and remains ignored after uninstall.

## Validation and Limits

All 557 repository tests passed in 55.54 seconds, including real CLI
transactions with deterministic npm fixtures, first-install npm/validation
failures, timeout, SIGINT, SIGKILL, exact-version retry/repair, receipt-write
failure, custom layout recovery, context preservation, replacement and unsafe
receipt rejection, inspection, dry-run, and uninstall regressions. Two
offline startup tests used the actual Codex 0.154.0 native binary with
disposable HOME directories. Black, Ruff, compileall, diff checks, an explicit
24-file entropy scan, and sentinel/tripwire verification passed.

No client checkout or remote machine was accessed for reproduction or repair.
Old receipt-less debris still requires inspection and an explicit ownership
decision; this fix does not establish the external report's precise cause or
automatically adopt unknown data. The separately tracked same-UID pathname
check-to-use limitation is unchanged.
