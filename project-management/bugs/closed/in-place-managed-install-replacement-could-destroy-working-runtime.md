# In-place managed install replacement could destroy the working runtime

Status: Closed  
Date opened: 2026-09-09T23:56:26-07:00  
Date closed: 2026-09-10T00:12:09-07:00  
Owner: Codex  
Scope: managed Codex installation, upgrade, repair, and channel switching

## Summary

Package maintenance wrote the managed package manifest and ran npm directly
against the published prefix. A timeout, interruption, terminal failure, or
invalid downloaded payload could therefore leave the previously working local
CLI missing or incomplete.

## Root cause

The live package tree served as both installation workspace and published
runtime. There was no independent candidate, atomic activation point, retained
rollback slot, or stable cross-operation maintenance lock. Some predictable
support-file conflicts could also remain undiscovered until after package
work.

## Resolution

- Prepare every package change in a transaction-unique candidate and validate
  exact package, platform, native-payload, and CLI version evidence there.
- Reversibly rename the candidate into inactive fixed slot `a` or `b`, then
  atomically switch one strict pointer while retaining the former active slot.
- Treat the pointer and project/layout-bound completion record as durable
  authority; explicitly reconcile root projections after the commit point.
- Preflight protected context roots and every managed projection before npm
  installation or slot mutation.
- Serialize maintenance through a stable project-root lock, emit bounded
  progress, and terminate complete subprocess groups on timeout, interruption,
  `SIGHUP`, or `SIGTERM`.
- Recover damaged installs only from concordant exact-version evidence, without
  deleting project-local context, authentication, sessions, or history.

## Validation

Black passed across 54 serialized files. Ruff, compileall, entropy scanning
(292 files with zero findings), and entropy-tripwire verification passed. The
full timeout-wrapped pytest suite passed with 330 tests, including real
leader/grandchild process-group termination and adversarial pointer, repair,
filesystem, and post-commit failures.
