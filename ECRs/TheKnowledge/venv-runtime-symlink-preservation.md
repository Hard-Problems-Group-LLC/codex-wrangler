# Engineering Change Request: Venv Runtime Symlink Preservation

## Summary

TheKnowledge's validation runtime resolver should convert relative executable
candidates to absolute paths without resolving their final symbolic links.
Resolving a normal `.venv/bin/python` symlink can collapse the candidate to the
bare pyenv interpreter and hide the virtual environment's installed tools.

## Incident

On a rebuilt Rocky Linux 10.2 host, the project `.venv` was correctly created
with CPython `3.14.6` and contained Black `26.3.1`. The required Black timeout
harness nevertheless reported that no runtime supplied the `black` module.

The resolver applied `Path.resolve()` to `.venv/bin/python`. A standard venv
uses a symlink there, so the result became the underlying
`~/.pyenv/versions/3.14.6/bin/python3.14` executable. That base interpreter is
intentionally sparse and does not contain project validation tools.

The consuming project also carried a local `tool_validation_profiles.py`
overlay, but its repo-root timeout wrapper inserted the submodule script
directory ahead of the local script directory. The shared helper therefore
loaded the stale submodule resolver instead of the project overlay.

## Requested Contract

- Convert relative executable candidates with an absolute-path operation that
  preserves symlink components, such as `os.path.abspath`.
- Do not use `Path.resolve()` for a venv command path when environment identity
  depends on that path.
- Add a regression fixture where `.venv/bin/python` points to a pyenv base
  interpreter and only the venv supplies Black.
- Define deterministic precedence for consuming-project helper overlays. A
  documented local overlay must load before the submodule default.
- Ensure serial or nested validation subprocesses retain that overlay or carry
  the selected venv through a supported absolute runtime override that
  preserves the venv path.
- Keep validation failures explicit about the candidate path that was probed
  and why it was rejected.

## Why This Belongs To TheKnowledge

The failing resolver and timeout harness are managed shared tooling. Every
consuming project using a conventional symlinked virtual environment can hit
the same false missing-tool diagnosis after a clean rebuild.
