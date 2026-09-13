"""Durable, narrowly bound authority for an unfinished first installation."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
from typing import Any, Optional

from .constants import INITIAL_INSTALL_FILENAME, SCRIPT_NAME
from .filesystem import atomic_write_text, fsync_directory
from .models import CodexWranglerError, Config, Layout
from .releases import is_exact_version


def initial_install_path(layout: Layout) -> Path:
    """Keep intent outside the runtime tree, including atomic-write debris."""

    return layout.project_root / INITIAL_INSTALL_FILENAME


def directory_identity(project_root: Path, directory: Path) -> list[int]:
    """Identify a real contained directory without following intermediate links."""

    cursor = project_root
    parts = directory.relative_to(project_root).parts
    for part in (None, *parts):
        if part is not None:
            cursor /= part
        info = cursor.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise CodexWranglerError(
                "Initialization requires real directory components: {}".format(cursor)
            )
    return [info.st_dev, info.st_ino]


def layout_paths(layout: Layout) -> dict[str, str]:
    """Return the complete managed path binding used by initialization intent."""

    return {
        "local_dir": layout.local_dir_relative,
        "codex_home_dir": layout.codex_home_relative,
        "launcher": layout.launcher_relative,
        "readme_local": layout.readme_relative,
    }


def read_initial_install(
    layout: Layout, *, discover_layout: bool = False
) -> Optional[dict[str, Any]]:
    """Read bounded, non-following intent; reject invalid or transplanted proof."""

    path = initial_install_path(layout)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    try:
        if path.is_symlink():
            raise ValueError("receipt is a symbolic link")
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError:
            return None
        with os.fdopen(descriptor, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("receipt is not a single-link regular file")
            raw = handle.read(16385)
            if len(raw) > 16384:
                raise ValueError("receipt exceeds its size bound")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("receipt is not an object")
        if discover_layout:
            from .layout import build_layout

            paths = payload.get("paths")
            names = ("local_dir", "codex_home_dir", "launcher", "readme_local")
            if not isinstance(paths, dict) or any(
                not isinstance(paths.get(name), str) for name in names
            ):
                raise ValueError("receipt layout is incomplete")
            layout = build_layout(
                layout.project_root,
                *(paths[name] for name in names),
            )
        if (
            payload.get("kind") != SCRIPT_NAME + "-initial-install"
            or type(payload.get("schema_version")) is not int
            or payload["schema_version"] != 1
            or payload.get("project_root") != str(layout.project_root)
            or payload.get("paths") != layout_paths(layout)
            or type(payload.get("shared_home")) is not bool
            or type(payload.get("reasonable_permissions_enabled")) is not bool
            or not isinstance(payload.get("codex_version"), str)
            or not is_exact_version(payload["codex_version"])
        ):
            raise ValueError("receipt fields do not bind this project and layout")
        expected = {
            "project": directory_identity(layout.project_root, layout.project_root),
            "runtime": directory_identity(layout.project_root, layout.local_dir),
            "home": (
                None
                if payload["shared_home"]
                else directory_identity(layout.project_root, layout.codex_home_dir)
            ),
        }
        identities = payload.get("identities")
        if not isinstance(identities, dict) or identities.keys() != expected.keys():
            raise ValueError("receipt directory identities are incomplete")
        for label, identity in expected.items():
            recorded = identities[label]
            if recorded != identity or (
                identity is not None
                and (
                    not isinstance(recorded, list)
                    or any(type(value) is not int for value in recorded)
                )
            ):
                raise ValueError("{} directory identity changed".format(label))
        return payload
    except (OSError, ValueError, RecursionError, CodexWranglerError) as exc:
        raise CodexWranglerError(
            "Invalid or stale first-install receipt {}: {}. Preserve the "
            "receipt and target directories; refusing to infer ownership.".format(
                path, exc
            )
        ) from exc


def require_initial_install_selection(config: Config, receipt: dict[str, Any]) -> None:
    """Do not turn retry or repair into an implicit version or HOME transition."""

    for name in ("codex_version", "shared_home", "reasonable_permissions_enabled"):
        if getattr(config, name) != receipt[name]:
            raise CodexWranglerError(
                "Unfinished first install records {}={!r}; finish that install "
                "before changing its selection.".format(name, receipt[name])
            )


def write_initial_install(config: Config) -> None:
    """Publish intent after empty-root preflight and before any candidate writes.

    The caller holds MaintenanceLock and has already published ignore coverage
    and prepared the isolated HOME. No existing context is read or populated.
    """

    existing = read_initial_install(config.layout)
    if existing is not None:
        require_initial_install_selection(config, existing)
        return
    runtime = config.layout.local_dir
    runtime.mkdir(parents=True, exist_ok=True)
    directory_identity(config.project_root, runtime)
    if any(runtime.iterdir()):
        raise CodexWranglerError(
            "Cannot create first-install authority over nonempty runtime: {}".format(
                runtime
            )
        )
    if not is_exact_version(config.codex_version):
        raise CodexWranglerError("First-install receipt requires an exact version.")
    identities = {
        "project": directory_identity(config.project_root, config.project_root),
        "runtime": directory_identity(config.project_root, runtime),
        "home": (
            None
            if config.shared_home
            else directory_identity(config.project_root, config.layout.codex_home_dir)
        ),
    }
    # The sidecar must not survive a crash without its bound directory entries.
    directories = [runtime]
    if not config.shared_home:
        directories.append(config.layout.codex_home_dir)
    for directory in directories:
        cursor = directory
        while True:
            fsync_directory(cursor)
            if cursor == config.project_root:
                break
            cursor = cursor.parent
    payload = {
        "kind": SCRIPT_NAME + "-initial-install",
        "schema_version": 1,
        "project_root": str(config.project_root),
        "paths": layout_paths(config.layout),
        "identities": identities,
        "codex_version": config.codex_version,
        "shared_home": config.shared_home,
        "reasonable_permissions_enabled": config.reasonable_permissions_enabled,
    }
    atomic_write_text(
        initial_install_path(config.layout),
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
    )


def clear_initial_install(layout: Layout) -> None:
    """Retire still-bound intent only after completion or before owned uninstall."""

    if read_initial_install(layout) is not None:
        try:
            initial_install_path(layout).unlink()
            fsync_directory(layout.project_root)
        except OSError as exc:
            raise CodexWranglerError(
                "Could not durably retire first-install receipt {}: {}".format(
                    initial_install_path(layout), exc
                )
            ) from exc
