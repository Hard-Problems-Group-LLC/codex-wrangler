"""Filesystem layout and state-discovery helpers."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Dict, Optional, Tuple

from .constants import (
    DEFAULT_HOME_DIR,
    DEFAULT_LOCAL_DIR,
    LEGACY_HOME_DIR,
    LEGACY_LOCAL_DIR,
    MAINTENANCE_LOCK_FILENAME,
    METADATA_FILENAME,
    SCHEMA_VERSION,
    SCRIPT_NAME,
)
from .models import CodexWranglerError, ExistingState, Layout
from .releases import is_exact_version

SAFE_MANAGED_PATH_COMPONENT_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


def validate_managed_relative_syntax(relative_path: Path, label: str) -> None:
    """Reject path syntax unsafe in generated shell and ignore boundaries."""

    for component in relative_path.parts:
        if not SAFE_MANAGED_PATH_COMPONENT_PATTERN.fullmatch(component):
            raise CodexWranglerError(
                "{} contains unsupported path syntax in component {!r}. "
                "Use only ASCII letters, digits, dot, underscore, and hyphen "
                "in managed path components.".format(label, component)
            )


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
    validate_managed_relative_syntax(normalized_relative, label)

    lexical_path = project_root / normalized_relative
    try:
        relative_parts = lexical_path.relative_to(project_root).parts
    except ValueError as exc:
        raise CodexWranglerError(
            "{} escapes the project root after normalization: {}".format(
                label, raw_relative_path
            )
        ) from exc
    cursor = project_root
    for index, part in enumerate(relative_parts):
        cursor /= part
        if not os.path.lexists(str(cursor)):
            continue
        if cursor.is_symlink():
            raise CodexWranglerError(
                "{} may not contain symbolic-link components: {}".format(
                    label,
                    cursor,
                )
            )
        if index < len(relative_parts) - 1 and not cursor.is_dir():
            raise CodexWranglerError(
                "{} parent is not a directory: {}".format(label, cursor)
            )

    resolved = lexical_path.resolve()
    try:
        resolved.relative_to(project_root)
    except ValueError as exc:
        raise CodexWranglerError(
            "{} escapes the project root after normalization: {}".format(
                label, raw_relative_path
            )
        ) from exc

    return str(normalized_relative), resolved


def resolve_default_migration_path(
    project_root: Path,
    raw_relative_path: str,
    label: str,
    canonical_relative: str,
    legacy_relative: str,
) -> Tuple[str, Path]:
    """Resolve a default path, tolerating only its exact staged migration link."""

    normalized_relative = Path(os.path.normpath(raw_relative_path))
    if str(normalized_relative) != canonical_relative:
        return resolve_relative_within_root(project_root, raw_relative_path, label)

    lexical_path = project_root / normalized_relative
    staged_link = False
    if lexical_path.is_symlink():
        try:
            staged_link = os.readlink(lexical_path) == canonical_relative
        except OSError:
            staged_link = False
    legacy_path = project_root / legacy_relative
    if not (staged_link and legacy_path.is_dir() and not legacy_path.is_symlink()):
        return resolve_relative_within_root(project_root, raw_relative_path, label)

    try:
        relative_parts = lexical_path.relative_to(project_root).parts
    except ValueError as exc:
        raise CodexWranglerError(
            "{} escapes the project root after normalization: {}".format(
                label,
                raw_relative_path,
            )
        ) from exc
    cursor = project_root
    for part in relative_parts[:-1]:
        cursor /= part
        if os.path.lexists(str(cursor)) and cursor.is_symlink():
            raise CodexWranglerError(
                "{} may not contain symbolic-link components: {}".format(
                    label,
                    cursor,
                )
            )
        if os.path.lexists(str(cursor)) and not cursor.is_dir():
            raise CodexWranglerError(
                "{} parent is not a directory: {}".format(label, cursor)
            )
    return str(normalized_relative), lexical_path


def managed_paths_overlap(left: Path, right: Path) -> bool:
    """Return whether either path contains the other, including case aliases."""

    # Managed paths may not yet exist, so host filesystem probes cannot reliably
    # establish case sensitivity. Compare component-wise casefolded paths as a
    # conservative second boundary: a layout accepted on Linux must not alias
    # context or generated outputs when transplanted to a default macOS or
    # Windows filesystem.
    left_parts = tuple(part.casefold() for part in left.parts)
    right_parts = tuple(part.casefold() for part in right.parts)
    shared_length = min(len(left_parts), len(right_parts))
    return left_parts[:shared_length] == right_parts[:shared_length]


def require_disjoint_managed_paths(paths: Tuple[Tuple[str, Path], ...]) -> None:
    """Reject layouts where one managed tree could consume another output."""

    for left_index, (left_label, left_path) in enumerate(paths):
        for right_label, right_path in paths[left_index + 1 :]:
            if managed_paths_overlap(left_path, right_path):
                raise CodexWranglerError(
                    "{} and {} must not overlap: {}, {}".format(
                        left_label,
                        right_label,
                        left_path,
                        right_path,
                    )
                )


def build_layout(
    project_root: Path,
    local_dir_raw: str,
    codex_home_raw: str,
    launcher_raw: str,
    readme_raw: str,
    *,
    allow_default_migration_staging: bool = False,
) -> Layout:
    """Resolve all managed paths into a validated layout structure."""

    if allow_default_migration_staging:
        local_dir_relative, local_dir = resolve_default_migration_path(
            project_root,
            local_dir_raw,
            "--local-dir",
            DEFAULT_LOCAL_DIR,
            LEGACY_LOCAL_DIR,
        )
        codex_home_relative, codex_home_dir = resolve_default_migration_path(
            project_root,
            codex_home_raw,
            "--codex-home-dir",
            DEFAULT_HOME_DIR,
            LEGACY_HOME_DIR,
        )
    else:
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
    gitignore_path = project_root / ".gitignore"
    maintenance_lock_path = project_root / MAINTENANCE_LOCK_FILENAME
    local_state_root = project_root / ".local"
    reserved_context_paths = (
        ("legacy .codex-home context", project_root / LEGACY_HOME_DIR),
        ("canonical Codex home", project_root / DEFAULT_HOME_DIR),
        ("reserved .codex context", project_root / ".codex"),
        ("reserved .agents context", project_root / ".agents"),
        ("repository .git data", project_root / ".git"),
    )
    require_disjoint_managed_paths(
        (
            ("--local-dir", local_dir),
            ("--codex-home-dir", codex_home_dir),
            ("--launcher", launcher_path),
            ("--readme-local", readme_path),
            ("repository .gitignore", gitignore_path),
            ("stable maintenance lock", maintenance_lock_path),
        )
    )
    if local_dir == local_state_root:
        raise CodexWranglerError(
            "--local-dir and the project-local state root must not overlap: "
            "{}".format(local_state_root)
        )
    if codex_home_dir == local_state_root:
        raise CodexWranglerError(
            "--codex-home-dir and the project-local state root must not "
            "overlap: {}".format(local_state_root)
        )

    # Slot rotation and uninstall remove the managed local tree wholesale.
    # The canonical runtime may safely occupy one child beneath `.local`, but
    # it must remain disjoint from every protected context/VCS tree.
    require_disjoint_managed_paths(
        (("--local-dir", local_dir), *reserved_context_paths)
    )
    for output_label, output_path in (
        ("--launcher", launcher_path),
        ("--readme-local", readme_path),
    ):
        for reserved_label, reserved_path in (
            ("reserved .local context", local_state_root),
            *reserved_context_paths,
        ):
            require_disjoint_managed_paths(
                (
                    (output_label, output_path),
                    (reserved_label, reserved_path),
                )
            )
    codex_home_reserved_paths = (
        *reserved_context_paths,
        ("legacy managed runtime", project_root / LEGACY_LOCAL_DIR),
        ("canonical managed runtime", project_root / DEFAULT_LOCAL_DIR),
    )
    for reserved_label, reserved_path in codex_home_reserved_paths:
        if reserved_path == codex_home_dir and reserved_label in (
            "legacy .codex-home context",
            "canonical Codex home",
        ):
            continue
        require_disjoint_managed_paths(
            (
                ("--codex-home-dir", codex_home_dir),
                (reserved_label, reserved_path),
            )
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
        gitignore_path=gitignore_path,
    )


def read_json_file(path: Path) -> Optional[Dict[str, Any]]:
    """Read one unlinked regular JSON object, or return None when absent."""

    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise CodexWranglerError(
            "Failed to inspect JSON path {}: {}".format(path, exc)
        ) from exc
    if not stat.S_ISREG(mode):
        raise CodexWranglerError(
            "Expected an unlinked regular JSON file at {}, but found another "
            "filesystem object.".format(path)
        )

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise CodexWranglerError(
                    "Expected an unlinked regular JSON file at {}, but the "
                    "opened object was not regular.".format(path)
                )
            with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
                descriptor = -1
                payload = json.load(handle)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    except CodexWranglerError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CodexWranglerError(
            "Failed to parse JSON in {}: {}".format(path, exc)
        ) from exc
    except OSError as exc:
        raise CodexWranglerError(
            "Failed to read JSON in {}: {}".format(path, exc)
        ) from exc
    if not isinstance(payload, dict):
        raise CodexWranglerError("Expected JSON object in {}".format(path))
    return payload


def metadata_looks_managed(
    payload: Optional[Dict[str, Any]],
    layout: Layout,
) -> bool:
    """Return whether root metadata belongs to this exact managed layout."""

    if not payload or payload.get("script_name") != SCRIPT_NAME:
        return False
    recorded_schema = payload.get("schema_version")
    if (
        not isinstance(recorded_schema, int)
        or isinstance(recorded_schema, bool)
        or recorded_schema not in (1, SCHEMA_VERSION)
    ):
        return False
    recorded_root = payload.get("project_root")
    if not isinstance(recorded_root, str) or not Path(recorded_root).is_absolute():
        return False
    try:
        canonical_recorded_root = Path(recorded_root).expanduser().resolve()
    except OSError:
        return False
    if canonical_recorded_root != layout.project_root:
        return False
    paths = payload.get("paths")
    if not isinstance(paths, dict):
        return False
    expected_path_sets = [
        {
            "local_dir": layout.local_dir_relative,
            "codex_home_dir": layout.codex_home_relative,
            "launcher": layout.launcher_relative,
            "readme_local": layout.readme_relative,
        }
    ]
    legacy_compatibility_path = layout.project_root / LEGACY_LOCAL_DIR
    legacy_home_compatibility_path = layout.project_root / LEGACY_HOME_DIR
    try:
        legacy_compatibility_target = (
            os.readlink(legacy_compatibility_path)
            if legacy_compatibility_path.is_symlink()
            else None
        )
        legacy_home_compatibility_target = (
            os.readlink(legacy_home_compatibility_path)
            if legacy_home_compatibility_path.is_symlink()
            else None
        )
    except OSError:
        legacy_compatibility_target = None
        legacy_home_compatibility_target = None
    legacy_runtime_alias_is_bound = (
        layout.local_dir_relative == DEFAULT_LOCAL_DIR
        and layout.codex_home_relative == DEFAULT_HOME_DIR
        and layout.local_dir.is_dir()
        and not layout.local_dir.is_symlink()
        and legacy_compatibility_target == DEFAULT_LOCAL_DIR
    )
    shared_home = payload.get("shared_home")
    legacy_home_alias_is_bound = (
        layout.codex_home_dir.is_dir()
        and not layout.codex_home_dir.is_symlink()
        and legacy_home_compatibility_target == DEFAULT_HOME_DIR
    )
    legacy_alias_is_bound = legacy_runtime_alias_is_bound and (
        shared_home is True or (shared_home is False and legacy_home_alias_is_bound)
    )
    if legacy_alias_is_bound:
        expected_path_sets.append(
            {
                "local_dir": LEGACY_LOCAL_DIR,
                "codex_home_dir": LEGACY_HOME_DIR,
                "launcher": layout.launcher_relative,
                "readme_local": layout.readme_relative,
            }
        )
    return any(
        all(paths.get(name) == value for name, value in expected_paths.items())
        for expected_paths in expected_path_sets
    )


def _select_consistent_boolean_authority(
    label: str,
    evidence: Tuple[Tuple[str, bool], ...],
) -> Optional[bool]:
    """Return one concordant recovery value or reject ambiguous authority."""

    values = {value for _, value in evidence}
    if len(values) > 1:
        details = ", ".join("{}={}".format(source, value) for source, value in evidence)
        raise CodexWranglerError(
            "Conflicting {} recovery authority was found with no validated "
            "active slot: {}. Refusing to guess.".format(label, details)
        )
    return next(iter(values)) if values else None


def read_existing_state(layout: Layout) -> ExistingState:
    """Read any previously managed state available at the target paths."""

    try:
        metadata = read_json_file(layout.metadata_path)
    except CodexWranglerError:
        metadata = None
    if not metadata_looks_managed(metadata, layout):
        metadata = None
    requested_codex_selector = None
    codex_channel = None
    pinned_codex_version = None
    available_versions: Dict[str, Optional[str]] = {}
    available_versions_updated_at = None
    reasonable_permissions_enabled = None
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
                if isinstance(raw_version, str) and is_exact_version(raw_version):
                    available_versions[raw_channel] = raw_version.strip()
                elif raw_version is None:
                    available_versions[raw_channel] = None
        raw_updated_at = metadata.get("available_versions_updated_at")
        if isinstance(raw_updated_at, str):
            available_versions_updated_at = raw_updated_at
        raw_reasonable_permissions = metadata.get("reasonable_permissions_enabled")
        if isinstance(raw_reasonable_permissions, bool):
            reasonable_permissions_enabled = raw_reasonable_permissions
        raw_shared_home = metadata.get("shared_home")
        if isinstance(raw_shared_home, bool):
            shared_home = raw_shared_home

    # The atomic pointer and completed slot record are authoritative after a
    # promotion. Root metadata is a recoverable compatibility projection and
    # may legitimately lag when power fails immediately after the commit.
    root_shared_home = shared_home
    root_reasonable_permissions = reasonable_permissions_enabled
    active_authority_selected = False
    try:
        from .slots import (
            layout_for_slot,
            observe_repair_runtime,
            read_slot_metadata,
        )

        active = observe_repair_runtime(layout)
    except CodexWranglerError:
        active = None
    if active is not None and active.slot_name is not None:
        active_layout = layout_for_slot(layout, active.slot_name)
        slot_metadata = read_slot_metadata(
            layout,
            active.slot_name,
            allow_pending_legacy_home=True,
        )
        if slot_metadata:
            active_authority_selected = True
            raw_selector = slot_metadata.get("codex_selector")
            raw_channel = slot_metadata.get("codex_channel")
            raw_version = slot_metadata.get("codex_version")
            if isinstance(raw_selector, str):
                requested_codex_selector = raw_selector
            if isinstance(raw_channel, str) or raw_channel is None:
                codex_channel = raw_channel
            if isinstance(raw_version, str):
                pinned_codex_version = raw_version
            shared_home = slot_metadata.get("shared_home")
            reasonable_permissions_enabled = slot_metadata.get(
                "reasonable_permissions_enabled"
            )
            raw_available_versions = slot_metadata.get("available_versions")
            if isinstance(raw_available_versions, dict):
                available_versions = {}
                for (
                    raw_channel,
                    raw_available_version,
                ) in raw_available_versions.items():
                    if not isinstance(raw_channel, str):
                        continue
                    if isinstance(raw_available_version, str):
                        available_versions[raw_channel] = raw_available_version.strip()
                    elif raw_available_version is None:
                        available_versions[raw_channel] = None
            raw_updated_at = slot_metadata.get("available_versions_updated_at")
            available_versions_updated_at = (
                raw_updated_at if isinstance(raw_updated_at, str) else None
            )
        if requested_codex_selector is None:
            try:
                requested_codex_selector = infer_requested_version_from_package_json(
                    active_layout.local_package_json_path
                )
            except CodexWranglerError:
                requested_codex_selector = None
        if pinned_codex_version is None:
            try:
                pinned_codex_version = infer_installed_version_from_lockfile(
                    active_layout.local_package_lock_path
                )
            except CodexWranglerError:
                pinned_codex_version = None

    if not active_authority_selected:
        shared_authority = []
        permissions_authority = []
        if root_shared_home is not None:
            shared_authority.append(("root metadata", root_shared_home))
        if root_reasonable_permissions is not None:
            permissions_authority.append(("root metadata", root_reasonable_permissions))
        for slot_name in ("a", "b"):
            slot_metadata = read_slot_metadata(
                layout,
                slot_name,
                allow_pending_legacy_home=True,
            )
            if slot_metadata is None:
                continue
            shared_authority.append(
                (
                    "completed slot {}".format(slot_name),
                    slot_metadata["shared_home"],
                )
            )
            permissions_authority.append(
                (
                    "completed slot {}".format(slot_name),
                    slot_metadata["reasonable_permissions_enabled"],
                )
            )
        shared_home = _select_consistent_boolean_authority(
            "HOME-mode",
            tuple(shared_authority),
        )
        reasonable_permissions_enabled = _select_consistent_boolean_authority(
            "reasonable-permissions",
            tuple(permissions_authority),
        )

    # Metadata is authoritative when present, but conservative fallback
    # inference keeps update/inspect useful for older managed installs.
    if requested_codex_selector is None:
        try:
            requested_codex_selector = infer_requested_version_from_package_json(
                layout.local_package_json_path
            )
        except CodexWranglerError:
            requested_codex_selector = None
    if pinned_codex_version is None:
        try:
            pinned_codex_version = infer_installed_version_from_lockfile(
                layout.local_package_lock_path
            )
        except CodexWranglerError:
            pinned_codex_version = None
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
        reasonable_permissions_enabled=reasonable_permissions_enabled,
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
