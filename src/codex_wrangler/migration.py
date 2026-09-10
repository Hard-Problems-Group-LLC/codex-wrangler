"""Canonical default-layout discovery and preservation-first migration."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Optional, Tuple

from .constants import (
    DEFAULT_HOME_DIR,
    DEFAULT_LOCAL_DIR,
    LEGACY_HOME_DIR,
    LEGACY_LOCAL_DIR,
    SCRIPT_MARKER,
)
from .filesystem import (
    fsync_directory,
    upsert_gitignore_block,
    validate_gitignore_block_update,
    validate_text_file_write,
    write_text_file,
)
from .layout import metadata_looks_managed, read_json_file
from .models import CodexWranglerError, Config, Layout, LayoutMigration
from .repair import prove_managed_install
from .rendering import build_gitignore_block, build_launcher_content
from .runtime import eprint
from .slots import MaintenanceLock, observe_repair_runtime, read_slot_metadata

_AT_FDCWD = -100
_RENAME_EXCHANGE = 2


def _lexists(path: Path) -> bool:
    """Return whether a path or dangling link occupies one pathname."""

    return os.path.lexists(str(path))


def _readlink_matches(path: Path, expected_target: str) -> bool:
    """Return whether one path is exactly the expected relative link."""

    if not path.is_symlink():
        return False
    try:
        return os.readlink(path) == expected_target
    except OSError:
        return False


def _classify_component(
    legacy_path: Path,
    canonical_path: Path,
    expected_link_target: str,
    label: str,
) -> str:
    """Classify one canonical/legacy path pair or reject ambiguous state."""

    legacy_present = _lexists(legacy_path)
    canonical_present = _lexists(canonical_path)
    legacy_real = (
        legacy_present and not legacy_path.is_symlink() and legacy_path.is_dir()
    )
    canonical_real = (
        canonical_present
        and not canonical_path.is_symlink()
        and canonical_path.is_dir()
    )
    legacy_link = _readlink_matches(legacy_path, expected_link_target)
    staged_link = _readlink_matches(canonical_path, expected_link_target)

    if not legacy_present and not canonical_present:
        return "absent"
    if legacy_real and not canonical_present:
        return "legacy"
    if legacy_real and staged_link:
        return "staged"
    if legacy_link and canonical_real:
        return "migrated"
    if not legacy_present and canonical_real:
        return "canonical"

    raise CodexWranglerError(
        "Cannot automatically migrate {} because the canonical and legacy "
        "paths are ambiguous or conflicting: {}, {}. Preserve both paths and "
        "resolve the conflict explicitly; codex-wrangler did not merge, "
        "delete, or overwrite either one.".format(
            label,
            legacy_path,
            canonical_path,
        )
    )


def _directory_has_entries(path: Path, label: str) -> bool:
    """Return whether a proven real directory contains at least one entry."""

    try:
        with os.scandir(path) as entries:
            return next(entries, None) is not None
    except OSError as exc:
        raise CodexWranglerError(
            "Cannot inspect {} before canonical-layout migration: {}".format(
                label,
                exc,
            )
        ) from exc


def legacy_layout_from_canonical(canonical_layout: Layout) -> Layout:
    """Return a lexical legacy layout without following compatibility links."""

    legacy_root = canonical_layout.project_root / LEGACY_LOCAL_DIR
    legacy_home = canonical_layout.project_root / LEGACY_HOME_DIR
    return replace(
        canonical_layout,
        local_dir_relative=LEGACY_LOCAL_DIR,
        codex_home_relative=LEGACY_HOME_DIR,
        local_dir=legacy_root,
        codex_home_dir=legacy_home,
        local_package_json_path=legacy_root / "package.json",
        local_package_lock_path=legacy_root / "package-lock.json",
        local_node_modules_dir=legacy_root / "node_modules",
        metadata_path=legacy_root / ".codex-wrangler.json",
    )


def select_default_state_layout(
    canonical_layout: Layout,
    legacy_layout: Layout,
) -> Layout:
    """Select the layout containing package authority before migration."""

    state = _classify_component(
        legacy_layout.local_dir,
        canonical_layout.local_dir,
        DEFAULT_LOCAL_DIR,
        "managed Codex runtime",
    )
    if state in ("legacy", "staged"):
        return legacy_layout
    return canonical_layout


def _has_exact_legacy_default_paths(payload: object, layout: Layout) -> bool:
    """Return whether metadata names the complete historical default layout."""

    if not isinstance(payload, dict):
        return False
    paths = payload.get("paths")
    return (
        isinstance(paths, dict)
        and paths.get("local_dir") == LEGACY_LOCAL_DIR
        and paths.get("codex_home_dir") == LEGACY_HOME_DIR
        and paths.get("launcher") == layout.launcher_relative
        and paths.get("readme_local") == layout.readme_relative
    )


def _legacy_runtime_state_is_bound(
    canonical_layout: Layout,
    state_layout: Layout,
) -> bool:
    """Bind historical metadata to its real legacy or migrated runtime tree."""

    legacy_runtime = canonical_layout.project_root / LEGACY_LOCAL_DIR
    canonical_runtime = canonical_layout.project_root / DEFAULT_LOCAL_DIR
    if state_layout.local_dir == legacy_runtime:
        return legacy_runtime.is_dir() and not legacy_runtime.is_symlink()
    return (
        state_layout.local_dir == canonical_runtime
        and canonical_runtime.is_dir()
        and not canonical_runtime.is_symlink()
        and _readlink_matches(
            legacy_runtime,
            DEFAULT_LOCAL_DIR,
        )
    )


def _legacy_slot_home_mode(
    canonical_layout: Layout,
    state_layout: Layout,
    slot_name: str,
) -> Optional[bool]:
    """Return HOME mode from one validated historical default slot."""

    slot_metadata = read_slot_metadata(
        state_layout,
        slot_name,
        allow_pending_legacy_home=True,
    )
    if (
        slot_metadata is None
        or slot_metadata.get("local_dir") != LEGACY_LOCAL_DIR
        or (
            "paths" in slot_metadata
            and not _has_exact_legacy_default_paths(
                slot_metadata,
                canonical_layout,
            )
        )
    ):
        return None
    return slot_metadata["shared_home"]


def discover_pending_legacy_home_mode(
    canonical_layout: Layout,
    state_layout: Layout,
) -> Optional[bool]:
    """Recover authoritative HOME mode while an old namespace still needs work."""

    if not _legacy_runtime_state_is_bound(canonical_layout, state_layout):
        return None

    local_state = _classify_component(
        canonical_layout.project_root / LEGACY_LOCAL_DIR,
        canonical_layout.local_dir,
        DEFAULT_LOCAL_DIR,
        "managed Codex runtime",
    )
    legacy_layout = legacy_layout_from_canonical(canonical_layout)

    # A validated selected slot is the commit authority. Root metadata and
    # rollback slots are only recovery evidence when no valid pointer survives.
    try:
        active = observe_repair_runtime(state_layout)
    except CodexWranglerError:
        active = None
    if active is not None and active.slot_name is not None:
        active_mode = _legacy_slot_home_mode(
            canonical_layout,
            state_layout,
            active.slot_name,
        )
        if active_mode is not None:
            recorded_mode = active_mode
        else:
            recorded_mode = None
    else:
        recorded_mode = None

    root_mode = None
    try:
        root_metadata = read_json_file(state_layout.metadata_path)
    except CodexWranglerError:
        root_metadata = None
    if (
        not state_layout.metadata_path.is_symlink()
        and metadata_looks_managed(root_metadata, legacy_layout)
        and root_metadata is not None
        and isinstance(root_metadata.get("shared_home"), bool)
    ):
        root_mode = root_metadata["shared_home"]

    if recorded_mode is None:
        evidence = []
        if root_mode is not None:
            evidence.append(("root metadata", root_mode))
        for slot_name in ("a", "b"):
            slot_mode = _legacy_slot_home_mode(
                canonical_layout,
                state_layout,
                slot_name,
            )
            if slot_mode is not None:
                evidence.append(("completed slot {}".format(slot_name), slot_mode))
        modes = {mode for _, mode in evidence}
        if len(modes) > 1:
            details = ", ".join(
                "{}={}".format(source, mode) for source, mode in evidence
            )
            raise CodexWranglerError(
                "Conflicting legacy HOME-mode recovery evidence was found "
                "with no validated active slot: {}. Canonical migration and "
                "repair refuse to guess.".format(details)
            )
        recorded_mode = next(iter(modes)) if modes else None

    if recorded_mode is None:
        return None

    namespace_pending = local_state in ("legacy", "staged")
    if recorded_mode is False:
        home_state = _classify_component(
            canonical_layout.project_root / LEGACY_HOME_DIR,
            canonical_layout.codex_home_dir,
            DEFAULT_HOME_DIR,
            "isolated Codex home",
        )
        namespace_pending = namespace_pending or home_state in ("legacy", "staged")

    # Once every namespace component selected by the authoritative mode is
    # canonical, ordinary explicit HOME transitions must remain available.
    return recorded_mode if namespace_pending else None


def prove_legacy_isolated_home(
    canonical_layout: Layout,
    state_layout: Layout,
) -> Tuple[str, ...]:
    """Prove that the old HOME path belongs to the historical managed layout."""

    evidence = []
    try:
        root_metadata = read_json_file(state_layout.metadata_path)
    except CodexWranglerError:
        root_metadata = None
    legacy_layout = legacy_layout_from_canonical(canonical_layout)
    runtime_is_bound = _legacy_runtime_state_is_bound(
        canonical_layout,
        state_layout,
    )
    root_metadata_is_valid = (
        runtime_is_bound
        and not state_layout.metadata_path.is_symlink()
        and metadata_looks_managed(root_metadata, legacy_layout)
        and root_metadata is not None
        and isinstance(root_metadata.get("shared_home"), bool)
        and _has_exact_legacy_default_paths(root_metadata, canonical_layout)
    )
    if root_metadata_is_valid and root_metadata.get("shared_home") is False:
        evidence.append(str(state_layout.metadata_path))

    try:
        active = observe_repair_runtime(state_layout)
    except CodexWranglerError:
        active = None
    active_slot_name = active.slot_name if active is not None else None

    for slot_name in ("a", "b"):
        slot_metadata = read_slot_metadata(
            state_layout,
            slot_name,
            allow_pending_legacy_home=True,
        )
        if slot_metadata is None or slot_metadata.get("shared_home") is not False:
            continue
        path_tuple_is_valid = _has_exact_legacy_default_paths(
            slot_metadata,
            canonical_layout,
        )
        pathless_active_is_historical = (
            slot_name == active_slot_name
            and "paths" not in slot_metadata
            and slot_metadata.get("local_dir") == LEGACY_LOCAL_DIR
            and runtime_is_bound
        )
        if path_tuple_is_valid or pathless_active_is_historical:
            evidence.append(
                str(
                    state_layout.local_dir
                    / "slots"
                    / slot_name
                    / ".codex-wrangler-slot.json"
                )
            )

    if evidence:
        return tuple(evidence)
    raise CodexWranglerError(
        "Cannot prove that {} is the isolated HOME selected by the exact "
        "historical codex-wrangler default layout. Nothing was moved; preserve "
        "that directory and restore valid managed metadata before retrying.".format(
            state_layout.project_root / LEGACY_HOME_DIR
        )
    )


def build_default_layout_migration(
    canonical_layout: Layout,
    legacy_layout: Layout,
    *,
    shared_home: bool,
    require_managed_local: bool = True,
) -> Optional[LayoutMigration]:
    """Return a validated default-layout migration plan when one is pending."""

    state_layout = select_default_state_layout(canonical_layout, legacy_layout)
    local_state = _classify_component(
        legacy_layout.local_dir,
        canonical_layout.local_dir,
        DEFAULT_LOCAL_DIR,
        "managed Codex runtime",
    )
    home_state = "shared"
    if not shared_home:
        home_state = _classify_component(
            legacy_layout.codex_home_dir,
            canonical_layout.codex_home_dir,
            DEFAULT_HOME_DIR,
            "isolated Codex home",
        )

    migrate_local = local_state in ("legacy", "staged")
    migrate_home = home_state in ("legacy", "staged")
    if migrate_local and require_managed_local:
        prove_managed_install(legacy_layout)
    if (
        require_managed_local
        and not shared_home
        and migrate_local
        and home_state in ("absent", "canonical")
    ):
        if home_state == "canonical" and _directory_has_entries(
            canonical_layout.codex_home_dir,
            "canonical isolated HOME",
        ):
            detail = "the canonical isolated HOME is nonempty and unbound"
        else:
            detail = (
                "no real legacy isolated HOME or completed compatibility "
                "binding exists"
            )
        raise CodexWranglerError(
            "Cannot migrate the legacy managed runtime in isolated-HOME mode "
            "because {}: {}, {}. No managed path was changed. Restore the "
            "expected real legacy HOME before retrying; use an empty directory "
            "only after confirming that no historical state should exist.".format(
                detail,
                legacy_layout.codex_home_dir,
                canonical_layout.codex_home_dir,
            )
        )
    if migrate_home and require_managed_local:
        prove_legacy_isolated_home(canonical_layout, state_layout)
    if not migrate_local and not migrate_home:
        return None
    return LayoutMigration(
        legacy_layout=legacy_layout,
        canonical_layout=canonical_layout,
        state_layout=state_layout,
        migrate_local=migrate_local,
        migrate_home=migrate_home,
        local_state=local_state,
        home_state=home_state,
    )


def _in_process_exchange_available() -> bool:
    """Return whether the running interpreter can call libc.renameat2."""

    try:
        import ctypes
    except ImportError:
        return False
    try:
        return hasattr(ctypes.CDLL(None, use_errno=True), "renameat2")
    except OSError:
        return False


def _external_exchange_python() -> Optional[str]:
    """Return one bounded system Python capable of calling libc.renameat2."""

    probe = (
        "import ctypes;"
        "raise SystemExit(0 if hasattr(ctypes.CDLL(None,use_errno=True),"
        "'renameat2') else 1)"
    )
    for raw_path in ("/usr/bin/python3", "/usr/bin/python"):
        candidate = Path(raw_path)
        if not candidate.is_file() or not os.access(candidate, os.X_OK):
            continue
        try:
            completed = subprocess.run(
                [str(candidate), "-I", "-c", probe],
                check=False,
                cwd="/",
                env={
                    "PATH": "/usr/bin:/bin",
                    "LANG": "C",
                    "LC_ALL": "C",
                },
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if completed.returncode == 0:
            return str(candidate)
    return None


def atomic_exchange_supported() -> bool:
    """Return whether the host exposes usable Linux renameat2 semantics."""

    if os.name != "posix" or not sys.platform.startswith("linux"):
        return False
    return _in_process_exchange_available() or _external_exchange_python() is not None


def _rename_exchange_in_process(left: Path, right: Path) -> None:
    """Call renameat2 through this interpreter's optional ctypes module."""

    import ctypes

    library = ctypes.CDLL(None, use_errno=True)
    renameat2 = library.renameat2
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        _AT_FDCWD,
        os.fsencode(left),
        _AT_FDCWD,
        os.fsencode(right),
        _RENAME_EXCHANGE,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), str(left), str(right))


