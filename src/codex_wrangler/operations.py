"""Operational behaviors for install, inspect, uninstall, and self-test."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import json
import os
import platform
from pathlib import Path
import stat
import sys
import tempfile
from typing import Any, Dict, Iterator, List, Mapping, Optional

from .config import managed_authority_token
from .constants import (
    ACTIVE_SLOT_FILE_NAME,
    ACTIVE_SYMLINK_NAME,
    CANDIDATE_SMOKE_TIMEOUT_SECONDS,
    GITIGNORE_BEGIN,
    GITIGNORE_END,
    LEGACY_HOME_DIR,
    LEGACY_LOCAL_DIR,
    README_MARKER,
    SCHEMA_VERSION,
    SCRIPT_NAME,
    SCRIPT_MARKER,
    SCRIPT_VERSION,
)
from .environment import collect_runtime_diagnostics
from .filesystem import (
    atomic_write_text,
    ensure_managed_directories,
    fsync_directory,
    maybe_remove_empty_parent,
    read_regular_text_if_present,
    require_regular_file_or_absent,
    require_safe_managed_path,
    remove_file_if_managed,
    remove_tree,
    upsert_gitignore_block,
    validate_gitignore_block_update,
    validate_text_file_write,
    write_metadata,
    write_text_file,
)
from .layout import (
    infer_installed_version_from_lockfile,
    infer_requested_version_from_package_json,
    metadata_looks_managed,
    read_existing_state,
    read_json_file,
)
from .migration import (
    compatibility_links_for_uninstall,
    migration_observation_message,
    remove_compatibility_links,
)
from .models import CodexWranglerError, Config, SelfTestResult
from .native_payload import validate_native_payload
from .repair import build_repair_plan
from .releases import (
    fetch_available_codex_versions,
    resolve_install_version,
    resolve_upgrade_version,
)
from .rendering import (
    build_available_versions_table,
    build_gitignore_block,
    build_install_summary,
    build_known_versions,
    build_launcher_content,
    build_maintenance_lock_gitignore_block,
    build_local_package_json,
    build_local_readme_content,
    build_metadata,
    local_package_json_looks_managed,
)
from .runtime import (
    detect_npm_binaries,
    ensure_command_exists,
    eprint,
    run_command,
    utc_now_iso,
)
from .slots import (
    MaintenanceLock,
    PointerCommitDurabilityError,
    PointerCommittedInterrupt,
    activate_candidate_slot,
    config_for_slot,
    config_for_unique_candidate,
    discover_active_runtime,
    fsync_candidate_tree,
    inactive_slot_name,
    legacy_runtime_is_usable,
    observe_active_runtime,
    observe_repair_runtime,
    promote_active_slot,
    read_slot_metadata,
    restore_slot_swap,
    slots_directory,
    validate_slot_container,
    write_slot_metadata,
)


def ensure_gitignore_block(config: Config) -> None:
    """Ensure the managed `.gitignore` block exists for local artifacts."""

    upsert_gitignore_block(
        config.layout.gitignore_path,
        build_gitignore_block(config.layout),
        dry_run=config.dry_run,
    )


def write_managed_supporting_files(config: Config) -> None:
    """Write the managed launcher, README, .gitignore block, and metadata."""

    # Publish ignore coverage before any managed directory or support artifact.
    # If a post-pointer projection fails, context cannot become newly visible to Git.
    ensure_gitignore_block(config)
    ensure_managed_directories(config)
    write_text_file(
        config.layout.launcher_path,
        build_launcher_content(config),
        force=config.force,
        dry_run=config.dry_run,
        executable=True,
        managed_markers=(SCRIPT_MARKER,),
    )
    write_text_file(
        config.layout.readme_path,
        build_local_readme_content(config),
        force=config.force,
        dry_run=config.dry_run,
        managed_markers=(README_MARKER,),
    )
    write_metadata(
        config.layout.metadata_path,
        build_metadata(config),
        dry_run=config.dry_run,
    )


def require_real_directory_target(
    project_root: Path,
    path: Path,
    label: str,
) -> None:
    """Require every occupied target component to remain a real directory."""

    try:
        relative_parts = path.relative_to(project_root).parts
    except ValueError as exc:
        raise CodexWranglerError(
            "{} escapes the project root: {}".format(label, path)
        ) from exc

    cursor = project_root
    for part in relative_parts:
        cursor /= part
        if not os.path.lexists(str(cursor)):
            continue
        if cursor.is_symlink() or not cursor.is_dir():
            raise CodexWranglerError(
                "Expected only real directory components for {}, but found "
                "another filesystem object: {}".format(label, cursor)
            )


def ensure_isolated_home_ready(
    config: Config,
    *,
    allow_create: bool = True,
) -> None:
    """Access-check and durably bind HOME before slot activation."""

    if config.shared_home:
        return
    home = config.layout.codex_home_dir
    if not allow_create and not os.path.lexists(str(home)):
        raise CodexWranglerError(
            "Repair will not recreate missing managed isolated HOME: {}. "
            "Restore the preserved directory before retrying, or use a "
            "separate explicit install only after confirming that no history "
            "should exist.".format(home)
        )
    try:
        home.mkdir(parents=True, exist_ok=True)
        require_real_directory_target(
            config.project_root,
            home,
            "managed Codex home directory",
        )
        required_access = os.R_OK | os.W_OK | os.X_OK
        if not os.access(home, required_access):
            raise CodexWranglerError(
                "Managed isolated HOME is not readable, writable, and "
                "searchable: {}".format(home)
            )

        # Sync each directory back through the project root so every mkdir
        # entry is durable before a slot record can make isolated mode active.
        cursor = home
        while True:
            fsync_directory(cursor)
            if cursor == config.project_root:
                break
            cursor = cursor.parent
    except CodexWranglerError:
        raise
    except OSError as exc:
        raise CodexWranglerError(
            "Cannot create or durably validate managed isolated HOME {} "
            "before slot activation: {}".format(home, exc)
        ) from exc


def preflight_managed_supporting_files(config: Config) -> None:
    """Prove every post-commit projection is publishable before mutation."""

    if config.operation == "install":
        require_safe_install_adoption(config)
    elif config.operation in ("upgrade", "update"):
        require_existing_managed_install(config)
        if config.operation == "upgrade":
            require_safe_isolated_home_adoption(config)
    validate_slot_container(config.layout)
    directory_targets = [
        (config.layout.launcher_path.parent, "managed launcher directory"),
        (config.layout.readme_path.parent, "managed README directory"),
    ]
    if not config.shared_home:
        directory_targets.append(
            (config.layout.codex_home_dir, "managed Codex home directory")
        )
    for path, label in directory_targets:
        require_real_directory_target(
            config.project_root,
            path,
            label,
        )

    validate_text_file_write(
        config.layout.local_package_json_path,
        build_local_package_json(config.codex_version),
        force=(config.force or config.operation == "repair"),
        managed_content_predicate=local_package_json_looks_managed,
    )
    require_regular_file_or_absent(
        config.layout.local_package_lock_path,
        "managed root package-lock projection",
    )
    require_regular_file_or_absent(
        config.layout.metadata_path,
        "managed root metadata projection",
    )
    validate_text_file_write(
        config.layout.launcher_path,
        build_launcher_content(config),
        force=config.force,
        managed_markers=(SCRIPT_MARKER,),
    )
    validate_text_file_write(
        config.layout.readme_path,
        build_local_readme_content(config),
        force=config.force,
        managed_markers=(README_MARKER,),
    )
    validate_gitignore_block_update(config.layout.gitignore_path)


def directory_has_entries(path: Path, label: str) -> bool:
    """Return whether one real directory contains any entry."""

    if not os.path.lexists(str(path)):
        return False
    if path.is_symlink() or not path.is_dir():
        raise CodexWranglerError(
            "{} must be a real directory or absent: {}".format(label, path)
        )
    try:
        next(path.iterdir())
    except StopIteration:
        return False
    except OSError as exc:
        raise CodexWranglerError(
            "Cannot inspect {} before managed adoption: {}".format(path, exc)
        ) from exc
    return True


def managed_install_home_modes(config: Config) -> List[bool]:
    """Return HOME modes from exact root and completed-slot authority."""

    validate_slot_container(config.layout)
    modes: List[bool] = []
    metadata = None
    if not config.layout.metadata_path.is_symlink():
        try:
            metadata = read_json_file(config.layout.metadata_path)
        except CodexWranglerError:
            metadata = None
    if metadata_looks_managed(metadata, config.layout):
        root_mode = metadata.get("shared_home")
        if isinstance(root_mode, bool):
            modes.append(root_mode)
    for slot_name in ("a", "b"):
        slot_metadata = read_slot_metadata(config.layout, slot_name)
        if slot_metadata is not None:
            modes.append(slot_metadata["shared_home"])
    return modes


def require_safe_isolated_home_adoption(config: Config) -> None:
    """Reject nonempty isolated HOME state without exact managed ownership."""

    if config.shared_home or config.force:
        return
    if not directory_has_entries(
        config.layout.codex_home_dir,
        "isolated HOME root",
    ):
        return
    if False not in managed_install_home_modes(config):
        raise CodexWranglerError(
            "The managed runtime has no exact ownership record for the "
            "nonempty isolated HOME root. Refusing to adopt that path "
            "without --force: {}".format(config.layout.codex_home_dir)
        )


def require_safe_install_adoption(config: Config) -> None:
    """Refuse to mint ownership over nonempty, unowned default roots."""

    local_has_entries = directory_has_entries(
        config.layout.local_dir,
        "managed local install root",
    )
    home_has_entries = False
    if not config.shared_home:
        home_has_entries = directory_has_entries(
            config.layout.codex_home_dir,
            "isolated HOME root",
        )
    if config.force:
        return

    home_modes = managed_install_home_modes(config)
    if home_modes:
        if not config.shared_home and home_has_entries and False not in home_modes:
            raise CodexWranglerError(
                "The managed runtime has no exact ownership record for the "
                "nonempty isolated HOME root. Refusing to adopt that path "
                "without --force: {}".format(config.layout.codex_home_dir)
            )
        return

    occupied = []
    if local_has_entries:
        occupied.append(str(config.layout.local_dir))
    if home_has_entries:
        occupied.append(str(config.layout.codex_home_dir))
    if occupied:
        raise CodexWranglerError(
            "Refusing install because pre-existing project-local path(s) "
            "contain data without exact managed ownership evidence: {}. "
            "Preserve those paths and use standalone --repair when applicable, "
            "or pass --force only to adopt them deliberately.".format(
                ", ".join(occupied)
            )
        )


def require_existing_managed_install(config: Config) -> None:
    """Reject update-like operations without exact managed ownership."""

    home_modes = managed_install_home_modes(config)
    if home_modes:
        return
    raise CodexWranglerError(
        "No exactly bound managed Codex install was found under {}. Use "
        "standalone codex-wrangler --repair {} when recoverable evidence "
        "exists, or install into empty project-local roots.".format(
            config.project_root,
            config.project_root,
        )
    )


DISPOSABLE_NPM_CACHE_RELATIVE_PATHS = (
    ("_codex-wrangler-maintenance",),
    ("_npx",),
    ("_cacache", "tmp"),
)


def require_safe_maintenance_environment_paths(
    maintenance_root: Path,
    directories: List[Path],
) -> None:
    """Reject linked or non-directory maintenance-workspace path components."""

    if os.path.lexists(str(maintenance_root)) and (
        maintenance_root.is_symlink() or not maintenance_root.is_dir()
    ):
        raise CodexWranglerError(
            "Maintenance workspace must be a real directory or absent: {}".format(
                maintenance_root
            )
        )
    for directory in directories:
        try:
            relative_parts = directory.relative_to(maintenance_root).parts
        except ValueError as exc:
            raise CodexWranglerError(
                "Maintenance state escapes its transaction workspace: {}".format(
                    directory
                )
            ) from exc
        cursor = maintenance_root
        for part in relative_parts:
            cursor /= part
            if os.path.lexists(str(cursor)) and (
                cursor.is_symlink() or not cursor.is_dir()
            ):
                raise CodexWranglerError(
                    "Maintenance state path must be a real directory or absent: "
                    "{}".format(cursor)
                )


def disposable_npm_cache_paths(cache_dir: Path) -> List[Path]:
    """Return exact legacy and npm scratch roots that may contain links."""

    return [
        cache_dir.joinpath(*relative_parts)
        for relative_parts in DISPOSABLE_NPM_CACHE_RELATIVE_PATHS
    ]


def require_safe_persistent_npm_cache(config: Config, cache_dir: Path) -> None:
    """Reject links and special files in persistent, non-scratch npm cache state."""

    require_safe_managed_path(
        cache_dir,
        config.project_root,
        "managed persistent npm cache",
    )
    if not os.path.lexists(str(cache_dir)):
        return
    if cache_dir.is_symlink() or not cache_dir.is_dir():
        raise CodexWranglerError(
            "Managed persistent npm cache must be a real directory: {}".format(
                cache_dir
            )
        )

    disposable_paths = set(disposable_npm_cache_paths(cache_dir))
    pending = [cache_dir]
    while pending:
        directory = pending.pop()
        require_safe_managed_path(
            directory,
            config.project_root,
            "managed persistent npm cache directory",
        )
        try:
            with os.scandir(directory) as iterator:
                entries = list(iterator)
        except OSError as exc:
            raise CodexWranglerError(
                "Failed to inspect managed persistent npm cache {}: {}".format(
                    directory,
                    exc,
                )
            ) from exc
        for entry in entries:
            entry_path = Path(entry.path)
            try:
                mode = entry.stat(follow_symlinks=False).st_mode
            except OSError as exc:
                raise CodexWranglerError(
                    "Failed to inspect managed persistent npm cache entry {}: "
                    "{}".format(entry_path, exc)
                ) from exc
            if entry_path in disposable_paths:
                if not stat.S_ISDIR(mode):
                    raise CodexWranglerError(
                        "Disposable npm cache scratch root must be a real directory "
                        "or absent: {}".format(entry_path)
                    )
                # npm and older wrangler releases legitimately create links inside
                # these exact scratch roots. They are purged without following
                # descendants before any managed npm child starts.
                continue
            if stat.S_ISLNK(mode):
                raise CodexWranglerError(
                    "Managed persistent npm cache may not contain symbolic links: "
                    "{}".format(entry_path)
                )
            if stat.S_ISDIR(mode):
                pending.append(entry_path)
            elif not stat.S_ISREG(mode):
                raise CodexWranglerError(
                    "Managed persistent npm cache may contain only regular files "
                    "and real directories; found a special filesystem object: "
                    "{}".format(entry_path)
                )


def purge_disposable_npm_cache_scratch(
    config: Config,
    cache_dir: Path,
    *,
    dry_run: bool,
) -> None:
    """Safely remove exact scratch roots, then revalidate persistent cache state."""

    require_safe_persistent_npm_cache(config, cache_dir)
    labels = (
        "legacy wrangler maintenance scratch",
        "managed local npx scratch cache",
        "managed local npm cache temp files",
    )
    for scratch_path, label in zip(disposable_npm_cache_paths(cache_dir), labels):
        remove_tree(
            scratch_path,
            label=label,
            project_root=config.project_root,
            dry_run=dry_run,
        )
    require_safe_persistent_npm_cache(config, cache_dir)


def _maintenance_environment_paths(
    env: Mapping[str, str],
) -> tuple[Path, Path, List[Path]]:
    """Validate and return cache, workspace, and required maintenance directories."""

    selected_cache = Path(os.path.abspath(env["NPM_CONFIG_CACHE"]))
    maintenance_home = Path(os.path.abspath(env["HOME"]))
    if maintenance_home.name != "home":
        raise CodexWranglerError(
            "Maintenance HOME must be the workspace home directory: {}".format(
                maintenance_home
            )
        )
    maintenance_root = maintenance_home.parent
    try:
        maintenance_root.relative_to(selected_cache)
    except ValueError:
        pass
    else:
        raise CodexWranglerError(
            "Disposable maintenance workspace must remain outside the persistent "
            "npm cache: {}".format(maintenance_root)
        )
    try:
        selected_cache.relative_to(maintenance_root)
    except ValueError:
        pass
    else:
        raise CodexWranglerError(
            "Persistent npm cache must remain outside the disposable maintenance "
            "workspace: {}".format(selected_cache)
        )

    expected_paths = {
        "HOME": maintenance_home,
        "CODEX_HOME": maintenance_home / ".codex",
        "XDG_CONFIG_HOME": maintenance_home / ".config",
        "XDG_CACHE_HOME": maintenance_home / ".cache",
        "XDG_STATE_HOME": maintenance_home / ".local" / "state",
        "XDG_DATA_HOME": maintenance_home / ".local" / "share",
        "TMPDIR": maintenance_home / "tmp",
        "TMP": maintenance_home / "tmp",
        "TEMP": maintenance_home / "tmp",
    }
    for name, expected in expected_paths.items():
        if Path(os.path.abspath(env.get(name, ""))) != expected:
            raise CodexWranglerError(
                "Maintenance environment {} must resolve beneath transaction "
                "workspace {}: {}".format(name, maintenance_root, expected)
            )
    directories = list(
        dict.fromkeys(
            [
                maintenance_root,
                maintenance_home,
                maintenance_home / ".local",
                *expected_paths.values(),
            ]
        )
    )
    require_safe_maintenance_environment_paths(maintenance_root, directories)
    return selected_cache, maintenance_root, directories


def materialize_maintenance_environment(env: Mapping[str, str]) -> None:
    """Create one transaction-private maintenance HOME before a managed child."""

    selected_cache, maintenance_root, directories = _maintenance_environment_paths(env)
    if os.path.lexists(str(selected_cache)) and (
        selected_cache.is_symlink() or not selected_cache.is_dir()
    ):
        raise CodexWranglerError(
            "Selected npm cache must be a real directory or absent: {}".format(
                selected_cache
            )
        )
    try:
        selected_cache.mkdir(parents=True, exist_ok=True)
        for directory in directories:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(directory, 0o700)
    except OSError as exc:
        raise CodexWranglerError(
            "Failed to prepare disposable maintenance state in {}: {}".format(
                maintenance_root,
                exc,
            )
        ) from exc
    require_safe_maintenance_environment_paths(maintenance_root, directories)


def overlay_controlled_environment(
    env: Dict[str, str],
    values: Mapping[str, str],
) -> None:
    """Replace controlled variables without lowercase npm aliases surviving."""

    controlled = {name.upper() for name in values}
    for inherited_name in tuple(env):
        if inherited_name.upper() in controlled:
            del env[inherited_name]
    env.update(values)


def npm_local_environment(
    config: Config,
    *,
    cache_dir: Optional[Path] = None,
    maintenance_root: Optional[Path] = None,
) -> Dict[str, str]:
    """Return bounded npm configuration with cache and scratch kept disjoint."""

    persistent_cache = cache_dir is None
    if persistent_cache:
        validate_slot_container(config.layout)
    selected_cache = Path(
        os.path.abspath(
            str(
                cache_dir
                if cache_dir is not None
                else config.layout.local_dir / ".npm-cache"
            )
        )
    )
    if persistent_cache:
        require_safe_persistent_npm_cache(config, selected_cache)
        maintenance_container = config.layout.local_dir / ".maintenance"
        selected_maintenance_root = Path(
            os.path.abspath(
                str(
                    maintenance_root
                    if maintenance_root is not None
                    else maintenance_container / ".preview"
                )
            )
        )
        if selected_maintenance_root.parent != maintenance_container:
            raise CodexWranglerError(
                "Persistent-cache maintenance workspace must be one direct child "
                "of {}: {}".format(
                    maintenance_container,
                    selected_maintenance_root,
                )
            )
        require_safe_managed_path(
            selected_maintenance_root,
            config.project_root,
            "disposable maintenance workspace",
        )
    else:
        selected_maintenance_root = Path(
            os.path.abspath(
                str(
                    maintenance_root
                    if maintenance_root is not None
                    else Path(tempfile.gettempdir())
                    / "codex-wrangler-maintenance-preview"
                )
            )
        )

    maintenance_home = selected_maintenance_root / "home"
    maintenance_directories = [
        selected_maintenance_root,
        maintenance_home,
        maintenance_home / ".codex",
        maintenance_home / ".config",
        maintenance_home / ".cache",
        maintenance_home / ".local",
        maintenance_home / ".local" / "state",
        maintenance_home / ".local" / "share",
        maintenance_home / "tmp",
    ]
    require_safe_maintenance_environment_paths(
        selected_maintenance_root,
        maintenance_directories,
    )
    env = dict(os.environ)
    for name in tuple(env):
        if name.startswith("XDG_"):
            del env[name]
    overall_timeout_ms = max(1, config.npm_timeout_seconds * 1000)
    fetch_timeout_ms = max(1, min(120000, overall_timeout_ms // 2))
    retry_min_timeout_ms = max(1, min(10000, overall_timeout_ms // 20))
    retry_max_timeout_ms = max(
        retry_min_timeout_ms,
        min(30000, overall_timeout_ms // 10),
    )
    overlay_controlled_environment(
        env,
        {
            "NPM_CONFIG_CACHE": str(selected_cache),
            "NPM_CONFIG_FETCH_RETRIES": "2",
            "NPM_CONFIG_FETCH_RETRY_FACTOR": "2",
            "NPM_CONFIG_FETCH_RETRY_MINTIMEOUT": str(retry_min_timeout_ms),
            "NPM_CONFIG_FETCH_RETRY_MAXTIMEOUT": str(retry_max_timeout_ms),
            "NPM_CONFIG_FETCH_TIMEOUT": str(fetch_timeout_ms),
            "HOME": str(maintenance_home),
            "CODEX_HOME": str(maintenance_home / ".codex"),
            "XDG_CONFIG_HOME": str(maintenance_home / ".config"),
            "XDG_CACHE_HOME": str(maintenance_home / ".cache"),
            "XDG_STATE_HOME": str(maintenance_home / ".local" / "state"),
            "XDG_DATA_HOME": str(maintenance_home / ".local" / "share"),
            "TMPDIR": str(maintenance_home / "tmp"),
            "TMP": str(maintenance_home / "tmp"),
            "TEMP": str(maintenance_home / "tmp"),
        },
    )
    return env


@contextmanager
def npm_maintenance_environment(
    config: Config,
    *,
    cache_dir: Optional[Path] = None,
) -> Iterator[Dict[str, str]]:
    """Yield a fresh materialized HOME disjoint from the selected npm cache."""

    if cache_dir is None:
        selected_cache = config.layout.local_dir / ".npm-cache"
        validate_slot_container(config.layout)
        purge_disposable_npm_cache_scratch(
            config,
            selected_cache,
            dry_run=False,
        )
        selected_cache.mkdir(parents=True, exist_ok=True)
        require_safe_persistent_npm_cache(config, selected_cache)
        maintenance_container = config.layout.local_dir / ".maintenance"
        require_safe_managed_path(
            maintenance_container,
            config.project_root,
            "managed maintenance workspace container",
        )
        if os.path.lexists(str(maintenance_container)) and (
            maintenance_container.is_symlink() or not maintenance_container.is_dir()
        ):
            raise CodexWranglerError(
                "Managed maintenance workspace container must be a real directory "
                "or absent: {}".format(maintenance_container)
            )
        maintenance_container.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(maintenance_container, 0o700)
        with tempfile.TemporaryDirectory(
            prefix=".run-",
            dir=str(maintenance_container),
        ) as maintenance_name:
            env = npm_local_environment(
                config,
                maintenance_root=Path(maintenance_name),
            )
            materialize_maintenance_environment(env)
            yield env
        return

    selected_cache = Path(os.path.abspath(str(cache_dir)))
    with tempfile.TemporaryDirectory(
        prefix="codex-wrangler-maintenance-"
    ) as maintenance_name:
        env = npm_local_environment(
            config,
            cache_dir=selected_cache,
            maintenance_root=Path(maintenance_name),
        )
        materialize_maintenance_environment(env)
        yield env


@contextmanager
def npm_lookup_environment(config: Config) -> Iterator[Dict[str, str]]:
    """Yield disposable lookup cache and HOME state without target writes."""

    with tempfile.TemporaryDirectory(prefix="codex-wrangler-lookup-") as cache_name:
        with npm_maintenance_environment(
            config,
            cache_dir=Path(cache_name),
        ) as env:
            yield env


def npm_install_environment(
    config: Config,
    *,
    base_env: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    """Add install-only npm policy to a bounded maintenance environment."""

    env = dict(base_env) if base_env is not None else npm_local_environment(config)
    overlay_controlled_environment(
        env,
        {
            "NPM_CONFIG_AUDIT": "false",
            "NPM_CONFIG_ENGINE_STRICT": "true",
            "NPM_CONFIG_FOREGROUND_SCRIPTS": "true",
            "NPM_CONFIG_FUND": "false",
            "NPM_CONFIG_LOGLEVEL": config.npm_install_loglevel,
            "NPM_CONFIG_PROGRESS": "false",
            "NPM_CONFIG_UPDATE_NOTIFIER": "false",
        },
    )
    return env


def local_codex_bin_path(config: Config) -> Path:
    """Return the expected local Codex executable path for this platform."""

    binary_name = "codex.cmd" if os.name == "nt" else "codex"
    return config.layout.local_node_modules_dir / ".bin" / binary_name


def managed_npm_install_command(config: Config, npm_name: str) -> List[str]:
    """Return the npm install command for the managed Codex package tree."""

    return [
        npm_name,
        "install",
        "--prefix",
        str(config.layout.local_dir),
        "--no-audit",
        "--no-fund",
        "--foreground-scripts",
        "--loglevel={}".format(config.npm_install_loglevel),
        "--progress=false",
    ]


def local_codex_package_json_path(config: Config) -> Path:
    """Return the managed `@openai/codex` package manifest path."""

    return config.layout.local_node_modules_dir / "@openai" / "codex" / "package.json"


def expected_codex_platform_details() -> Optional[tuple[str, str, str]]:
    """Return the package name, target triple, and executable suffix for this host."""

    raw_machine = platform.machine().lower()
    if raw_machine in ("x86_64", "amd64"):
        arch = "x64"
        target_arch = "x86_64"
    elif raw_machine in ("aarch64", "arm64"):
        arch = "arm64"
        target_arch = "aarch64"
    else:
        return None

    if sys.platform.startswith("linux"):
        family = "linux"
        target_suffix = "unknown-linux-musl"
        executable_suffix = ""
    elif sys.platform == "darwin":
        family = "darwin"
        target_suffix = "apple-darwin"
        executable_suffix = ""
    elif sys.platform in ("win32", "cygwin", "msys"):
        family = "win32"
        target_suffix = "pc-windows-msvc"
        executable_suffix = ".exe"
    else:
        return None
    return (
        "@openai/codex-{}-{}".format(family, arch),
        "{}-{}".format(target_arch, target_suffix),
        executable_suffix,
    )


def expected_codex_platform_package_name() -> Optional[str]:
    """Return the expected `@openai/codex-*` package for this host."""

    details = expected_codex_platform_details()
    return details[0] if details is not None else None


def local_codex_platform_package_json_path(
    config: Config,
    package_name: Optional[str],
) -> Optional[Path]:
    """Return the expected platform package manifest path when known."""

    if package_name is None:
        return None
    scope, name = package_name.split("/", 1)
    return config.layout.local_node_modules_dir / scope / name / "package.json"


def local_codex_native_binary_paths(config: Config) -> List[Path]:
    """Return required and present optional native Codex executable paths."""

    details = expected_codex_platform_details()
    if details is None:
        return []
    package_name, target_triple, executable_suffix = details
    package_json = local_codex_platform_package_json_path(config, package_name)
    if package_json is None:
        return []
    binary_dir = package_json.parent / "vendor" / target_triple / "bin"
    paths = [binary_dir / "codex{}".format(executable_suffix)]
    code_mode_host = binary_dir / "codex-code-mode-host{}".format(executable_suffix)
    if code_mode_host.exists():
        paths.append(code_mode_host)
    return paths


def inspect_local_codex_native_payloads(config: Config) -> List[Dict[str, Any]]:
    """Return structural validation records for expected native executables."""

    records: List[Dict[str, Any]] = []
    for path in local_codex_native_binary_paths(config):
        record: Dict[str, Any] = {
            "path": str(path),
            "exists": path.exists(),
            "valid": False,
            "format": None,
            "file_size": None,
            "declared_extent": None,
            "error": None,
        }
        try:
            record["file_size"] = path.stat().st_size
        except OSError:
            pass
        try:
            validation = validate_native_payload(path)
        except CodexWranglerError as exc:
            record["error"] = str(exc)
        else:
            record.update(
                {
                    "valid": True,
                    "format": validation.format_name,
                    "file_size": validation.file_size,
                    "declared_extent": validation.declared_extent,
                }
            )
        records.append(record)
    return records


def read_package_version(package_json_path: Path) -> Optional[str]:
    """Read a regular package.json version, or return None when unavailable."""

    try:
        payload = read_json_file(package_json_path)
    except CodexWranglerError:
        return None
    if payload is None:
        return None
    version = payload.get("version")
    return version if isinstance(version, str) and version else None


def infer_requested_version_with_error(
    package_json_path: Path,
) -> tuple[Optional[str], Optional[str]]:
    """Infer requested Codex version and preserve parse errors for inspection."""

    try:
        return infer_requested_version_from_package_json(package_json_path), None
    except CodexWranglerError as exc:
        return None, str(exc)


def infer_lockfile_version_with_error(
    lockfile_path: Path,
) -> tuple[Optional[str], Optional[str]]:
    """Infer installed Codex version and preserve parse errors for inspection."""

    try:
        return infer_installed_version_from_lockfile(lockfile_path), None
    except CodexWranglerError as exc:
        return None, str(exc)


def read_metadata_with_error(
    metadata_path: Path,
) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Read managed metadata and preserve parse errors for inspection."""

    try:
        return read_json_file(metadata_path), None
    except CodexWranglerError as exc:
        return None, str(exc)


