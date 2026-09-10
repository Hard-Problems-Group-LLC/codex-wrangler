"""Helpers for explicit user-home targeting across install entry points."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping, Optional

CODEX_HOME_DIRNAME = ".local/codex-home"
LEGACY_CODEX_HOME_DIRNAME = ".codex-home"


def current_home() -> Path:
    """Return the current process home directory as a resolved path."""

    return Path.home().expanduser().resolve()


def default_user_bin_dir(user_home: Path) -> Path:
    """Return the managed user-local bin directory under one target home."""

    return user_home.expanduser().resolve() / ".local" / "bin"


def default_user_share_dir(user_home: Path) -> Path:
    """Return the managed user-local share directory under one target home."""

    return user_home.expanduser().resolve() / ".local" / "share"


def default_tool_venv(user_home: Path, tool_name: str) -> Path:
    """Return the default managed venv path for one tool under one home."""

    return default_user_share_dir(user_home) / tool_name / "venv"


def default_pyenv_root(user_home: Optional[Path] = None) -> Path:
    """Return the pyenv root for the selected target home."""

    if user_home is not None:
        return user_home.expanduser().resolve() / ".pyenv"

    configured = os.environ.get("PYENV_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return current_home() / ".pyenv"


def _resolve_without_staged_link_failure(path: Path) -> Path:
    """Resolve one path, preserving a lexical identity for loops or failures."""

    try:
        return path.expanduser().resolve()
    except (OSError, RuntimeError):
        return Path(os.path.abspath(str(path.expanduser())))


def repo_local_codex_home(repo_root: Path) -> Path:
    """Return the active canonical or exact staged repo-local Codex HOME."""

    resolved_root = repo_root.expanduser().resolve()
    canonical = resolved_root / CODEX_HOME_DIRNAME
    legacy = resolved_root / LEGACY_CODEX_HOME_DIRNAME
    if canonical.is_symlink():
        try:
            exact_staged_link = os.readlink(canonical) == CODEX_HOME_DIRNAME
        except OSError:
            exact_staged_link = False
        if exact_staged_link and legacy.is_dir() and not legacy.is_symlink():
            return legacy.resolve()
    return _resolve_without_staged_link_failure(canonical)


def is_repo_local_codex_home(user_home: Path, repo_root: Path) -> bool:
    """Return True when one home matches the repository's isolated Codex home."""

    resolved_home = _resolve_without_staged_link_failure(user_home)
    return resolved_home in {
        repo_local_codex_home(repo_root),
        _resolve_without_staged_link_failure(repo_root / LEGACY_CODEX_HOME_DIRNAME),
    }


def describe_user_home_source(
    repo_root: Path,
    user_home: Path,
    explicit_home: Optional[Path],
) -> str:
    """Describe why one target user home was selected."""

    if explicit_home is not None:
        return "explicit --user-home target"
    if is_repo_local_codex_home(user_home, repo_root):
        return "current repo-local Codex home"
    return "current process HOME"


def resolve_user_home(
    repo_root: Path,
    explicit_home: Optional[Path] = None,
    *,
    allow_isolated_home: bool = False,
) -> Path:
    """Return the intended home for user-scoped installer side effects."""

    if explicit_home is not None:
        return explicit_home.expanduser().resolve()

    user_home = current_home()
    if is_repo_local_codex_home(user_home, repo_root) and not allow_isolated_home:
        raise RuntimeError(
            "Current HOME resolves to the repo-local Codex home ({}). Rerun "
            "from a normal terminal, pass --user-home PATH to target a "
            "different user scope explicitly, or pass --allow-isolated-home "
            "to install into the isolated AI home on purpose.".format(user_home)
        )
    return user_home


def user_scope_subprocess_environment(
    user_home: Path,
    base_environment: Optional[Mapping[str, str]] = None,
) -> dict[str, str]:
    """Return a subprocess environment bounded to one selected user home.

    Explicit user-home selection must control cache and configuration discovery
    as well as output paths.  In particular, an installer launched from a
    project-local AI home must not let pip or another child process reuse that
    caller's XDG directories.
    """

    environment = dict(os.environ if base_environment is None else base_environment)
    for name in tuple(environment):
        if name.startswith("XDG_"):
            environment.pop(name)
    environment.pop("CODEX_HOME", None)
    environment.pop("CLAUDE_CONFIG_DIR", None)

    resolved_home = user_home.expanduser().resolve()
    cache_home = resolved_home / ".cache"
    environment.update(
        {
            "HOME": str(resolved_home),
            "XDG_CONFIG_HOME": str(resolved_home / ".config"),
            "XDG_CACHE_HOME": str(cache_home),
            "XDG_STATE_HOME": str(resolved_home / ".local" / "state"),
            "XDG_DATA_HOME": str(resolved_home / ".local" / "share"),
            "PIP_CACHE_DIR": str(cache_home / "pip"),
        }
    )
    return environment


__all__ = [
    "CODEX_HOME_DIRNAME",
    "LEGACY_CODEX_HOME_DIRNAME",
    "current_home",
    "default_pyenv_root",
    "default_tool_venv",
    "default_user_bin_dir",
    "default_user_share_dir",
    "describe_user_home_source",
    "is_repo_local_codex_home",
    "repo_local_codex_home",
    "resolve_user_home",
    "user_scope_subprocess_environment",
]
