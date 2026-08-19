# Engineering Change Request: Pinned Pyenv and Transplant-Safe Bootstrap

## Summary

TheKnowledge's managed Python starter should treat an existing pyenv
controller and plugin checkout as user-owned, deliberately versioned tooling.
It should reuse those checkouts without an unattended `git pull`, and stage 2
should provision the configured project runtime without also installing the
minimum bootstrap interpreter.

## Incident

During recovery of a consuming project on a rebuilt Rocky Linux 10.2 host, the
newer workstation policy deliberately retained clean, verified pyenv controller
repositories at detached release tags and removed host-built interpreters from
the previous operating system. The policy selected CPython `3.14.6` for normal
development and retained Python 3.9 only as a source/bootstrap compatibility
floor.

The current managed starter conflicts with that policy in two ways:

- `ensure_pyenv_installed` and `ensure_pyenv_virtualenv_plugin` run
  `git pull --ff-only` whenever their existing checkouts are present; and
- stage 2 calls `ensure_pyenv_context` for both the bootstrap floor and the
  steady-state runtime, even though stage 1 has already proved that its current
  interpreter satisfies the bootstrap minimum.

A detached reviewed tag cannot be updated with the assumed branch pull. In an
AI workspace sandbox, the same update also fails because the user home is
correctly outside the repository write boundary.

## Newer Policy Source

The rebuilt host's maintained Python administration strategy is newer than the
submodule snapshot. It requires deliberate reviewed tag changes for pyenv and
its plugin, treats project environments as reproducible generated state, and
says a minimum-version interpreter should be installed only when local
compatibility testing needs it.

## Proposed Contract

- If the expected pyenv executable already exists, return it without running
  Git commands.
- If the expected plugin checkout already exists and is needed, reuse it
  without running Git commands.
- Clone a missing controller or plugin from the declared upstream, but do not
  silently change an existing checkout's branch, tag, remote, or worktree.
- In stage 2, validate the current interpreter against the configured
  bootstrap minimum, then provision only the steady-state runtime context.
- Keep minimum supported Python, bootstrap execution Python, and steady-state
  project runtime as separate documented concepts.
- Add tests for detached existing checkouts and for runtime-only stage-2
  provisioning.
- Preserve executable modes for compatibility entry points during managed
  starter refreshes.

## Non-Goals

- Choose one universal steady-state Python version for every consuming
  project.
- Update or repair arbitrary user-owned pyenv repositories.
- Remove minimum-version CI or explicit local compatibility testing.
- Broaden an AI sandbox's write access to the operator home.

## Why This Belongs To TheKnowledge

The conflicting helpers are managed starter code copied into consuming
projects. Fixing only one consumer would leave future clean-system and
transplant recoveries exposed to the same ownership violation and detached-HEAD
failure.