def require_uninstall_ownership(config: Config) -> Dict[str, Any]:
    """Return exact managed metadata or refuse destructive uninstall."""

    metadata_path = config.layout.metadata_path
    if metadata_path.is_symlink():
        raise CodexWranglerError(
            "Refusing uninstall because managed metadata may not be a "
            "symbolic link: {}".format(metadata_path)
        )
    metadata = read_json_file(metadata_path)
    if not metadata_looks_managed(metadata, config.layout):
        raise CodexWranglerError(
            "Managed metadata at {} does not bind this exact project and "
            "layout. Refusing uninstall without --force because ownership "
            "cannot be proven.".format(metadata_path)
        )
    recorded_shared_home = metadata.get("shared_home")
    if (
        not isinstance(recorded_shared_home, bool)
        or recorded_shared_home != config.shared_home
    ):
        raise CodexWranglerError(
            "Managed metadata HOME mode at {} does not match the locked "
            "installation state. Refusing uninstall without --force.".format(
                metadata_path
            )
        )
    return metadata


def require_candidate_path(
    config: Config,
    path: Path,
    label: str,
    *,
    allow_final_symlink: bool = False,
) -> Path:
    """Return a candidate path only when every resolution stays in its prefix."""

    prefix = config.layout.local_dir
    if prefix.is_symlink() or not prefix.is_dir():
        raise CodexWranglerError(
            "Candidate prefix is not a real directory: {}".format(prefix)
        )
    try:
        relative_parts = path.relative_to(prefix).parts
    except ValueError as exc:
        raise CodexWranglerError(
            "Candidate {} is outside its npm prefix: {}".format(label, path)
        ) from exc
    cursor = prefix
    for index, part in enumerate(relative_parts):
        cursor /= part
        final_component = index == len(relative_parts) - 1
        if cursor.is_symlink() and not (allow_final_symlink and final_component):
            raise CodexWranglerError(
                "Candidate {} may not use a symbolic-link path component: {}".format(
                    label,
                    cursor,
                )
            )
    if not os.path.lexists(str(path)):
        raise CodexWranglerError("Candidate {} is missing: {}".format(label, path))
    if path.is_symlink() and not allow_final_symlink:
        raise CodexWranglerError(
            "Candidate {} may not be a symbolic link: {}".format(label, path)
        )
    try:
        canonical_prefix = prefix.resolve(strict=True)
        canonical_path = path.resolve(strict=True)
        canonical_path.relative_to(canonical_prefix)
    except (OSError, ValueError) as exc:
        raise CodexWranglerError(
            "Candidate {} resolves outside its npm prefix or cannot be "
            "resolved: {}".format(label, path)
        ) from exc
    if not canonical_path.is_file():
        raise CodexWranglerError(
            "Candidate {} is not a regular file: {}".format(label, path)
        )
    return canonical_path


