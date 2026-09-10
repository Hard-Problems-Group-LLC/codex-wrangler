"""Atomic A/B npm-prefix selection for managed Codex installations."""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
import errno
import json
import os
from pathlib import Path
import stat
import time
from typing import Any, BinaryIO, Dict, Optional
import uuid

from .constants import (
    ACTIVE_SLOT_FILE_NAME,
    ACTIVE_SYMLINK_NAME,
    CODEX_CHANNELS,
    DEFAULT_HOME_DIR,
    DEFAULT_LOCAL_DIR,
    LEGACY_HOME_DIR,
    LEGACY_LOCAL_DIR,
    MAINTENANCE_LOCK_FILENAME,
    SCRIPT_NAME,
    SLOT_METADATA_FILENAME,
    SLOT_NAMES,
    SLOTS_DIRECTORY_NAME,
)
from .filesystem import fsync_directory, write_metadata
from .models import CodexWranglerError, Config, Layout
from .releases import infer_codex_channel, is_exact_version
from .runtime import COMMAND_HEARTBEAT_SECONDS, eprint, utc_now_iso


@dataclass(frozen=True)
class ActiveRuntime:
    """One resolved runtime selected by a managed pointer or legacy layout."""

    kind: str
    prefix: Optional[Path]
    slot_name: Optional[str] = None
    pointer_kind: Optional[str] = None


@dataclass(frozen=True)
class SlotSwap:
    """One reversible candidate-to-fixed-slot rename transaction."""

    layout: Layout
    slot_name: str
    candidate_prefix: Path
    retired_prefix: Optional[Path]


class PointerCommitDurabilityError(CodexWranglerError):
    """Report a completed pointer swap whose directory flush then failed."""


class PointerCommittedInterrupt(KeyboardInterrupt):
    """Report interruption after the active pointer already changed."""


def slots_directory(layout: Layout) -> Path:
    """Return the fixed container for managed A/B npm prefixes."""

    return layout.local_dir / SLOTS_DIRECTORY_NAME


def slot_prefix(layout: Layout, slot_name: str) -> Path:
    """Return one fixed slot prefix after validating its name."""

    if slot_name not in SLOT_NAMES:
        raise CodexWranglerError("Unknown managed install slot: {!r}".format(slot_name))
    return slots_directory(layout) / slot_name


def slot_metadata_path(layout: Layout, slot_name: str) -> Path:
    """Return the completion-record path for one fixed slot."""

    return slot_prefix(layout, slot_name) / SLOT_METADATA_FILENAME


def _lexists(path: Path) -> bool:
    """Return whether a path entry exists, including a dangling symlink."""

    return os.path.lexists(str(path))


def _readlink_matches(path: Path, expected_target: str) -> bool:
    """Return whether one path is the exact expected relative link."""

    if not path.is_symlink():
        return False
    try:
        return os.readlink(path) == expected_target
    except OSError:
        return False


def validate_managed_root(layout: Layout) -> None:
    """Prove the local install root is contained and has no linked component."""

    try:
        project_root = layout.project_root.resolve(strict=True)
    except OSError as exc:
        raise CodexWranglerError(
            "Failed to resolve the project root before managed writes: {}".format(exc)
        ) from exc
    if not project_root.is_dir():
        raise CodexWranglerError(
            "Project root is not a directory: {}".format(project_root)
        )

    relative = Path(layout.local_dir_relative)
    if relative.is_absolute():
        raise CodexWranglerError(
            "Managed local directory must be project-relative: {}".format(relative)
        )
    lexical_local = Path(
        os.path.abspath(os.path.join(str(project_root), str(relative)))
    )
    try:
        local_parts = lexical_local.relative_to(project_root).parts
    except ValueError as exc:
        raise CodexWranglerError(
            "Managed local install escapes the project root: {}".format(lexical_local)
        ) from exc
    if not local_parts:
        raise CodexWranglerError(
            "Managed local install may not be the project root itself: {}".format(
                lexical_local
            )
        )

    configured_local = Path(os.path.abspath(str(layout.local_dir)))
    if configured_local != lexical_local:
        raise CodexWranglerError(
            "Managed local install layout is inconsistent: configured {}, expected "
            "{}".format(configured_local, lexical_local)
        )

    cursor = project_root
    for part in local_parts:
        cursor /= part
        if not _lexists(cursor):
            continue
        if cursor.is_symlink():
            raise CodexWranglerError(
                "Managed local install root or ancestor may not be a symbolic "
                "link: {}".format(cursor)
            )
        if cursor != lexical_local and not cursor.is_dir():
            raise CodexWranglerError(
                "Managed local install ancestor is not a directory: {}".format(cursor)
            )

    if _lexists(lexical_local) and not lexical_local.is_dir():
        raise CodexWranglerError(
            "Managed local install root is not a directory: {}".format(lexical_local)
        )
    try:
        lexical_local.resolve(strict=False).relative_to(project_root)
    except ValueError as exc:
        raise CodexWranglerError(
            "Managed local install escapes the canonical project root: {}".format(
                lexical_local
            )
        ) from exc


