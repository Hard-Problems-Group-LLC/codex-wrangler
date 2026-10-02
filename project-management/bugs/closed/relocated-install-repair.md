# Repair cannot recover records after a project move

Status: Closed
Opened: 2026-10-01T21:01:01-07:00
Resolved: 2026-10-01T21:36:09-07:00
Owner: Codex
Priority: High

## Evidence

Read-only inspection of an external retained checkout found canonical A/B
slots and managed root metadata naming their former absolute project root.
The active pointer selects slot B, both slots record Codex 0.154.0, and the
isolated HOME exists. Plain repair reports missing HOME authority because
root and slot validators reject the changed project-root binding. The
generated launcher applies the same binding and rejects the active record.

## Direction

The operator authorized utility improvements and test/repair runs in the
retained checkout. Add explicit previous-root recovery, safe automatic origin
discovery, and actionable read-only inspection. Preserve context and prior
installations. Contract: `docs/specifications/relocated_install_repair.md`.

## Resolution

Added bounded origin inspection and mutually exclusive automatic/explicit
relocation repair modifiers. Preserve exact active selection, recorded HOME
and permissions, validate origin again under lock, and rebuild through the
normal candidate transaction. Rebind surviving rollback records after
promotion and retain displaced slots and old candidate evidence. Moved intent
still requires unchanged directory identities and is rebound for retry.
Current-root custom path discovery requires completion or receipt authority;
root metadata alone does not redirect ordinary repair.

## Verification

All 648 tests passed in 79.82 seconds, including native Codex smoke tests,
renamed/copied CLI images, interrupted publication, legacy migration, custom
and shared HOME layouts, and displaced-slot retention. Black, Ruff,
compileall, explicit candidate entropy scans, sentinel/tripwire, and diff
checks passed. The separately tracked parent entropy-wrapper scope defect
remains; parent changes were scanned directly.

The operator-authorized external retained checkout was repaired successfully
at Codex 0.154.0 without changing HOME or permission mode. Inspection reports
no issues or warnings, ordinary repair dry-run succeeds, and the generated
launcher reports the expected version. A metadata-only preservation snapshot
covering 118,606 accessible context entries and 15 opaque directory boundaries
matched after repair. Both prior runtime identities and old candidates were
retained. Native launcher verification subsequently refreshed temporary
PATH-alias metadata; no context or credential contents were inspected.