def verify_local_codex_binary(
    config: Config,
    *,
    env: Optional[Mapping[str, str]] = None,
) -> None:
    """Prove one candidate contains the selected exact working Codex version."""

    local_codex_bin = local_codex_bin_path(config)
    require_candidate_path(
        config,
        local_codex_bin,
        "Codex npm shim",
        allow_final_symlink=True,
    )
    if os.name != "nt" and not os.access(local_codex_bin, os.X_OK):
        raise CodexWranglerError(
            "Local Codex executable is not executable: {}".format(local_codex_bin)
        )

    expected_platform_package = expected_codex_platform_package_name()
    platform_manifest = local_codex_platform_package_json_path(
        config,
        expected_platform_package,
    )
    if platform_manifest is not None:
        if not os.path.lexists(str(platform_manifest)):
            raise CodexWranglerError(
                "Candidate is missing the expected {} package manifest: {}".format(
                    expected_platform_package,
                    platform_manifest,
                )
            )
        require_candidate_path(
            config,
            platform_manifest,
            "{} package manifest".format(expected_platform_package),
        )
        try:
            platform_payload = read_json_file(platform_manifest)
        except CodexWranglerError as exc:
            raise CodexWranglerError(
                "Candidate {} package manifest is invalid: {}".format(
                    expected_platform_package,
                    exc,
                )
            ) from exc
        platform_version = (
            platform_payload.get("version") if platform_payload is not None else None
        )
        platform_suffix = expected_platform_package.rsplit("codex-", 1)[-1]
        expected_platform_version = "{}-{}".format(
            config.codex_version,
            platform_suffix,
        )
        if platform_version != expected_platform_version:
            raise CodexWranglerError(
                "Candidate {} package manifest records version {}, expected {}.".format(
                    expected_platform_package,
                    platform_version or "<missing>",
                    expected_platform_version,
                )
            )

    for native_binary in local_codex_native_binary_paths(config):
        require_candidate_path(config, native_binary, "native Codex executable")
        validate_native_payload(native_binary)

    require_candidate_path(
        config,
        config.layout.local_package_json_path,
        "package.json",
    )
    requested_version = infer_requested_version_from_package_json(
        config.layout.local_package_json_path
    )
    if requested_version != config.codex_version:
        raise CodexWranglerError(
            "Candidate package.json requests @openai/codex {}, expected {}.".format(
                requested_version or "<missing>",
                config.codex_version,
            )
        )
    require_candidate_path(
        config,
        config.layout.local_package_lock_path,
        "package-lock.json",
    )
    locked_version = infer_installed_version_from_lockfile(
        config.layout.local_package_lock_path
    )
    if locked_version != config.codex_version:
        raise CodexWranglerError(
            "Candidate package-lock.json records @openai/codex {}, expected {}.".format(
                locked_version or "<missing>",
                config.codex_version,
            )
        )
    installed_manifest = local_codex_package_json_path(config)
    require_candidate_path(
        config,
        installed_manifest,
        "@openai/codex package manifest",
    )
    installed_version = read_package_version(installed_manifest)
    if installed_version != config.codex_version:
        raise CodexWranglerError(
            "Candidate node_modules contains @openai/codex {}, expected {}.".format(
                installed_version or "<missing>",
                config.codex_version,
            )
        )

    verification_env = dict(env) if env is not None else npm_local_environment(config)
    materialize_maintenance_environment(verification_env)
    version_result = run_command(
        [str(local_codex_bin), "--version"],
        cwd=str(config.project_root),
        capture_output=True,
        env=verification_env,
        timeout_seconds=CANDIDATE_SMOKE_TIMEOUT_SECONDS,
    )
    version_text = (
        (version_result.stdout or "") + (version_result.stderr or "")
    ).strip()
    expected_version_text = "codex-cli {}".format(config.codex_version)
    if version_text != expected_version_text:
        raise CodexWranglerError(
            "Local Codex executable reported {!r}, expected {!r}.".format(
                version_text or "<no output>",
                expected_version_text,
            )
        )