def validate_slot_container(layout: Layout) -> None:
    """Reject symbolic or escaped slot containers before candidate writes."""

    validate_managed_root(layout)
    container = slots_directory(layout)
    if container.is_symlink():
        raise CodexWranglerError(
            "Managed slots directory may not be a symbolic link: {}".format(container)
        )
    if _lexists(container) and not container.is_dir():
        raise CodexWranglerError(
            "Managed slots path must be a directory: {}".format(container)
        )
    try:
        container.resolve(strict=False).relative_to(layout.local_dir)
    except ValueError as exc:
        raise CodexWranglerError(
            "Managed slots directory escapes the local install: {}".format(container)
        ) from exc
    for slot_name in SLOT_NAMES:
        prefix = slot_prefix(layout, slot_name)
        if prefix.is_symlink():
            raise CodexWranglerError(
                "Managed install slot may not be a symbolic link: {}".format(prefix)
            )
        if _lexists(prefix) and not prefix.is_dir():
            raise CodexWranglerError(
                "Managed install slot must be a directory: {}".format(prefix)
            )


def require_completed_slot(layout: Layout, slot_name: str) -> Path:
    """Return one real fixed slot only when its completion record is valid."""

    prefix = slot_prefix(layout, slot_name)
    if prefix.is_symlink() or not prefix.is_dir():
        raise CodexWranglerError(
            "Managed active pointer selects a missing or invalid slot directory: "
            "{}".format(prefix)
        )
    if read_slot_metadata(layout, slot_name) is None:
        raise CodexWranglerError(
            "Managed active pointer selects an incomplete or unverified slot: "
            "{}".format(prefix)
        )
    return prefix


def _read_pointer_file(path: Path) -> str:
    """Read one strict fallback pointer without accepting extra content."""

    if path.is_symlink() or not path.is_file():
        raise CodexWranglerError(
            "Managed active-slot pointer must be a regular file: {}".format(path)
        )
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise CodexWranglerError(
            "Failed to read managed active-slot pointer {}: {}".format(path, exc)
        ) from exc
    for slot_name in SLOT_NAMES:
        if content == (slot_name + "\n").encode("ascii"):
            return slot_name
    raise CodexWranglerError(
        "Managed active-slot pointer must contain exactly `a` or `b` and one "
        "newline: {}".format(path)
    )


def _read_pointer_symlink(path: Path) -> str:
    """Read one strict relative POSIX active-slot symlink."""

    if not path.is_symlink():
        raise CodexWranglerError(
            "Managed active pointer must be a symbolic link: {}".format(path)
        )
    try:
        target = os.readlink(path)
    except OSError as exc:
        raise CodexWranglerError(
            "Failed to read managed active pointer {}: {}".format(path, exc)
        ) from exc
    for slot_name in SLOT_NAMES:
        if target == "{}/{}".format(SLOTS_DIRECTORY_NAME, slot_name):
            return slot_name
    raise CodexWranglerError(
        "Managed active pointer target must be exactly `slots/a` or `slots/b`: "
        "{} -> {}".format(path, target)
    )


def legacy_codex_bin_path(layout: Layout) -> Path:
    """Return the platform-specific root-layout Codex npm shim path."""

    binary_name = "codex.cmd" if os.name == "nt" else "codex"
    return layout.local_node_modules_dir / ".bin" / binary_name


def legacy_runtime_is_usable(layout: Layout) -> bool:
    """Return whether a contained executable legacy npm shim survives."""

    prefix = layout.local_dir
    shim = legacy_codex_bin_path(layout)
    if prefix.is_symlink() or not prefix.is_dir() or not _lexists(shim):
        return False
    try:
        relative_parts = shim.relative_to(prefix).parts
    except ValueError:
        return False
    cursor = prefix
    for index, part in enumerate(relative_parts):
        cursor /= part
        if cursor.is_symlink() and index != len(relative_parts) - 1:
            return False
    try:
        resolved = shim.resolve(strict=True)
        resolved.relative_to(prefix.resolve(strict=True))
    except (OSError, ValueError):
        return False
    return resolved.is_file() and (os.name == "nt" or os.access(shim, os.X_OK))


