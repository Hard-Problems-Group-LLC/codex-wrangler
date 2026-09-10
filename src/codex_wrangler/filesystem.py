"""Managed filesystem mutation helpers."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Callable, Dict, Optional, Sequence

from .constants import GITIGNORE_BEGIN, GITIGNORE_END, README_MARKER, SCRIPT_MARKER
from .models import CodexWranglerError, Config
from .runtime import eprint


def require_regular_file_or_absent(path: Path, label: str) -> None:
    """Reject a projection target that is linked or not a regular file."""

    if not os.path.lexists(str(path)):
        return
    if path.is_symlink():
        raise CodexWranglerError(
            "Refusing to overwrite symbolic-link target for {}: {}".format(
                label,
                path,
            )
        )
    try:
        mode = os.lstat(path).st_mode
    except OSError as exc:
        raise CodexWranglerError(
            "Failed to inspect {} at {}: {}".format(label, path, exc)
        ) from exc
    if not stat.S_ISREG(mode):
        raise CodexWranglerError(
            "Expected a regular file or absent path for {}, but found another "
            "filesystem object: {}".format(label, path)
        )


def read_regular_text_if_present(path: Path, label: str) -> Optional[str]:
    """Read one regular projection target, or return ``None`` when absent."""

    require_regular_file_or_absent(path, label)
    if not os.path.lexists(str(path)):
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise CodexWranglerError(
            "Failed to read {} at {}: {}".format(label, path, exc)
        ) from exc


def fsync_directory(path: Path) -> None:
    """Flush directory entries when the current platform supports it."""

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        if os.name == "nt":
            return
        raise
    try:
        try:
            os.fsync(descriptor)
        except OSError:
            if os.name != "nt":
                raise
    finally:
        os.close(descriptor)


def atomic_write_text(
    path: Path,
    content: str,
    *,
    executable: bool = False,
) -> None:
    """Durably replace one regular text file without a truncated live state."""

    require_regular_file_or_absent(path, "managed text projection")
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = 0o755 if executable else 0o644
    if path.exists() and path.is_file():
        mode = stat.S_IMODE(path.stat().st_mode)
        if executable:
            mode |= 0o111
    descriptor, temporary_name = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=".{}.".format(path.name),
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    replaced = False
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.chmod(temporary, mode)
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        replaced = True
        fsync_directory(path.parent)
    except OSError as exc:
        if replaced:
            raise CodexWranglerError(
                "Atomically replaced {}, but failed to flush its containing "
                "directory; the new file is visible with uncertain crash "
                "durability: {}".format(path, exc)
            ) from exc
        raise CodexWranglerError(
            "Failed to atomically replace {}: {}".format(path, exc)
        ) from exc
    finally:
        if temporary.exists():
            temporary.unlink()


def _block_end_index(text: str, marker: str) -> int:
    """Return the index just after the marker line, even at EOF."""

    start = text.index(marker)
    newline_index = text.find("\n", start)
    if newline_index == -1:
        return len(text)
    return newline_index + 1


def upsert_gitignore_block(
    gitignore_path: Path,
    block_text: str,
    dry_run: bool,
) -> None:
    """Insert or replace the managed `.gitignore` block idempotently."""

    existing_text = (
        read_regular_text_if_present(
            gitignore_path,
            "repository .gitignore",
        )
        or ""
    )

    if GITIGNORE_BEGIN in existing_text and GITIGNORE_END not in existing_text:
        raise CodexWranglerError(
            "Found a managed .gitignore begin marker without its end marker in "
            "{}. Refusing to guess.".format(gitignore_path)
        )

    if GITIGNORE_BEGIN in existing_text and GITIGNORE_END in existing_text:
        begin_index = existing_text.index(GITIGNORE_BEGIN)
        end_index = _block_end_index(existing_text, GITIGNORE_END)
        replacement = (
            existing_text[:begin_index] + block_text + existing_text[end_index:]
        )
    else:
        prefix = existing_text
        if prefix and not prefix.endswith("\n"):
            prefix += "\n"
        if prefix and not prefix.endswith("\n\n"):
            prefix += "\n"
        replacement = prefix + block_text

    if replacement == existing_text:
        eprint("[codex-wrangler] .gitignore block already up to date")
        return

    action = "Would update" if dry_run else "Updating"
    eprint("[codex-wrangler] {} {}".format(action, gitignore_path))
    if dry_run:
        return

    atomic_write_text(gitignore_path, replacement)


def remove_gitignore_block(gitignore_path: Path, dry_run: bool) -> bool:
    """Remove the managed `.gitignore` block if it exists."""

    if not gitignore_path.exists():
        return False

    existing_text = gitignore_path.read_text(encoding="utf-8")
    if GITIGNORE_BEGIN not in existing_text:
        return False
    if GITIGNORE_END not in existing_text:
        raise CodexWranglerError(
            "Found a managed .gitignore begin marker without its end marker in "
            "{}. Refusing to guess.".format(gitignore_path)
        )

    begin_index = existing_text.index(GITIGNORE_BEGIN)
    end_index = _block_end_index(existing_text, GITIGNORE_END)
    replacement = existing_text[:begin_index] + existing_text[end_index:]
    replacement = replacement.rstrip() + ("\n" if replacement.strip() else "")

    action = "Would remove" if dry_run else "Removing"
    eprint("[codex-wrangler] {} managed block from {}".format(action, gitignore_path))
    if dry_run:
        return True

    atomic_write_text(gitignore_path, replacement)
    return True


def write_text_file(
    path: Path,
    content: str,
    force: bool,
    dry_run: bool,
    executable: bool = False,
    managed_markers: Sequence[str] = (),
    managed_content_predicate: Optional[Callable[[str], bool]] = None,
) -> None:
    """Write a deterministic text file with conservative overwrite rules."""

    existing = read_regular_text_if_present(path, "managed text file")
    if existing is not None:
        if existing == content:
            eprint("[codex-wrangler] Unchanged: {}".format(path))
            if executable and not dry_run:
                path.chmod(path.stat().st_mode | 0o111)
            return
        marker_present = any(marker in existing for marker in managed_markers)
        predicate_matches = (
            managed_content_predicate(existing)
            if managed_content_predicate is not None
            else False
        )
        if not force and not marker_present and not predicate_matches:
            raise CodexWranglerError(
                "Refusing to overwrite existing file without --force: {}".format(path)
            )

    action = "Would write" if dry_run else "Writing"
    eprint("[codex-wrangler] {}: {}".format(action, path))
    if dry_run:
        return

    atomic_write_text(path, content, executable=executable)


def validate_text_file_write(
    path: Path,
    content: str,
    force: bool,
    *,
    managed_markers: Sequence[str] = (),
    managed_content_predicate: Optional[Callable[[str], bool]] = None,
) -> None:
    """Validate one future managed text replacement without changing state."""

    existing = read_regular_text_if_present(path, "managed text file")
    if existing is None or existing == content:
        return
    marker_present = any(marker in existing for marker in managed_markers)
    predicate_matches = (
        managed_content_predicate(existing)
        if managed_content_predicate is not None
        else False
    )
    if not force and not marker_present and not predicate_matches:
        raise CodexWranglerError(
            "Refusing to overwrite existing file without --force: {}".format(path)
        )


def validate_gitignore_block_update(gitignore_path: Path) -> None:
    """Validate that a future managed ignore-block update is unambiguous."""

    existing = (
        read_regular_text_if_present(
            gitignore_path,
            "repository .gitignore",
        )
        or ""
    )
    if GITIGNORE_BEGIN in existing and GITIGNORE_END not in existing:
        raise CodexWranglerError(
            "Found a managed .gitignore begin marker without its end marker in "
            "{}. Refusing to guess.".format(gitignore_path)
        )


def write_metadata(path: Path, payload: Dict[str, object], dry_run: bool) -> None:
    """Write the managed metadata file as pretty JSON."""

    action = "Would write" if dry_run else "Writing"
    eprint("[codex-wrangler] {} metadata: {}".format(action, path))
    if dry_run:
        return

    atomic_write_text(
        path,
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
    )


def ensure_managed_directories(config: Config) -> None:
    """Create the managed directories unless this is a dry-run."""

    if config.dry_run:
        eprint(
            "[codex-wrangler] Would ensure directory exists: {}".format(
                config.layout.local_dir
            )
        )
        if not config.shared_home:
            eprint(
                "[codex-wrangler] Would ensure directory exists: {}".format(
                    config.layout.codex_home_dir
                )
            )
        if config.layout.launcher_path.parent != config.project_root:
            eprint(
                "[codex-wrangler] Would ensure directory exists: {}".format(
                    config.layout.launcher_path.parent
                )
            )
        return

    config.layout.local_dir.mkdir(parents=True, exist_ok=True)
    if not config.shared_home:
        config.layout.codex_home_dir.mkdir(parents=True, exist_ok=True)
    config.layout.launcher_path.parent.mkdir(parents=True, exist_ok=True)


def require_safe_managed_path(path: Path, project_root: Path, label: str) -> None:
    """Require a contained path reached without symbolic-link components."""

    try:
        canonical_root = project_root.resolve(strict=True)
    except OSError as exc:
        raise CodexWranglerError(
            "Failed to resolve the project root before touching {}: {}".format(
                label,
                exc,
            )
        ) from exc
    lexical_path = Path(os.path.abspath(str(path)))
    try:
        relative_parts = lexical_path.relative_to(canonical_root).parts
    except ValueError as exc:
        raise CodexWranglerError(
            "{} is outside the project root and will not be touched: {}".format(
                label, path
            )
        ) from exc
    if not relative_parts:
        raise CodexWranglerError(
            "{} resolves to the project root itself and will not be touched.".format(
                label
            )
        )

    cursor = canonical_root
    for index, part in enumerate(relative_parts):
        cursor /= part
        if not os.path.lexists(str(cursor)):
            continue
        try:
            mode = os.lstat(cursor).st_mode
        except OSError as exc:
            raise CodexWranglerError(
                "Failed to inspect {} path component {}: {}".format(
                    label,
                    cursor,
                    exc,
                )
            ) from exc
        if stat.S_ISLNK(mode):
            raise CodexWranglerError(
                "{} path may not contain a symbolic link component: {}".format(
                    label,
                    cursor,
                )
            )
        if index < len(relative_parts) - 1 and not stat.S_ISDIR(mode):
            raise CodexWranglerError(
                "{} ancestor is not a directory: {}".format(label, cursor)
            )

    try:
        resolved = lexical_path.resolve()
        resolved.relative_to(canonical_root)
    except (OSError, ValueError) as exc:
        raise CodexWranglerError(
            "{} cannot be resolved safely beneath the project root: {}".format(
                label,
                path,
            )
        ) from exc


def remove_file_if_managed(
    path: Path,
    expected_content: str,
    force: bool,
    dry_run: bool,
    label: str,
) -> bool:
    """Remove a generated file only when ownership can be proven."""

    if not path.exists() and not path.is_symlink():
        return False
    if not path.is_file():
        raise CodexWranglerError(
            "Expected a file at {}, but found something else. Refusing to "
            "remove it automatically{}.".format(
                path,
                " even with --force" if force else " without --force",
            )
        )

    actual_content = path.read_text(encoding="utf-8")
    marker_present = SCRIPT_MARKER in actual_content or README_MARKER in actual_content
    if actual_content != expected_content and not (marker_present and force):
        raise CodexWranglerError(
            "Refusing to remove {} because it does not exactly match the "
            "expected generated content. Use --force to override.".format(path)
        )

    action = "Would remove" if dry_run else "Removing"
    eprint("[codex-wrangler] {} {}: {}".format(action, label, path))
    if dry_run:
        return True
    path.unlink()
    fsync_directory(path.parent)
    return True


def remove_tree(path: Path, label: str, project_root: Path, dry_run: bool) -> bool:
    """Remove a managed directory tree after path safety checks."""

    if not path.exists() and not path.is_symlink():
        return False

    require_safe_managed_path(path, project_root, label)
    if path.is_symlink():
        raise CodexWranglerError(
            "{} is a symbolic link and will not be removed automatically: {}".format(
                label,
                path,
            )
        )
    if not path.is_dir():
        raise CodexWranglerError(
            "{} is not a directory and will not be removed automatically: {}".format(
                label,
                path,
            )
        )
    action = "Would remove" if dry_run else "Removing"
    eprint("[codex-wrangler] {} {}: {}".format(action, label, path))
    if dry_run:
        return True

    shutil.rmtree(path)
    fsync_directory(path.parent)
    return True


def maybe_remove_empty_parent(path: Path, stop_at: Path, dry_run: bool) -> None:
    """Remove empty parent directories up to, but not including, stop_at."""

    current = path.parent
    while current != stop_at and current.exists():
        try:
            next(current.iterdir())
            break
        except StopIteration:
            action = "Would remove" if dry_run else "Removing"
            eprint("[codex-wrangler] {} empty directory: {}".format(action, current))
            if not dry_run:
                current.rmdir()
                fsync_directory(current.parent)
            current = current.parent