def remove_managed_install_file(config: Config, path: Path, label: str) -> bool:
    """Remove one managed npm artifact file after path safety checks."""

    if not path.exists():
        return False
    if not path.is_file():
        raise CodexWranglerError(
            "Expected {} to be a file, but found something else: {}".format(
                label,
                path,
            )
        )
    require_safe_managed_path(path, config.project_root, label)
    action = "Would remove" if config.dry_run else "Removing"
    eprint("[codex-wrangler] {} {}: {}".format(action, label, path))
    if config.dry_run:
        return True
    path.unlink()
    return True


def prepare_candidate_install(config: Config, candidate_config: Config) -> None:
    """Prepare one new unique prefix and bounded shared-cache scratch data."""

    validate_slot_container(config.layout)
    candidate_prefix = candidate_config.layout.local_dir
    if (
        candidate_prefix.parent != config.layout.local_dir
        or not candidate_prefix.name.startswith(".candidate-")
    ):
        raise CodexWranglerError(
            "Candidate prefix is not transaction-unique beneath the managed "
            "local root: {}".format(candidate_prefix)
        )
    if os.path.lexists(str(candidate_prefix)):
        raise CodexWranglerError(
            "Transaction-unique candidate path already exists: {}".format(
                candidate_prefix
            )
        )
    purge_disposable_npm_cache_scratch(
        config,
        config.layout.local_dir / ".npm-cache",
        dry_run=config.dry_run,
    )


def require_observed_authority_unchanged(config: Config) -> None:
    """Abort a mutation when managed authority changed before lock acquisition."""

    if config.observed_authority_token is None:
        return
    try:
        active = discover_active_runtime(config.layout)
    except CodexWranglerError:
        active_slot = None
        active_pointer_kind = None
    else:
        active_slot = active.slot_name
        active_pointer_kind = active.pointer_kind
    existing = read_existing_state(config.layout)
    current_token = managed_authority_token(
        existing,
        active_slot,
        active_pointer_kind,
    )
    if current_token != config.observed_authority_token:
        raise CodexWranglerError(
            "Managed authority changed while this operation was preparing. "
            "No candidate installation began and no active runtime was changed; "
            "rerun the command so explicit options are applied to current state."
        )


def reload_authoritative_config(
    config: Config,
    *,
    preserve_reasonable_permissions: bool = False,
) -> Config:
    """Reload active managed state after acquiring the maintenance lock."""

    active = discover_active_runtime(config.layout)
    existing = read_existing_state(config.layout)
    return replace(
        config,
        codex_selector=(existing.requested_codex_selector or config.codex_selector),
        codex_channel=(existing.codex_channel or config.codex_channel),
        codex_version=(existing.pinned_codex_version or config.codex_version),
        shared_home=(
            existing.shared_home
            if existing.shared_home is not None
            else config.shared_home
        ),
        reasonable_permissions_enabled=(
            config.reasonable_permissions_enabled
            if preserve_reasonable_permissions
            else (
                existing.reasonable_permissions_enabled
                if existing.reasonable_permissions_enabled is not None
                else config.reasonable_permissions_enabled
            )
        ),
        available_versions=dict(existing.available_versions),
        available_versions_updated_at=existing.available_versions_updated_at,
        active_slot=active.slot_name,
        active_pointer_kind=active.pointer_kind,
    )


def publish_installed_state(config: Config, candidate_config: Config) -> None:
    """Publish compatibility projections and generated files after promotion."""

    write_text_file(
        config.layout.local_package_json_path,
        build_local_package_json(config.codex_version),
        force=(config.force or config.operation == "repair"),
        dry_run=config.dry_run,
        managed_content_predicate=local_package_json_looks_managed,
    )
    try:
        lockfile_content = candidate_config.layout.local_package_lock_path.read_text(
            encoding="utf-8"
        )
    except OSError as exc:
        raise CodexWranglerError(
            "Failed to read validated candidate package-lock.json: {}".format(exc)
        ) from exc
    atomic_write_text(config.layout.local_package_lock_path, lockfile_content)
    write_managed_supporting_files(config)


def observe_candidate_pointer_commit(
    config: Config,
    candidate_slot: str,
) -> tuple[Optional[bool], Optional[str]]:
    """Report whether the active pointer committed, preserving uncertainty.

    An asynchronous interrupt may arrive after ``os.replace`` changed the
    pointer but before the promotion helper returned. Callers must not move a
    selected fixed slot away merely because their local assignment did not
    finish.
    """

    try:
        observed = observe_repair_runtime(config.layout)
    except CodexWranglerError as exc:
        return None, str(exc)
    return observed.slot_name == candidate_slot, None


def publish_projection_state(config: Config) -> None:
    """Commit active-slot configuration, then refresh root support projections."""

    active = discover_active_runtime(config.layout)
    slot_record_committed = False
    if active.slot_name is not None:
        write_slot_metadata(config, active.slot_name)
        slot_record_committed = not config.dry_run
    try:
        write_managed_supporting_files(config)
    except KeyboardInterrupt as exc:
        if slot_record_committed:
            raise PointerCommittedInterrupt(
                "Interrupted after active-slot configuration committed; the runtime "
                "remains active, but one or more root projections may need to be "
                "refreshed."
            ) from exc
        raise
    except Exception as exc:
        if slot_record_committed:
            raise CodexWranglerError(
                "Active-slot configuration committed, but refreshing generated "
                "support files failed: {}. The runtime remains active; rerun the "
                "same maintenance operation to reconcile the projections.".format(exc)
            ) from exc
        raise