def observe_active_runtime(layout: Layout) -> ActiveRuntime:
    """Resolve pointer syntax without requiring a completed selected slot."""

    validate_slot_container(layout)
    symlink_path = layout.local_dir / ACTIVE_SYMLINK_NAME
    file_path = layout.local_dir / ACTIVE_SLOT_FILE_NAME
    symlink_present = _lexists(symlink_path)
    file_present = _lexists(file_path)
    if symlink_present and file_present:
        raise CodexWranglerError(
            "Both managed active pointer forms exist; refusing ambiguous state: "
            "{}, {}".format(symlink_path, file_path)
        )
    if symlink_present:
        slot_name = _read_pointer_symlink(symlink_path)
        return ActiveRuntime(
            "slot",
            slot_prefix(layout, slot_name),
            slot_name,
            "symlink",
        )
    if file_present:
        slot_name = _read_pointer_file(file_path)
        return ActiveRuntime(
            "slot",
            slot_prefix(layout, slot_name),
            slot_name,
            "file",
        )
    if legacy_runtime_is_usable(layout):
        return ActiveRuntime("legacy", layout.local_dir)
    return ActiveRuntime("none", None)


def observe_repair_runtime(layout: Layout) -> ActiveRuntime:
    """Resolve pointer form for repair while tolerating corrupt content."""

    validate_slot_container(layout)
    symlink_path = layout.local_dir / ACTIVE_SYMLINK_NAME
    file_path = layout.local_dir / ACTIVE_SLOT_FILE_NAME
    symlink_present = _lexists(symlink_path)
    file_present = _lexists(file_path)
    if symlink_present and file_present:
        raise CodexWranglerError(
            "Both managed active pointer forms exist; repair cannot choose one "
            "authoritative pointer safely: {}, {}".format(symlink_path, file_path)
        )
    if symlink_present:
        try:
            slot_name = _read_pointer_symlink(symlink_path)
        except CodexWranglerError:
            return ActiveRuntime("corrupt", None, pointer_kind="symlink")
        return ActiveRuntime(
            "slot", slot_prefix(layout, slot_name), slot_name, "symlink"
        )
    if file_present:
        try:
            slot_name = _read_pointer_file(file_path)
        except CodexWranglerError:
            return ActiveRuntime("corrupt", None, pointer_kind="file")
        return ActiveRuntime("slot", slot_prefix(layout, slot_name), slot_name, "file")
    if legacy_runtime_is_usable(layout):
        return ActiveRuntime("legacy", layout.local_dir)
    return ActiveRuntime("none", None)


def discover_active_runtime(layout: Layout) -> ActiveRuntime:
    """Resolve only a verified A/B pointer or the legacy root prefix."""

    active = observe_active_runtime(layout)
    if active.slot_name is None:
        if active.kind == "none":
            completed_slots = [
                slot_name
                for slot_name in SLOT_NAMES
                if read_slot_metadata(layout, slot_name) is not None
            ]
            if completed_slots:
                raise CodexWranglerError(
                    "Managed slots {} are complete, but no active pointer or usable "
                    "legacy runtime selects one. Refusing to guess; inspect the "
                    "target and use standalone --repair when its exact-version "
                    "evidence is unambiguous.".format(", ".join(completed_slots))
                )
        return active
    return ActiveRuntime(
        active.kind,
        require_completed_slot(layout, active.slot_name),
        active.slot_name,
        active.pointer_kind,
    )


def inactive_slot_name(active: ActiveRuntime) -> str:
    """Return the only slot safe to prepare for the current active state."""

    if active.slot_name == "a":
        return "b"
    return "a"


def layout_for_slot(layout: Layout, slot_name: str) -> Layout:
    """Return a layout whose npm-prefix paths address one candidate slot."""

    prefix = slot_prefix(layout, slot_name)
    relative = "{}/{}/{}".format(
        layout.local_dir_relative.rstrip("/"),
        SLOTS_DIRECTORY_NAME,
        slot_name,
    )
    return replace(
        layout,
        local_dir_relative=relative,
        local_dir=prefix,
        local_package_json_path=prefix / "package.json",
        local_package_lock_path=prefix / "package-lock.json",
        local_node_modules_dir=prefix / "node_modules",
        metadata_path=prefix / SLOT_METADATA_FILENAME,
    )


