"""Discover moved authority without reading context or using the old directory."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
import shlex
import stat
from typing import Any, Optional

from .constants import (
    DEFAULT_HOME_DIR,
    DEFAULT_LOCAL_DIR,
    LEGACY_HOME_DIR,
    LEGACY_LOCAL_DIR,
    SCRIPT_NAME,
    SLOT_METADATA_FILENAME,
)
from .filesystem import write_metadata
from .initialization import initial_install_path, layout_paths, read_initial_install
from .layout import build_layout, metadata_looks_managed
from .models import CodexWranglerError, Layout
from .releases import is_exact_version
from .slots import (
    observe_repair_runtime,
    read_slot_metadata,
    regular_contained_file,
    slot_metadata_path,
    validate_slot_container,
)


@dataclass(frozen=True)
class RelocationRecord:
    """One locally validated binding, with no authority over its old pathname."""

    path: Path
    root: Path
    layout: Layout
    active: bool = False


@dataclass
class RelocationScan:
    """Bounded evidence and safe diagnostics for inspection or repair planning."""

    records: list[RelocationRecord]
    recorded_roots: set[str]
    issues: list[str]


def unique_record_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject ambiguous duplicate JSON fields instead of selecting a value."""

    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate authority fields")
        result[key] = value
    return result