def install_like_operation(config: Config) -> int:
    """Shared implementation for install and upgrade-style operations."""

    if config.operation == "install":
        require_safe_install_adoption(config)
    elif config.operation == "upgrade":
        require_existing_managed_install(config)
        require_safe_isolated_home_adoption(config)

    if config.reconfigure_only:
        resolved_config = config
    else:
        if config.operation == "repair":
            preliminary_plan = build_repair_plan(config.layout)
            if config.codex_version != preliminary_plan.codex_version:
                raise CodexWranglerError(
                    "Repair configuration selected {}, but target evidence "
                    "selects {}. Nothing was removed; rebuild configuration "
                    "from the target before retrying.".format(
                        config.codex_version,
                        preliminary_plan.codex_version,
                    )
                )
        npm_name, _ = detect_npm_binaries()
        ensure_command_exists("node")
        ensure_command_exists(npm_name)

        try:
            if config.operation == "upgrade":
                resolved_config = resolve_upgrade_version(config)
            elif config.operation == "repair":
                resolved_config = config
            else:
                with npm_lookup_environment(config) as lookup_env:
                    resolved_config = resolve_install_version(
                        config,
                        npm_name,
                        env=lookup_env,
                        timeout_seconds=config.npm_timeout_seconds,
                    )
        except KeyboardInterrupt:
            eprint(
                "[codex-wrangler] Version resolution was interrupted before "
                "candidate preparation; the active runtime remains untouched."
            )
            raise
        except CodexWranglerError as exc:
            raise CodexWranglerError(
                "{} No candidate installation began; the active runtime remains "
                "untouched.".format(exc)
            ) from exc

    eprint(
        "[codex-wrangler] Target project root: {}".format(resolved_config.project_root)
    )
    eprint("[codex-wrangler] Operation: {}".format(resolved_config.operation))
    eprint("[codex-wrangler] Codex selector: {}".format(resolved_config.codex_selector))
    eprint("[codex-wrangler] Codex version: {}".format(resolved_config.codex_version))
    eprint("[codex-wrangler] Version source: {}".format(resolved_config.version_source))
    eprint(
        "[codex-wrangler] HOME isolation: {}".format(
            "shared user HOME"
            if resolved_config.shared_home
            else "project-local isolated HOME"
        )
    )
    eprint(
        "[codex-wrangler] Reasonable permissions default: {}".format(
            "enabled" if resolved_config.reasonable_permissions_enabled else "disabled"
        )
    )

    summary_config = resolved_config
    if resolved_config.reconfigure_only:
        if resolved_config.dry_run:
            published_config = reload_authoritative_config(
                resolved_config,
                preserve_reasonable_permissions=True,
            )
            summary_config = published_config
            preflight_managed_supporting_files(published_config)
            publish_projection_state(published_config)
            eprint(
                "[codex-wrangler] Skipping npm install because this command only "
                "updates managed launcher state."
            )
        else:
            with MaintenanceLock(resolved_config.layout):
                published_config = reload_authoritative_config(
                    resolved_config,
                    preserve_reasonable_permissions=True,
                )
                summary_config = published_config
                preflight_managed_supporting_files(published_config)
                publish_projection_state(published_config)
                eprint(
                    "[codex-wrangler] Skipping npm install because this command only "
                    "updates managed launcher state."
                )
    elif resolved_config.dry_run:
        repairing_invalid_pointer = False
        try:
            active = discover_active_runtime(resolved_config.layout)
        except CodexWranglerError:
            if resolved_config.operation != "repair":
                raise
            active = observe_repair_runtime(resolved_config.layout)
            repairing_invalid_pointer = True
        if repairing_invalid_pointer and active.slot_name is None:
            candidate_slot = (
                "b"
                if read_slot_metadata(resolved_config.layout, "a") is not None
                else "a"
            )
        else:
            candidate_slot = inactive_slot_name(active)
        preflight_managed_supporting_files(resolved_config)
        candidate_config = config_for_unique_candidate(resolved_config)
        prepare_candidate_install(resolved_config, candidate_config)
        eprint(
            "[codex-wrangler] Would preserve active runtime: {}".format(
                active.prefix or "<none>"
            )
        )
        eprint(
            "[codex-wrangler] Would prepare unique candidate for inactive slot "
            "{}: {}".format(candidate_slot, candidate_config.layout.local_dir)
        )
        eprint(
            "[codex-wrangler] Would set: NPM_CONFIG_CACHE={}".format(
                npm_local_environment(resolved_config)["NPM_CONFIG_CACHE"]
            )
        )
        eprint(
            "[codex-wrangler] Would run: {}".format(
                " ".join(managed_npm_install_command(candidate_config, npm_name))
            )
        )
    elif resolved_config.skip_install:
        eprint(
            "[codex-wrangler] Skipping npm install by request; active runtime and "
            "published managed files were not changed."
        )
    else:
        with MaintenanceLock(resolved_config.layout):
            require_observed_authority_unchanged(resolved_config)
            if resolved_config.operation == "repair":
                repair_plan = build_repair_plan(resolved_config.layout)
                if resolved_config.codex_version != repair_plan.codex_version:
                    raise CodexWranglerError(
                        "Repair configuration selected {}, but locked target "
                        "evidence now selects {}. Nothing was removed; rebuild "
                        "configuration from the target before retrying.".format(
                            resolved_config.codex_version,
                            repair_plan.codex_version,
                        )
                    )
            repairing_invalid_pointer = False
            try:
                active = discover_active_runtime(resolved_config.layout)
            except CodexWranglerError:
                if resolved_config.operation != "repair":
                    raise
                active = observe_repair_runtime(resolved_config.layout)
                repairing_invalid_pointer = True
                eprint(
                    "[codex-wrangler] Repairing an incomplete selected slot; "
                    "both fixed slots remain unchanged until the unique "
                    "candidate validates."
                )
            if repairing_invalid_pointer and active.slot_name is None:
                # A corrupt pointer has no trustworthy selected slot. Preserve a
                # valid slot A when one survives, otherwise use A deterministically;
                # any replaced fixed-slot contents remain quarantined after repair.
                candidate_slot = (
                    "b"
                    if read_slot_metadata(resolved_config.layout, "a") is not None
                    else "a"
                )
            else:
                candidate_slot = inactive_slot_name(active)
            preflight_managed_supporting_files(resolved_config)
            # Publish the complete ignore boundary under the maintenance lock
            # before any candidate or pointer mutation. A stale launcher reads
            # the committed slot record, so a shared-to-isolated switch must
            # never become active before its canonical HOME is ignored.
            ensure_gitignore_block(resolved_config)
            ensure_isolated_home_ready(
                resolved_config,
                allow_create=resolved_config.operation != "repair",
            )
            candidate_config = config_for_unique_candidate(resolved_config)
            eprint(
                "[codex-wrangler] Preserving active runtime: {}".format(
                    active.prefix or "<none>"
                )
            )
            eprint(
                "[codex-wrangler] Preparing unique candidate for inactive slot "
                "{}: {}".format(candidate_slot, candidate_config.layout.local_dir)
            )
            slot_swap = None
            try:
                prepare_candidate_install(resolved_config, candidate_config)
                write_text_file(
                    candidate_config.layout.local_package_json_path,
                    build_local_package_json(resolved_config.codex_version),
                    force=True,
                    dry_run=False,
                    managed_content_predicate=local_package_json_looks_managed,
                )
                with npm_maintenance_environment(resolved_config) as install_env:
                    run_command(
                        managed_npm_install_command(candidate_config, npm_name),
                        cwd=str(resolved_config.project_root),
                        env=npm_install_environment(
                            resolved_config,
                            base_env=install_env,
                        ),
                        timeout_seconds=resolved_config.npm_timeout_seconds,
                    )
                with npm_maintenance_environment(resolved_config) as verification_env:
                    verify_local_codex_binary(
                        candidate_config,
                        env=verification_env,
                    )
                write_slot_metadata(
                    resolved_config,
                    candidate_slot,
                    candidate_config,
                )
                fsync_candidate_tree(candidate_config.layout.local_dir)
                slot_swap = activate_candidate_slot(
                    resolved_config.layout,
                    candidate_config.layout.local_dir,
                    candidate_slot,
                )
                fixed_candidate_config = config_for_slot(
                    resolved_config,
                    candidate_slot,
                )
                with npm_maintenance_environment(resolved_config) as verification_env:
                    verify_local_codex_binary(
                        fixed_candidate_config,
                        env=verification_env,
                    )
                if read_slot_metadata(resolved_config.layout, candidate_slot) is None:
                    raise CodexWranglerError(
                        "Moved candidate lost its valid slot completion record."
                    )
                fsync_candidate_tree(fixed_candidate_config.layout.local_dir)
                pointer_kind = promote_active_slot(
                    resolved_config.layout,
                    candidate_slot,
                    allow_invalid_current=repairing_invalid_pointer,
                )
            except (PointerCommitDurabilityError, PointerCommittedInterrupt):
                # os.replace already committed the verified slot. Moving it now
                # would make the active pointer select unrelated content.
                raise
            except KeyboardInterrupt as exc:
                if slot_swap is not None:
                    pointer_committed, pointer_error = observe_candidate_pointer_commit(
                        resolved_config,
                        candidate_slot,
                    )
                    if pointer_committed:
                        raise PointerCommittedInterrupt(
                            "Interrupted after the active pointer switched to "
                            "verified slot {}; the new runtime remains active "
                            "and no rollback was attempted.".format(candidate_slot)
                        ) from exc
                    if pointer_committed is None:
                        raise PointerCommittedInterrupt(
                            "Interrupted after candidate activation, and the active "
                            "pointer could not be reread safely ({}). No rollback "
                            "was attempted because the pointer may already select "
                            "verified slot {}; inspect the managed state before "
                            "retrying.".format(pointer_error, candidate_slot)
                        ) from exc
                    try:
                        restore_slot_swap(slot_swap)
                    except CodexWranglerError as restore_error:
                        eprint("[codex-wrangler] CRITICAL: {}".format(restore_error))
                eprint(
                    "[codex-wrangler] Candidate installation was interrupted; "
                    "the active runtime remains untouched at {}.".format(
                        active.prefix or "<no previously active runtime>"
                    )
                )
                raise
            except CodexWranglerError as exc:
                if slot_swap is not None:
                    pointer_committed, pointer_error = observe_candidate_pointer_commit(
                        resolved_config,
                        candidate_slot,
                    )
                    if pointer_committed:
                        raise CodexWranglerError(
                            "{} The active pointer nevertheless committed verified "
                            "slot {}; the new runtime remains active and no rollback "
                            "was attempted.".format(exc, candidate_slot)
                        ) from exc
                    if pointer_committed is None:
                        raise CodexWranglerError(
                            "{} Candidate activation had begun, but the active "
                            "pointer could not be reread safely ({}). No rollback "
                            "was attempted because the pointer may already select "
                            "verified slot {}; inspect the managed state before "
                            "retrying.".format(exc, pointer_error, candidate_slot)
                        ) from exc
                    try:
                        restore_slot_swap(slot_swap)
                    except CodexWranglerError as restore_error:
                        raise CodexWranglerError(
                            "{} Candidate was not promoted, but restoring the "
                            "retired inactive slot also failed: {}. The active "
                            "pointer was not changed.".format(exc, restore_error)
                        ) from restore_error
                raise CodexWranglerError(
                    "{} Candidate installation was not promoted; the active "
                    "runtime remains untouched at {}.".format(
                        exc,
                        active.prefix or "<no previously active runtime>",
                    )
                ) from exc
            except Exception as exc:
                if slot_swap is not None:
                    pointer_committed, pointer_error = observe_candidate_pointer_commit(
                        resolved_config,
                        candidate_slot,
                    )
                    if pointer_committed:
                        raise CodexWranglerError(
                            "Unexpected candidate error after the active pointer "
                            "committed verified slot {}: {}. The new runtime remains "
                            "active and no rollback was attempted.".format(
                                candidate_slot,
                                exc,
                            )
                        ) from exc
                    if pointer_committed is None:
                        raise CodexWranglerError(
                            "Unexpected candidate error after activation began: {}. "
                            "The active pointer could not be reread safely ({}), so "
                            "no rollback was attempted; inspect the managed state "
                            "before retrying.".format(exc, pointer_error)
                        ) from exc
                    try:
                        restore_slot_swap(slot_swap)
                    except CodexWranglerError as restore_error:
                        raise CodexWranglerError(
                            "Unexpected candidate error: {}. Restoring the retired "
                            "inactive slot also failed: {}. The active pointer was "
                            "not changed.".format(exc, restore_error)
                        ) from restore_error
                raise CodexWranglerError(
                    "Unexpected candidate installation failure before promotion: "
                    "{}. The active runtime remains untouched at {}.".format(
                        exc,
                        active.prefix or "<no previously active runtime>",
                    )
                ) from exc
            published_config = replace(
                resolved_config,
                active_slot=candidate_slot,
                active_pointer_kind=pointer_kind,
            )
            summary_config = published_config
            try:
                publish_installed_state(published_config, fixed_candidate_config)
            except KeyboardInterrupt as exc:
                raise PointerCommittedInterrupt(
                    "Interrupted while refreshing generated support files after "
                    "the active pointer committed verified slot {}; the new runtime "
                    "remains active, but one or more projections may need to be "
                    "refreshed.".format(candidate_slot)
                ) from exc
            except Exception as exc:
                raise CodexWranglerError(
                    "Active pointer committed verified slot {}, but refreshing "
                    "generated support files failed: {}. The new runtime remains "
                    "active; rerun the same maintenance operation to reconcile the "
                    "projections.".format(candidate_slot, exc)
                ) from exc
            if slot_swap.retired_prefix is not None and repairing_invalid_pointer:
                eprint(
                    "[codex-wrangler] Retained previous inactive-slot evidence "
                    "after corrupt-pointer repair: {}".format(slot_swap.retired_prefix)
                )
            elif slot_swap.retired_prefix is not None:
                try:
                    remove_tree(
                        slot_swap.retired_prefix,
                        label="retired inactive managed install slot",
                        project_root=resolved_config.project_root,
                        dry_run=False,
                    )
                except KeyboardInterrupt as exc:
                    raise PointerCommittedInterrupt(
                        "Interrupted while cleaning retired inactive-slot data after "
                        "the active pointer committed verified slot {}; the new "
                        "runtime remains active and retained recovery data is safe "
                        "to inspect later.".format(candidate_slot)
                    ) from exc
                except (CodexWranglerError, OSError) as exc:
                    eprint(
                        "[codex-wrangler] Warning: retained retired inactive "
                        "slot after successful promotion: {}".format(exc)
                    )

    print(build_install_summary(summary_config))
    return 0