def config_for_unique_candidate(config: Config) -> Config:
    """Return a configuration for one transaction-unique candidate prefix."""

    candidate_name = ".candidate-{}".format(uuid.uuid4().hex)
    prefix = config.layout.local_dir / candidate_name
    relative = "{}/{}".format(
        config.layout.local_dir_relative.rstrip("/"),
        candidate_name,
    )
    candidate_layout = replace(
        config.layout,
        local_dir_relative=relative,
        local_dir=prefix,
        local_package_json_path=prefix / "package.json",
        local_package_lock_path=prefix / "package-lock.json",
        local_node_modules_dir=prefix / "node_modules",
        metadata_path=prefix / SLOT_METADATA_FILENAME,
    )
    return replace(config, layout=candidate_layout)


def config_for_slot(config: Config, slot_name: str) -> Config:
    """Return a configuration whose npm operations target one fixed slot."""

    return replace(config, layout=layout_for_slot(config.layout, slot_name))


def build_slot_metadata(config: Config, slot_name: str) -> Dict[str, object]:
    """Build the durable completion record for one validated candidate."""

    return {
        "schema_version": 1,
        "script_name": SCRIPT_NAME,
        "state": "complete",
        "generated_at": utc_now_iso(),
        "project_root": str(config.project_root),
        "local_dir": config.layout.local_dir_relative,
        "paths": {
            "local_dir": config.layout.local_dir_relative,
            "codex_home_dir": config.layout.codex_home_relative,
            "launcher": config.layout.launcher_relative,
            "readme_local": config.layout.readme_relative,
        },
        "slot": slot_name,
        "codex_selector": config.codex_selector,
        "codex_channel": config.codex_channel,
        "codex_version": config.codex_version,
        "version_source": config.version_source,
        "shared_home": config.shared_home,
        "reasonable_permissions_enabled": config.reasonable_permissions_enabled,
        "available_versions": dict(config.available_versions),
        "available_versions_updated_at": config.available_versions_updated_at,
    }


def regular_contained_file(prefix: Path, path: Path) -> bool:
    """Return whether one file is regular, unlinked, and prefix-contained."""

    if prefix.is_symlink() or not prefix.is_dir():
        return False
    try:
        relative_parts = path.relative_to(prefix).parts
    except ValueError:
        return False
    cursor = prefix
    if cursor.is_symlink():
        return False
    for part in relative_parts:
        cursor /= part
        if cursor.is_symlink():
            return False
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return False
        path.resolve(strict=True).relative_to(prefix.resolve(strict=True))
    except (OSError, ValueError):
        return False
    return True


def _slot_record_paths_match(
    payload: Dict[str, Any],
    layout: Layout,
    *,
    legacy_alias: bool,
) -> bool:
    """Validate a complete path tuple or one narrowly historical omission."""

    direct_legacy_layout = (
        layout.local_dir_relative == LEGACY_LOCAL_DIR
        and layout.codex_home_relative == LEGACY_HOME_DIR
    )
    legacy_binding = direct_legacy_layout or legacy_alias
    if "paths" not in payload:
        # Path tuples predate the canonical namespace. A missing tuple is
        # therefore credible only for the exact historical default layout.
        return payload.get("local_dir") == LEGACY_LOCAL_DIR and legacy_binding
    paths = payload["paths"]
    if not isinstance(paths, dict):
        return False
    current_paths = {
        "local_dir": layout.local_dir_relative,
        "codex_home_dir": layout.codex_home_relative,
        "launcher": layout.launcher_relative,
        "readme_local": layout.readme_relative,
    }
    if payload.get("local_dir") == layout.local_dir_relative and all(
        paths.get(name) == value for name, value in current_paths.items()
    ):
        return True
    legacy_paths = {
        "local_dir": LEGACY_LOCAL_DIR,
        "codex_home_dir": LEGACY_HOME_DIR,
        "launcher": layout.launcher_relative,
        "readme_local": layout.readme_relative,
    }
    return (
        payload.get("local_dir") == LEGACY_LOCAL_DIR
        and legacy_binding
        and all(paths.get(name) == value for name, value in legacy_paths.items())
    )