def _rename_exchange(left: Path, right: Path) -> None:
    """Atomically exchange two Linux pathnames with a verified helper."""

    if _in_process_exchange_available():
        _rename_exchange_in_process(left, right)
        return

    helper = _external_exchange_python()
    if helper is None:
        raise OSError("No Python runtime can call Linux renameat2")
    program = (
        "import ctypes,os,sys;"
        "lib=ctypes.CDLL(None,use_errno=True);"
        "fn=lib.renameat2;"
        "fn.argtypes=[ctypes.c_int,ctypes.c_char_p,ctypes.c_int,"
        "ctypes.c_char_p,ctypes.c_uint];"
        "fn.restype=ctypes.c_int;"
        "result=fn(-100,os.fsencode(sys.argv[1]),-100,"
        "os.fsencode(sys.argv[2]),2);"
        "number=ctypes.get_errno();"
        "raise SystemExit(0 if result==0 else number or 1)"
    )
    completed = subprocess.run(
        [helper, "-I", "-c", program, str(left), str(right)],
        check=False,
        cwd="/",
        env={
            "PATH": "/usr/bin:/bin",
            "LANG": "C",
            "LC_ALL": "C",
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=15,
    )
    if completed.returncode != 0:
        raise OSError(
            completed.returncode,
            (
                os.strerror(completed.returncode)
                if completed.returncode < 256
                else "renameat2 helper failed"
            ),
            str(left),
            str(right),
        )


def _exchange_directory_with_compatibility_link(
    legacy_path: Path,
    canonical_path: Path,
    expected_link_target: str,
    label: str,
) -> None:
    """Move one real directory atomically and leave its old name as a link."""

    canonical_path.parent.mkdir(parents=True, exist_ok=True)
    if canonical_path.parent.is_symlink() or not canonical_path.parent.is_dir():
        raise CodexWranglerError(
            "Canonical {} parent must be a real directory: {}".format(
                label,
                canonical_path.parent,
            )
        )
    if legacy_path.is_symlink() or not legacy_path.is_dir():
        raise CodexWranglerError(
            "Legacy {} source must be a real directory: {}".format(
                label,
                legacy_path,
            )
        )

    staged_here = False
    if not _lexists(canonical_path):
        try:
            os.symlink(
                expected_link_target,
                canonical_path,
                target_is_directory=True,
            )
            staged_here = True
            fsync_directory(canonical_path.parent)
        except OSError as exc:
            raise CodexWranglerError(
                "Failed to stage atomic {} migration at {}: {}".format(
                    label,
                    canonical_path,
                    exc,
                )
            ) from exc
    elif not _readlink_matches(canonical_path, expected_link_target):
        raise CodexWranglerError(
            "Canonical {} path is no longer the expected staged link: {}".format(
                label,
                canonical_path,
            )
        )

    try:
        _rename_exchange(legacy_path, canonical_path)
    except (OSError, subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
        committed = (
            _readlink_matches(legacy_path, expected_link_target)
            and canonical_path.is_dir()
            and not canonical_path.is_symlink()
        )
        if committed:
            raise CodexWranglerError(
                "Atomic {} migration committed, but completion was interrupted; "
                "the real directory is at {} and the compatibility link is at "
                "{}.".format(label, canonical_path, legacy_path)
            ) from exc
        if staged_here and _readlink_matches(canonical_path, expected_link_target):
            try:
                canonical_path.unlink()
                fsync_directory(canonical_path.parent)
            except OSError as cleanup_error:
                raise CodexWranglerError(
                    "Atomic {} migration did not commit, and its staged link "
                    "could not be removed: {}. The legacy directory remains at "
                    "{}.".format(label, cleanup_error, legacy_path)
                ) from cleanup_error
        raise CodexWranglerError(
            "Atomic {} migration did not commit: {}. The legacy directory "
            "remains at {}.".format(label, exc or "interrupted", legacy_path)
        ) from exc

    if not (
        _readlink_matches(legacy_path, expected_link_target)
        and canonical_path.is_dir()
        and not canonical_path.is_symlink()
    ):
        raise CodexWranglerError(
            "Atomic {} migration returned an unexpected state; preserve both "
            "paths for inspection: {}, {}.".format(
                label,
                legacy_path,
                canonical_path,
            )
        )
    try:
        fsync_directory(legacy_path.parent)
        if canonical_path.parent != legacy_path.parent:
            fsync_directory(canonical_path.parent)
    except OSError as exc:
        raise CodexWranglerError(
            "Atomic {} migration committed, but directory durability could not "
            "be confirmed: {}. The real directory is at {} and the "
            "compatibility link is at {}.".format(
                label,
                exc,
                canonical_path,
                legacy_path,
            )
        ) from exc


def compatibility_links_for_uninstall(
    config: Config,
) -> Tuple[Tuple[Path, str], ...]:
    """Validate exact old/new compatibility links paired with removed trees."""

    links = []

    def add_component(
        configured_relative: str,
        legacy_path: Path,
        canonical_path: Path,
        canonical_relative: str,
        label: str,
    ) -> None:
        if configured_relative == canonical_relative:
            link_path = legacy_path
        elif configured_relative == str(legacy_path.relative_to(config.project_root)):
            link_path = canonical_path
        else:
            return
        if not _lexists(link_path):
            return
        if not _readlink_matches(link_path, canonical_relative):
            raise CodexWranglerError(
                "Refusing uninstall because the {} compatibility path is not "
                "the exact managed link: {}".format(label, link_path)
            )
        links.append((link_path, canonical_relative))

    root = config.project_root
    add_component(
        config.layout.local_dir_relative,
        root / LEGACY_LOCAL_DIR,
        root / DEFAULT_LOCAL_DIR,
        DEFAULT_LOCAL_DIR,
        "runtime",
    )
    if not config.shared_home:
        add_component(
            config.layout.codex_home_relative,
            root / LEGACY_HOME_DIR,
            root / DEFAULT_HOME_DIR,
            DEFAULT_HOME_DIR,
            "isolated HOME",
        )
    return tuple(links)


def remove_compatibility_links(
    links: Tuple[Tuple[Path, str], ...],
    *,
    dry_run: bool,
) -> None:
    """Remove links prevalidated as exact migration compatibility artifacts."""

    for link_path, expected_target in links:
        if not _readlink_matches(link_path, expected_target):
            raise CodexWranglerError(
                "Compatibility link changed during uninstall; refusing to "
                "remove it: {}".format(link_path)
            )
        action = "Would remove" if dry_run else "Removing"
        eprint(
            "[codex-wrangler] {} compatibility link: {}".format(
                action,
                link_path,
            )
        )
        if not dry_run:
            link_path.unlink()
            fsync_directory(link_path.parent)


def migration_observation_message(migration: LayoutMigration) -> str:
    """Return one concise pending-migration diagnostic."""

    components = []
    if migration.migrate_local:
        components.append("{} -> {}".format(LEGACY_LOCAL_DIR, DEFAULT_LOCAL_DIR))
    if migration.migrate_home:
        components.append("{} -> {}".format(LEGACY_HOME_DIR, DEFAULT_HOME_DIR))
    return "Pending canonical Codex layout migration: {}.".format(", ".join(components))


def apply_default_layout_migration(config: Config) -> None:
    """Apply a validated migration while holding stable project maintenance."""

    planned = config.layout_migration
    if planned is None:
        return
    if not atomic_exchange_supported():
        raise CodexWranglerError(
            "{} This host does not provide the required atomic directory/link "
            "exchange, so no path was changed.".format(
                migration_observation_message(planned)
            )
        )

    with MaintenanceLock(planned.state_layout):
        locked_home_mode = discover_pending_legacy_home_mode(
            planned.canonical_layout,
            planned.state_layout,
        )
        if locked_home_mode is not None and locked_home_mode != config.shared_home:
            raise CodexWranglerError(
                "Managed HOME-mode authority changed while canonical migration "
                "was waiting for its maintenance lock. Expected {}, now {}. "
                "No launcher or namespace path was changed.".format(
                    "shared" if config.shared_home else "isolated",
                    "shared" if locked_home_mode else "isolated",
                )
            )
        migration_home_mode = (
            locked_home_mode if locked_home_mode is not None else config.shared_home
        )
        current = build_default_layout_migration(
            planned.canonical_layout,
            planned.legacy_layout,
            shared_home=migration_home_mode,
        )
        if current is None:
            return

        validate_text_file_write(
            planned.canonical_layout.launcher_path,
            build_launcher_content(config),
            force=config.force,
            managed_markers=(SCRIPT_MARKER,),
        )
        validate_gitignore_block_update(planned.canonical_layout.gitignore_path)

        # Publish the complete ignore boundary before a runnable bridge or
        # canonical context path can appear. Validation above makes this write
        # deterministic before any namespace exchange.
        upsert_gitignore_block(
            planned.canonical_layout.gitignore_path,
            build_gitignore_block(planned.canonical_layout),
            dry_run=False,
        )
        planned.canonical_layout.launcher_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        write_text_file(
            planned.canonical_layout.launcher_path,
            build_launcher_content(config),
            force=config.force,
            dry_run=False,
            executable=True,
            managed_markers=(SCRIPT_MARKER,),
        )

        eprint("[codex-wrangler] {}".format(migration_observation_message(current)))
        if current.migrate_local:
            _exchange_directory_with_compatibility_link(
                current.legacy_layout.local_dir,
                current.canonical_layout.local_dir,
                DEFAULT_LOCAL_DIR,
                "managed Codex runtime",
            )
            eprint(
                "[codex-wrangler] Migrated managed runtime to {}".format(
                    current.canonical_layout.local_dir
                )
            )
        if current.migrate_home:
            _exchange_directory_with_compatibility_link(
                current.legacy_layout.codex_home_dir,
                current.canonical_layout.codex_home_dir,
                DEFAULT_HOME_DIR,
                "isolated Codex home",
            )
            eprint(
                "[codex-wrangler] Migrated isolated Codex home to {}".format(
                    current.canonical_layout.codex_home_dir
                )
            )

        # Re-discovery proves that every requested component now has either a
        # canonical real directory or its exact legacy compatibility link.
        remaining = build_default_layout_migration(
            current.canonical_layout,
            current.legacy_layout,
            shared_home=migration_home_mode,
        )
        if remaining is not None:
            raise CodexWranglerError(
                "Canonical layout migration stopped in a safe partial state: "
                "{}".format(migration_observation_message(remaining))
            )


def config_for_observed_layout(config: Config) -> Config:
    """Return a read-only configuration pointed at pre-migration authority."""

    migration = config.layout_migration
    if migration is None:
        return config
    return replace(config, layout=migration.state_layout)


__all__ = [
    "apply_default_layout_migration",
    "atomic_exchange_supported",
    "build_default_layout_migration",
    "compatibility_links_for_uninstall",
    "config_for_observed_layout",
    "discover_pending_legacy_home_mode",
    "legacy_layout_from_canonical",
    "migration_observation_message",
    "prove_legacy_isolated_home",
    "remove_compatibility_links",
    "select_default_state_layout",
]