def update_operation(config: Config) -> int:
    """Refresh the locally known Codex channel versions without upgrading."""

    require_existing_managed_install(config)

    npm_name, _ = detect_npm_binaries()
    ensure_command_exists("node")
    ensure_command_exists(npm_name)

    with npm_lookup_environment(config) as lookup_env:
        available_versions = fetch_available_codex_versions(
            npm_name,
            config.project_root,
            env=lookup_env,
            timeout_seconds=config.npm_timeout_seconds,
        )

    def build_updated_config() -> Config:
        """Reload authority and combine it with the fetched catalog."""

        require_existing_managed_install(config)
        locked_config = reload_authoritative_config(config)
        return replace(
            locked_config,
            available_versions=available_versions,
            available_versions_updated_at=utc_now_iso(),
        )

    if config.dry_run:
        updated_config = build_updated_config()
        preflight_managed_supporting_files(updated_config)
        publish_projection_state(updated_config)
    else:
        with MaintenanceLock(config.layout):
            updated_config = build_updated_config()
            preflight_managed_supporting_files(updated_config)
            publish_projection_state(updated_config)

    eprint(
        "[codex-wrangler] Target project root: {}".format(updated_config.project_root)
    )
    eprint("[codex-wrangler] Operation: update")
    eprint("[codex-wrangler] Refreshing locally known stable/beta/alpha versions")

    print(
        "NOTE: This command updates available version information, but does not "
        'perform an upgrade; use "codex-wrangler --upgrade --channel '
        '<stable|beta|alpha> [--version <latest|x.y.z>]" for that.'
    )
    print("")
    print(build_available_versions_table(updated_config.available_versions))
    if updated_config.available_versions_updated_at:
        print("")
        print("Updated at: {}".format(updated_config.available_versions_updated_at))
    return 0


def launcher_matches_expected(config: Config) -> bool:
    """Return True when the launcher exactly matches the expected content."""

    path = config.layout.launcher_path
    if not path.exists() or not path.is_file():
        return False
    actual = path.read_text(encoding="utf-8")
    return actual == build_launcher_content(config)


def readme_matches_expected(config: Config) -> bool:
    """Return True when the generated local README exactly matches expectation."""

    path = config.layout.readme_path
    if not path.exists() or not path.is_file():
        return False
    actual = path.read_text(encoding="utf-8")
    return actual == build_local_readme_content(config)


def filesystem_entry_identity(
    path: Path,
    label: str,
) -> tuple[int, int, int, int, int]:
    """Return a no-follow identity tuple for one active-runtime path entry."""

    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise CodexWranglerError(
            "Failed to identify {} while resolving active runtime: {}".format(
                label,
                exc,
            )
        ) from exc
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def resolve_active_runtime_snapshot(
    config: Config,
) -> tuple[Config, tuple[Any, ...]]:
    """Resolve one runtime plus pointer and prefix identities for later comparison."""

    active = discover_active_runtime(config.layout)
    if active.slot_name is None:
        runtime_config = config
    else:
        runtime_config = replace(
            config_for_slot(config, active.slot_name),
            active_slot=active.slot_name,
            active_pointer_kind=active.pointer_kind,
        )

    pointer_path = None
    if active.pointer_kind == "symlink":
        pointer_path = config.layout.local_dir / ACTIVE_SYMLINK_NAME
    elif active.pointer_kind == "file":
        pointer_path = config.layout.local_dir / ACTIVE_SLOT_FILE_NAME
    pointer_identity = (
        filesystem_entry_identity(pointer_path, "active pointer")
        if pointer_path is not None
        else None
    )
    prefix_identity = (
        filesystem_entry_identity(active.prefix, "active runtime prefix")
        if active.prefix is not None
        else None
    )
    snapshot = (
        active.kind,
        active.slot_name,
        active.pointer_kind,
        str(active.prefix) if active.prefix is not None else None,
        pointer_identity,
        prefix_identity,
    )
    return runtime_config, snapshot


def active_runtime_config(config: Config) -> tuple[Config, Optional[str]]:
    """Resolve the npm prefix selected for launch while preserving diagnostics."""

    try:
        runtime_config, _ = resolve_active_runtime_snapshot(config)
    except CodexWranglerError as exc:
        return config, str(exc)
    return runtime_config, None


def inspect_fixed_slot(config: Config, slot_name: str) -> Dict[str, Any]:
    """Collect completion and version evidence for one fixed A/B slot."""

    slot_config = config_for_slot(config, slot_name)
    completion = read_slot_metadata(config.layout, slot_name)
    requested_version, package_error = infer_requested_version_with_error(
        slot_config.layout.local_package_json_path
    )
    locked_version, lock_error = infer_lockfile_version_with_error(
        slot_config.layout.local_package_lock_path
    )
    installed_manifest = local_codex_package_json_path(slot_config)
    return {
        "path": str(slot_config.layout.local_dir),
        "exists": slot_config.layout.local_dir.is_dir(),
        "completion_record_valid": completion is not None,
        "completion_record": completion,
        "requested_version": requested_version,
        "package_json_error": package_error,
        "locked_version": locked_version,
        "package_lock_error": lock_error,
        "installed_version": read_package_version(installed_manifest),
    }