def read_slot_metadata(
    layout: Layout,
    slot_name: str,
    *,
    allow_pending_legacy_home: bool = False,
) -> Optional[Dict[str, Any]]:
    """Return a validated completion record, or ``None`` when incomplete.

    ``allow_pending_legacy_home`` is reserved for migration proof while the
    runtime has moved but the historical isolated HOME has not.
    """

    prefix = slot_prefix(layout, slot_name)
    path = slot_metadata_path(layout, slot_name)
    if not regular_contained_file(prefix, path):
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    version = payload.get("codex_version")
    selector = payload.get("codex_selector")
    channel = payload.get("codex_channel")
    legacy_runtime_path = layout.project_root / LEGACY_LOCAL_DIR
    legacy_home_path = layout.project_root / LEGACY_HOME_DIR
    canonical_home_path = layout.project_root / DEFAULT_HOME_DIR
    legacy_runtime_bound = _readlink_matches(
        legacy_runtime_path,
        DEFAULT_LOCAL_DIR,
    )
    legacy_home_migrated = (
        _readlink_matches(legacy_home_path, DEFAULT_HOME_DIR)
        and canonical_home_path.is_dir()
        and not canonical_home_path.is_symlink()
    )
    legacy_home_pending = (
        allow_pending_legacy_home
        and legacy_home_path.is_dir()
        and not legacy_home_path.is_symlink()
        and (
            not _lexists(canonical_home_path)
            or _readlink_matches(canonical_home_path, DEFAULT_HOME_DIR)
        )
    )
    legacy_alias = (
        layout.local_dir_relative == DEFAULT_LOCAL_DIR
        and layout.codex_home_relative == DEFAULT_HOME_DIR
        and payload.get("local_dir") == LEGACY_LOCAL_DIR
        and legacy_runtime_bound
        and (
            payload.get("shared_home") is True
            or (
                payload.get("shared_home") is False
                and (legacy_home_migrated or legacy_home_pending)
            )
        )
    )
    selector_channel = (
        infer_codex_channel(selector) if isinstance(selector, str) else None
    )
    if (
        not isinstance(payload.get("schema_version"), int)
        or isinstance(payload.get("schema_version"), bool)
        or payload.get("schema_version") != 1
        or payload.get("script_name") != SCRIPT_NAME
        or payload.get("state") != "complete"
        or not isinstance(payload.get("generated_at"), str)
        or not payload.get("generated_at")
        or payload.get("project_root") != str(layout.project_root)
        or (payload.get("local_dir") != layout.local_dir_relative and not legacy_alias)
        or not _slot_record_paths_match(
            payload,
            layout,
            legacy_alias=legacy_alias,
        )
        or payload.get("slot") != slot_name
        or not isinstance(selector, str)
        or selector_channel is None
        or channel not in (*CODEX_CHANNELS, None)
        or (
            selector != "latest" and channel is not None and channel != selector_channel
        )
        or not isinstance(version, str)
        or not is_exact_version(version)
        or not isinstance(payload.get("version_source"), str)
        or not payload.get("version_source")
        or not isinstance(payload.get("shared_home"), bool)
        or not isinstance(payload.get("reasonable_permissions_enabled"), bool)
        or not isinstance(payload.get("available_versions"), dict)
        or (
            payload.get("available_versions_updated_at") is not None
            and not isinstance(payload.get("available_versions_updated_at"), str)
        )
    ):
        return None

    available_versions = payload["available_versions"]
    if any(
        channel_name not in CODEX_CHANNELS
        or (
            version_value is not None
            and (
                not isinstance(version_value, str)
                or not is_exact_version(version_value)
            )
        )
        for channel_name, version_value in available_versions.items()
    ):
        return None

    package_path = prefix / "package.json"
    lock_path = prefix / "package-lock.json"
    installed_path = prefix / "node_modules" / "@openai" / "codex" / "package.json"
    evidence_paths = (package_path, lock_path, installed_path)
    if any(
        not regular_contained_file(prefix, evidence_path)
        for evidence_path in evidence_paths
    ):
        return None
    try:
        package_payload = json.loads(package_path.read_text(encoding="utf-8"))
        lock_payload = json.loads(lock_path.read_text(encoding="utf-8"))
        installed_payload = json.loads(installed_path.read_text(encoding="utf-8"))
        requested_version = package_payload["devDependencies"]["@openai/codex"]
        locked_version = lock_payload["packages"]["node_modules/@openai/codex"][
            "version"
        ]
        installed_version = installed_payload["version"]
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
    ):
        return None
    if not all(
        candidate_version == version
        for candidate_version in (
            requested_version,
            locked_version,
            installed_version,
        )
    ):
        return None
    return payload


def write_slot_metadata(
    config: Config,
    slot_name: str,
    target_config: Optional[Config] = None,
) -> None:
    """Publish one completion record after candidate validation."""

    candidate = target_config or config_for_slot(config, slot_name)
    write_metadata(
        candidate.layout.metadata_path,
        build_slot_metadata(config, slot_name),
        dry_run=config.dry_run,
    )


