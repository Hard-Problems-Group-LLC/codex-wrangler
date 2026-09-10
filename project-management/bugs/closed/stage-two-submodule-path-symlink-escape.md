# Stage-Two Submodule Path Symlink Escape

Status: Closed  
Date opened: 2026-09-10T04:54:00-07:00  
Date closed: 2026-09-10T05:04:01-07:00  
Owner: Codex  
Scope: stage-two recursive initialize-only submodule traversal

## Summary

A configured submodule path was checked only for lexical absolute and `..`
syntax. Git status, initialization, or nested recursion could therefore follow
a final or ancestor symlink into an external repository.

## Root cause

`Path.is_dir()` followed links, and containment was not rebound around each Git
operation. Preserving divergent initialized submodules had removed destructive
checkout reconciliation but had not made their filesystem path a strict trust
boundary.

## Resolution

Every existing component must now be a real directory and the resolved child
must remain beneath its current repository. Validation occurs before Git
status, immediately before initialization, and again before nested recursion.
Final- and ancestor-link fixtures prove the operation stops before any
submodule command or write can reach the external checkout, while `+` divergent
submodules remain preserved.

## Validation

The focused bootstrap/config suite passed 113 tests; the full repository gate
passed all 481 tests with Black, Ruff, compileall, entropy, and tripwire clean.