def read_relocation_record(project_root: Path, path: Path) -> Optional[dict[str, Any]]:
    """Read at most 16 KiB of real, contained, single-link authority evidence."""

    if not os.path.lexists(path):
        return None
    if not regular_contained_file(project_root, path):
        raise CodexWranglerError("Unsafe relocation record: {}".format(path))
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    try:
        with os.fdopen(os.open(path, flags), "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("authority must be a single-link regular file")
            raw = handle.read(16385)
            if len(raw) > 16384:
                raise ValueError("authority exceeds its size bound")
        payload = json.loads(raw, object_pairs_hook=unique_record_fields)
        if not isinstance(payload, dict):
            raise ValueError("authority must be a JSON object")
        return payload
    except (OSError, ValueError, RecursionError) as exc:
        # JSON error text can include input. Diagnostics expose only the path
        # and error class, never arbitrary content from an authority file.
        raise CodexWranglerError(
            "Invalid relocation record {} ({}).".format(path, type(exc).__name__)
        ) from exc


def record_layout(base: Layout, payload: dict[str, Any], root: Path) -> Layout:
    """Recover an original path tuple while enforcing current-root boundaries."""

    paths = payload.get("paths")
    if paths is None:
        return replace(base, repair_from_root=root)
    names = ("local_dir", "codex_home_dir", "launcher", "readme_local")
    if not isinstance(paths, dict) or any(
        not isinstance(paths.get(name), str) for name in names
    ):
        raise CodexWranglerError("Relocation record has incomplete path bindings.")
    if (
        base.local_dir_relative == DEFAULT_LOCAL_DIR
        and base.codex_home_relative == DEFAULT_HOME_DIR
        and paths["local_dir"] == LEGACY_LOCAL_DIR
        and paths["codex_home_dir"] == LEGACY_HOME_DIR
    ):
        # Existing metadata/slot validators must prove the exact migration
        # aliases before accepting these historical names at a canonical root.
        return replace(base, repair_from_root=root)
    candidate = build_layout(base.project_root, *(paths[name] for name in names))
    return replace(candidate, repair_from_root=root)


def scan_relocation(layout: Layout) -> RelocationScan:
    """Inspect only known authority locations, retaining all observed origins."""

    scan = RelocationScan([], set(), [])
    bases = [layout]
    legacy = layout.project_root / LEGACY_LOCAL_DIR
    if (
        layout.local_dir_relative != LEGACY_LOCAL_DIR
        and legacy.is_dir()
        and not legacy.is_symlink()
    ):
        try:
            bases.append(
                build_layout(
                    layout.project_root,
                    LEGACY_LOCAL_DIR,
                    LEGACY_HOME_DIR,
                    layout.launcher_relative,
                    layout.readme_relative,
                )
            )
        except CodexWranglerError as exc:
            scan.issues.append(str(exc))
    sources = [(layout, initial_install_path(layout), "receipt", False)]
    for base in bases:
        try:
            validate_slot_container(base)
            active = observe_repair_runtime(base)
        except CodexWranglerError as exc:
            scan.issues.append(str(exc))
            continue
        sources.append((base, base.metadata_path, "root", False))
        for slot in ("a", "b"):
            sources.append(
                (base, slot_metadata_path(base, slot), slot, active.slot_name == slot)
            )
    for base, path, kind, active in sources:
        try:
            payload = read_relocation_record(layout.project_root, path)
            if payload is None:
                continue
            managed = (
                payload.get("kind") == SCRIPT_NAME + "-initial-install"
                if kind == "receipt"
                else payload.get("script_name") == SCRIPT_NAME
            )
            raw_root = payload.get("project_root")
            if (
                not managed
                or not isinstance(raw_root, str)
                or not Path(raw_root).is_absolute()
            ):
                raise CodexWranglerError(
                    "Unrecognized relocation authority: {}".format(path)
                )
            scan.recorded_roots.add(raw_root)
            root = Path(os.path.normpath(raw_root))
            candidate = record_layout(base, payload, root)
            if kind == "receipt":
                valid = read_initial_install(candidate) is not None
            elif candidate.local_dir != base.local_dir:
                valid = False
            elif kind == "root":
                valid = (
                    metadata_looks_managed(payload, candidate)
                    and type(payload.get("shared_home")) is bool
                    and type(payload.get("reasonable_permissions_enabled")) is bool
                    and isinstance(payload.get("codex_version"), str)
                    and is_exact_version(payload["codex_version"])
                )
            else:
                valid = (
                    read_slot_metadata(candidate, kind, allow_pending_legacy_home=True)
                    is not None
                )
            if not valid:
                raise CodexWranglerError(
                    "Relocation binding did not validate: {}".format(path)
                )
            scan.records.append(RelocationRecord(path, root, candidate, active))
        except (CodexWranglerError, OSError, ValueError) as exc:
            scan.issues.append(str(exc))
    return scan


def choose_relocation_layout(
    layout: Layout, scan: RelocationScan, previous_root: Path
) -> Layout:
    """Choose bound geometry using active authority, or require concordance."""

    origins = [record for record in scan.records if record.root == previous_root]
    if not origins:
        raise CodexWranglerError(
            "No validated relocation authority names {}. Inspect with {} --inspect {}. "
            "A previous-root option alone cannot establish ownership.".format(
                previous_root, SCRIPT_NAME, shlex.quote(str(layout.project_root))
            )
        )
    compatible = [
        record
        for record in scan.records
        if record.root in (previous_root, layout.project_root)
    ]
    active = [record for record in compatible if record.active]
    choices = active or compatible
    bindings = {
        tuple(sorted(layout_paths(record.layout).items())) for record in choices
    }
    if len(bindings) != 1:
        raise CodexWranglerError(
            "Conflicting relocation layout bindings; preserve the records and inspect "
            "the installation instead of guessing its context paths."
        )
    selected = choices[0].layout
    selected = replace(selected, repair_from_root=previous_root)
    if selected.local_dir_relative == LEGACY_LOCAL_DIR:
        # Default namespace migration remains a separate guarded transaction.
        # Carry only the old root into its canonical layout and normal discovery.
        return replace(layout, repair_from_root=previous_root)
    return selected


def resolve_repair_relocation(
    layout: Layout, previous_root: Optional[Path], *, automatic: bool = False
) -> Layout:
    """Require explicit root proof or select the sole observed foreign origin."""

    scan = scan_relocation(layout)
    if automatic:
        roots = scan.recorded_roots - {str(layout.project_root)}
        if len(roots) > 1:
            raise CodexWranglerError(
                "Automatic repair found conflicting recorded project roots: {}. "
                "Inspect and select --repair-from-root explicitly.".format(
                    ", ".join(sorted(roots))
                )
            )
        if not roots:
            return replace(layout, repair_from_root=None)
        previous_root = Path(os.path.normpath(next(iter(roots))))
    if previous_root is None or previous_root == layout.project_root:
        raise CodexWranglerError(
            "--repair-from-root must name a different absolute project root."
        )
    return choose_relocation_layout(layout, scan, previous_root)


def discover_current_repair_layout(layout: Layout) -> Layout:
    """Recover supporting paths from completed slots or identity-checked intent."""

    current = [
        record
        for record in scan_relocation(layout).records
        if record.root == layout.project_root
        and (
            record.path == initial_install_path(layout)
            or record.path.name == SLOT_METADATA_FILENAME
        )
    ]
    if not current:
        return layout
    selected = choose_relocation_layout(
        layout, RelocationScan(current, set(), []), layout.project_root
    )
    return replace(selected, repair_from_root=None, repair_auto=False)


def require_relocation_authority(layout: Layout) -> None:
    """Recheck the selected origin and geometry before starting repair writes."""

    if layout.repair_auto:
        selected = resolve_repair_relocation(layout, None, automatic=True)
        if selected.repair_from_root != layout.repair_from_root:
            raise CodexWranglerError(
                "Relocation origin authority changed; rerun repair from current state."
            )
    elif layout.repair_from_root is None:
        return
    else:
        selected = choose_relocation_layout(
            layout, scan_relocation(layout), layout.repair_from_root
        )
    if selected.local_dir_relative == LEGACY_LOCAL_DIR:
        selected = replace(selected, repair_from_root=layout.repair_from_root)
    if layout_paths(selected) != layout_paths(layout):
        raise CodexWranglerError(
            "Relocation layout authority changed; rerun repair from current state."
        )


def finish_relocation(layout: Layout) -> None:
    """Keep validated rollback authority usable after the replacement commits."""

    if layout.repair_from_root is None:
        return
    for slot in ("a", "b"):
        path = slot_metadata_path(layout, slot)
        payload = read_slot_metadata(layout, slot)
        if payload is not None and payload["project_root"] == str(
            layout.repair_from_root
        ):
            read_relocation_record(layout.project_root, path)
            write_metadata(
                path,
                {**payload, "project_root": str(layout.project_root)},
                dry_run=False,
            )


def relocation_inspection(layout: Layout) -> dict[str, Any]:
    """Explain origin discovery and suggest a shell-safe, bounded repair command."""

    scan = scan_relocation(layout)
    roots = sorted(scan.recorded_roots)
    command = "{} --repair {} --repair-auto".format(
        SCRIPT_NAME, shlex.quote(str(layout.project_root))
    )
    error = None
    previous_root = None
    try:
        selected = resolve_repair_relocation(layout, None, automatic=True)
        previous_root = selected.repair_from_root
        # The ordinary planner still owns version, HOME-mode, and path checks.
        from .repair import build_repair_plan
        from .layout import read_existing_state

        build_repair_plan(selected)
        existing = read_existing_state(selected)
        if existing.shared_home is None:
            error = "HOME mode requires an explicit --shared-home or --isolated-home choice."
        elif not existing.shared_home and not selected.codex_home_dir.is_dir():
            error = "Recorded isolated HOME is missing; repair will not recreate it."
    except CodexWranglerError as exc:
        error = str(exc)
    return {
        "recorded_project_roots": roots,
        "validated_records": [
            {
                "path": str(record.path),
                "project_root": str(record.root),
                "active": record.active,
            }
            for record in scan.records
        ],
        "previous_project_root": (
            str(previous_root) if previous_root is not None else None
        ),
        "relocation_required": any(root != str(layout.project_root) for root in roots),
        "automatic_repair_possible": error is None,
        "suggested_command": command if error is None else None,
        "blocker": error,
        "record_issues": scan.issues,
    }