def fsync_candidate_tree(prefix: Path) -> None:
    """Flush a validated candidate tree before making it active."""

    def fail_walk(error: OSError) -> None:
        """Turn an unreadable subtree into a bounded validation failure."""

        raise CodexWranglerError(
            "Failed to traverse validated install tree {}: {}".format(prefix, error)
        ) from error

    started_at = time.monotonic()
    next_heartbeat = started_at + COMMAND_HEARTBEAT_SECONDS
    flushed_files = 0
    eprint("[codex-wrangler] Flushing validated install tree: {}".format(prefix))
    directories = []
    for root, dirnames, filenames in os.walk(
        prefix,
        followlinks=False,
        onerror=fail_walk,
    ):
        root_path = Path(root)
        directories.append(root_path)
        for dirname in dirnames:
            candidate_dir = root_path / dirname
            if candidate_dir.is_symlink():
                raise CodexWranglerError(
                    "Candidate install contains a symbolic-link directory: {}".format(
                        candidate_dir
                    )
                )
        for filename in filenames:
            path = root_path / filename
            if path.is_symlink():
                continue
            try:
                if not stat.S_ISREG(os.lstat(path).st_mode):
                    raise CodexWranglerError(
                        "Validated install contains a non-regular file: {}".format(path)
                    )
                with path.open("rb") as handle:
                    os.fsync(handle.fileno())
                flushed_files += 1
            except CodexWranglerError:
                raise
            except OSError as exc:
                raise CodexWranglerError(
                    "Failed to flush candidate install file {}: {}".format(path, exc)
                ) from exc
            now = time.monotonic()
            if now >= next_heartbeat:
                eprint(
                    "[codex-wrangler] Still flushing validated install after "
                    "{}s ({} files): {}".format(
                        max(0, int(now - started_at)),
                        flushed_files,
                        prefix,
                    )
                )
                next_heartbeat = now + COMMAND_HEARTBEAT_SECONDS
    try:
        for directory in reversed(directories):
            fsync_directory(directory)
        fsync_directory(prefix.parent)
    except OSError as exc:
        raise CodexWranglerError(
            "Failed to flush validated install tree {}: {}".format(prefix, exc)
        ) from exc
    eprint(
        "[codex-wrangler] Flushed validated install tree in {:.1f}s ({} files): "
        "{}".format(time.monotonic() - started_at, flushed_files, prefix)
    )


def activate_candidate_slot(
    layout: Layout,
    candidate_prefix: Path,
    slot_name: str,
) -> SlotSwap:
    """Rename a validated unique candidate into one fixed slot reversibly."""

    validate_slot_container(layout)
    if slot_name not in SLOT_NAMES:
        raise CodexWranglerError("Unknown managed install slot: {!r}".format(slot_name))
    expected_parent = layout.local_dir
    if (
        candidate_prefix.parent != expected_parent
        or not candidate_prefix.name.startswith(".candidate-")
        or candidate_prefix.is_symlink()
        or not candidate_prefix.is_dir()
    ):
        raise CodexWranglerError(
            "Candidate install is not a real transaction-unique managed "
            "directory: {}".format(candidate_prefix)
        )

    container = slots_directory(layout)
    container.mkdir(parents=True, exist_ok=True)
    fsync_directory(layout.local_dir)
    fixed_prefix = slot_prefix(layout, slot_name)
    retired_prefix: Optional[Path] = None
    candidate_moved = False
    try:
        if _lexists(fixed_prefix):
            if fixed_prefix.is_symlink() or not fixed_prefix.is_dir():
                raise CodexWranglerError(
                    "Inactive managed install slot is not a real directory: "
                    "{}".format(fixed_prefix)
                )
            if read_slot_metadata(layout, slot_name) is None:
                raise CodexWranglerError(
                    "Refusing to retire occupied managed install slot {} because "
                    "it lacks a valid managed completion record.".format(fixed_prefix)
                )
            retired_prefix = container / ".{}.retired-{}".format(
                slot_name,
                uuid.uuid4().hex,
            )
            os.replace(fixed_prefix, retired_prefix)
            fsync_directory(container)
        os.replace(candidate_prefix, fixed_prefix)
        candidate_moved = True
        fsync_directory(container)
        fsync_directory(layout.local_dir)
    except (OSError, CodexWranglerError, KeyboardInterrupt) as exc:
        # A signal can be delivered after rename(2) committed but before
        # ``os.replace`` returned to Python.  Observe the exact source/dest
        # state so rollback does not mistake a completed second rename for an
        # unstarted one and overwrite the newly populated fixed slot.
        candidate_moved = candidate_moved or (
            not _lexists(candidate_prefix) and _lexists(fixed_prefix)
        )
        restoration_errors = []
        if candidate_moved and _lexists(fixed_prefix):
            try:
                os.replace(fixed_prefix, candidate_prefix)
            except (OSError, KeyboardInterrupt) as restore_exc:
                restoration_errors.append(str(restore_exc) or "interrupted")
        if retired_prefix is not None and _lexists(retired_prefix):
            try:
                os.replace(retired_prefix, fixed_prefix)
            except (OSError, KeyboardInterrupt) as restore_exc:
                restoration_errors.append(str(restore_exc) or "interrupted")
        try:
            fsync_directory(container)
            fsync_directory(layout.local_dir)
        except (OSError, KeyboardInterrupt) as restore_exc:
            restoration_errors.append(str(restore_exc) or "interrupted")
        detail = (
            " Rollback also reported: {}.".format("; ".join(restoration_errors))
            if restoration_errors
            else ""
        )
        if isinstance(exc, KeyboardInterrupt) and not restoration_errors:
            raise
        raise CodexWranglerError(
            "Failed to activate validated candidate in slot {}: {}.{}".format(
                slot_name,
                exc or "interrupted",
                detail,
            )
        ) from exc
    return SlotSwap(
        layout=layout,
        slot_name=slot_name,
        candidate_prefix=candidate_prefix,
        retired_prefix=retired_prefix,
    )


