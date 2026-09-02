# Truncated native payload was not detected

Status: Closed  
Date opened: 2026-09-02T01:42:12-07:00  
Date closed: 2026-09-02T02:19:09-07:00  
Owner: Codex  
Scope: managed Codex package verification, inspection, and self-test

## Summary

A previously successful managed npm installation later contained truncated
native Codex executables. Metadata, lockfile, package-version, and executable
bit checks remained internally consistent, so `--inspect` reported no issue.
Launching the local CLI then terminated with a segmentation fault.

The associated project-local context was independently backed up and found to
be structurally sound. This defect concerns validation of the managed runtime,
not preservation or parsing of session data.

## Evidence

- Both affected native executables were exact 8,388,608-byte prefixes of the
  authenticated upstream files.
- Their ELF headers declared file-backed regions and section tables far beyond
  the actual end of each file.
- The cached platform archive was also an exact truncated prefix and did not
  match its recorded subresource-integrity digest.
- A fresh package download matched the lockfile integrity value, and its native
  executables passed both structural inspection and independent digest checks.
- The historical npm log recorded a successful install, so the evidence is
  consistent with post-success storage damage during an interruption. The
  precise filesystem writeback mechanism is not established.

## Root cause

`codex-wrangler` ran a CLI version smoke test immediately after installation,
but it did not retain or later re-evaluate structural completeness of installed
native payloads. Inspection checked package and launcher metadata without
validating the executable format. npm package integrity authenticates fetched
archives; it does not continuously attest extracted files after installation.

## Resolution

- Validate standardized file-backed extents declared by supported ELF64,
  Mach-O64, fat Mach-O, and PE32+ payloads without fixed binary sizes or
  external inspection commands.
- Run structural validation before the post-install CLI smoke test.
- Surface invalid payloads as inspection issues.
- Prevent self-test from executing a payload after structural validation
  fails.
- Add synthetic complete/truncated fixtures and incident-shaped regressions.
- Add a generated-launcher health gate that refuses to forward the requested
  command when the local CLI version smoke fails or returns unexpected output,
  while accepting a valid version line surrounded by CLI diagnostics.
- Add a standalone absolute-path repair operation that proves target ownership,
  selects a single surviving exact version, performs bounded npm cleanup, and
  preserves context and history.

## Validation

- The repository quality gate passed Black, Ruff, compileall, entropy and
  tripwire checks, and all 221 pytest tests.
- Synthetic exact-boundary and truncated fixtures passed for ELF64, Mach-O64,
  both fat Mach-O table forms, and PE32+.
- The validator accepted both repaired native executables at their exact real
  file sizes and rejected incident-shaped truncated prefixes.
- A real standalone repair inferred five concordant exact-version records,
  reinstalled that version, passed inspection and self-test, and left the
  complete session-tree hash unchanged.
