# Casefold-Equivalent Managed Paths Could Alias Protected State

Status: Closed  
Date opened: 2026-09-10T04:55:00-07:00  
Date closed: 2026-09-10T05:04:01-07:00  
Owner: Codex  
Scope: managed runtime, HOME, generated outputs, lock, Git, and reserved paths

## Summary

Initially absent custom paths that differed only by case passed case-sensitive
lexical containment checks. A layout accepted on Linux could therefore alias a
runtime and protected HOME or generated output after transplant to a default
case-insensitive macOS or Windows filesystem.

## Root cause

Managed overlap detection relied on `Path.relative_to`, whose comparison follows
the current host rather than the most conservative supported-host semantics.
Absent paths offered no filesystem identity with which to detect the future
alias.

## Resolution

Overlap detection now compares absolute path components under Unicode case
folding, conservatively rejecting equality or containment aliases before any
path needs to exist. Parameterized regressions cover runtime/HOME equality,
generated-output equality, and reserved context aliases.

## Validation

All 91 configuration tests and the 113-test combined bootstrap/config suite
passed; the full repository gate passed all 481 tests with Black, Ruff,
compileall, entropy, and tripwire clean.