def gather_inspection_report(config: Config) -> Dict[str, Any]:
    """Collect a conservative JSON inspection report for the target project."""

    npm_name, npx_name = detect_npm_binaries()
    metadata, metadata_parse_error = read_metadata_with_error(
        config.layout.metadata_path
    )
    runtime_environment = collect_runtime_diagnostics(
        config.project_root,
        config.shared_home,
        config.layout.codex_home_dir,
        npm_name,
        npx_name,
    )

    try:
        gitignore_text = (
            read_regular_text_if_present(
                config.layout.gitignore_path,
                "repository .gitignore",
            )
            or ""
        )
        gitignore_read_error = None
    except CodexWranglerError as exc:
        gitignore_text = ""
        gitignore_read_error = str(exc)
    gitignore_managed_block_present = (
        GITIGNORE_BEGIN in gitignore_text and GITIGNORE_END in gitignore_text
    )
    try:
        observed_active = observe_active_runtime(config.layout)
        pointer_observation_error = None
    except CodexWranglerError as exc:
        observed_active = None
        pointer_observation_error = str(exc)
    pointer_start_signature = (
        observed_active.kind if observed_active is not None else None,
        observed_active.slot_name if observed_active is not None else None,
        observed_active.pointer_kind if observed_active is not None else None,
        pointer_observation_error,
    )
    runtime_config, active_pointer_error = active_runtime_config(config)
    if active_pointer_error is None:
        active_pointer_error = pointer_observation_error
    observed_slot = observed_active.slot_name if observed_active is not None else None
    inactive_slot = (
        inactive_slot_name(observed_active) if observed_active is not None else None
    )
    fixed_slots = {
        slot_name: inspect_fixed_slot(config, slot_name) for slot_name in ("a", "b")
    }
    candidate_debris: List[str] = []
    retired_debris: List[str] = []
    if config.layout.local_dir.is_dir():
        candidate_debris = sorted(
            str(path)
            for path in config.layout.local_dir.glob(".candidate-*")
            if path.is_dir() and not path.is_symlink()
        )
        slot_container = slots_directory(config.layout)
        if slot_container.is_dir() and not slot_container.is_symlink():
            retired_debris = sorted(
                str(path)
                for path in slot_container.glob(".*.retired-*")
                if path.is_dir() and not path.is_symlink()
            )
    legacy_codex_bin = local_codex_bin_path(config)
    legacy_runtime_present = os.path.lexists(str(legacy_codex_bin))
    legacy_runtime_usable = legacy_runtime_is_usable(config.layout)
    local_codex_bin = local_codex_bin_path(runtime_config)
    local_codex_package_json = local_codex_package_json_path(runtime_config)
    expected_platform_package = expected_codex_platform_package_name()
    local_platform_package_json = local_codex_platform_package_json_path(
        runtime_config,
        expected_platform_package,
    )
    local_native_payloads = inspect_local_codex_native_payloads(runtime_config)
    requested_codex_version, local_package_json_parse_error = (
        infer_requested_version_with_error(
            runtime_config.layout.local_package_json_path
        )
    )
    installed_lockfile_version, local_package_lock_parse_error = (
        infer_lockfile_version_with_error(runtime_config.layout.local_package_lock_path)
    )

    report = {
        "schema_version": SCHEMA_VERSION,
        "script_name": SCRIPT_NAME,
        "script_version": SCRIPT_VERSION,
        "generated_at": utc_now_iso(),
        "project_root": str(config.project_root),
        "operation": "inspect",
        "layout_migration": (
            {
                "pending": True,
                "local_state": config.layout_migration.local_state,
                "home_state": config.layout_migration.home_state,
                "migrate_local": config.layout_migration.migrate_local,
                "migrate_home": config.layout_migration.migrate_home,
                "legacy_paths": {
                    "local_dir": str(config.layout_migration.legacy_layout.local_dir),
                    "codex_home_dir": str(
                        config.layout_migration.legacy_layout.codex_home_dir
                    ),
                },
                "canonical_paths": {
                    "local_dir": str(
                        config.layout_migration.canonical_layout.local_dir
                    ),
                    "codex_home_dir": str(
                        config.layout_migration.canonical_layout.codex_home_dir
                    ),
                },
            }
            if config.layout_migration is not None
            else {
                "pending": False,
            }
        ),
        "known_versions": build_known_versions(
            config.available_versions,
            config.available_versions_updated_at,
        ),
        "requested_configuration": {
            "codex_selector": config.codex_selector,
            "codex_channel": config.codex_channel,
            "codex_version": config.codex_version,
            "shared_home": config.shared_home,
            "reasonable_permissions_enabled": (config.reasonable_permissions_enabled),
            "version_source": config.version_source,
        },
        "runtime_environment": runtime_environment,
        "paths": {
            "local_dir": str(config.layout.local_dir),
            "codex_home_dir": str(config.layout.codex_home_dir),
            "launcher": str(config.layout.launcher_path),
            "readme_local": str(config.layout.readme_path),
            "local_package_json": str(runtime_config.layout.local_package_json_path),
            "local_package_lock": str(runtime_config.layout.local_package_lock_path),
            "local_codex_bin": str(local_codex_bin),
            "local_codex_package_json": str(local_codex_package_json),
            "local_codex_platform_package_json": (
                str(local_platform_package_json)
                if local_platform_package_json is not None
                else None
            ),
            "local_codex_native_payloads": [
                item["path"] for item in local_native_payloads
            ],
            "metadata": str(config.layout.metadata_path),
            "gitignore": str(config.layout.gitignore_path),
            "active_runtime_prefix": str(runtime_config.layout.local_dir),
        },
        "state": {
            "git_repository": (config.project_root / ".git").exists(),
            "local_dir_exists": config.layout.local_dir.exists(),
            "local_package_json_exists": (
                runtime_config.layout.local_package_json_path.exists()
            ),
            "local_package_lock_exists": (
                runtime_config.layout.local_package_lock_path.exists()
            ),
            "node_modules_exists": runtime_config.layout.local_node_modules_dir.exists(),
            "active_slot": observed_slot,
            "active_pointer_kind": (
                observed_active.pointer_kind if observed_active is not None else None
            ),
            "active_pointer_valid": active_pointer_error is None,
            "active_pointer_error": active_pointer_error,
            "inactive_slot": inactive_slot,
            "legacy_runtime_present": legacy_runtime_present,
            "legacy_runtime_usable": legacy_runtime_usable,
            "slots": fixed_slots,
            "candidate_debris": candidate_debris,
            "retired_slot_debris": retired_debris,
            "local_codex_bin_exists": local_codex_bin.exists(),
            "local_codex_bin_executable": (
                os.access(local_codex_bin, os.X_OK)
                if local_codex_bin.exists()
                else False
            ),
            "expected_codex_platform_package": expected_platform_package,
            "local_codex_platform_package_json_exists": (
                local_platform_package_json.exists()
                if local_platform_package_json is not None
                else False
            ),
            "metadata_exists": metadata is not None,
            "metadata_parse_error": metadata_parse_error,
            "metadata": metadata,
            "available_versions": dict(config.available_versions),
            "available_versions_updated_at": config.available_versions_updated_at,
            "reasonable_permissions_enabled": (config.reasonable_permissions_enabled),
            "codex_home_exists": config.layout.codex_home_dir.exists(),
            "launcher_exists": config.layout.launcher_path.exists(),
            "launcher_executable": (
                os.access(config.layout.launcher_path, os.X_OK)
                if config.layout.launcher_path.exists()
                else False
            ),
            "launcher_matches_expected": launcher_matches_expected(config),
            "readme_exists": config.layout.readme_path.exists(),
            "readme_matches_expected": readme_matches_expected(config),
            "gitignore_exists": config.layout.gitignore_path.exists(),
            "gitignore_read_error": gitignore_read_error,
            "gitignore_managed_block_present": gitignore_managed_block_present,
            "requested_codex_version_in_package_json": requested_codex_version,
            "local_package_json_parse_error": local_package_json_parse_error,
            "installed_codex_version_in_package_dir": (
                read_package_version(local_codex_package_json)
            ),
            "installed_codex_platform_package_version": (
                read_package_version(local_platform_package_json)
                if local_platform_package_json is not None
                else None
            ),
            "local_codex_native_payloads": local_native_payloads,
            "local_codex_native_payloads_valid": (
                all(item["valid"] for item in local_native_payloads)
                if local_native_payloads
                else None
            ),
            "installed_codex_version_in_lockfile": installed_lockfile_version,
            "local_package_lock_parse_error": local_package_lock_parse_error,
            "node_found": runtime_environment["resolved_commands"]["node"]["found"],
            "npm_found": runtime_environment["resolved_commands"]["npm"]["found"],
            "npx_found": runtime_environment["resolved_commands"]["npx"]["found"],
        },
        "warnings": [],
        "issues": [],
    }

    state = report["state"]
    warnings = report["warnings"]
    issues = report["issues"]

    if config.layout_migration is not None:
        warnings.append(migration_observation_message(config.layout_migration))
    if not state["git_repository"]:
        warnings.append("Target project root does not appear to be a git repository.")
    if state["active_pointer_error"]:
        issues.append(state["active_pointer_error"])
    if state["gitignore_read_error"]:
        issues.append(
            "Repository .gitignore could not be read safely: {}".format(
                state["gitignore_read_error"]
            )
        )
    if state["metadata_parse_error"]:
        issues.append(
            "Managed metadata could not be parsed: {}".format(
                state["metadata_parse_error"]
            )
        )
    if state["local_package_json_parse_error"]:
        issues.append(
            "Managed package.json could not be parsed: {}".format(
                state["local_package_json_parse_error"]
            )
        )
    if state["local_package_lock_parse_error"]:
        issues.append(
            "Managed package-lock.json could not be parsed: {}".format(
                state["local_package_lock_parse_error"]
            )
        )
    if state["local_package_json_exists"] and not state["node_modules_exists"]:
        issues.append(
            "Managed package manifest exists, but node_modules is missing. The install may be incomplete."
        )
    if state["node_modules_exists"] and not state["local_codex_bin_exists"]:
        issues.append(
            "Managed node_modules exists, but the local Codex executable is missing."
        )
    if state["local_codex_bin_exists"] and not state["local_codex_bin_executable"]:
        issues.append("Local Codex executable exists but is not executable.")
    if (
        state["expected_codex_platform_package"]
        and state["node_modules_exists"]
        and not state["local_codex_platform_package_json_exists"]
    ):
        issues.append(
            "Expected platform package {} is missing its package.json under "
            "node_modules.".format(state["expected_codex_platform_package"])
        )
    if state["local_codex_native_payloads_valid"] is False:
        native_errors = [
            item["error"]
            for item in state["local_codex_native_payloads"]
            if item["error"]
        ]
        issues.append(
            "Managed native Codex payload validation failed: {}".format(
                "; ".join(native_errors)
            )
        )
    if state["launcher_exists"] and not state["launcher_matches_expected"]:
        warnings.append(
            "Launcher exists but does not exactly match the expected generated content."
        )
    if state["readme_exists"] and not state["readme_matches_expected"]:
        warnings.append(
            "Local README exists but does not exactly match the expected generated content."
        )
    if not state["gitignore_managed_block_present"]:
        warnings.append("Managed .gitignore block is not present.")
    package_manager = runtime_environment["package_manager_declaration"]
    if package_manager and package_manager.get("read_error"):
        warnings.append(
            "Target project package.json could not be parsed for packageManager: {}".format(
                package_manager["read_error"]
            )
        )
    selector_alignment = runtime_environment["selector_alignment"]
    if (
        selector_alignment["status"] in ("mismatch", "unknown")
        and selector_alignment["signals_present"]
    ):
        warnings.append(selector_alignment["summary"])
    requested_codex_version = state["requested_codex_version_in_package_json"]
    installed_package_dir_version = state["installed_codex_version_in_package_dir"]
    installed_lockfile_version = state["installed_codex_version_in_lockfile"]
    if (
        requested_codex_version
        and installed_package_dir_version
        and requested_codex_version != installed_package_dir_version
    ):
        issues.append(
            "Managed package.json requests @openai/codex {}, but node_modules "
            "contains {}.".format(
                requested_codex_version,
                installed_package_dir_version,
            )
        )
    if (
        requested_codex_version
        and installed_lockfile_version
        and requested_codex_version != installed_lockfile_version
    ):
        issues.append(
            "Managed package.json requests @openai/codex {}, but package-lock "
            "records {}.".format(requested_codex_version, installed_lockfile_version)
        )
    if not state["node_found"] or not state["npm_found"]:
        issues.append("Required node/npm commands are not available in PATH.")
    if metadata and metadata.get("project_root") != str(config.project_root):
        issues.append(
            "Managed metadata project_root does not match the inspected project root."
        )

    try:
        final_active = observe_active_runtime(config.layout)
        pointer_end_signature = (
            final_active.kind,
            final_active.slot_name,
            final_active.pointer_kind,
            None,
        )
    except CodexWranglerError as exc:
        pointer_end_signature = (None, None, None, str(exc))
    if pointer_end_signature != pointer_start_signature:
        issues.append(
            "Managed active pointer changed during inspection; retry for one "
            "coherent snapshot."
        )

    return report