def restore_slot_swap(swap: SlotSwap) -> None:
    """Restore the pre-activation inactive slot after a pre-pointer failure."""

    fixed_prefix = slot_prefix(swap.layout, swap.slot_name)
    restoration_errors = []
    if _lexists(fixed_prefix):
        try:
            os.replace(fixed_prefix, swap.candidate_prefix)
        except (OSError, KeyboardInterrupt) as exc:
            restoration_errors.append(str(exc) or "interrupted")
    if swap.retired_prefix is not None and _lexists(swap.retired_prefix):
        try:
            os.replace(swap.retired_prefix, fixed_prefix)
        except (OSError, KeyboardInterrupt) as exc:
            restoration_errors.append(str(exc) or "interrupted")
    try:
        fsync_directory(slots_directory(swap.layout))
        fsync_directory(swap.layout.local_dir)
    except (OSError, KeyboardInterrupt) as exc:
        restoration_errors.append(str(exc) or "interrupted")
    if restoration_errors:
        raise CodexWranglerError(
            "Failed to fully restore inactive slot {} after candidate rejection: "
            "{}".format(swap.slot_name, "; ".join(restoration_errors))
        )


def _promote_symlink(layout: Layout, slot_name: str) -> None:
    """Atomically replace the POSIX pointer with commit-aware diagnostics."""

    active_path = layout.local_dir / ACTIVE_SYMLINK_NAME
    temporary = layout.local_dir / ".active-{}.tmp".format(uuid.uuid4().hex)
    expected_target = "{}/{}".format(SLOTS_DIRECTORY_NAME, slot_name)
    try:
        os.symlink(expected_target, temporary, target_is_directory=True)
        try:
            os.replace(temporary, active_path)
        except KeyboardInterrupt as exc:
            if active_path.is_symlink() and os.readlink(active_path) == expected_target:
                raise PointerCommittedInterrupt(
                    "Interrupted after the active pointer switched to verified "
                    "slot {}.".format(slot_name)
                ) from exc
            raise
        try:
            fsync_directory(layout.local_dir)
        except KeyboardInterrupt as exc:
            raise PointerCommittedInterrupt(
                "Interrupted after the active pointer switched to verified "
                "slot {}.".format(slot_name)
            ) from exc
        except OSError as exc:
            raise PointerCommitDurabilityError(
                "Active pointer now selects verified slot {}, but the managed "
                "directory flush failed; runtime activation succeeded with "
                "uncertain crash durability: {}".format(slot_name, exc)
            ) from exc
    finally:
        if _lexists(temporary):
            temporary.unlink()


