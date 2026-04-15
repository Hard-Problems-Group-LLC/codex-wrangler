"""Filesystem layout and state-discovery helpers."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .constants import METADATA_FILENAME
from .models import CodexWranglerError, ExistingState, Layout


def resolve_project_root(raw_path: str) -> Path:
    """Resolve and validate the target project directory."""

    project_root = Path(raw_path).expanduser().resolve()
    if not project_root.exists():
        raise CodexWranglerError("Project root does not exist: {}".format(project_root))
    if not project_root.is_dir():
        raise CodexWranglerError(
            "Project root is not a directory: {}".format(project_root)
        )
    return project_root


def resolve_relative_within_root(
    project_root: Path,
    raw_relative_path: str,
    label: str,
) -> Tuple[str, Path]:
    """Resolve a relative path and prove it stays beneath project_root."""

    relative_path = Path(raw_relative_path)
    if relative_path.is_absolute():
        raise CodexWranglerError(
            "{} must be relative to the project root, not absolute: {}".format(
                label, raw_relative_path
            )
        )

    normalized_relative = Path(os.path.normpath(str(relative_path)))
    if str(normalized_relative) in ("", "."):
        raise CodexWranglerError(
            "{} may not resolve to the project root itself.".format(label)
        )

    resolved = (project_root / normalized_relative).resolve()
    try:
        resolved.relative_to(project_root)
    except ValueError as exc:
        raise CodexWranglerError(
            "{} escapes the project root after normalization: {}".format(
                label, raw_relative_path
            )
        ) from exc

    return str(normalized_relative), resolved


def build_layout(
    project_root: Path,
    local_dir_raw: str,
    codex_home_raw: str,
    launcher_raw: str,
    readme_raw: str,
) -> Layout:
    """Resolve all managed paths into a validated layout structure."""

    local_dir_relative, local_dir = resolve_relative_within_root(
        project_root, local_dir_raw, "--local-dir"
    )
    codex_home_relative, codex_home_dir = resolve_relative_within_root(
        project_root, codex_home_raw, "--codex-home-dir"
    )
    launcher_relative, launcher_path = resolve_relative_within_root(
        project_root, launcher_raw, "--launcher"
    )
    readme_relative, readme_path = resolve_relative_within_root(
        project_root, readme_raw, "--readme-local"
    )

    return Layout(
        project_root=project_root,
        local_dir_relative=local_dir_relative,
        codex_home_relative=codex_home_relative,
        launcher_relative=launcher_relative,
        readme_relative=readme_relative,
        local_dir=local_dir,
        codex_home_dir=codex_home_dir,
        launcher_path=launcher_path,
        readme_path=readme_path,
        local_package_json_path=local_dir / "package.json",
        local_package_lock_path=local_dir / "package-lock.json",
        local_node_modules_dir=local_dir / "node_modules",
        metadata_path=local_dir / METADATA_FILENAME,
        gitignore_path=project_root / ".gitignore",
    )


def read_json_file(path: Path) -> Optional[Dict[str, Any]]:
    """Read a JSON object file or return None when the file is absent."""

    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise CodexWranglerError("Expected JSON object in {}".format(path))
    return payload


def read_existing_state(layout: Layout) -> ExistingState:
    """Read any previously managed state available at the target paths."""

    metadata = read_json_file(layout.metadata_path)
    requested_codex_selector = None
    codex_channel = None
    pinned_codex_version = None
    available_versions: Dict[str, Optional[str]] = {}
    available_versions_updated_at = None
    shared_home = None

    if metadata:
        requested_codex_selector = metadata.get("codex_selector")
        codex_channel = metadata.get("codex_channel")
        pinned_codex_version = metadata.get("codex_version")
        raw_available_versions = metadata.get("available_versions")
        if isinstance(raw_available_versions, dict):
            for raw_channel, raw_version in raw_available_versions.items():
                if not isinstance(raw_channel, str):
                    continue
                if isinstance(raw_version, str):
                    available_versions[raw_channel] = raw_version
                elif raw_version is None:
                    available_versions[raw_channel] = None
        raw_updated_at = metadata.get("available_versions_updated_at")
        if isinstance(raw_updated_at, str):
            available_versions_updated_at = raw_updated_at
        raw_shared_home = metadata.get("shared_home")
        if isinstance(raw_shared_home, bool):
            shared_home = raw_shared_home

    # Metadata is authoritative when present, but conservative fallback
    # inference keeps update/inspect useful for older managed installs.
    if requested_codex_selector is None:
        requested_codex_selector = infer_requested_version_from_package_json(
            layout.local_package_json_path
        )
    if pinned_codex_version is None:
        pinned_codex_version = infer_installed_version_from_lockfile(
            layout.local_package_lock_path
        )
    if pinned_codex_version is None:
        pinned_codex_version = requested_codex_selector

    return ExistingState(
        metadata=metadata,
        requested_codex_selector=(
            requested_codex_selector
            if isinstance(requested_codex_selector, str)
            else None
        ),
        codex_channel=codex_channel if isinstance(codex_channel, str) else None,
        pinned_codex_version=(
            pinned_codex_version if isinstance(pinned_codex_version, str) else None
        ),
        available_versions=available_versions,
        available_versions_updated_at=available_versions_updated_at,
        shared_home=shared_home,
    )


def infer_requested_version_from_package_json(
    package_json_path: Path,
) -> Optional[str]:
    """Return the pinned Codex version from the managed local package manifest."""

    payload = read_json_file(package_json_path)
    if not payload:
        return None
    dev_dependencies = payload.get("devDependencies")
    if not isinstance(dev_dependencies, dict):
        return None
    version = dev_dependencies.get("@openai/codex")
    return version if isinstance(version, str) else None


def infer_installed_version_from_lockfile(lockfile_path: Path) -> Optional[str]:
    """Return the installed Codex version from package-lock.json when present."""

    payload = read_json_file(lockfile_path)
    if not payload:
        return None

    packages = payload.get("packages")
    if not isinstance(packages, dict):
        return None

    codex_entry = packages.get("node_modules/@openai/codex")
    if not isinstance(codex_entry, dict):
        return None
    version = codex_entry.get("version")
    return version if isinstance(version, str) else None