def inspect_operation(config: Config) -> int:
    """Emit a JSON inspection report to stdout."""

    report = gather_inspection_report(config)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if not report["issues"] else 1


def uninstall_operation(config: Config) -> int:
    """Conservatively remove the managed installation."""

    if config.dry_run:
        return uninstall_locked_operation(config)
    with MaintenanceLock(config.layout):
        try:
            locked_config = reload_authoritative_config(config)
        except CodexWranglerError:
            if not config.force:
                raise
            locked_config = config
        return uninstall_locked_operation(locked_config)


def uninstall_locked_operation(config: Config) -> int:
    """Remove managed paths while the caller owns maintenance serialization."""

    compatibility_links = compatibility_links_for_uninstall(config)
    if not config.force:
        require_uninstall_ownership(config)

    remove_file_if_managed(
        config.layout.launcher_path,
        build_launcher_content(config),
        force=config.force,
        dry_run=config.dry_run,
        label="launcher",
    )
    remove_file_if_managed(
        config.layout.readme_path,
        build_local_readme_content(config),
        force=config.force,
        dry_run=config.dry_run,
        label="local README",
    )
    remove_compatibility_links(
        compatibility_links,
        dry_run=config.dry_run,
    )
    remove_tree(
        config.layout.local_dir,
        label="managed local directory",
        project_root=config.project_root,
        dry_run=config.dry_run,
    )
    if not config.shared_home:
        remove_tree(
            config.layout.codex_home_dir,
            label="managed Codex home directory",
            project_root=config.project_root,
            dry_run=config.dry_run,
        )
    maybe_remove_empty_parent(
        config.layout.launcher_path,
        stop_at=config.project_root,
        dry_run=config.dry_run,
    )
    # Keep the complete ignore block until all potentially sensitive local
    # state is absent. Uninstall intentionally preserves unrelated `.local`
    # content and historical `.codex` state, regardless of the recorded HOME
    # mode; narrowing their ignore coverage could expose history or auth data.
    protected_paths = (
        config.project_root / ".codex",
        config.project_root / LEGACY_LOCAL_DIR,
        config.project_root / LEGACY_HOME_DIR,
        config.layout.codex_home_dir,
    )
    protected_state_survives = any(
        os.path.lexists(str(path)) for path in protected_paths
    )
    local_state_root = config.project_root / ".local"
    if os.path.lexists(str(local_state_root)):
        if local_state_root.is_dir() and not local_state_root.is_symlink():
            try:
                protected_state_survives = protected_state_survives or any(
                    local_state_root.iterdir()
                )
            except OSError:
                protected_state_survives = True
        else:
            protected_state_survives = True
    retained_ignore_block = (
        build_gitignore_block(config.layout)
        if protected_state_survives
        else build_maintenance_lock_gitignore_block()
    )
    upsert_gitignore_block(
        config.layout.gitignore_path,
        retained_ignore_block,
        dry_run=config.dry_run,
    )

    print(
        "Uninstall complete for {}{}.".format(
            config.project_root,
            " (dry-run)" if config.dry_run else "",
        )
    )
    return 0


def selftest_operation(config: Config) -> int:
    """Run a pessimistic verification suite against the managed install."""

    npm_name, _ = detect_npm_binaries()
    try:
        _, initial_audit_runtime_snapshot = resolve_active_runtime_snapshot(config)
    except CodexWranglerError as exc:
        initial_audit_runtime_snapshot = ("unresolved", str(exc))

    report = gather_inspection_report(config)
    results: List[SelfTestResult] = []

    def record(name: str, ok: bool, detail: str) -> None:
        results.append(SelfTestResult(name=name, ok=ok, detail=detail))

    state = report["state"]
    runtime_environment = report["runtime_environment"]
    resolved_commands = runtime_environment["resolved_commands"]
    record(
        "metadata_exists", bool(state["metadata_exists"]), "managed metadata missing"
    )
    record(
        "launcher_matches_expected",
        bool(state["launcher_matches_expected"]),
        "launcher missing or not exactly as expected",
    )
    record(
        "readme_matches_expected",
        bool(state["readme_matches_expected"]),
        "local README missing or not exactly as expected",
    )
    record(
        "gitignore_managed_block_present",
        bool(state["gitignore_managed_block_present"]),
        "managed .gitignore block missing",
    )
    record(
        "installed_codex_version_present",
        bool(state["installed_codex_version_in_lockfile"]),
        "installed Codex version could not be inferred from package-lock.json",
    )
    record(
        "node_command_found",
        bool(state["node_found"]),
        "node command is not available in PATH",
    )
    record(
        "npm_command_found",
        bool(state["npm_found"]),
        "npm command is not available in PATH",
    )
    record(
        "local_codex_bin_exists",
        bool(state.get("local_codex_bin_exists")),
        "local Codex executable is missing from node_modules/.bin",
    )
    record(
        "local_codex_bin_executable",
        bool(state.get("local_codex_bin_executable")),
        "local Codex executable is not executable",
    )
    native_payloads = state.get("local_codex_native_payloads") or []
    native_payload_errors = [
        item.get("error") for item in native_payloads if item.get("error")
    ]
    native_payloads_valid = state.get("local_codex_native_payloads_valid") is True
    if native_payload_errors:
        native_payload_detail = "; ".join(native_payload_errors)
    elif native_payloads_valid:
        native_payload_detail = (
            "native Codex payloads passed declared-extent validation"
        )
    else:
        native_payload_detail = "native Codex payload validation was unavailable"
    record(
        "local_codex_native_payloads_valid",
        native_payloads_valid,
        native_payload_detail,
    )
    selector_alignment = runtime_environment["selector_alignment"]
    record(
        "runtime_selector_alignment",
        selector_alignment["status"] != "mismatch",
        selector_alignment["summary"],
    )

    if (
        state["launcher_exists"]
        and state["launcher_matches_expected"]
        and state.get("local_codex_native_payloads_valid") is True
    ):
        try:
            version_result = run_command(
                [str(config.layout.launcher_path), "--version"],
                cwd=str(config.project_root),
                capture_output=True,
            )
        except CodexWranglerError as exc:
            record("launcher_version_runs", False, str(exc))
        else:
            version_text = (version_result.stdout or "").strip()
            record(
                "launcher_version_runs",
                version_text.startswith("codex-cli "),
                "launcher did not report a Codex version",
            )

        try:
            resume_result = run_command(
                [str(config.layout.launcher_path), "resume", "--help"],
                cwd=str(config.project_root),
                capture_output=True,
            )
        except CodexWranglerError as exc:
            record("launcher_resume_help_runs", False, str(exc))
        else:
            resume_help = (resume_result.stdout or "") + (resume_result.stderr or "")
            record(
                "launcher_resume_help_runs",
                "Usage: codex resume" in resume_help,
                "launcher resume --help did not return the expected usage text",
            )
    else:
        record(
            "launcher_commands_skipped",
            False,
            "launcher verification commands were skipped because the launcher or native payload is not trustworthy",
        )

    if resolved_commands["npm"]["found"]:
        try:
            with MaintenanceLock(config.layout):
                runtime_config, locked_audit_runtime_snapshot = (
                    resolve_active_runtime_snapshot(config)
                )
                if locked_audit_runtime_snapshot != initial_audit_runtime_snapshot:
                    raise CodexWranglerError(
                        "Managed active pointer or runtime changed after self-test "
                        "inspection began; npm audit was not run against a mixed "
                        "snapshot. Retry self-test."
                    )
                with npm_maintenance_environment(config) as audit_env:
                    audit_result = run_command(
                        [
                            npm_name,
                            "audit",
                            "--prefix",
                            str(runtime_config.layout.local_dir),
                            "--json",
                        ],
                        cwd=str(config.project_root),
                        capture_output=True,
                        env=audit_env,
                        timeout_seconds=config.npm_timeout_seconds,
                    )
        except CodexWranglerError as exc:
            record("npm_audit_clean", False, str(exc))
        else:
            try:
                audit_payload = json.loads(audit_result.stdout or "")
            except json.JSONDecodeError as exc:
                record(
                    "npm_audit_clean",
                    False,
                    "npm audit returned invalid JSON: {}".format(exc),
                )
            else:
                vulnerabilities = audit_payload.get("metadata", {}).get(
                    "vulnerabilities", {}
                )
                total_vulnerabilities = vulnerabilities.get("total")
                record(
                    "npm_audit_clean",
                    total_vulnerabilities == 0,
                    "npm audit reported {} total vulnerabilities".format(
                        total_vulnerabilities
                    ),
                )
    else:
        record("npm_audit_clean", False, "npm command is not available in PATH")

    failures = [result for result in results if not result.ok]
    for result in results:
        print(
            "[{}] {} - {}".format(
                "PASS" if result.ok else "FAIL",
                result.name,
                result.detail,
            )
        )

    if failures:
        print("")
        print("Self-test failed.")
        for result in failures:
            print(" - {}: {}".format(result.name, result.detail))
        return 1

    print("")
    print("Self-test passed.")
    return 0