def _promote_file(layout: Layout, slot_name: str) -> None:
    """Atomically replace the strict fallback pointer with commit awareness."""

    active_path = layout.local_dir / ACTIVE_SLOT_FILE_NAME
    temporary = layout.local_dir / ".active-slot-{}.tmp".format(uuid.uuid4().hex)
    try:
        with temporary.open("x", encoding="ascii", newline="") as handle:
            handle.write(slot_name + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.replace(temporary, active_path)
        except KeyboardInterrupt as exc:
            committed_content = b""
            if active_path.is_file() and not active_path.is_symlink():
                try:
                    committed_content = active_path.read_bytes()
                except OSError:
                    pass
            if committed_content == (slot_name + "\n").encode("ascii"):
                raise PointerCommittedInterrupt(
                    "Interrupted after the active pointer switched to verified "
                    "slot {}.".format(slot_name)
                ) from exc
            raise
        try:
            fsync_directory(layout.local_dir)
        except KeyboardInterrupt as exc:
            raise PointerCommittedInterrupt(
                "Interrupted after the active pointer switched to verified "
                "slot {}.".format(slot_name)
            ) from exc
        except OSError as exc:
            raise PointerCommitDurabilityError(
                "Active pointer now selects verified slot {}, but the managed "
                "directory flush failed; runtime activation succeeded with "
                "uncertain crash durability: {}".format(slot_name, exc)
            ) from exc
    except (PointerCommitDurabilityError, PointerCommittedInterrupt):
        raise
    except OSError as exc:
        raise CodexWranglerError(
            "Failed to atomically promote managed active-slot file: {}".format(exc)
        ) from exc
    finally:
        if _lexists(temporary):
            temporary.unlink()


def promote_active_slot(
    layout: Layout,
    slot_name: str,
    *,
    allow_invalid_current: bool = False,
) -> str:
    """Commit one validated slot through the platform-appropriate pointer."""

    active = (
        observe_repair_runtime(layout)
        if allow_invalid_current
        else observe_active_runtime(layout)
    )
    if not allow_invalid_current and active.slot_name is not None:
        # A present pointer remains strict, while a first promotion may
        # explicitly select the newly completed orphan slot that this helper
        # is committing.
        require_completed_slot(layout, active.slot_name)
    require_completed_slot(layout, slot_name)
    symlink_path = layout.local_dir / ACTIVE_SYMLINK_NAME
    if active.pointer_kind == "file":
        _promote_file(layout, slot_name)
        return "file"
    if active.pointer_kind == "symlink" or os.name == "posix":
        try:
            _promote_symlink(layout, slot_name)
            return "symlink"
        except OSError as exc:
            fallback_errors = {
                errno.EPERM,
                errno.EACCES,
                errno.ENOSYS,
                getattr(errno, "EOPNOTSUPP", errno.ENOSYS),
            }
            if active.pointer_kind == "symlink" or exc.errno not in fallback_errors:
                raise CodexWranglerError(
                    "Failed to atomically promote managed active pointer: {}".format(
                        exc
                    )
                ) from exc
            if _lexists(symlink_path):
                raise CodexWranglerError(
                    "Refusing file-pointer fallback while active symlink exists: "
                    "{}".format(symlink_path)
                ) from exc
    _promote_file(layout, slot_name)
    return "file"


class MaintenanceLock(AbstractContextManager["MaintenanceLock"]):
    """Serialize package transactions without blocking runtime launchers."""

    def __init__(self, layout: Layout) -> None:
        self.layout = layout
        self.path = layout.project_root / MAINTENANCE_LOCK_FILENAME
        self._handle: Optional[BinaryIO] = None

    def __enter__(self) -> "MaintenanceLock":
        validate_managed_root(self.layout)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        validate_managed_root(self.layout)
        if self.path.is_symlink():
            raise CodexWranglerError(
                "Managed maintenance lock may not be a symbolic link: {}".format(
                    self.path
                )
            )
        if _lexists(self.path) and not self.path.is_file():
            raise CodexWranglerError(
                "Managed maintenance lock must be a regular file: {}".format(self.path)
            )
        flags = os.O_RDWR | os.O_CREAT | os.O_APPEND
        flags |= getattr(os, "O_BINARY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.path, flags, 0o600)
            handle = os.fdopen(descriptor, "a+b")
        except OSError as exc:
            raise CodexWranglerError(
                "Failed to open managed maintenance lock {}: {}".format(
                    self.path,
                    exc,
                )
            ) from exc
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            handle.close()
            raise CodexWranglerError(
                "Managed maintenance lock must be a regular file: {}".format(self.path)
            )
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            handle.close()
            raise CodexWranglerError(
                "Another codex-wrangler maintenance operation is already active "
                "for {}.".format(self.path.parent)
            ) from exc
        self._handle = handle
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return None
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
        return None
